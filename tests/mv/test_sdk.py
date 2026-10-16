"""Official SDK adapter contract tests; fake transport, no Comfy/model execution."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from voice_motion import mv_sdk


TRANSCRIPT = {"engine": "qwen3_asr_0.6b_cpu", "language": "ko", "text": "피어나",
              "segments": [{"start": .1, "end": 1.8, "text": "피어나"}], "warnings": []}
METADATA = {"duration": 2., "width": 960, "height": 540, "fps": 24, "shots": 2,
            "warnings": [], "engines": {"render": "test_renderer"}}


class FakeOutput:
    def __init__(self, name, contents, writes):
        self.name, self.contents, self.writes = name, contents, writes

    async def to_file(self, destination):
        self.writes.append(Path(destination))
        Path(destination).write_bytes(self.contents)


class FakeJob:
    id = "actual-comfy-workflow-id"
    status = "succeeded"
    error = None

    def __init__(self, outputs):
        self.outputs = outputs

    async def wait(self, timeout=None):
        self.timeout = timeout
        return self

    async def events(self):
        if False:
            yield None

    def get_outputs(self, node_id):
        if node_id != "mv_create":
            raise AssertionError("Wrong output node")
        return self.outputs


class FakeClient:
    def __init__(self, job):
        self.job = job
        self.workflows = self
        self.graph = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def from_json(self, graph):
        self.graph = graph
        return graph

    async def submit(self, workflow, **kwargs):
        self.submitted = workflow, kwargs
        return self.job


class SDKAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        (ROOT / "temp").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="mv-sdk-test-", dir=ROOT / "temp")
        self.project = Path(self.temp.name).resolve()
        self.job_id = "unit-test-job"
        self.audio = self.project / "input" / "mv" / self.job_id / "source.wav"
        self.audio.parent.mkdir(parents=True)
        self.audio.write_bytes(b"RIFF-test-fixture")
        self.output = self.project / "output" / "mv" / self.job_id
        self.output.mkdir(parents=True)
        self.root_patch = patch.object(mv_sdk, "PROJECT_ROOT", self.project)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.addCleanup(self.temp.cleanup)

    def make_client(self, metadata=METADATA, missing=None):
        writes = []
        payloads = {"video.mp4": b"actual-test-video-bytes", "project.json": b'{"format":"mv-test"}',
                    "storyboard.jpg": b"actual-test-image-bytes",
                    "result.json": json.dumps(metadata).encode()}
        client = FakeClient(FakeJob([FakeOutput(name, contents, writes)
                                    for name, contents in payloads.items() if name != missing]))
        return client, writes

    async def run_fake(self, client):
        async def no_events(job, progress):
            return

        with patch.object(mv_sdk, "sdk_status", return_value={"available": True}), \
             patch.object(mv_sdk, "_make_client", return_value=client), \
             patch.object(mv_sdk, "_events", new=no_events):
            return await mv_sdk.run_mv_workflow(self.audio, TRANSCRIPT, self.output, "bloom", 31)

    async def test_actual_graph_and_all_artifacts_cross_sdk_boundary(self):
        client, writes = self.make_client()
        result = await self.run_fake(client)
        self.assertEqual(set(client.graph), {"mv_create"})
        node = client.graph["mv_create"]
        self.assertEqual(node["class_type"], "VoiceMotionMVCreate")
        self.assertEqual(node["inputs"]["job_id"], self.job_id)
        self.assertEqual(json.loads(node["inputs"]["transcript_json"]), TRANSCRIPT)
        self.assertEqual(node["inputs"]["theme"], "bloom")
        self.assertEqual(node["inputs"]["seed"], 31)
        self.assertNotIn("metadata", client.submitted[1])
        self.assertEqual(len(writes), 4)
        self.assertTrue(all(path.parent == self.output for path in writes))
        self.assertEqual(Path(result["video_path"]).read_bytes(), b"actual-test-video-bytes")
        self.assertEqual(result["workflow_id"], client.job.id)
        self.assertEqual(result["provider"], "comfy-sdk-local-api-v2")

    async def test_missing_artifact_fails_before_downloading(self):
        client, writes = self.make_client(missing="video.mp4")
        with self.assertRaisesRegex(RuntimeError, "all required"):
            await self.run_fake(client)
        self.assertEqual(writes, [])

    async def test_node_failure_is_not_silently_run_outside_comfy(self):
        client, writes = self.make_client()
        client.job.status = "failed"
        client.job.error = {"message": "test render rejected input"}
        with self.assertRaisesRegex(RuntimeError, "test render rejected"):
            await self.run_fake(client)
        self.assertEqual(writes, [])

    async def test_output_name_cannot_choose_a_filesystem_path(self):
        client, writes = self.make_client()
        client.job.outputs[0].name = "../video.mp4"
        with self.assertRaisesRegex(RuntimeError, "unexpected"):
            await self.run_fake(client)
        self.assertEqual(writes, [])

    async def test_invalid_metadata_does_not_claim_success(self):
        client, _ = self.make_client(metadata={**METADATA, "duration": float("nan")})
        with self.assertRaisesRegex(RuntimeError, "invalid MV metadata"):
            await self.run_fake(client)

    async def test_existing_result_is_not_overwritten(self):
        existing = self.output / "video.mp4"
        existing.write_bytes(b"keep")
        client, _ = self.make_client()
        with self.assertRaisesRegex(RuntimeError, "overwrite"):
            await self.run_fake(client)
        self.assertEqual(existing.read_bytes(), b"keep")

    def test_mismatched_jobs_and_unapproved_paths_rejected(self):
        other = self.project / "source.wav"
        other.write_bytes(b"other")
        with self.assertRaisesRegex(ValueError, "input"):
            mv_sdk.validate_job_paths(other, self.output)
        with self.assertRaisesRegex(ValueError, "output"):
            mv_sdk.validate_job_paths(self.audio, self.audio.parent)
        for value in ("../outside", "x", "foo/bar-123", "abc:def123", "abc\\def123"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                mv_sdk.build_mv_graph(value, TRANSCRIPT)

    def test_nonfinite_transcript_and_settings_rejected(self):
        for transcript, theme, seed in (({"segments": [float("nan")]}, "auto", 7),
                                        (TRANSCRIPT, "unknown", 7),
                                        (TRANSCRIPT, "auto", True)):
            with self.subTest(theme=theme, seed=seed), self.assertRaises(ValueError):
                mv_sdk.build_mv_graph(self.job_id, transcript, theme, seed)

    def test_canonical_stereo_float_wav_can_exceed_32_mib_but_not_64(self):
        for size, accepted in ((33*1024*1024, True), (64*1024*1024, True), (64*1024*1024+1, False)):
            with self.audio.open('wb') as source:
                source.truncate(size)
            if accepted:
                self.assertEqual(mv_sdk.validate_job_paths(self.audio, self.output)[0], self.audio)
            else:
                with self.assertRaisesRegex(ValueError, '64 MiB'):
                    mv_sdk.validate_job_paths(self.audio, self.output)

    @unittest.skipUnless(importlib.util.find_spec("comfy_sdk"), "Pinned official SDK not installed")
    async def test_real_sdk_wire_omits_metadata_incompatible_with_local_proxy(self):
        import httpx
        from comfy_sdk import ComfyError

        seen = []

        def handle(request):
            seen.append((str(request.url), dict(request.headers), json.loads(request.content)))
            # Deliberate terminal response: no workflow is submitted anywhere.
            return httpx.Response(422, json={"error": {"code": "invalid_request", "message": "unit-test-terminal"}})

        client = mv_sdk._make_client()
        await client._low._client.aclose()
        client._low._client = httpx.AsyncClient(transport=httpx.MockTransport(handle), trust_env=False)
        with patch.object(mv_sdk, "_make_client", return_value=client):
            with self.assertRaises(ComfyError):
                await mv_sdk.run_mv_workflow(self.audio, TRANSCRIPT, self.output)
        self.assertEqual(len(seen), 1)
        url, headers, body = seen[0]
        self.assertEqual(url, "http://127.0.0.1:8795/api/v2/jobs")
        self.assertEqual(headers["idempotency-key"], f"voice-motion-mv-{self.job_id}")
        self.assertNotIn("metadata", body)
        self.assertEqual(body["workflow"]["mv_create"]["class_type"], "VoiceMotionMVCreate")

    @unittest.skipUnless(importlib.util.find_spec("comfy_sdk"), "Pinned official SDK not installed")
    async def test_official_client_captures_only_fixed_local_origins_and_no_ambient_key(self):
        ambient = {"COMFY_BASE_URL": "https://external.invalid", "COMFY_API_KEY": "unit-test-only-key",
                   "COMFY_ROUTER_BASE_URL": "https://router.invalid", "NO_PROXY": "localhost"}
        with patch.dict(os.environ, ambient):
            client = mv_sdk._make_client()
            try:
                self.assertEqual(client._low.base_url, "http://127.0.0.1:8795")
                self.assertEqual(client._low.router_base_url, "http://127.0.0.1:8795")
                self.assertFalse(client._low.authenticated)
                self.assertEqual({name: os.environ[name] for name in ambient}, ambient)
            finally:
                await client.aclose()


if __name__ == "__main__":
    unittest.main()
