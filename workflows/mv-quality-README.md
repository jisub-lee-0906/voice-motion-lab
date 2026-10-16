# Prepared GPU quality benchmark — not executed

This package is a source-only preparation. No GPU package, model weight, server,
or inference was installed, downloaded, started, or executed while preparing it.
The source graphs passed structural checks; GPU compatibility and image quality
remain untested. The existing CPU MV app and existing Studio are separate.

## Planned runtime and resources

- Dedicated `.gpu-venv`, Python 3.12 x64.
- Official `torch==2.14.1+cu130`, `torchvision==0.29.1+cu130`, pinned wheel SHA256s
  in `tools/setup_mv_gpu.ps1`; Comfy source revision is fixed there too.
- [NVIDIA lists RTX 5070 Ti at compute capability 12.0](https://developer.nvidia.com/cuda/gpus).
  [PyTorch introduced Blackwell support with CUDA 12.8](https://pytorch.org/blog/pytorch-2-7/).
  The selected official Windows cp312 CUDA 13.0 wheels were verified to exist.
  The installed NVIDIA driver must support the selected runtime; no script changes
  or installs a driver. Runtime kernel compatibility is still to be checked.
- Comfy source requirements accept `torch` and `torchvision` without conflicting
  version pins. Constraints retain the selected CUDA wheels, Transformers 5.20,
  and the source's fixed kitchen/aimdo versions. Actual resolver/runtime success
  is not claimed until the dedicated installation and `pip check` are run.
- Explicit resource approval for the current attempt, at least **12 GiB free
  VRAM / 16 GiB free RAM / no other GPU compute PID**, and low current utilization.
  Setup/start refuse occupied resources; they never unload, stop or restart any
  process. These are conservative launch guards, not proof that a graph fits.
- Model weights: **12,451,817,860 bytes**; allow at least 40 GiB disk free for
  weights, isolated packages, caches and intermediate files.

## Commands for a later approved resource window

Read-only inventory is safe without a GPU window:

```powershell
.\.mv-venv\Scripts\python.exe .\tools\setup_mv_quality.py --status
.\tools\setup_mv_gpu.ps1
```

The following commands are reviewable future actions. **Do not run them until
the root agent/user explicitly approves the current shared-resource window.**
The flags acknowledge that approval; they do not create permission themselves.

```powershell
.\tools\setup_mv_gpu.ps1 -Install -ResourceApproved -Python .\.mv-venv\Scripts\python.exe
.\.mv-venv\Scripts\python.exe .\tools\setup_mv_quality.py --download --resource-approved
pwsh -NoProfile -File (Join-Path (Get-Location) 'start-mv-gpu.ps1') -ResourceApproved
```

The GPU instance uses **8796**, `.runtime/mv-quality/models`, `input/mv-quality`,
`output/mv-quality`, `temp/gpu`, `user/gpu` and `.cache/mv-gpu`. It has no custom
nodes or shared model paths. It never attaches to an occupied 8796.

Optional separate SDK proxy, after the new GPU instance is verified:

```powershell
pwsh -NoProfile -File (Join-Path (Get-Location) 'start-mv-quality-proxy.ps1') -ResourceApproved
```

This uses the project's existing official proxy package in `.mv-venv`, its own
state directory, **8798 → 8796**, and verifies actual port ownership/ancestry
before connecting. It never uses CPU MV ports 8794/8795 or Studio port 8188.

## Strict benchmark budget

`mv-quality-benchmark.json` specifies exactly two original hero candidates:
**seed 7 and seed 19**, four Euler steps, CFG 1, batch 1, 1024×1024. Set node `6`
to the selected seed and node `13` to a distinct filename prefix for each run.
There are no automatic retries or automatically queued jobs in these scripts.

Use the actual two images to review the visual direction. Stage one accepted
canonical image as this instance's `input/mv-quality/hero-reference.png`. The
three bounded edit shots are pocket extraction / hand release / star-linked door
at 1024×576, matching the original courier in the art bible. The
edit graph encodes that same original reference and conditions both branches
through `ReferenceLatent`; it does not use each previous edit as the next sole
reference. These benchmark prompts are replaceable original art directions.

API graphs are flattened core-node graphs ready for a later explicit SDK run.
GUI graphs have matching links and allow inspection in the separate Comfy UI.
Original official JSON hashes, adaptations and MIT attribution are recorded in
`mv-quality-provenance.json` and `mv-quality-upstream.LICENSE`. The explicit asset
downloader verifies official source JSON, model hashes and original licenses.

Retain each raw PNG, exact submitted graph, seed, prompt, actual timings,
resource snapshots and hashes. The current runner records snapshots, not peak
VRAM/RAM. Do not call the benchmark passed until the same original
character is recognizable across three good compositions and poses. RGBA layers,
background repair, convincing animation and automatic image selection remain
separate unvalidated steps. No whole-song rendering should hide a failed art gate.

## Official SDK runner

Default planning is read-only: no model, resource probe, HTTP or output writes.

```powershell
.\.mv-venv\Scripts\python.exe tools/run_mv_quality.py --dry-run
```

Only after a user-approved resource window, model integrity verification and the
dedicated 8796/8798 services are ready:

```powershell
.\.mv-venv\Scripts\python.exe tools/run_mv_quality.py --run --resource-approved
# After reviewing a real hero PNG, a separate, explicit bounded edit stage:
.\.mv-venv\Scripts\python.exe tools/run_mv_quality.py --run --resource-approved --stage edit --reference <project-quality-PNG>
```

The runner verifies actual listener ancestry, loopback origins and isolated paths,
idle queue and no foreign compute process before submitting. It fixes the exact
node/input/wiring schema and cross-checks the editable GUI. Official SDK 0.5.0
handles submit/poll/download; a per-client HTTP boundary refuses a second wire
POST, SDK's internal 429 retry, redirects and unrelated job/artifact reads. PNGs
are bounded while streaming through the SDK's public `to_stream()` interface.

All reports begin with `success:false`. A technical success is
`generated_awaiting_art_review`, never a finished MV or automatically accepted art.
Unknown submissions retain their idempotency key and failure report; do not rerun
blindly. The runner does not cancel, unload or restart any server. A separate edit
invocation also requires the initial free-memory threshold; cached models can make
it refuse. Resolve that only within this project's own runtime and recheck shared
resources. It is not permission to free models in another Studio.
