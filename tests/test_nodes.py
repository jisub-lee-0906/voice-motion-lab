"""Comfy adapter tests using a stub, NOT a real Comfy execution test."""

import importlib.util
import json
from pathlib import Path
import re
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "custom_nodes" / "voice_motion"


class SocketType:
    @staticmethod
    def Input(name, **kwargs):
        return SimpleNamespace(id=name, **kwargs)

    @staticmethod
    def Output(name=None, **kwargs):
        return SimpleNamespace(id=name, **kwargs)


class StubOutput:
    def __init__(self, *result, ui=None):
        self.result = result
        self.ui = ui


def load_pack_with_stub():
    api = ModuleType("comfy_api")
    latest = ModuleType("comfy_api.latest")
    latest.ComfyExtension = type("ComfyExtension", (), {})
    latest.io = SimpleNamespace(
        ComfyNode=type("ComfyNode", (), {}),
        Schema=lambda **kwargs: SimpleNamespace(**kwargs),
        String=SocketType, Int=SocketType, Float=SocketType, Combo=SocketType,
        NodeOutput=StubOutput,
    )
    latest.ui = SimpleNamespace(PreviewText=lambda text: {"text": [text]})
    name = "_voice_motion_node_pack_test"
    for key in list(sys.modules):
        if key == name or key.startswith(name + "."):
            del sys.modules[key]
    spec = importlib.util.spec_from_file_location(
        name, PACK / "__init__.py", submodule_search_locations=[str(PACK)]
    )
    package = importlib.util.module_from_spec(spec)
    sys.modules[name] = package
    with patch.dict(sys.modules, {"comfy_api": api, "comfy_api.latest": latest}):
        spec.loader.exec_module(package)
        node_module = sys.modules[name + ".nodes"]
    return package, node_module


def analysis_fixture():
    return {
        "version": 1, "duration": 0.01, "sample_rate": 8000,
        "engine": "signal", "frames": [{
            "time": 0.0, "energy": 0.0, "pitch": 0.5,
            "pitch_confidence": 0.0, "onset": 0.0, "progress": 0.0,
        }], "events": [], "warnings": [],
    }


class NodePackStubTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.package, cls.nodes = load_pack_with_stub()

    def test_v3_entrypoint_registers_only_project_nodes(self):
        def complete_without_io(coroutine):
            try:
                coroutine.send(None)
            except StopIteration as complete:
                return complete.value
            self.fail("Pack registration unexpectedly attempted asynchronous I/O.")

        extension = complete_without_io(self.package.comfy_entrypoint())
        nodes = complete_without_io(extension.get_node_list())
        self.assertEqual(
            [node.define_schema().node_id for node in nodes],
            ["VoiceMotionAnalyze", "VoiceMotionProject", "VoiceMotionMVCreate"],
        )

    def test_analyze_adapter_and_history_contract(self):
        calls = []

        def analyze(samples, sample_rate, **settings):
            calls.append((samples, sample_rate, settings))
            return analysis_fixture()

        with patch.object(self.nodes, "_backend_analyze", return_value=analyze):
            output = self.package.VoiceMotionAnalyze.execute(
                "[0,0.25]", 8000, "signal", 1.2, 0.4
            )
        self.assertEqual(calls, [([0, 0.25], 8000, {
            "engine": "signal", "sensitivity": 1.2, "smoothing": 0.4,
        })])
        self.assertEqual(json.loads(output.result[0]), analysis_fixture())
        self.assertEqual(output.ui, {"text": [output.result[0]]})

    def test_explicit_model_error_is_not_silently_relabelled(self):
        def unavailable(*args, **kwargs):
            raise RuntimeError("basic_pitch is unavailable")

        with patch.object(self.nodes, "_backend_analyze", return_value=unavailable):
            with self.assertRaisesRegex(RuntimeError, "basic_pitch"):
                self.package.VoiceMotionAnalyze.execute("[0]", 8000, "basic_pitch", 1, .35)

    def test_invalid_samples_rejected_before_backend_import(self):
        with patch.object(self.nodes, "_backend_analyze") as backend:
            for sample_json in ("{}", "[true]", '["0"]', "[NaN]", "[Infinity]", "[1e999]", "not JSON"):
                with self.subTest(sample_json=sample_json), self.assertRaises(ValueError):
                    self.package.VoiceMotionAnalyze.execute(sample_json, 8000, "auto", 1, .35)
            with self.assertRaises(ValueError):
                self.package.VoiceMotionAnalyze.execute("[0]", True, "signal", 1, .35)
            backend.assert_not_called()

    def test_json_size_and_duration_limits(self):
        with self.assertRaises(ValueError):
            self.nodes._json_load(" " * (3 * 1024 * 1024 + 1), "input")
        with self.assertRaises(ValueError):
            self.nodes._samples(json.dumps([0] * (8000 * 15 + 1)), 8000)

    def test_project_preserves_svg_and_original_audio(self):
        svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><defs><linearGradient id="paint"><stop stop-color="#fff"/></linearGradient></defs><path d="M0 0 L50 50" stroke="url(#paint)"/></svg>'
        samples = [0.0] * 80
        output = self.package.VoiceMotionProject.execute(
            svg, json.dumps(analysis_fixture()), '{"preset":"orbit","intensity":0.8}',
            json.dumps(samples), 8000, "../../asset.svg",
        )
        project = json.loads(output.result[0])
        self.assertEqual(project["format"], "voice-motion-project")
        self.assertEqual(project["asset"], {"name": "asset.svg", "svg": svg})
        self.assertEqual(project["audio"], {"sample_rate": 8000, "samples": samples})
        self.assertEqual(project["settings"]["preset"], "orbit")
        self.assertEqual(project["settings"]["smoothing"], .35)
        self.assertEqual(output.ui, {"text": [output.result[0]]})

    def test_project_rejects_mismatched_audio(self):
        with self.assertRaisesRegex(ValueError, "duration"):
            self.package.VoiceMotionProject.execute(
                '<svg xmlns="http://www.w3.org/2000/svg"/>', json.dumps(analysis_fixture()),
                '{"preset":"fly"}', json.dumps([0] * 800), 8000, "asset.svg",
            )

    def test_project_intensity_matches_web_supported_range(self):
        def project(intensity):
            return self.package.VoiceMotionProject.execute(
                '<svg xmlns="http://www.w3.org/2000/svg"/>',
                json.dumps(analysis_fixture()),
                json.dumps({"preset": "fly", "intensity": intensity}),
                json.dumps([0] * 80), 8000, "asset.svg",
            )

        for boundary in (0, 1.8):
            with self.subTest(boundary=boundary):
                self.assertEqual(json.loads(project(boundary).result[0])["settings"]["intensity"], boundary)
        for unsupported in (-0.01, 1.81, 3):
            with self.subTest(unsupported=unsupported), self.assertRaisesRegex(ValueError, "intensity"):
                project(unsupported)

    def test_svg_rejects_active_and_external_content(self):
        assets = [
            '<svg onload="alert(1)"/>',
            '<svg><script>alert(1)</script></svg>',
            '<svg><foreignObject><div/></foreignObject></svg>',
            '<svg><image href="https://example.org/a.png"/></svg>',
            '<svg><use href="javascript:alert(1)"/></svg>',
            '<svg><style>@import "https://example.org/a.css";</style></svg>',
            '<svg><rect fill="url(https://example.org/a.svg#b)"/></svg>',
            '<svg><style>rect{fill:u\\72l(https://example.org/a)}</style></svg>',
            '<!DOCTYPE svg [<!ENTITY x "external">]><svg>&x;</svg>',
            '<?xml-stylesheet href="https://example.org/a.css"?><svg/>',
            '<svg xmlns:x="http://example.org"><x:rect/></svg>',
            '<svg xml:base="https://example.org"><use href="#a"/></svg>',
        ]
        for svg in assets:
            with self.subTest(svg=svg), self.assertRaises(ValueError):
                self.nodes.validate_svg(svg)

    def test_svg_browser_allowlists_do_not_drift(self):
        """The two import paths must support the same documented static profile."""
        browser = (ROOT / "web/core.mjs").read_text(encoding="utf-8")
        for javascript_name, python_names in (
            ("tags", self.nodes._SVG_ELEMENTS),
            ("attrs", self.nodes._SVG_ATTRIBUTES),
            ("SVG_STYLE_PROPS", self.nodes._SVG_STYLE_PROPERTIES),
        ):
            expression = re.search(
                rf"const {javascript_name} = new Set\('([^']+)'(\.toLowerCase\(\))?\.split\(' '\)\)",
                browser,
            )
            self.assertIsNotNone(expression, f"Browser {javascript_name} declaration changed; review compatibility.")
            names = expression.group(1)
            if expression.group(2):
                names = names.lower()
            self.assertEqual(python_names, frozenset(names.split()))

    def test_svg_compatible_features_preserve_markup(self):
        assets = [
            '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180"><text x="5" y="20" style="font-family:Arial;font-size:18px;fill:#345;paint-order:stroke">안녕</text></svg>',
            '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="-20,-10,100,80"><defs><path id="1" d="M0 0L5 10"/></defs><use xlink:href="#1"/></svg>',
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><defs><clipPath id="clip"><circle cx="50" cy="50" r="40"/></clipPath><filter id="shadow"><feDropShadow dx="2" dy="2" stdDeviation="2"/></filter></defs><g clip-path="url(#clip)"><rect id="label@home" width="100" height="100" style="filter:url(\'#shadow\');fill:#abc;opacity:.8"/></g></svg>',
        ]
        for svg in assets:
            with self.subTest(svg=svg):
                self.assertEqual(self.nodes.validate_svg(svg), svg)

    def test_svg_rejects_features_web_import_cannot_render(self):
        fragments = [
            '<style>rect { fill: red; }</style><rect width="10" height="10"/>',
            '<text><textPath href="#path">label</textPath></text>',
            '<filter><feImage href="#path"/></filter>',
            '<rect class="accent"/>',
            '<rect data-label="name"/>',
            '<g xml:space="preserve"><text>label</text></g>',
            '<rect style="display:none"/>',
            '<rect style="position:fixed"/>',
            '<rect fill="url(#paint) red"/>',
            '<use href="#paint "/>',
            '<g xmlns:other="http://example.org"/>',
            '<g xmlns:alias="http://www.w3.org/1999/xlink"><use alias:href="#path"/></g>',
        ]
        for fragment in fragments:
            svg = f'<svg xmlns="http://www.w3.org/2000/svg">{fragment}</svg>'
            with self.subTest(fragment=fragment), self.assertRaises(ValueError):
                self.nodes.validate_svg(svg)
        for svg in (
            '<svg><rect width="10" height="10"/></svg>',
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 0 100"/>',
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100001 100"/>',
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="NaN 0 10 10"/>',
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10"/>',
        ):
            with self.subTest(svg=svg), self.assertRaises(ValueError):
                self.nodes.validate_svg(svg)

    def test_svg_complexity_limit_and_escaped_presentation_url(self):
        svg = '<svg xmlns="http://www.w3.org/2000/svg">' + '<rect/>' * 4999 + '</svg>'
        self.assertEqual(self.nodes.validate_svg(svg), svg)
        with self.assertRaisesRegex(ValueError, "5000"):
            self.nodes.validate_svg(svg.replace('</svg>', '<rect/></svg>'))
        for attributes in (
            'fill="u\\72l(https://example.org/image.svg)"',
            'fill="u&#92;72l(https://example.org/image.svg)"',
            'fill="j&#97;vascript:alert(1)"',
            'fill="u/**/rl(https://example.org/image.svg)"',
            'style="fill:ex&#112;ression(alert(1))"',
        ):
            with self.subTest(attributes=attributes), self.assertRaises(ValueError):
                self.nodes.validate_svg(f'<svg xmlns="http://www.w3.org/2000/svg"><rect {attributes}/></svg>')

    def test_workflow_api_and_gui_widget_contract(self):
        api = json.loads((ROOT / "workflows/voice_motion_analysis.api.json").read_text(encoding="utf-8"))
        gui = json.loads((ROOT / "workflows/voice_motion_analysis.gui.json").read_text(encoding="utf-8"))
        node = api["voice_motion_analysis"]
        self.assertEqual(node["class_type"], "VoiceMotionAnalyze")
        schema = self.package.VoiceMotionAnalyze.define_schema()
        fields = [item.id for item in schema.inputs]
        self.assertEqual(set(fields), set(node["inputs"]))
        self.assertEqual(gui["nodes"][0]["widgets_values"], [node["inputs"][field] for field in fields])
        self.assertEqual(gui["nodes"][0]["outputs"][0]["name"], "analysis_json")
        project_graph = json.loads((ROOT / "workflows/voice_motion_project.api.json").read_text(encoding="utf-8"))
        self.assertEqual(project_graph["voice_motion_project"]["inputs"]["analysis_json"], ["voice_motion_analysis", 0])


if __name__ == "__main__":
    unittest.main()
