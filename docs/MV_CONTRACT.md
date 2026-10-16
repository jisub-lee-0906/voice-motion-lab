# v0.3 contest MV creator contract

User authorized relaxing mobile/browser-only/no-backend limits for a contest-first
song-to-lyric-driven 2D animated music-video product. Existing Studio isolation is
unchanged. No paid endpoints, existing-server jobs, submission, or SNS publication.

## First concrete deliverable

One audio file, no manually selected scene assets or timeline cuts, produces an
original 16:9 2D music video, synchronized lyric text, and editable directing JSON.
Start with a 15–45 second excerpt (max 60 seconds); reject oversized audio clearly.
Original flat illustrated character, recurring visual motifs, distinct shot types,
musically timed cuts, foreground/background motion, poses and readable typography.
Do not represent these authored procedural assets as image-model generation or
claim the reference artist's quality. Keep an extension boundary for future
generated illustration packs. Real ASR is required for the normal audio-only path.
Never silently substitute demo lyrics. Instrumental/uncertain transcription is
reported. A user-provided lyric correction is optional and explicitly identified.

## Execution

Local MV app :8797 → bounded single job manager → project-local CPU ASR subprocess
→ official Comfy Python SDK → own API v2 proxy :8795 → own ComfyUI :8794
→ VoiceMotionMVCreate → actual MP4 / project.json / storyboard.jpg file outputs.
The app derives a VP9/Opus WebM preview from that actual MP4 for browsers without
H.264 support. The SDK graph remains responsible for the final MP4 rendering.
No decorative SDK call: the graph performs audio analysis, scene planning and final
video rendering. Output retrieval must return actual artifact bytes, with errors
and progress surfaced. No cloud keys or existing account tokens needed locally.

Dedicated env `.mv-venv`; models under `.runtime/mv-models`, project caches only.
Existing independent Comfy CPU env may gain necessary project-only dependencies.
Root controls all installs/model executions/server lifecycle. Agents do not start
processes or execute heavy models. Shared GPU is currently occupied; do not use it.

## Ownership

- sdk agent: `src/voice_motion/mv_sdk.py`, new `custom_nodes/voice_motion/mv_nodes.py`,
  extension registration, `workflows/mv-*`, `tools/setup_mv_sdk.py`, proxy start script,
  own SDK/node tests. Research exact contest requirements. Coordinate dependencies.
- engine agent: `src/voice_motion/mv_engine.py`, `mv_art.py`, own tests under
  `tests/mv/test_engine.py`, `test_art.py`. Do not edit existing analysis/renderer.
- UI agent: `web/mv/index.html`, `app.mjs`, `style.css`, optional helper modules.
  No old BOP/Studio files or root server changes. The UI runs only on :8797 for now.
- root: ASR, project setup, synthetic original test song, job manager/HTTP server,
  docs, runtime processes, validation, publication of source only after QA.

## Engine interface

```python
create_music_video(audio_path: Path, output_dir: Path, transcript: dict,
                   theme='auto', seed=7, progress=None) -> dict
# progress(float 0..1, message:str), no GPU, at most two CPU threads
# output_dir is an existing, project-owned job directory
# result {video_path, project_path, storyboard_path, duration, width,height,
#         fps, shots, warnings, engines, ...} paths absolute for internal use only
```

Input decoded through PyAV, finite mono metrics and transient/beat analysis using
numpy/scipy. Render via Pillow and PyAV (CPU H.264/AAC MP4), 960×540 at 24fps; keep
original audio and deterministic frame timestamps. Korean fonts from Windows may
be read-only references; don't copy proprietary fonts into source/public bundle.
Use font fallbacks; no system install. All intermediate files are job-owned.

Transcript: `{engine,language,text,segments:[{start,end,text,
words:[{start,end,text}]}],warnings:[]}` in seconds relative to the input excerpt.
Model timestamps and estimated timings must be distinguished in metadata.
Optional corrected text must set engine `provided_lyrics` and timing source.

## SDK interface

```python
async def run_mv_workflow(audio_path: Path, transcript: dict, output_dir: Path,
                          theme='auto', seed=7, progress=None) -> dict
# same result contract as engine, plus workflow_id/provider metadata
# official comfy-sdk, explicitly fixed local proxy; no external fallback
```

Root will stage/validate audio under project's input/mv/<job-id>/source.wav.
The node must restrict input/output paths to this project's approved directories;
never accept arbitrary filesystem paths from HTTP clients. Backend output_dir is
output/mv/<job-id>. Proxy may have separate owned state, never existing Studio.

## HTTP / UI contract (root implementation)

GET `/api/mv/health`:
`{status,version,asr:{available,engine},comfy:{available,provider},limits:{max_seconds:60,max_bytes:...}}`

POST `/api/mv/jobs` JSON (max 24MiB):
`{audio:{samples:[finite float],sample_rate:16000},title:'...',theme:'auto'|'bloom'|'fracture'|'orbit',seed:7,lyrics?:'...'}`.
Browser decodes/downmixes/resamples selected local audio and sends only after an
explicit Create click. A visible note explains local backend transfer and retained
project-local output. No third-party audio upload or automatic cloud fallback.
Response202 `{id,status:'queued'}`; 429 if busy. No unbounded jobs/threads.

GET `/api/mv/jobs/<id>`:
`{id,status:'queued'|'transcribing'|'directing'|'rendering'|'succeeded'|'failed',
progress:0..1,message,title,error?,result?:{video_url,preview_url?,project_url,storyboard_url,
duration,width,height,fps,shots,engines,warnings}}`.
Safe opaque job ids only; no absolute path/host info in responses.
GET `/api/mv/jobs/<id>/artifacts/video.mp4|preview.webm|project.json|storyboard.jpg` serves only
fixed corresponding output for that job, with range requests for playable video.
GET `/api/mv/demo.wav` returns original synthetic music/TTS fixture, labeled.
No cancellation endpoint until SDK can cancel exactly this workflow safely.
UI shows actual status, elapsed time, in-flight disable, clear failures, file
chooser, optional lyrics details, theme picker and demo. Video controls/playsinline,
download, storyboard and project output; responsive mobile layout but backend
loopback means local desktop use. Preserve basic app at its existing public URL.
Completed jobs can be reopened after an app restart. Interrupted jobs are reported
as interrupted and are never silently restarted or reported as successful.
