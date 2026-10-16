"""JSON-only, CPU analysis nodes for the independent Voice Motion Lab project."""

from __future__ import annotations

import importlib
import importlib.util
import json
from io import StringIO
import math
from pathlib import Path
import re
import sys
from threading import Lock
import xml.etree.ElementTree as ET

from comfy_api.latest import io, ui

_BACKEND_PACKAGE = "_voice_motion_lab_backend"
_BACKEND_LOCK = Lock()
_MAX_JSON_BYTES = 3 * 1024 * 1024
_SVG_NAMESPACE = "http://www.w3.org/2000/svg"
_XLINK_NAMESPACE = "http://www.w3.org/1999/xlink"
_SVG_ELEMENTS = frozenset(
    "svg g defs symbol use path rect circle ellipse line polyline polygon text tspan "
    "title desc linearGradient radialGradient stop clipPath mask pattern "
    "marker filter feBlend feColorMatrix feComponentTransfer feComposite "
    "feConvolveMatrix feDiffuseLighting feDisplacementMap feDistantLight "
    "feDropShadow feFlood feFuncA feFuncB feFuncG feFuncR feGaussianBlur "
    "feMerge feMergeNode feMorphology feOffset fePointLight feSpecularLighting "
    "feSpotLight feTile feTurbulence".split()
)
_SVG_ATTRIBUTES = frozenset(
    "id xmlns xmlns:xlink version viewbox width height x y x1 x2 y1 y2 cx cy r rx ry "
    "d points transform opacity fill fill-rule fill-opacity stroke stroke-width "
    "stroke-linecap stroke-linejoin stroke-miterlimit stroke-dasharray stroke-dashoffset "
    "stroke-opacity vector-effect preserveaspectratio gradientunits gradienttransform "
    "spreadmethod offset stop-color stop-opacity clip-path clippathunits mask maskunits "
    "maskcontentunits patternunits patterncontentunits patterntransform marker-start "
    "marker-mid marker-end markerunits markerwidth markerheight refx refy orient filter "
    "filterunits primitiveunits color color-interpolation color-interpolation-filters "
    "in in2 result type values mode operator k1 k2 k3 k4 order kernelmatrix divisor bias "
    "targetx targety edgemode kernelunitlength preservealpha stddeviation dx dy scale "
    "xchannelselector ychannelselector surfacescale diffuseconstant specularconstant "
    "specularexponent limitingconeangle azimuth elevation pointsatx pointsaty pointsatz "
    "z numoctaves seed stitchtiles basefrequency flood-color flood-opacity slope intercept "
    "amplitude exponent tablevalues font-family font-size font-style font-weight "
    "letter-spacing word-spacing text-anchor dominant-baseline alignment-baseline "
    "textlength lengthadjust rotate href xlink:href style".split()
)
_SVG_STYLE_PROPERTIES = frozenset(
    "fill fill-rule fill-opacity stroke stroke-width stroke-linecap stroke-linejoin "
    "stroke-miterlimit stroke-dasharray stroke-dashoffset stroke-opacity opacity color "
    "clip-path mask filter font-family font-size font-style font-weight letter-spacing "
    "word-spacing text-anchor dominant-baseline paint-order".split()
)
_LOCAL_FRAGMENT = r"#[A-Za-z0-9_:.-]+"
_LOCAL_URL = re.compile(r"url\(\s*([\"']?)(" + _LOCAL_FRAGMENT + r")\1\s*\)", re.I)


def _json_load(text: str, label: str):
    if not isinstance(text, str) or len(text.encode("utf-8")) > _MAX_JSON_BYTES:
        raise ValueError(f"{label} must be UTF-8 JSON no larger than 3 MiB.")

    def invalid_constant(value):
        raise ValueError(f"{label} must not contain {value}.")

    try:
        return json.loads(text, parse_constant=invalid_constant)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"{label} is not valid JSON.") from exc


