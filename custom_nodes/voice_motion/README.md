# Voice Motion Lab — independent ComfyUI V3 pack

This pack belongs to the **Voice Motion Lab checkout**. Do not install it into,
copy files into, or submit work to an existing ComfyUI/Studio environment.

Use only the separate Comfy instance explicitly configured for this project
(loopback port **8794**). From the project root, pass
`--extra-model-paths-config comfy_paths.yaml` to that isolated instance. The
project configuration uses `base_path: .` and `custom_nodes: custom_nodes` to load
this source folder directly; a copied pack or directory junction is unnecessary.
The backend loader resolves this source location and loads
`../../src/voice_motion` under a private module namespace. It does not modify
`sys.path`, model folders, or Comfy registries.
ComfyUI needs its current V3 `comfy_api.latest` API. The project backend's optional
analysis dependencies belong exclusively to the isolated project environment.

## Nodes

- **VoiceMotionAnalyze**: `samples_json` (mono numeric array), `sample_rate`,
  `engine` (`auto`, `signal`, `basic_pitch`), `sensitivity`, `smoothing` → slot 0
  `analysis_json` STRING. It is an output node. History UI contains
  `text: ["<analysis JSON>"]`; this is the `/api/analyze-comfy` bridge contract.
  `auto` may report a labeled signal fallback; explicit unavailable `basic_pitch`
  fails. The backend runs CPU-only and does not store submitted audio.
- **VoiceMotionProject**: accepted static SVG, analysis JSON, settings JSON,
  original audio JSON/sample rate, and asset name → editable project JSON STRING.
  Use the same samples/rate that generated the analysis. This node never writes
  files and does not claim to render animated SVG or video. Import the returned
  JSON into the web app to preview/edit/export through its shared renderer.

SVG is a bounded static profile: active elements, event handlers, external
references, XML entities, CSS escapes/at-rules, stylesheet elements, `textPath`,
and `feImage` are rejected. The tag, attribute, and inline style property lists
match the web importer. Paths, text, local gradients/masks/filters, and safe inline
styles remain supported; imported assets need the SVG namespace and a valid
viewBox when one is supplied. Assets are limited to 5000 elements. Accepted source
markup is retained so paths, gradients, text, and viewBox are not rewritten.

`workflows/voice_motion_analysis.api.json` is the queue API graph;
`workflows/voice_motion_analysis.gui.json` is an editable frontend graph.
The bundled 80-sample silence is **synthetic**, not a model-quality demo. Replace
it with actual samples before evaluating a voice performance.
`voice_motion_project.api.json` additionally wires analysis into project output.

## Verification boundaries

`tests/test_nodes.py` uses a **Comfy API stub** to verify adapter contracts, JSON
limits, SVG rejection, and workflow structure. These tests do not establish that
a Comfy graph has executed. Separately, this project's isolated CPU instance has
verified `VoiceMotionAnalyze` V3 schema/registration and successful queued
`basic_pitch` execution through history. `VoiceMotionProject` also completed an
actual queued analysis-to-project graph; its output JSON was imported successfully
in the web app. See the root `VERIFICATION.md` for the synthetic fixture and limits.

Original implementation; the root project's MIT license applies. Basic Pitch and
other external components retain their own licenses and notices, separately
listed by the project. No code was copied from the user's existing ComfyUI,
custom nodes, or Studio workflows.
