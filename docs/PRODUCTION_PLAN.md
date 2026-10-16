# Product direction and submission readiness

## The public product

Make a playful **sound-to-mascot web app** for people who do not edit video.
The first session should be: choose a character → make a five-second sound →
watch it move → save a video. SVG import, analysis-engine choices and curve
controls belong in an advanced creator view. The current Studio UI is a technical
prototype; it is not yet this simplified public interface.

The BOP! gallery is an actual production test with original vector mascots and
synthetic performances. No animation was generated inside the source SVGs, and
gallery videos are the native app exports. It demonstrates timing, silence and
elastic response. It is not evidence of human microphone usability or semantic
understanding of sounds.

## Hosting and computation

- The present Basic Pitch ONNX model is about 225 KiB and runs on CPU. A permanent
  GPU server is not required for the current feature set.
- A desktop browser proof now runs the actual model with single-thread WASM.
  The first 43,844 PCM samples (~1.99 seconds) took 71.9ms in one observed run,
  with aggregate output statistics matching Python within stated tolerances.
  See [the proof](BROWSER_PROOF.md). The full audio/microphone analysis pipeline
  and mobile performance are not yet ported or verified.
- Canvas preview, SVG generation and video recording already happen in the
  browser. Original audio need not be uploaded merely to create a downloadable
  video if analysis can move into the browser.
- Keep any production Comfy workflow meaningful: actual analysis/refinement or
  artifact creation, not a decorative SDK call. The current app's configured
  analysis route really queues its own Comfy nodes.
- Public hosting must not expose the developer's local machine or existing Studio.
  Decide hosting budget, concurrency limits and retention policy after measuring
  the simplified public flow. No paid service or public inference endpoint has
  been deployed.

## Official Comfy integration

The [competition page](https://comfy.org/open-source-higgsfield) asks for a working
creative app with a usable interface and example outputs, code/workflows/license/
local setup, a demo of at most two minutes, and a social post. A deployed try-it
link is encouraged rather than mandatory. Submission is intentionally deferred
until the production build is ready.

The current v0.1 calls the native ComfyUI Server `/prompt` route. Whether that
legacy route alone satisfies the competition's named Developer Platform is not
explicitly settled by the published wording. Do not claim verified eligibility.

The proposed submission architecture uses the [official Comfy SDK](https://docs.comfy.org/development/api-development/sdks)
and [local API v2 proxy](https://docs.comfy.org/development/comfyui-server/api-proxy),
which officially support self-hosted ComfyUI:

```
Browser UI → project backend → official Comfy SDK
           → project API v2 proxy :8795 → isolated ComfyUI :8794
           → Basic Pitch → editable motion → browser export
```

Research pins: `comfy-sdk==0.5.0`, `comfy-api-proxy==0.1.6`.
They are **not installed or integrated in v0.1**. Never use their default ComfyUI
ports or fall through to Cloud when configuration is absent. The proxy needs a
downloadable analysis JSON output; the current STRING history output alone is
not exposed through SDK `get_outputs()`. Save only project-owned analysis artifacts
under a bounded retention policy. Avoid persistence of input audio/workflow samples
without an explicit product decision. Official SDK use does not itself guarantee
contest eligibility.

## Gates before final submission

1. A playful first-use UI; microphone errors, stop/retry and mobile use tested with
   consenting people. Starter characters require no SVG knowledge.
2. Full browser analysis port and mobile performance measured; a chosen, bounded
   deployment architecture. Preserve the user's existing Studio isolation.
3. Actual official SDK workflow → output bytes → UI integration proven end to end.
4. Production exports: predictable framing, intentional ending/loop behavior,
   seekable video metadata, audio/video alignment, supported sharing formats.
5. Human voice/noise tests and simple usability trials. Existing synthetic inputs
   are repeatable test fixtures, not a substitute for these trials.
6. Final demonstration, public links, personal details and required consents checked
   before the single competition submission. No application has been submitted.

Known output limitation: browser MediaRecorder WebM files can initially report
infinite duration and lack usable seek ranges. The BOP! videos played successfully,
but their container metadata needs improvement before calling exports production-ready.
