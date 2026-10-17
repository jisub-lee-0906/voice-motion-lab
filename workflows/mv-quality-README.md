# GPU quality benchmark — still generation tested, art gate pending

The isolated FLUX.2 klein 4B FP8 benchmark generated two hero PNGs and three
reference-edit PNGs on RTX 5070 Ti 16GB. This is a five-still technical experiment;
the art gate, actual animation, a 15-second MV and audio-only automatic directing
remain incomplete. The CPU MV app and the user's existing Studio stay separate.

The current source prompts now prepare **color direction v2**, described in
`mv-color-script.v2.json` and [the color board](../docs/mv-color-script.html).
The five recorded images below used v1 prompts; their raw run files are unchanged.
No v2 images have been generated. The v2 colors and value-area targets are original
design choices, not sampled reference RGBs, measured statistics or model guarantees.
API/GUI prompts remain synchronized. A colored-light plan is not an implemented
time-varying lighting renderer or an art-gate pass.

## Verified runtime and resource policy

- Dedicated `.gpu-venv`, Python 3.12 x64.
- Official `torch==2.14.1+cu130`, `torchvision==0.29.1+cu130`, CUDA 13.0, pinned
  wheel SHA256s in `tools/setup_mv_gpu.ps1`; Comfy source revision is fixed there too.
  The dedicated installation passed `pip check`, `sm_120` recognition and a small
  actual BF16 GPU operation before the image runs.
- [NVIDIA lists RTX 5070 Ti at compute capability 12.0](https://developer.nvidia.com/cuda/gpus).
  [PyTorch introduced Blackwell support with CUDA 12.8](https://pytorch.org/blog/pytorch-2-7/).
  The selected Windows cp312 CUDA 13.0 stack executed this fixed benchmark.
  This does not validate every CUDA kernel or a different model. No script changes
  or installs a driver.
- Comfy source requirements accept `torch` and `torchvision` without conflicting
  version pins. Constraints retain the selected CUDA wheels, Transformers 5.20,
  and the source's fixed kitchen/aimdo versions. The recorded installed stack
  passed dependency checking; reproducibility still requires the pinned sources.
- Every new execution needs explicit approval for its current resource window.
  Default launch guards remain **12 GiB free VRAM / 16 GiB free RAM**, startup
  GPU utilization at most 5%, and no unapproved GPU contexts. XML C/G/C+G types,
  live CIM executable paths, file SHA256s and UTC creation times are checked
  against a short current lease. Unknown GUI apps are refused too. A previously
  approved idle original Comfy context needs explicit queue/unload/reservation
  evidence; the guard never calls the original server API or renews its lease.
- This run used the explicit `controlled_mmap_probe` lease: initial RAM floor
  **8 GiB**, subsequent candidate floor **4 GiB**, with the initial VRAM guard
  unchanged. These are controlled-test conditions, not general model minimums.
  Shared available RAM was observed down to **5.972 GiB**; the 3 GiB watchdog
  did not trigger. Peak VRAM and per-process peak RAM were not measured.
  Setup/start never unload, stop or restart an existing process.
- Model weights: **12,451,817,860 bytes**; allow at least 40 GiB disk free for
  weights, isolated packages, caches and intermediate files.

## Recorded runs

| Run ID | Budget and output | Observed elapsed time |
|---|---|---|
| `afb670840e3d497898c9cbe5af5c870a` | seeds 7/19, two 1024×1024 hero PNGs | 25.176 s total |
| `d560c40a6c4d4dff824b49b127562ade` | edit preflight refused for RAM; zero candidates, zero regeneration | no image generated |
| `f17c039c90b64ef2ae1beeb55845a4fa` | three 1024×576 reference edits | about 8.2 s per SDK candidate interval; 37.036 s total |

Artifacts and reports remain local at `output/mv-quality/runs/<run-id>/`.
The same canonical `hero_seed_19/original.png` from the hero run was selected
by an agent: `selection:agent_reviewed`, `human_art_approval:false`. The user
has not approved the art. S01 already holds the star and duplicates it; S02
does not establish release; S03 communicates a new space best, but star scale,
coat continuity and back-view identity remain imperfect. Technical success is
`generated_awaiting_art_review`, not an art-gate pass.

Timings include submission, validation and retrieval overhead for this one
machine; they are not isolated inference timing or performance promises.
See [the verification record](../VERIFICATION.md) for scope and limitations.
The download manifest and `mv-quality-provenance.json` retain their original
preparation-time `gpu_tested:false` declarations; actual runs are separate
evidence and do not rewrite that historical provenance.

## Commands for the next approved resource window

After this experiment, only the verified project GPU services were stopped with
an empty queue; the CPU MV services and prior completed result were restored.
The current lease was retired as completed. The commands below require a new
current resource check and approval; the recorded trial is not ongoing consent.

Read-only preparation status and resource inspection do not allocate GPU models:

```powershell
.\.mv-venv\Scripts\python.exe .\tools\setup_mv_quality.py --status
.\.mv-venv\Scripts\python.exe .\tools\run_mv_quality.py --resource-status
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

Separate SDK proxy, after the new GPU instance is verified:

```powershell
pwsh -NoProfile -File (Join-Path (Get-Location) 'start-mv-quality-proxy.ps1') -ResourceApproved
```

This uses the project's existing official proxy package in `.mv-venv`, its own
state directory, **8798 → 8796**, and verifies actual port ownership/ancestry
before connecting. It never uses CPU MV ports 8794/8795 or the original Studio endpoint.

## Strict benchmark budget

`mv-quality-benchmark.json` specifies exactly two original hero candidates:
**seed 7 and seed 19**, four Euler steps, CFG 1, batch 1, 1024×1024. Set node `6`
to the selected seed and node `13` to a distinct filename prefix for each run.
There are no candidate regeneration retries. The runner requires explicit
`--run` and enforces one submission wire POST per candidate, including HTTP 429.

Use the actual two images to review the visual direction. A separate edit stage
requires an explicit reviewed project PNG and stages a fresh per-run copy. The
three bounded edit shots are pocket extraction / hand release / star-linked door
at 1024×576, matching the original courier in the art bible. The
edit graph encodes that same original reference and conditions both branches
through `ReferenceLatent`; it does not use each previous edit as the next sole
reference. These benchmark prompts are replaceable original art directions.

API graphs are flattened core-node graphs used by the actual SDK runs.
GUI graphs have matching links and allow inspection in the separate Comfy UI.
Original official JSON hashes, adaptations and MIT attribution are recorded in
`mv-quality-provenance.json` and `mv-quality-upstream.LICENSE`. The explicit asset
downloader verifies official source JSON, model hashes and original licenses.

Retain each raw PNG, exact submitted graph, seed, prompt, actual timings,
resource snapshots and hashes. The current runner records snapshots, not peak
VRAM/RAM. The separate watchdog observed shared available RAM, not process peak
memory. Do not call the art benchmark passed until the same original
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
idle own queue and the current resource lease before submitting. A Windows venv
redirector base child is permitted only directly below its exact dedicated venv
with identical argument tails and matching project `pyvenv.cfg`. It fixes the exact
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
