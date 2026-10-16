# BOP! MV v0.3 — contest-first implementation and remaining work

The current contest direction is a local song-to-lyric-driven 2D music-video
creator. The user relaxed the browser-only and server-free constraints for this
advanced path; isolation from the user's existing ComfyUI Studio remains mandatory.
The published five-second BOP! v0.2 app stays available as a separate browser product.

## Current implemented path

```text
Local MV UI :8797 → bounded job manager (one active job)
  → project CPU Qwen3 ASR → Qwen3 ForcedAligner
  → official comfy-sdk 0.5.0 → project API v2 proxy 0.1.6 :8795
  → independent ComfyUI :8794 → VoiceMotionMVCreate
  → actual MP4 / editable project JSON / storyboard artifacts
```

The normal path accepts one audio file with no manually chosen scene sources or
timeline cuts. Browser decoding/downmixing/resampling produces 16kHz mono PCM;
oversized duration is rejected before upload. The backend accepts 1–60 seconds,
with 15–45 seconds recommended for the first excerpt. Output is 960×540 at 24fps,
CPU H.264/AAC MP4, a scene contact sheet and an editable directing JSON document.
The audio in the video comes from the submitted resampled mono audio, so this is
not a promise of original stereo fidelity.

The SDK graph performs the creative work: actual audio feature analysis, lyric
symbol/repetition rules, shot planning, authored 2D art and final rendering. It is
not an unused workflow or decorative SDK call. File results must be retrieved as
real bytes through the official API v2 path. No direct-engine fallback or cloud
endpoint is silently substituted. The UI polls actual stage/progress and displays
real failures. It does not pretend to cancel an SDK job that it cannot safely cancel.

The dedicated `.mv-venv` contains server/SDK/proxy dependencies. The project's own
`.comfy-venv` runs ASR/alignment in a subprocess and the independent CPU Comfy runtime.
Pinned Qwen ASR and alignment weights total about 3.4GB and live only under
`.runtime/mv-models`. CPU work uses two threads, models load sequentially, and
Windows job admission checks at least 5GB available RAM. No existing GPU work is
unloaded or interrupted. Setup/start instructions and exact model revisions are
in [MV_CREATOR.md](MV_CREATOR.md).

## What is still a draft

The current art is original, directly authored procedural 2D illustration with
recurring character/motif state, poses, compositions, transitions and lyric text.
It proves the automatic directing/rendering boundary; it is not image-model art
or a claim to the polish of the reference music videos. Theme semantics currently
use a Korean/English lexicon rather than a general lyric-understanding model.
Measured spectral onsets/energy inform timing; exact downbeats are not guaranteed.

Two actual CPU OpenVINO LCM illustration trials failed the intended flat-art
direction and were not adopted. This optional experiment is separate from the
default MV pipeline. Future artwork packs must preserve identity, layer consistency,
provenance and animation usability before they can replace the current renderer.

The explicit 32-second demo is original procedural music mixed with Microsoft
Zira synthetic spoken TTS. It is **not natural singing or a human recording**.
The default demo request still runs real ASR/alignment; expected fixture lyrics
are not prefilled into the production request. Natural Korean singing, strong
accompaniment, multiple singers and diverse song structures remain quality tests.

## Live verification status

Code implementation and tests must be distinguished from whole-product evidence.
The initial SDK result-metadata failure has been repaired. An actual UI run now
completed a 32-second synthetic fixture through real CPU ASR/alignment and the
official SDK graph, producing 13 shots in about 46 seconds on this PC. Including
the derived WebM preview, the final repeated UI run took about 49 seconds. These
are observed fixture timings, not general performance promises. Independent decoded
MP4 inspection confirmed 768 H.264 frames at 960×540/24fps, 32 seconds of AAC audio,
source correlation 0.99999464 and zero measured audio lag, continuous shot coverage
and no absolute host paths in the directing JSON. Actual WebM browser playback
advanced to 8.09 seconds with readyState 4. Browser-downloaded MP4 SHA-256 matched
the rendered master. A numeric-extension routing error was fixed; H.264 codec
support was not the established cause of that earlier playback failure. Completed
results survive refresh and app restart. Python 82 + Node 65 = 147 tests passed.
Current evidence is recorded separately by version in `VERIFICATION.md`.

User input and outputs stay in ignored project-owned `input/mv/<job-id>` and
`output/mv/<job-id>` directories. They are not uploaded to GitHub or external
inference services. Model/cache/temp/proxy state also remain in this checkout.
No automatic retention cleanup is currently implemented. JSON includes lyrics
and MP4 includes audio; users should inspect a file before sharing it.

## Before a final contest submission

1. Preserve the verified local UI → CPU ASR/alignment → SDK graph → actual files
   flow. Add fresh-install and physical-device checks before distributing a runtime.
2. Test several authorized excerpts with natural singing, particularly Korean;
   assess lyric accuracy, word timing, scene variation and character consistency.
3. Improve artwork and visual direction toward a useful original MV product.
   Any generated-illustration option must pass identity/layer/quality checks and
   keep model/version/license/engine metadata explicit.
4. Recheck the [competition](https://comfy.org/open-source-higgsfield) requirements
   against the actual build. Official SDK use is implemented but neither that
   fact nor this plan alone guarantees eligibility.
5. Prepare the two-minute demonstration and representative outputs, source,
   workflows, licenses and required submission details. Required SNS publication
   and final submission have **not** been performed. The user deferred final
   submission until production readiness; do not submit automatically.

The original ComfyUI Studio, shared models, workflows, environments and servers
remain untouched. This local MV creator is not deployed to GitHub Pages. The
[public BOP! v0.2 URL](https://jisub-lee-0906.github.io/voice-motion-lab/) continues
to serve only its browser-native five-second experience.

---

# Historical record: BOP! v0.2 implementation and submission plan

The following is the earlier v0.2 plan and its verification evidence. Statements
such as “pending”, “not installed” and “integration in progress” describe that
earlier stage, not the current v0.3 implementation. Keep these test figures scoped
to v0.2; they do not establish MV creator quality or contest eligibility.

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
