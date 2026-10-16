# BOP! v0.2 — current implementation and submission work

## Implemented product

BOP! is now the default browser app: choose one of three original characters,
explicitly record five seconds, preview the motion, then save or share a real
video. The synthetic demo works without microphone permission. No account,
Python installation, Comfy server or remote inference endpoint is required for
this public basic mode.

The former SVG/audio creator interface is preserved locally at
`/studio/index.html`. Its advanced analysis still uses the project Python backend
and can optionally queue the project's isolated Comfy workflow. This local Studio
is not included in the standalone public static build.

Current browser implementation:

- Full audio analysis runs in a Worker using `onnxruntime-web@1.30.0`, one CPU
  WASM thread and the actual Spotify Basic Pitch model. It includes resampling,
  overlapping model windows with absolute timing, RMS/noise gating, pitch
  confidence, energy-gated impacts and silence-held progress.
- Missing runtime/model assets cause an honestly labeled, real JavaScript signal
  fallback. An explicit Basic Pitch request fails instead of fabricating an AI result.
- Preview and export use the same portrait scene renderer: 720×960, 24fps, five
  seconds. Original sound accompanies the generated video. Supported WebCodecs
  configurations prefer H.264/AAC MP4, then WebM; MediaRecorder is the realtime
  fallback. Actual format/support is detected, not assumed. WebM finalization
  code addresses container duration/seek metadata; actual output QA remains necessary.
- Microphone acquisition is explicit. Recording cancellation, worker termination,
  stale-take rejection, media cleanup, permission/error messages, native sharing
  detection, touch targets and reduced-motion handling are implemented.
- No user audio is uploaded or cached. Only public static app/model/demo assets
  enter the optional service-worker cache. A saved/shared video contains its sound;
  a friend challenge URL contains the character choice only.

The model is about 225 KiB; required self-hosted model/runtime/license files total
14,557,305 bytes. The first visit also loads app/character/demo/export assets.
An always-on GPU or inference server is unnecessary for this basic flow. A static
HTTPS host is still needed to distribute the site and allow normal microphone use.

## Evidence and limits

The full analysis regression suite passes 21 tests, including six complete
five-second synthetic fixtures against Python signal and Basic Pitch references.
Actual Node-hosted single-thread WASM inference is exercised. Every model input
chunk matches Python byte-for-byte. On these fixtures, rounded time, energy,
pitch and progress match exactly; maximum confidence difference is 0.000203 and
maximum onset difference is 0.000011 across CPU/WASM kernels. Impact counts and
times match. This is full-frame comparison, not the earlier aggregate-only proof.

The [initial desktop browser proof](BROWSER_PROOF.md) separately records actual
browser inference for one raw model window. It is historical evidence for the
runtime, not the latest whole-product verification record.

The root integration check is in progress. Do not infer completed real-phone
Safari testing, human microphone usability, public deployment or universal MP4
support from the code or synthetic tests. No final contest submission has been made.

## Static delivery

`tools/build_static.py` creates a self-contained static site with relative
module/asset URLs, pinned same-origin runtimes, licenses and a hashed build
manifest. It excludes Python, APIs, local Studio, recordings and developer outputs.
The output directory must be empty; the builder never deletes existing files.
No runtime CDN or paid endpoint is required. HTTPS, proper module/WASM serving,
cache behavior and subpath deployment must be verified on the chosen host before
adding a public try-it URL. Browser storage limits can prevent offline reuse.

The original ComfyUI/Studio remains outside this project and is never modified,
restarted or used for this app. All optional local services and dependencies stay
in this checkout. Basic browser inference uses no GPU.

## Official Comfy integration is still pending

The [competition](https://comfy.org/open-source-higgsfield) names the Comfy
Developer Platform. The existing advanced local route really executes this
project's Comfy nodes through native `/prompt`, but whether that route alone
satisfies the named requirement has not been established. The new browser basic
mode does not call Comfy at all. Do not claim confirmed contest eligibility.

The proposed optional creator/refinement path would use the
[official SDK](https://docs.comfy.org/development/api-development/sdks) and
[self-hosted API v2 proxy](https://docs.comfy.org/development/comfyui-server/api-proxy):

```
Advanced creator → project backend → official Comfy SDK
                 → project proxy :8795 → isolated ComfyUI :8794
                 → actual project-owned artifact → creator UI
```

Research pins are `comfy-sdk==0.5.0` and `comfy-api-proxy==0.1.6`; they are not
installed or integrated. Configuration must remain explicit and project-local,
with no default external Cloud endpoint or discovery of the user's existing
servers. The current STRING history output alone is not exposed as a downloadable
SDK v2 artifact: an actual file output node, output retrieval and bounded artifact
retention are required. Persisting workflows can also persist input samples, so
input retention must be designed before enabling proxy persistence.

The creator path needs a useful creative function beyond what the browser already
does. A decorative SDK badge, unused workflow export or redundant call is not a
sound integration plan. Defining that function, implementing it and checking the
competition interpretation remain submission work; official SDK use alone does
not guarantee eligibility.

## Before a final submission

1. Complete whole-flow desktop QA: real playable output, audio/video alignment,
   five-second ending, metadata/seek, fallback encoders, sharing and cancellation.
2. Test consenting human voices, background noise, silence and microphone-denied
   recovery. Measure a real iPhone/Safari and representative Android browsers,
   including loading, memory/battery, backgrounding and download behavior.
3. Verify a public HTTPS static build, repository subpath, cold/warm/offline cache
   behavior and the absence of recorded-audio network requests. Add its confirmed URL.
4. Decide and deliver the meaningful official Comfy SDK creator/artifact path,
   with actual workflow execution and returned output bytes; resolve eligibility.
5. Prepare the final demonstration, example outputs, source/workflows/licenses,
   required personal details and any consents before submitting. Existing synthetic
   gallery results are clearly labeled and do not stand in for human usability tests.
