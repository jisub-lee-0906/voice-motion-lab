"""Comfy adapter tests use API stubs, not a live queued graph or render."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
# Load just the existing stub helpers by file identity. Adding tests/ to
# sys.path would make unrelated test_server modules collide during discovery.
_helper_spec = importlib.util.spec_from_file_location("_mv_node_stub_helpers", ROOT / "tests" / "test_nodes.py")
_helpers = importlib.util.module_from_spec(_helper_spec)
_helper_spec.loader.exec_module(_helpers)
SocketType, StubOutput = _helpers.SocketType, _helpers.StubOutput


def load_mv_pack():
    api = ModuleType("comfy_api")
    latest = ModuleType("comfy_api.latest")
    latest.ComfyExtension = type("ComfyExtension", (), {})
    latest.io = SimpleNamespace(ComfyNode=type("ComfyNode", (), {}),
                               Schema=lambda **kwargs: SimpleNamespace(**kwargs),
                               String=SocketType, Int=SocketType, Float=SocketType,
                               Combo=SocketType, NodeOutput=StubOutput)
    latest.ui = SimpleNamespace(PreviewText=lambda text: {"text": [text]})
    name = "_voice_motion_mv_node_test"
    pack = ROOT / "custom_nodes" / "voice_motion"
    for key in list(sys.modules):
        if key == name or key.startswith(name + "."):
            del sys.modules[key]
    spec = importlib.util.spec_from_file_location(name, pack / "__init__.py", submodule_search_locations=[str(pack)])
    package = importlib.util.module_from_spec(spec)
    sys.modules[name] = package
    with patch.dict(sys.modules, {"comfy_api": api, "comfy_api.latest": latest}):
        spec.loader.exec_module(package)
        nodes = sys.modules[name + ".mv_nodes"]
    return package, nodes


class MVNodeTests(unittest.TestCase):
    def setUp(self):
        self.package, self.nodes = load_mv_pack()
        (ROOT / "temp").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="mv-node-test-", dir=ROOT / "temp")
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name).resolve()
        self.job_id = "unit-test-job"
        self.audio = self.project / "input" / "mv" / self.job_id / "source.wav"
        self.audio.parent.mkdir(parents=True)
        self.audio.write_bytes(b"RIFF-test-audio")
        self.output = self.project / "output" / "mv" / self.job_id
        self.output.mkdir(parents=True)
        folder_paths = ModuleType("folder_paths")
        folder_paths.get_output_directory = lambda: str(self.project / "output")
        comfy = ModuleType("comfy")
        utils = ModuleType("comfy.utils")
        self.progress = []
        utils.ProgressBar = lambda total: SimpleNamespace(update_absolute=lambda value, maximum: self.progress.append((value, maximum)))
        self.modules = patch.dict(sys.modules, {"folder_paths": folder_paths, "comfy": comfy, "comfy.utils": utils})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.root_patch = patch.object(self.nodes, "PROJECT_ROOT", self.project)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.transcript = {"engine": "qwen3_asr_0.6b_cpu", "segments": [{"start": 0., "end": 1., "text": "피어나"}]}

    def test_node_executes_engine_and_exposes_actual_file_references(self):
        calls = []

        def engine(audio, output, transcript, **options):
            calls.append((audio, output, transcript, options))
            options["progress"](.5, "Rendering")
            result = {"duration": 2., "width": 960, "height": 540, "fps": 24, "shots": 2, "warnings": [], "engines": {"art": "procedural"}}
            for key, name in (("video_path", "video.mp4"), ("project_path", "project.json"), ("storyboard_path", "storyboard.jpg")):
                path = output / name
                path.write_bytes(b"engine-output")
                result[key] = str(path)
            return result

        with patch.object(self.nodes, "_create_engine", return_value=engine):
            result = self.package.VoiceMotionMVCreate.execute(self.job_id, json.dumps(self.transcript), "bloom", 9)
        self.assertEqual(calls[0][:3], (self.audio, self.output / "rendered", self.transcript))
        self.assertEqual(calls[0][3]["theme"], "bloom")
        self.assertEqual(self.progress, [(500, 1000)])
        metadata = json.loads(result.result[0])
        self.assertEqual(metadata["video_path"], "video.mp4")
        self.assertNotIn(str(self.project), result.result[0])
        refs = [ref for values in result.ui.values() for ref in values]
        self.assertEqual({ref["filename"] for ref in refs}, {"video.mp4", "project.json", "storyboard.jpg", "result.json"})
        for ref in refs:
            self.assertEqual(ref["subfolder"], f"mv/{self.job_id}/rendered")
            self.assertEqual(ref["type"], "output")
            self.assertTrue((self.project / "output" / ref["subfolder"] / ref["filename"]).is_file())

    def test_invalid_job_cannot_import_or_execute_engine(self):
        with patch.object(self.nodes, "_create_engine") as engine:
            for job_id in ("../outside", "x", "test/job-123", "test\\job-123", "abcdefgh:"):
                with self.subTest(job_id=job_id), self.assertRaises(ValueError):
                    self.package.VoiceMotionMVCreate.execute(job_id, json.dumps(self.transcript), "auto", 7)
            engine.assert_not_called()

    def test_wrong_comfy_output_directory_is_rejected(self):
        sys.modules["folder_paths"].get_output_directory = lambda: str(self.project / "some-other-output")
        with patch.object(self.nodes, "_create_engine") as engine, self.assertRaisesRegex(RuntimeError, "isolated Comfy"):
            self.package.VoiceMotionMVCreate.execute(self.job_id, json.dumps(self.transcript), "auto", 7)
        engine.assert_not_called()

    def test_fake_or_nonfinite_transcript_rejected_without_render(self):
        for value in ('{"engine":"demo","segments":[]}', '{"engine":"provided_lyrics","segments":[NaN]}', "[]"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.nodes._transcript(value)

    def test_existing_render_is_never_overwritten(self):
        (self.output / "rendered").mkdir()
        with patch.object(self.nodes, "_create_engine") as engine, self.assertRaisesRegex(ValueError, "already has"):
            self.package.VoiceMotionMVCreate.execute(self.job_id, json.dumps(self.transcript), "auto", 7)
        engine.assert_not_called()

    def test_canonical_wave_size_bound_matches_sdk(self):
        for size, accepted in ((33*1024*1024, True), (64*1024*1024, True), (64*1024*1024+1, False)):
            with self.audio.open('wb') as source:
                source.truncate(size)
            if accepted:
                self.assertEqual(self.nodes._job_paths(self.job_id)[0], self.audio)
            else:
                with self.assertRaisesRegex(ValueError, '64 MiB'):
                    self.nodes._job_paths(self.job_id)

    def test_api_gui_and_schema_use_the_same_fields_and_widgets(self):
        api = json.loads((ROOT / "workflows" / "mv-create.api.json").read_text())
        gui = json.loads((ROOT / "workflows" / "mv-create.gui.json").read_text())
        schema = self.package.VoiceMotionMVCreate.define_schema()
        names = [field.id for field in schema.inputs]
        self.assertEqual(set(names), set(api["mv_create"]["inputs"]))
        self.assertEqual(gui["nodes"][0]["widgets_values"], [api["mv_create"]["inputs"][name] for name in names])
        self.assertTrue(schema.is_output_node)
        self.assertTrue(schema.not_idempotent)


if __name__ == "__main__":
    unittest.main()
