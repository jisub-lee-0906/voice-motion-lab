# Browser play mode v0.2 implementation contract

Goal: no account, choose an original BOP! character, explicitly record five seconds,
then immediately preview and automatically prepare a shareable video. Basic mode
must work from a static HTTPS host without Python, ComfyUI or inference APIs.
Never upload/store microphone samples, add telemetry or silently use a server fallback.

## Ownership for this implementation

- UI agent: web/index.html, web/play/app.mjs, style.css, scene.mjs, mic.mjs, and UI tests.
- Analysis agent: web/play/analysis-client.mjs, analysis.worker.mjs, analysis.mjs and
  analysis Node/reference tests. Optional same-group Python reference generator.
- Export agent: web/play/export-video.mjs, media helpers/tests and
  tools/setup_export_runtime.py for any pinned self-hosted muxer asset.
- Root: legacy Studio preservation, server static/CSP, offline cache, build/deploy,
  README, integration fixes after coordination, browser verification.

All existing isolation rules apply. Shared core.mjs stays compatible with legacy
Studio. No existing ComfyUI/Studio modifications, no GPU inference or paid APIs.

## Analysis module interface

`BrowserAnalyzer` exported by `./analysis-client.mjs`:

```js
const analyzer = new BrowserAnalyzer({ onProgress: message => {} });
await analyzer.warmup(); // model availability metadata, no mic or audio network
const analysis = await analyzer.analyze(samples, sampleRate, {
  engine: 'auto', sensitivity: 1, smoothing: .35, signal: abortSignal
});
analyzer.cancel(); // safely abort current work, reset worker/session
analyzer.dispose();
```

Keep copied original samples usable after worker transfer. Analysis is the existing
version 1 `{duration,sample_rate,engine,frames,events,warnings}` schema; engine is
actual `basic_pitch` or explicitly labeled `signal`. Worker uses one CPU WASM
thread. Dynamic runtime import allows a true local signal fallback if runtime or
model cannot load. Reuse same-origin `../browser-proof/vendor/` assets without
duplicating runtime files. No API fetches or automatic remote fallback.

Port the Python model chunking/time correction and energy-gated onset handling.
Preserve silence holds and reject unsafe input. Tests must compare full fixtures,
not only output shape or aggregate model statistics.

## Video module interface

```js
import { createMemeVideo, shareVideo, downloadVideo } from './export-video.mjs';
const video = await createMemeVideo({
  renderFrame: (ctx, time) => {}, samples, sampleRate: 22050,
  duration: 5, width: 720, height: 960, fps: 24,
  onProgress: ({progress, message}) => {}, signal: abortSignal,
});
// {blob,mimeType,extension,width,height,duration,method}
downloadVideo(video, 'bop-meme');
await shareVideo(video, 'bop-meme'); // only inside explicit trusted click
```

Prefer fast offline browser encoding with MP4 where actually supported. Use a
self-hosted, pinned, licensed muxer if necessary. Feature-detect and fall back to
MediaRecorder (MP4 or WebM) using original audio; never claim unsupported MP4.
Source audio is not played through speakers while making the file. Abort must
release tracks, encoders, contexts and timers. Do not permanently set hidden flags
or leave promises pending. Handle native sharing cancellation normally. Use
software-preferred encoding, no GPU model inference.

## User-facing behavior

- Korean, playful, accessible. Three large character buttons (bounce/fly/orbit),
  one primary five-second record button, clear listening countdown and stop/cancel.
- Open AudioContext/resume and request mic from explicit user gesture. Count only
  actual recording time, trim/pad to five seconds. Stop tracks on finish, error,
  cancellation, visibility/page exit. No automatic microphone prompt.
- Warm the model while recording; immediately show motion preview when analysis
  completes and auto-prepare video with progress. Fast encoding preferred;
  realtime fallback can play preview while preparing. Ready output is a real
  playable Blob video, not a canvas labeled as a saved video.
- Insecure context, denied/missing mic, silence, interrupted recording, model
  failure and unsupported encoding have useful recovery. Add an explicitly labeled
  synthetic five-second demo so first-use can be tried without mic.
- Render portrait scene with large character, expressive sound-driven motion,
  caption and small brand mark. Keep art in frame. Preview and export share the
  same drawing function. Do not imply semantic understanding of arbitrary sounds.
- Changing character can reuse recorded sound; abort stale export and never let
  an old take replace a newer one. Include repeat/record again, save and native
  share where supported. No sign-in before creation or sharing.
- Mobile: no horizontal scroll at 360px, 44px+ targets, safe areas, readable type,
  playsinline video, reduced-motion support, visible keyboard focus and ARIA status.
- Pure UI/assets GET requests only. Friend remix URLs may contain character ID,
  never the recorded audio. No analytics, uploads, account or paid endpoint.

Legacy Studio will be preserved under web/studio by root before UI replacement.
Default local URL remains 8793; standalone static deployment must also work under
a GitHub Pages repository subpath. Use relative asset/module URLs.
