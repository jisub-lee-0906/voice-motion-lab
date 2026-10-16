"""Project-only 2D music-video renderer and real downloadable Comfy outputs."""
from __future__ import annotations

import importlib
import importlib.util
import json
from pathlib import Path
import re
import sys
from threading import Lock

from comfy_api.latest import io

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_JOB_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{7,63}\Z")
_BACKEND_LOCK = Lock()
_BACKEND_PACKAGE = "_voice_motion_mv_backend"


def _create_engine():
    with _BACKEND_LOCK:
        if _BACKEND_PACKAGE not in sys.modules:
            source = PROJECT_ROOT / "src" / "voice_motion"
            spec = importlib.util.spec_from_file_location(
                _BACKEND_PACKAGE, source / "__init__.py", submodule_search_locations=[str(source)])
            if spec is None or spec.loader is None:
                raise RuntimeError("This checkout's MV engine is missing.")
            module = importlib.util.module_from_spec(spec)
            sys.modules[_BACKEND_PACKAGE] = module
            try:
                spec.loader.exec_module(module)
            except BaseException:
                sys.modules.pop(_BACKEND_PACKAGE, None)
                raise
        return importlib.import_module(f"{_BACKEND_PACKAGE}.mv_engine").create_music_video


def _job_paths(job_id):
    if not isinstance(job_id, str) or not _JOB_ID.fullmatch(job_id):
        raise ValueError("Invalid MV job identifier.")
    approved_audio = PROJECT_ROOT / "input" / "mv" / job_id / "source.wav"
    approved_output = PROJECT_ROOT / "output" / "mv" / job_id
    if approved_audio.resolve(strict=True) != approved_audio or not approved_audio.is_file():
        raise ValueError("The MV audio must stay inside this project's input job directory.")
    if approved_output.resolve(strict=True) != approved_output or not approved_output.is_dir():
        raise ValueError("The MV result must stay inside this project's output job directory.")
    if approved_audio.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("MV input exceeds 32 MiB.")
    # /view, used by the official proxy for real bytes, must serve this checkout.
    import folder_paths
    if Path(folder_paths.get_output_directory()).resolve() != PROJECT_ROOT / "output":
        raise RuntimeError("VoiceMotionMVCreate requires this project's isolated Comfy output directory.")
    return approved_audio, approved_output


def _transcript(encoded):
    if not isinstance(encoded, str) or len(encoded.encode("utf-8")) > 1024 * 1024:
        raise ValueError("MV transcript must be JSON no larger than 1 MiB.")
    try:
        value = json.loads(encoded, parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)))
    except (ValueError, RecursionError) as exc:
        raise ValueError("MV transcript is not finite JSON.") from exc
    if not isinstance(value, dict) or not isinstance(value.get("segments"), list):
        raise ValueError("MV transcript must contain segments.")
    if value.get("engine") not in ("faster-whisper", "whisper", "qwen3_asr", "qwen3_asr_0.6b_cpu", "provided_lyrics"):
        raise ValueError("MV transcript must identify its actual ASR or provided-lyrics engine.")
    return value


class VoiceMotionMVCreate(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="VoiceMotionMVCreate", display_name="Voice Motion · Direct and Render MV",
            category="Voice Motion Lab/Music Video", is_output_node=True, not_idempotent=True,
            description="Render an original lyric-driven 2D MV on CPU. Only this project's staged jobs are accepted.",
            inputs=[
                io.String.Input("job_id", default="replace_with_staged_job"),
                io.String.Input("transcript_json", default='{"engine":"provided_lyrics","segments":[]}', multiline=True),
                io.Combo.Input("theme", options=["auto", "bloom", "fracture", "orbit"], default="auto"),
                io.Int.Input("seed", default=7, min=0, max=2147483647),
            ],
            outputs=[io.String.Output("result_json")],
        )

    @classmethod
    def execute(cls, job_id, transcript_json, theme, seed):
        if theme not in ("auto", "bloom", "fracture", "orbit"):
            raise ValueError("Unsupported MV theme.")
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2147483647:
            raise ValueError("Invalid MV seed.")
        transcript = _transcript(transcript_json)
        audio, job_output = _job_paths(job_id)
        render_output = job_output / "rendered"
        if render_output.exists():
            raise ValueError("This MV job already has a render; create a new job instead of overwriting it.")
        render_output.mkdir()
        # Actual Comfy progress events are exposed by the API proxy's SSE bridge.
        from comfy.utils import ProgressBar
        bar = ProgressBar(1000)

        def report(value, message):
            bar.update_absolute(min(1000, max(0, int(value * 1000))), 1000)

        result = _create_engine()(audio, render_output, transcript, theme=theme, seed=seed, progress=report)
        safe_result = {key: value for key, value in result.items()
                       if key not in ("video_path", "project_path", "storyboard_path")}
        names = {"video_path": "video.mp4", "project_path": "project.json", "storyboard_path": "storyboard.jpg"}
        for key, name in names.items():
            actual = Path(result[key]).resolve(strict=True)
            if actual != render_output / name or not actual.is_file() or actual.stat().st_size == 0:
                raise RuntimeError("MV engine returned an unexpected or missing artifact.")
            safe_result[key] = name
        safe_result["node"] = "VoiceMotionMVCreate"
        safe_result["asset_origin"] = "original-authored-procedural-2d"
        encoded = json.dumps(safe_result, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        (render_output / "result.json").write_text(encoded, encoding="utf-8")

        def artifact(name):
            return {"filename": name, "subfolder": f"mv/{job_id}/rendered", "type": "output"}

        return io.NodeOutput(encoded, ui={
            "gifs": [artifact("video.mp4")],
            "images": [artifact("storyboard.jpg")],
            "files": [artifact("project.json"), artifact("result.json")],
        })