def _json_dump(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _backend_analyze():
    """Import only this checkout's backend, without adding global search paths.

    Load the source folder with this project's extra-model-paths configuration;
    keep the pack inside the checkout that owns its backend.
    """
    with _BACKEND_LOCK:
        if _BACKEND_PACKAGE not in sys.modules:
            backend = Path(__file__).resolve().parents[2] / "src" / "voice_motion"
            initializer = backend / "__init__.py"
            if not initializer.is_file():
                raise RuntimeError(
                    "Voice Motion Lab backend is missing. Use the node pack from "
                    "this project's checkout; do not copy it into an existing Comfy installation."
                )
            spec = importlib.util.spec_from_file_location(
                _BACKEND_PACKAGE, initializer, submodule_search_locations=[str(backend)]
            )
            if spec is None or spec.loader is None:
                raise RuntimeError("Cannot load the project-local analysis backend.")
            package = importlib.util.module_from_spec(spec)
            sys.modules[_BACKEND_PACKAGE] = package
            try:
                spec.loader.exec_module(package)
            except BaseException:
                sys.modules.pop(_BACKEND_PACKAGE, None)
                raise
        return importlib.import_module(f"{_BACKEND_PACKAGE}.analysis").analyze


def _validate_inline_style(value: str):
    for declaration in value.split(";"):
        if not declaration.strip():
            continue
        property_name, separator, css_value = declaration.partition(":")
        css_value = css_value.strip()
        if not separator or property_name.strip().lower() not in _SVG_STYLE_PROPERTIES:
            raise ValueError("Unsupported inline SVG style property.")
        if re.search(r"[\\@<>]|expression|javascript|vbscript", css_value, re.I):
            raise ValueError("Only safe inline SVG styles are supported.")
        if re.search(r"url\s*\(", css_value, re.I) and not _LOCAL_URL.fullmatch(css_value):
            raise ValueError("SVG style may reference local fragment IDs only.")


def validate_svg(text: str) -> str:
    """Reject active/external content while preserving accepted SVG bytes.

    This is a deliberately bounded static SVG profile, not an arbitrary SVG web
    browser. Returning the input avoids altering paths, styles, or viewBox data.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("A non-empty SVG asset is required.")
    if len(text.encode("utf-16-le")) // 2 > 1_000_000:
        raise ValueError("SVG asset must not exceed 1,000,000 characters.")
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)", text, re.I):
        raise ValueError("SVG document types and entities are forbidden.")
    if re.search(r"<\?(?!xml\s)", text, re.I):
        raise ValueError("SVG processing instructions are forbidden.")
    if re.search(r"(?:javascript|vbscript)\s*:|\bexpression\s*\(", text, re.I):
        raise ValueError("Executable SVG content is forbidden.")
    if re.search(
        r"<\s*(?:\w+:)?(?:script|foreignObject|iframe|object|embed|image|style|"
        r"animate|animateTransform|animateMotion|set|audio|video)\b|\son[a-z]+\s*=",
        text, re.I,
    ):
        raise ValueError("Active or embedded SVG content is unsupported.")
    if re.search(r"\b(?:xlink:)?href\s*=\s*['\"]\s*(?!#)", text, re.I):
        raise ValueError("SVG external references are forbidden.")
    for match in re.finditer(r"url\s*\(([^)]*)\)", text, re.I):
        if not re.fullmatch(r"\s*([\"']?)(" + _LOCAL_FRAGMENT + r")\1\s*", match.group(1)):
            raise ValueError("SVG may reference local fragment IDs only.")
    try:
        parser = ET.iterparse(StringIO(text), events=("start", "start-ns"))
        for event, value in parser:
            if event == "start-ns":
                prefix, namespace = value
                if not ((prefix == "" and namespace == _SVG_NAMESPACE) or
                        (prefix == "xlink" and namespace == _XLINK_NAMESPACE)):
                    raise ValueError("Unsupported SVG namespace declaration.")
        root = parser.root
    except (ET.ParseError, RecursionError) as exc:
        raise ValueError("SVG is not well-formed XML.") from exc
    if root.tag != f"{{{_SVG_NAMESPACE}}}svg":
        raise ValueError("Asset root must use the SVG namespace.")
    for count, element in enumerate(root.iter(), 1):
        if count > 5000:
            raise ValueError("SVG must not contain more than 5000 elements.")
        if not isinstance(element.tag, str):
            continue
        tag = element.tag
        if not tag.startswith(f"{{{_SVG_NAMESPACE}}}"):
            raise ValueError("Foreign SVG element namespaces are forbidden.")
        tag = tag.split("}", 1)[1]
        if tag not in _SVG_ELEMENTS:
            raise ValueError(f"Unsupported or active SVG element: {tag}.")
        for attribute, value in element.attrib.items():
            if attribute.startswith("{"):
                namespace, attribute = attribute[1:].split("}", 1)
                if namespace != _XLINK_NAMESPACE:
                    raise ValueError("Foreign SVG attribute namespaces are forbidden.")
                attribute = "xlink:" + attribute
            name = attribute.lower()
            if name not in _SVG_ATTRIBUTES or name.startswith("on"):
                raise ValueError(f"Unsupported SVG attribute: {attribute}.")
            if re.search(r"[\\\x00-\x08\x0b\x0c\x0e-\x1f\x7f]|/\*", value):
                raise ValueError("SVG attribute CSS escapes, controls, and comments are unsupported.")
            if re.search(r"(?:javascript|vbscript)\s*:|expression\s*\(", value, re.I):
                raise ValueError("Executable SVG attribute values are forbidden.")
            if name in ("href", "xlink:href"):
                if not re.fullmatch(_LOCAL_FRAGMENT, value):
                    raise ValueError("SVG href must be a local fragment ID.")
            if name == "style":
                _validate_inline_style(value)
            elif re.search(r"url\s*\(", value, re.I) and not _LOCAL_URL.fullmatch(value):
                raise ValueError("SVG may reference local fragment IDs only.")
    if "viewBox" in root.attrib:
        try:
            viewbox = [float(value) for value in re.split(r"[\s,]+", root.attrib["viewBox"].strip())]
        except ValueError as exc:
            raise ValueError("Invalid SVG viewBox.") from exc
        if (len(viewbox) != 4 or not all(math.isfinite(value) for value in viewbox) or
                not 0 < viewbox[2] <= 100000 or not 0 < viewbox[3] <= 100000):
            raise ValueError("Invalid SVG viewBox.")
    return text


def _samples(samples_json, sample_rate):
    samples = _json_load(samples_json, "samples_json")
    if isinstance(sample_rate, bool) or not isinstance(sample_rate, int) or not 8000 <= sample_rate <= 96000:
        raise ValueError("sample_rate must be an integer between 8000 and 96000.")
    if not isinstance(samples, list) or len(samples) > 15 * sample_rate:
        raise ValueError("samples_json must contain a mono audio array no longer than 15 seconds.")
    for value in samples:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("Audio samples must be finite numbers.")
    return samples


class VoiceMotionAnalyze(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="VoiceMotionAnalyze",
            display_name="Voice Motion · Analyze Audio",
            category="Voice Motion Lab",
            description="Analyze local mono samples on CPU. Signal fallback is explicitly labeled.",
            is_output_node=True,
            inputs=[
                io.String.Input("samples_json", default="[]", multiline=True),
                io.Int.Input("sample_rate", default=22050, min=8000, max=96000, step=1),
                io.Combo.Input("engine", options=["auto", "signal", "basic_pitch"], default="auto"),
                io.Float.Input("sensitivity", default=1.0, min=0.1, max=3.0, step=0.05),
                io.Float.Input("smoothing", default=0.35, min=0.0, max=1.0, step=0.01),
            ],
            outputs=[io.String.Output("analysis_json")],
        )

    @classmethod
    def execute(cls, samples_json, sample_rate, engine, sensitivity, smoothing):
        samples = _samples(samples_json, sample_rate)
        analysis = _backend_analyze()(
            samples, sample_rate, engine=engine, sensitivity=sensitivity, smoothing=smoothing
        )
        encoded = _json_dump(analysis)
        return io.NodeOutput(encoded, ui=ui.PreviewText(encoded))


class VoiceMotionProject(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="VoiceMotionProject",
            display_name="Voice Motion · Editable Project JSON",
            category="Voice Motion Lab",
            description="Package an accepted static SVG, original audio, and analysis. No file writes or rendering.",
            is_output_node=True,
            inputs=[
                io.String.Input("svg", default='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><circle cx="50" cy="50" r="30" fill="#8ce8cf"/></svg>', multiline=True),
                io.String.Input("analysis_json", default="{}", multiline=True, force_input=True),
                io.String.Input("settings_json", default='{"preset":"fly","intensity":1,"smoothing":0.35,"background":"#10151f","transparent":false}', multiline=True),
                io.String.Input("samples_json", default="[]", multiline=True),
                io.Int.Input("sample_rate", default=22050, min=8000, max=96000, step=1),
                io.String.Input("asset_name", default="asset.svg"),
            ],
            outputs=[io.String.Output("project_json")],
        )

    @classmethod
    def execute(cls, svg, analysis_json, settings_json, samples_json, sample_rate, asset_name):
        samples = _samples(samples_json, sample_rate)
        analysis = _json_load(analysis_json, "analysis_json")
        settings = _json_load(settings_json, "settings_json")
        if not isinstance(analysis, dict) or analysis.get("version") != 1 or not isinstance(analysis.get("frames"), list):
            raise ValueError("analysis_json must be a version 1 Voice Motion analysis.")
        if analysis.get("sample_rate") != sample_rate:
            raise ValueError("Project audio sample rate must match the analysis.")
        duration = analysis.get("duration")
        if isinstance(duration, bool) or not isinstance(duration, (float, int)) or not math.isfinite(duration):
            raise ValueError("Analysis duration must be finite.")
        if abs(duration - len(samples) / sample_rate) > 0.02:
            raise ValueError("Project audio duration must match the analysis.")
        if not isinstance(settings, dict) or settings.get("preset") not in ("fly", "bounce", "orbit"):
            raise ValueError("settings_json must choose fly, bounce, or orbit.")
        for name, default, low, high in (("intensity", 1.0, 0.0, 1.8), ("smoothing", 0.35, 0.0, 1.0)):
            value = settings.get(name, default)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"Invalid {name} setting.")
            settings[name] = value
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", settings.get("background", "#10151f")):
            raise ValueError("Background must be a six-digit hex color.")
        if not isinstance(settings.get("transparent", False), bool):
            raise ValueError("transparent must be boolean.")
        settings.setdefault("background", "#10151f")
        settings.setdefault("transparent", False)
        name = str(asset_name).replace("\\", "/").rsplit("/", 1)[-1][:120] or "asset.svg"
        project = {
            "format": "voice-motion-project", "version": 1,
            "asset": {"name": name, "svg": validate_svg(svg)},
            "audio": {"sample_rate": sample_rate, "samples": samples},
            "analysis": analysis, "settings": settings,
        }
        encoded = _json_dump(project)
        return io.NodeOutput(encoded, ui=ui.PreviewText(encoded))
