# Voice Motion Lab isolation

This is an independent contest project. Work only under this project root. Never modify, install into, submit jobs to, restart, or clean the user's existing ComfyUI/Studio, skills, registries, shared models or environments.

- Python packages belong only in project `.venv`, `.comfy-venv`, or `.mv-venv`; caches/models/input/output/temp/user belong here.
- The user authorized a contest-first MV creator with a local backend and potentially GPU inference. Check shared RAM/VRAM before every heavy execution; never unload or interrupt unrelated jobs. CPU with two threads remains the safe default. Do not install CUDA or launch GPU models while existing work occupies the device.
- Default app address: `127.0.0.1:8793`. A future separate Comfy environment uses `127.0.0.1:8794`; never default to 8188 or discover/use Studio implicitly.
- No paid APIs, external asset CDNs, automatic publication, or automatic microphone access.
- Read `SPEC.md` before editing. Agents own separate file groups; do not overwrite another agent's work.
- Preserve uploaded SVG appearance, reject executable or external SVG content. Uploaded audio is private and processed locally.
- Do not call a signal-processing fallback an AI/open-model result. Report the actual analysis engine in response metadata and UI.
- MV creator ports: own Comfy 8794, optional official API proxy 8795, MV app 8797. The published BOP basic app remains available.
- A waveform sample is a synthetic demonstration; label it as such.
