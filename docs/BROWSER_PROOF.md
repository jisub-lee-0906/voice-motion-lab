# Initial browser model inference proof (v0.1 historical record)

The v0.2 app now implements the complete five-second browser pipeline. The
analysis, microphone, preview and real video export are in `web/play/`; no
inference API is needed. The actual one-thread WASM tests compare all 301 frames
of six synthetic five-second cases against Python, with identical model-input
chunk hashes. Timing, energy, pitch and progress match to six decimals; the
largest confidence difference is 0.000203. See `VERIFICATION.md` for current
full-flow browser evidence and remaining physical-device checks.

The measurements below preserve the earlier one-window compatibility experiment.

The actual Spotify Basic Pitch ONNX model ran in a browser Web Worker using
`onnxruntime-web@1.30.0`, the CPU WASM provider and one thread. No model inference
API was called. Static same-origin requests loaded the runtime, model, reference
and an original synthetic WAV fixture.

Observed in one desktop Chromium run:

- Input: first 43,844 raw mono PCM samples at 22,050Hz (~1.99 seconds).
- Model preparation: 200.7ms; model inference: **71.9ms**.
- All three output shapes and finite values passed. Aggregate statistics matched
  the Python CPU reference with absolute tolerance 0.0001 + relative 0.001.
- The model, fixture and normalized input SHA-256 hashes matched the reference.
- Required self-hosted runtime/model/license assets total 14,557,305 bytes.

This is a compatibility proof, not a mobile speed claim or a complete port.
Output arrays were compared through aggregate statistics, not element by element.
At the time of this experiment, the full chunking, energy gate, pitch tracking,
impact detection, microphone, preview and export flow had not yet been ported.

## Reproduce

After `setup.ps1 -WithModel` and with the committed BOP! fixtures present:

```powershell
./.venv/Scripts/python.exe tools/setup_browser_proof.py
./tools/start_browser_proof.ps1
```

Open `http://127.0.0.1:8796/browser-proof/index.html` and press the actual inference
button. This separate static server avoids changing the Studio app's strict CSP
for an isolated technical experiment. Stop this terminal with Ctrl+C afterward.
The original ComfyUI installation is never contacted.

Large downloaded files stay under ignored `web/browser-proof/vendor/`; the pinned
official npm tarball is cached under the ignored project runtime directory.
SHA-512 npm integrity is checked before selecting files. Original MIT and
Apache-2.0 licenses are retained. The published manifest records exact hashes.

`web/browser-proof/reference.json` is the Python reference;
`web/browser-proof/observed-result.json` records the browser result with its user
agent removed. The proof UI performs fresh inference; it does not use the saved
observed result as an inference substitute.

## Product implication

A low-cost public play mode can move computation to the user's device and serve
static assets. Initial asset loading, caching, battery/memory behavior and mobile
compatibility must be measured. The planned Comfy SDK creator path must continue
to do meaningful workflow work; a browser proof alone does not establish contest
eligibility or a completed production app.
