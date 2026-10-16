# Voice Motion Lab v0.1 contract

This document preserves the basic v0.1 editor contract. Active contest MV work
uses [docs/MV_CONTRACT.md](docs/MV_CONTRACT.md) and the subsequent
[quality gate](docs/MV_QUALITY_GATE.md), [art bible](docs/MV_ART_BIBLE.md) and
[shot contract](docs/MV_SHOT_CONTRACT.md). The original Studio isolation rules
still apply to every version.

Goal: upload original SVG, record/import a five-second voice performance, analyze timing/pitch/impacts locally, preview a deterministic editable animation and export project JSON, animated SVG, and video.

No semantic promise that arbitrary sounds are understood. Sustained voice advances a chosen motion path, silence holds progress, attack peaks add impact/squash, and voiced pitch adds vertical expression. Signal analysis is a real non-model fallback; Basic Pitch ONNX on CPU is the open-model engine. Models load sequentially; no GPU.

## Web/API boundary

Static web app in `web/`, served by Python `server.py` on loopback port 8793. No frontend build or npm install needed. UI in Korean with a polished clear workspace, drawing canvas, 5s microphone capture, audio file import, explicit synthetic demo, waveform/timeline, path preset and intensity/smoothing controls, and export.

GET `/api/health`: JSON `{status:"ok", version:"0.1.0", engines:{signal:true,basic_pitch:boolean}, model:{...}, comfy:{configured:boolean,url:string|null}}`.

POST `/api/analyze`: JSON `{samples:[float...], sample_rate:int, engine:"auto"|"signal"|"basic_pitch", sensitivity:float, smoothing:float}`. Maximum 15 seconds and 3MB JSON, 8k..96k sample rates. Browser should downsample to 22050 or 16000 Hz mono. No audio is stored by this endpoint.

Response analysis:
```
{version:1, duration:5.0, sample_rate:22050, engine:"signal"|"basic_pitch", frames:[{time:0.0, energy:0.0, pitch:0.5, pitch_confidence:0.0, onset:0.0, progress:0.0}], events:[{time:1.2,kind:"impact",strength:0.8}], warnings:[]}
```
All frame numeric fields finite. time in seconds. energy/pitch/pitch_confidence/onset/progress are normalized 0..1. Frames at ~60 Hz. Empty/silent audio yields all-zero energy/progress, no events. Normalized pitch defaults to .5 with confidence zero when unvoiced. Progress monotonic, held through silence; last voiced progress reaches 1.0. Attack events have minimum spacing. Engine auto may fall back with explicit warning; explicit basic_pitch must fail clearly if unavailable.

GET `/api/model` reports installation metadata. POST `/api/analyze-comfy` accepts the same payload and only talks to explicitly configured project-only Comfy URL on 8794, checks project node prefix, uploads no files, queues only this project's graph, polls with timeout and returns the same analysis. Default unconfigured returns clear 503.

## Renderer / saved project

Web modules should export `sanitizeSvg(text)`, `motionAt(time, analysis, settings)`, and animated SVG creation for node/browser tests. Stage logical 960x540. Settings: `{preset:"fly"|"bounce"|"orbit", intensity:1, smoothing:0.35, background:"#10151f", transparent:false}`. motionAt returns `{x,y,scaleX,scaleY,rotation,opacity}`. Silence holds path progress. Bounds avoid clipping at default intensity. Scene output must match preview through a shared transform evaluator.

Project JSON `{format:"voice-motion-project",version:1,asset:{name,svg},audio:{sample_rate,samples},analysis,settings}`. Export keeps actual sampled transforms/keyframes, not merely a screenshot. SVG playback uses explicit time-based transform keyframes. Browser video export records the actual canvas with optional original audio into WebM. Import project JSON and edit again. No remote references in exports.

## Comfy pack

Create independent V3 pack in `custom_nodes/voice_motion/`, never install into existing Comfy. Node prefix `VoiceMotion`. JSON input/output avoids tensor dependency for CPU analysis: `VoiceMotionAnalyze` maps audio-samples JSON to analysis JSON; `VoiceMotionProject` packages source SVG, original audio, analysis, and settings as editable project JSON. Animated SVG and video are produced by the web app's shared renderer after project import, not by the Comfy Project node. Include API and editable GUI graphs in `workflows/`. Comfy server setup is optional independent installation, not a launch of user's Desktop. Distinguish stub tests from actual queued execution checks on the isolated instance.

## File ownership

- Frontend agent: `web/**`, frontend tests under `tests/web/**`.
- Backend agent: `src/**`, `server.py`, `tests/test_analysis.py`, `tests/test_server.py`; may add backend `requirements.txt`.
- Comfy agent: `custom_nodes/**`, `workflows/**`, `tests/test_nodes.py`.
- Root: `tools/**`, README, license/notices, metadata/config, scripts, runtime/dependency setup, integration fixes after coordination.
