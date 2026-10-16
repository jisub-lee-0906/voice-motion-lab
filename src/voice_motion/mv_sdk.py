"""Official Comfy SDK execution, restricted to this checkout's local MV graph.

The SDK really submits the renderer node and downloads its files through API v2.
It never calls a cloud endpoint, reads a stored token, or falls back to direct
engine execution. See tools/setup_mv_sdk.py for the pinned runtime.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
import importlib.metadata
import json
import math
import os
from pathlib import Path
import re
from threading import Lock

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROXY_URL = "http://127.0.0.1:8795"
COMFY_URL = "http://127.0.0.1:8794"
NODE_ID = "mv_create"
SDK_VERSION = "0.5.0"
PROXY_VERSION = "0.1.6"
_ENV_LOCK = Lock()
_JOB_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{7,63}\Z")
_ARTIFACTS = {
    "video.mp4": "video_path", "project.json": "project_path",
    "storyboard.jpg": "storyboard_path", "result.json": "metadata_path",
}


def sdk_status() -> dict:
    versions = {}
    for name in ("comfy-sdk", "comfy-api-proxy"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return {"available": versions["comfy-sdk"] == SDK_VERSION,
            "provider": "comfy-sdk-local-api-v2", "versions": versions,
            "proxy_url": PROXY_URL, "comfy_url": COMFY_URL}


def validate_job_paths(audio_path: Path, output_dir: Path) -> tuple[Path, Path, str]:
    """Only the fixed source.wav for a corresponding project-owned job is legal."""
    audio = Path(audio_path).resolve(strict=True)
    output = Path(output_dir).resolve(strict=True)
    job_id = output.name
    if not _JOB_ID.fullmatch(job_id):
        raise ValueError("Invalid MV job identifier.")
    approved_output = PROJECT_ROOT / "output" / "mv" / job_id
    approved_audio = PROJECT_ROOT / "input" / "mv" / job_id / "source.wav"
    # Compare against lexical approved paths as well as resolved boundaries:
    # a symlink/junction underneath an approved parent must not escape it.
    if output != approved_output or not output.is_dir():
        raise ValueError("MV output must be this project's corresponding job directory.")
    if audio != approved_audio or not audio.is_file():
        raise ValueError("MV input must be this project's corresponding source.wav.")
    if audio.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("MV source.wav exceeds the local 32 MiB limit.")
    return audio, output, job_id


def build_mv_graph(job_id: str, transcript: dict, theme: str = "auto", seed: int = 7) -> dict:
    if not _JOB_ID.fullmatch(job_id):
        raise ValueError("Invalid MV job identifier.")
    if theme not in ("auto", "bloom", "fracture", "orbit"):
        raise ValueError("Unsupported MV theme.")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2147483647:
        raise ValueError("MV seed must be an integer from 0 to 2147483647.")
    if not isinstance(transcript, dict) or not isinstance(transcript.get("segments"), list):
        raise ValueError("MV transcript must contain segments.")
    encoded = json.dumps(transcript, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > 1024 * 1024:
        raise ValueError("MV transcript exceeds 1 MiB.")
    return {NODE_ID: {"class_type": "VoiceMotionMVCreate", "inputs": {
        "job_id": job_id, "transcript_json": encoded, "theme": theme, "seed": seed,
    }}}


def _make_client():
    """The 0.5 SDK configures origins only via env, captured at construction.

    Set both origins and remove ambient keys just while constructing the client,
    then restore them before any await. No shared config or saved token changes.
    """
    from comfy_sdk import AsyncComfy

    names = ("COMFY_BASE_URL", "COMFY_ROUTER_BASE_URL", "COMFY_API_KEY", "NO_PROXY")
    with _ENV_LOCK:
        saved = {name: os.environ.get(name) for name in names}
        try:
            os.environ["COMFY_BASE_URL"] = PROXY_URL
            os.environ["COMFY_ROUTER_BASE_URL"] = PROXY_URL
            os.environ.pop("COMFY_API_KEY", None)
            # httpx captures environment proxy routing at construction too.
            os.environ["NO_PROXY"] = "*"
            return AsyncComfy(timeout=30, client_info="voice-motion-lab/0.3-local")
        finally:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


async def _events(job, progress):
    """Live progress is optional; authoritative wait/poll decides completion."""
    from comfy_sdk.events import Progress, StatusChange

    try:
        async for event in job.events():
            if isinstance(event, Progress) and math.isfinite(event.value):
                progress(.08 + .82 * min(1., max(0., event.value)),
                         event.message or "Comfy graph is rendering the music video")
            elif isinstance(event, StatusChange) and event.status == "queued":
                progress(.05, "Waiting for the isolated Comfy renderer")
    except Exception:
        # SSE is an enhancement. A dropped stream must not turn a successful
        # render into a failure; SDK polling still reports terminal errors.
        return


async def run_mv_workflow(audio_path: Path, transcript: dict, output_dir: Path,
                          theme="auto", seed=7, progress=None) -> dict:
    """Run the real local graph; retrieve and verify its four actual artifacts."""
    _, output_dir, job_id = validate_job_paths(audio_path, output_dir)
    graph = build_mv_graph(job_id, transcript, theme, seed)
    if any((output_dir / name).exists() for name in _ARTIFACTS):
        raise RuntimeError("MV destination already exists; refusing to overwrite a previous result.")
    if not sdk_status()["available"]:
        raise RuntimeError("Install the pinned official Comfy SDK with tools/setup_mv_sdk.py in .mv-venv.")
    report = progress or (lambda value, message: None)
    report(.02, "Submitting the music-video graph through the official Comfy SDK")
    async with _make_client() as client:
        workflow = client.workflows.from_json(graph)
        # SDK 0.5 accepts mapping metadata, whereas the official 0.1.6 local
        # proxy still accepts only an opaque string. Omit optional metadata:
        # the idempotency key and the graph's job_id already identify this run.
        job = await client.submit(workflow, idempotency_key=f"voice-motion-mv-{job_id}")
        listener = asyncio.create_task(_events(job, report))
        try:
            await job.wait(timeout=1800)
        finally:
            listener.cancel()
            with suppress(asyncio.CancelledError):
                await listener
        if job.status != "succeeded":
            error = job.error
            message = getattr(error, "message", None)
            if isinstance(error, dict):
                message = error.get("message")
            raise RuntimeError(f"The isolated Comfy MV graph {job.id} ended {job.status}: {message or 'check project Comfy log'}")

        outputs = job.get_outputs(NODE_ID)
        found = {}
        for artifact in outputs:
            # A filename is never interpreted as a caller-selected path.
            if artifact.name not in _ARTIFACTS or artifact.name in found:
                raise RuntimeError("Comfy returned an unexpected or duplicate MV artifact.")
            found[artifact.name] = artifact
        if set(found) != set(_ARTIFACTS):
            raise RuntimeError("Comfy completed without all required MP4, project, storyboard and metadata artifacts.")
        report(.92, "Retrieving the rendered files through Comfy API v2")
        for name, artifact in found.items():
            destination = output_dir / name
            if destination.exists():
                raise RuntimeError("MV destination already exists; refusing to overwrite a previous result.")
            await artifact.to_file(destination)
            if not destination.is_file() or destination.stat().st_size == 0:
                raise RuntimeError(f"Comfy artifact {name} was empty.")

    metadata_path = output_dir / "result.json"
    if metadata_path.stat().st_size > 1024 * 1024:
        raise RuntimeError("MV metadata exceeds the allowed limit.")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"),
                              parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (ValueError, UnicodeError) as exc:
        raise RuntimeError("Comfy returned invalid MV metadata.") from exc
    if not isinstance(metadata, dict):
        raise RuntimeError("Comfy MV metadata must be an object.")
    for name in ("duration", "width", "height", "fps", "shots"):
        value = metadata.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise RuntimeError(f"Comfy MV metadata has invalid {name}.")
    metadata.update({key: str(output_dir / name) for name, key in _ARTIFACTS.items()})
    metadata.update(workflow_id=job.id, provider="comfy-sdk-local-api-v2",
                    sdk_version=SDK_VERSION, artifact_transport="official-sdk-api-v2-download")
    report(1., "Music video, directing project and storyboard are ready")
    return metadata
