"""Bounded, project-only illustration benchmark through official Comfy SDK.

Default and --dry-run are read-only plans: no network, resource probe or SDK import.
Actual work requires --run --resource-approved. This never starts/stops a server,
downloads models, retries a failed candidate or interrupts another job.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import importlib.util
import json
import math
import os
from pathlib import Path
from pathlib import PureWindowsPath
import re
import shlex
import subprocess
import time
import urllib.request
from urllib.parse import urlsplit
import uuid

ROOT = Path(__file__).resolve().parents[1]
PROXY = 'http://127.0.0.1:8798'
COMFY = 'http://127.0.0.1:8796'
SDK_VERSION = '0.5.0'
MAX_PNG = 32 * 1024 * 1024
TEMPLATE_REVISION = '04a508b31bec1818e48122ca0761c1de4140a234'
WIDGET_FIELDS = {'1': ('unet_name', 'weight_dtype'), '2': ('clip_name', 'type', 'device'),
                 '3': ('vae_name',), '4': ('text',), '6': ('noise_seed',), '7': ('cfg',),
                 '8': ('sampler_name',), '9': ('steps', 'width', 'height'),
                 '10': ('width', 'height', 'batch_size'), '13': ('filename_prefix',), '14': ('image',)}
OUTPUT_TYPES = {'1': 'MODEL', '2': 'CLIP', '3': 'VAE', '4': 'CONDITIONING',
                '5': 'CONDITIONING', '6': 'NOISE', '7': 'GUIDER', '8': 'SAMPLER',
                '9': 'SIGMAS', '10': 'LATENT', '11': 'LATENT', '12': 'IMAGE',
                '14': 'IMAGE', '15': 'LATENT', '16': 'CONDITIONING', '17': 'CONDITIONING'}


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'),
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def owned(path, parent):
    """No traversal, symlinks or directory junction escapes, including parents."""
    path, parent = Path(os.path.abspath(path)), Path(os.path.abspath(parent))
    if path.resolve() != path or not path.is_relative_to(parent) or parent.resolve() != parent:
        raise ValueError('Only non-symlink paths in this project quality directory are permitted.')
    return path


def save_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    temporary.replace(path)


def sha256(path):
    value = hashlib.sha256()
    with path.open('rb') as source:
        while block := source.read(1024 * 1024):
            value.update(block)
    return value.hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def fixed_graph(stage):
    """Reviewed flattened official-template adaptation; not read from mutable JSON.

    Only text, seed, output prefix and the staged canonical reference may vary.
    Exact input keys, nodes, classes, values and edges below are immutable.
    """
    if stage not in ('hero', 'edit'): raise ValueError('Unknown benchmark stage.')
    def node(kind, **inputs): return {'class_type': kind, 'inputs': inputs}
    height = 1024 if stage == 'hero' else 576
    graph = {
        '1': node('UNETLoader', unet_name='flux-2-klein-4b-fp8.safetensors', weight_dtype='default'),
        '2': node('CLIPLoader', clip_name='qwen_3_4b.safetensors', type='flux2', device='default'),
        '3': node('VAELoader', vae_name='flux2-vae.safetensors'),
        '4': node('CLIPTextEncode', text='', clip=['2', 0]),
        '5': node('ConditioningZeroOut', conditioning=['4', 0]),
        '6': node('RandomNoise', noise_seed=7),
        '7': node('CFGGuider', model=['1', 0], positive=['4', 0], negative=['5', 0], cfg=1),
        '8': node('KSamplerSelect', sampler_name='euler'),
        '9': node('Flux2Scheduler', steps=4, width=1024, height=height),
        '10': node('EmptyFlux2LatentImage', width=1024, height=height, batch_size=1),
        '11': node('SamplerCustomAdvanced', noise=['6', 0], guider=['7', 0], sampler=['8', 0], sigmas=['9', 0], latent_image=['10', 0]),
        '12': node('VAEDecode', samples=['11', 0], vae=['3', 0]),
        '13': node('SaveImage', images=['12', 0], filename_prefix=''),
    }
    if stage == 'edit':
        graph.update({'14': node('LoadImage', image=''), '15': node('VAEEncode', pixels=['14', 0], vae=['3', 0]),
                      '16': node('ReferenceLatent', conditioning=['4', 0], latent=['15', 0]),
                      '17': node('ReferenceLatent', conditioning=['5', 0], latent=['15', 0])})
        graph['7']['inputs'].update(positive=['16', 0], negative=['17', 0])
    return graph


def validate_graph(graph, stage):
    if not isinstance(graph, dict) or 'nodes' in graph or not graph:
        raise ValueError('Submit a flattened API graph, never an upstream GUI workflow.')
    expected = fixed_graph(stage)
    try:
        text = graph['4']['inputs']['text']; seed = graph['6']['inputs']['noise_seed']
        prefix = graph['13']['inputs']['filename_prefix']
        if not isinstance(text, str) or not 1 <= len(text.encode('utf-8')) <= 8192:
            raise ValueError('Benchmark prompt must be nonempty and at most 8 KiB.')
        if type(seed) is not int or seed not in ((7, 19) if stage == 'hero' else (7,)):
            raise ValueError('Unapproved benchmark seed.')
        if not isinstance(prefix, str) or not re.fullmatch(r'benchmark/(?:hero_seed_(?:7|19)|edit_shot_01_seed_7|[0-9a-f]{32}/[A-Za-z0-9_-]{1,40})', prefix):
            raise ValueError('Unapproved output prefix.')
        expected['4']['inputs']['text'] = text; expected['6']['inputs']['noise_seed'] = seed
        expected['13']['inputs']['filename_prefix'] = prefix
        if stage == 'edit':
            reference = graph['14']['inputs']['image']
            if not isinstance(reference, str) or not re.fullmatch(r'(?:hero-reference\.png|[0-9a-f]{32}/reference\.png)', reference):
                raise ValueError('Only the explicitly staged canonical PNG reference is permitted.')
            expected['14']['inputs']['image'] = reference
        if canonical(graph) != canonical(expected):
            raise ValueError('Graph differs from the exact approved node/class/input/wiring schema.')
    except (KeyError, TypeError) as error:
        raise ValueError('Graph differs from the exact approved node/class/input/wiring schema.') from error


def validate_gui_matches(graph, stage):
    """Check shipped editable GUI is the same graph, including every widget/edge."""
    mode = 't2i' if stage == 'hero' else 'edit'
    gui = read_json(ROOT/f'workflows/mv-quality-{mode}.gui.json')
    nodes = {str(node['id']): node for node in gui['nodes']}
    if len(nodes) != len(gui['nodes']) or set(nodes) != set(graph):
        raise ValueError('GUI node set differs from the approved API graph.')
    expected_edges = []
    for key, api in graph.items():
        node = nodes[key]
        if node['type'] != api['class_type'] or node.get('mode') != 0:
            raise ValueError('GUI node class/mode differs from the approved API graph.')
        linked = {name: value for name, value in api['inputs'].items() if isinstance(value, list)}
        inputs = node.get('inputs', [])
        if len(inputs) != len(linked) or {entry['name'] for entry in inputs} != set(linked):
            raise ValueError('GUI input set differs from the approved API graph.')
        widgets = [api['inputs'][name] for name in WIDGET_FIELDS.get(key, ())]
        if key == '6': widgets.append('fixed')
        if key == '14': widgets.append('image')
        if canonical(node.get('widgets_values', [])) != canonical(widgets):
            raise ValueError('GUI widget values differ from the approved API graph.')
        output_types = [] if key == '13' else ([OUTPUT_TYPES[key], 'LATENT'] if key == '11' else
                                              ['IMAGE', 'MASK'] if key == '14' else [OUTPUT_TYPES[key]])
        outputs = node.get('outputs', [])
        if [entry.get('type') for entry in outputs] != output_types:
            raise ValueError('GUI output set differs from the approved API graph.')
        for index, entry in enumerate(outputs):
            outgoing = [link[0] for link in gui['links'] if link[1] == int(key) and link[2] == index]
            if sorted(entry.get('links') or []) != sorted(outgoing):
                raise ValueError('GUI output wiring differs from the approved API graph.')
        for index, entry in enumerate(inputs):
            origin, slot = linked[entry['name']]
            if entry.get('type') != OUTPUT_TYPES[origin]:
                raise ValueError('GUI connection type differs from the approved API graph.')
            expected_edges.append((int(origin), slot, int(key), index, OUTPUT_TYPES[origin]))
            connections = [link for link in gui['links'] if link[0] == entry.get('link')]
            if len(connections) != 1 or tuple(connections[0][1:]) != expected_edges[-1]:
                raise ValueError('GUI input wiring differs from the approved API graph.')
    if len({link[0] for link in gui['links']}) != len(gui['links']) or sorted(tuple(link[1:]) for link in gui['links']) != sorted(expected_edges):
        raise ValueError('GUI contains missing, extra or rewired connections.')
    provenance = read_json(ROOT/'workflows/mv-quality-provenance.json')
    adapted = gui.get('extra', {}).get('voice_motion_quality', {}).get('adapted_from')
    if provenance.get('source', {}).get('revision') != TEMPLATE_REVISION or adapted != 'Comfy-Org/workflow_templates@'+TEMPLATE_REVISION:
        raise ValueError('GUI/API provenance does not match the reviewed official template revision.')


def build_plan(stage='hero', count=None):
    plan = read_json(ROOT/'workflows/mv-quality-benchmark.json')
    if plan.get('format') != 'voice-motion-quality-benchmark' or plan.get('version') != 1 or plan.get('automatic_retries') != 0:
        raise ValueError('Unsupported benchmark plan or retry policy.')
    candidates = plan['hero_candidates'] if stage == 'hero' else plan['edit']['shots']
    maximum = 2 if stage == 'hero' else 3
    if len(candidates) != maximum or (stage == 'hero' and [item['seed'] for item in candidates] != [7, 19]):
        raise ValueError('Benchmark budget must remain two hero seeds (7,19) or three edits.')
    if count is None:
        count = maximum
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= maximum:
        raise ValueError(f'{stage} candidate count must be from 1 to {maximum}.')
    result = []
    for item in candidates[:count]:
        filename = item['workflow'] if stage == 'hero' else plan['edit']['workflow']
        if filename != f'mv-quality-{"t2i" if stage == "hero" else "edit"}.api.json':
            raise ValueError('Unexpected benchmark API file.')
        graph = read_json(ROOT/'workflows'/filename)
        validate_graph(graph, stage)
        validate_gui_matches(graph, stage)
        seed = item['seed'] if stage == 'hero' else plan['edit']['seed']
        if seed not in (7, 19) or (stage == 'edit' and seed != 7):
            raise ValueError('Unapproved benchmark seed.')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', item['id']):
            raise ValueError('Unsafe candidate identifier.')
        graph['6']['inputs']['noise_seed'] = seed
        if stage == 'edit':
            graph['4']['inputs']['text'] = item['prompt']
        result.append({'id': item['id'], 'seed': seed, 'workflow': graph,
                       'prompt': graph['4']['inputs']['text']})
    return result


# Read-only system snapshot. Only listener ancestors are emitted, never unrelated
# process command lines. No script interpolation, GPU context or Torch import.
SNAPSHOT_PS = r'''
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
$all=@{}; foreach($p in @(Get-CimInstance Win32_Process -ErrorAction Stop)) {$all[[int]$p.ProcessId]=$p}
$listeners=@(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object {$_.LocalPort -in @(8796,8798)} | ForEach-Object {
    @{port=[int]$_.LocalPort;address=[string]$_.LocalAddress;pid=[int]$_.OwningProcess}})
$selected=@{}
foreach($l in $listeners) {
    $next=[int]$l.pid; $seen=@{}
    while($next -and $all.ContainsKey($next) -and -not $seen.ContainsKey($next)) {
        $seen[$next]=$true; $p=$all[$next]
        $selected[$next]=@{pid=[int]$p.ProcessId;parent=[int]$p.ParentProcessId;name=[string]$p.Name;
          command=[string]$p.CommandLine;created=$p.CreationDate.ToUniversalTime().ToString('o')}
        $next=[int]$p.ParentProcessId
    }
}
$g=@(& nvidia-smi --query-gpu=index,name,memory.free,utilization.gpu,driver_version --format=csv,noheader,nounits)
if($LASTEXITCODE -ne 0 -or -not $g.Count) {throw 'Cannot inspect GPU.'}
$fields=([string]$g[0]).Split(',') | ForEach-Object {$_.Trim()}
if($fields.Count -ne 5 -or $fields[0] -ne '0') {throw 'Unexpected GPU inventory.'}
$xmlRows=@(& nvidia-smi -q -x)
if($LASTEXITCODE -ne 0 -or -not $xmlRows.Count) {throw 'Cannot inspect typed GPU contexts.'}
$xmlText=$xmlRows -join "`n"
if($xmlText.Length -gt 2097152) {throw 'GPU XML exceeds the inspection bound.'}
$gpuXml=[System.Xml.XmlDocument]::new();$gpuXml.XmlResolver=$null;$gpuXml.LoadXml($xmlText)
$devices=@($gpuXml.nvidia_smi_log.gpu)
if($devices.Count -ne 1 -or [string]$devices[0].product_name -ne $fields[1]) {throw 'Ambiguous GPU XML device identity.'}
$contexts=@();$pids=@();$hashes=@{}
foreach($context in @($devices[0].processes.process_info)) {
    if($null -eq $context) {continue}
    $contextPid=0
    if(-not [int]::TryParse([string]$context.pid,[ref]$contextPid) -or $contextPid -le 0) {throw 'Invalid GPU context PID.'}
    $contextType=([string]$context.type).Trim()
    $process=$all[$contextPid];$executable=$null;$created=$null;$processName=$null;$hash=$null
    if($process) {
        $processName=[string]$process.Name
        if($process.CreationDate) {$created=$process.CreationDate.ToUniversalTime().ToString('o')}
        if($process.ExecutablePath) {$executable=[string]$process.ExecutablePath}
    }
    if($executable) {
        if(-not $hashes.ContainsKey($executable)) {
            $hashStream=$null;$hashAlgorithm=$null
            try {
                $hashAlgorithm=[System.Security.Cryptography.SHA256]::Create()
                $hashStream=[System.IO.File]::OpenRead($executable)
                $hashes[$executable]=([System.BitConverter]::ToString($hashAlgorithm.ComputeHash($hashStream))).Replace('-','').ToLowerInvariant()
            }
            catch {$hashes[$executable]=$null}
            finally {
                if($hashStream) {$hashStream.Dispose()}
                if($hashAlgorithm) {$hashAlgorithm.Dispose()}
            }
        }
        $hash=$hashes[$executable]
    }
    $contexts += @{pid=$contextPid;xml_type=$contextType;xml_name=[string]$context.process_name;
      name=$processName;created=$created;executable_path=$executable;sha256=$hash}
    if($contextType -eq 'C') {$pids += $contextPid}
}
$os=Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
@{listeners=$listeners;processes=@($selected.Values);gpu=@{name=$fields[1];driver=$fields[4];
 free_vram_gib=([double]::Parse($fields[2],[Globalization.CultureInfo]::InvariantCulture)/1024);
 utilization=[double]::Parse($fields[3],[Globalization.CultureInfo]::InvariantCulture);
 free_ram_gib=([double]$os.FreePhysicalMemory/1024/1024);compute_pids=$pids;process_contexts=$contexts;
 context_source='nvidia-smi XML type + live CIM identity + executable SHA256'}} | ConvertTo-Json -Depth 6 -Compress
'''


def snapshot():
    encoded = base64.b64encode(SNAPSHOT_PS.encode('utf-16le')).decode('ascii')
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                            capture_output=True, text=True, encoding='utf-8', timeout=30, check=False)
    if result.returncode:
        raise RuntimeError('Read-only process/resource inspection failed; no submission performed.')
    return json.loads(result.stdout)


def tokens(command):
    # Windows command paths are quoted; posix=False preserves their backslashes.
    return [value[1:-1] if value.startswith('"') and value.endswith('"') else value
            for value in shlex.split(command, posix=False)]


def normalized(value):
    return value.replace('\\', '/').rstrip('/').casefold()


def option(argv, name, expected):
    matches = [i for i, token in enumerate(argv) if token.casefold() == name.casefold()]
    return len(matches) == 1 and matches[0]+1 < len(argv) and normalized(argv[matches[0]+1]) == normalized(str(expected))


def venv_redirector_base(environment):
    """Windows venv child uses home/python.exe, even with a venv creator.

    In nested project venvs, cfg.executable records the creating venv rather
    than the redirector child. Both declarations must remain explicit and
    project-controlled; no external cfg or system environment is searched.
    """
    path = owned(ROOT/environment/'pyvenv.cfg', ROOT/environment)
    if not path.is_file() or path.stat().st_size > 8192:
        raise RuntimeError('Dedicated project pyvenv.cfg is missing or oversized.')
    values = {}
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        if '=' not in line: continue
        key, value = (part.strip() for part in line.split('=', 1))
        key = key.casefold()
        if key in values: raise RuntimeError('Duplicate dedicated pyvenv.cfg field.')
        values[key] = value
    home = values.get('home', '')
    if not PureWindowsPath(home).is_absolute() or values.get('include-system-site-packages', '').casefold() != 'false':
        raise RuntimeError('Dedicated venv home/isolation configuration is invalid.')
    base = str(PureWindowsPath(home)/'python.exe')
    creators = {normalized(base)} | {normalized(str(ROOT/name/'Scripts/python.exe'))
                                    for name in ('.venv', '.mv-venv', '.gpu-venv')}
    if normalized(values.get('executable', '')) not in creators:
        raise RuntimeError('Dedicated venv executable is not its base or a known project creator.')
    return base


def process_proof(state):
    processes = {int(item['pid']): item for item in state['processes']}
    proof = {}
    for port, launcher, environment in ((8796, 'start-mv-gpu.ps1', '.gpu-venv'),
                                         (8798, 'start-mv-quality-proxy.ps1', '.mv-venv')):
        listeners = [item for item in state['listeners'] if item['port'] == port]
        if not listeners or any(item['address'] != '127.0.0.1' for item in listeners):
            raise RuntimeError(f'Port {port} must have a verified IPv4 loopback listener.')
        owners = {int(item['pid']) for item in listeners}
        if len(owners) != 1:
            raise RuntimeError('Ambiguous benchmark port ownership.')
        current = next(iter(owners)); visited = set(); chain = []; python_pids = []; redirector_child = False
        while True:
            if current in visited or current not in processes:
                raise RuntimeError('Missing or cyclic benchmark launcher ancestry.')
            visited.add(current); process = processes[current]
            argv = tokens(process['command'])
            created = datetime.fromisoformat(process['created'].replace('Z', '+00:00'))
            if created.tzinfo is None:
                raise RuntimeError('Process creation time must include a timezone.')
            if chain and created > datetime.fromisoformat(chain[-1]['created'].replace('Z', '+00:00')):
                raise RuntimeError('Reused PID or invalid benchmark parent creation time.')
            chain.append({'pid': current, 'created': process['created']})
            if process['name'].casefold() in ('pwsh.exe', 'powershell.exe'):
                if not python_pids or not option(argv, '-File', ROOT/launcher):
                    raise RuntimeError('Listener is not a descendant of the exact project launcher.')
                break
            if process['name'].casefold() != 'python.exe' or not argv:
                raise RuntimeError('Port owner must use the dedicated project Python entry.')
            dedicated_python = normalized(str(ROOT/environment/'Scripts/python.exe'))
            if normalized(argv[0]) != dedicated_python:
                parent = processes.get(int(process['parent']))
                parent_argv = tokens(parent['command']) if parent else []
                if python_pids or current != next(iter(owners)) or not parent or parent['name'].casefold() != 'python.exe' or not parent_argv or normalized(parent_argv[0]) != dedicated_python or argv[1:] != parent_argv[1:] or normalized(argv[0]) != normalized(venv_redirector_base(environment)):
                    raise RuntimeError('Only one exact configured base child directly below its dedicated venv is permitted.')
                redirector_child = True
            if not option(argv, '--port', str(port)):
                raise RuntimeError('Unexpected listener port argument.')
            if port == 8796:
                expected = {'--listen': '127.0.0.1', '--models-directory': ROOT/'.runtime/mv-quality/models',
                            '--input-directory': ROOT/'input/mv-quality', '--output-directory': ROOT/'output/mv-quality',
                            '--user-directory': ROOT/'user/gpu', '--temp-directory': ROOT/'temp/gpu',
                            '--base-directory': ROOT/'.runtime/mv-quality/comfy-state'}
                if normalized(str(ROOT/'.runtime/ComfyUI/main.py')) not in [normalized(arg) for arg in argv]:
                    raise RuntimeError('GPU listener has a different Comfy entry script.')
            else:
                expected = {'--host': '127.0.0.1', '--comfyui': COMFY,
                            '--state-dir': ROOT/'.runtime/mv-quality/proxy-state'}
                if not option(argv, '-m', 'comfy_api_proxy.cli') or 'run' not in argv:
                    raise RuntimeError('Proxy listener has a different Python module.')
            if not all(option(argv, name, value) for name, value in expected.items()):
                raise RuntimeError('Listener paths or upstream target do not match the quality project.')
            python_pids.append(current); current = int(process['parent'])
        proof[str(port)] = {'owner_pid': next(iter(owners)), 'chain': chain, 'python_pids': python_pids,
                            'venv_redirector_child': redirector_child}
    return proof


def utc_time(value):
    if not isinstance(value, str): raise RuntimeError('Resource identity requires an explicit UTC timestamp.')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None: raise RuntimeError('Resource timestamps must include their timezone.')
    return result.astimezone(timezone.utc)


def read_resource_lease():
    path = owned(ROOT/'.runtime/mv-quality/resource-lease.json', ROOT/'.runtime/mv-quality')
    if not path.exists(): return None
    if not path.is_file() or path.stat().st_size > 128 * 1024: raise RuntimeError('Invalid resource lease file.')
    return json.loads(path.read_text(encoding='utf-8-sig'))


def validate_resource_lease(lease, now=None):
    now = now or datetime.now(timezone.utc)
    if not isinstance(lease, dict) or lease.get('format') != 'voice-motion-quality-resource-lease' or lease.get('version') != 1:
        raise RuntimeError('An explicit current resource lease is required for non-benchmark GPU contexts.')
    if lease.get('approved_by') != 'root' or lease.get('user_authorized') is not True or lease.get('scope') != 'isolated-mv-quality-benchmark' or not lease.get('evidence'):
        raise RuntimeError('Resource lease has no exact root/user authorization scope or evidence.')
    if not isinstance(lease.get('id'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,64}', lease['id']):
        raise RuntimeError('Resource lease needs an opaque session identifier.')
    issued, expires = utc_time(lease.get('issued_at')), utc_time(lease.get('expires_at'))
    if not 0 < (expires-issued).total_seconds() <= 1800 or (issued-now).total_seconds() > 5 or now >= expires:
        raise RuntimeError('Resource lease is expired, future-dated or longer than 30 minutes.')
    contexts = lease.get('contexts')
    if not isinstance(contexts, list) or any(not isinstance(entry, dict) for entry in contexts):
        raise RuntimeError('Resource lease must contain exact context records.')
    identifiers = [entry.get('pid') for entry in contexts]
    if any(type(pid) is not int or pid <= 0 for pid in identifiers) or len(set(identifiers)) != len(identifiers):
        raise RuntimeError('Resource lease has duplicate or invalid process IDs.')
    profile = lease.get('profile', 'conservative')
    if profile == 'controlled_mmap_probe':
        if lease.get('min_free_ram_gib') != 8 or lease.get('min_followup_ram_gib') != 4:
            raise RuntimeError('Controlled mmap probe requires its explicit 8/4 GiB RAM bounds.')
    elif profile != 'conservative': raise RuntimeError('Unknown resource measurement profile.')
    return {'id': lease['id'], 'profile': profile, 'issued_at': lease['issued_at'], 'expires_at': lease['expires_at'],
            'fingerprint_sha256': hashlib.sha256(canonical(lease).encode('utf-8')).hexdigest(),
            'min_free_ram_gib': 8 if profile == 'controlled_mmap_probe' else 16,
            'min_followup_ram_gib': 4 if profile == 'controlled_mmap_probe' else 2,
            'measurement_status': 'operator-approved measurement profile; not a proven model minimum'}


def classify_gpu_contexts(state, proof=None, lease=None, now=None):
    contexts = state['gpu'].get('process_contexts')
    if not isinstance(contexts, list): raise RuntimeError('Typed GPU XML/CIM contexts are unavailable; refusing inference.')
    own_pids = set(proof['8796']['python_pids']) if proof else set()
    outside = [entry for entry in contexts if entry.get('pid') not in own_pids]
    if lease is None: lease = read_resource_lease()
    lease_info = validate_resource_lease(lease, now) if lease is not None else None
    if outside and lease_info is None:
        raise RuntimeError('Another GPU context is present without an explicit current resource lease.')
    leased = {entry['pid']: entry for entry in lease['contexts']} if lease_info else {}
    approved = []; seen = set()
    for current in contexts:
        pid = current.get('pid'); kind = current.get('xml_type')
        if type(pid) is not int or pid <= 0 or pid in seen or kind not in ('C', 'G', 'C+G'):
            raise RuntimeError('Unknown, duplicate or untyped GPU context; refusing inference.')
        seen.add(pid)
        if pid in own_pids:
            approved.append({'pid': pid, 'role': 'verified_own_comfy', 'xml_type': kind}); continue
        entry = leased.get(pid)
        if not entry or entry.get('manual_approval') is not True or entry.get('xml_type') != kind or not current.get('name') or not current.get('created'):
            raise RuntimeError(f'Unknown GPU context PID {pid}; no exact approved session identity.')
        if utc_time(current['created']) != utc_time(entry.get('created')) or normalized(current['name']) != normalized(str(entry.get('name', ''))):
            raise RuntimeError(f'GPU process identity changed or PID was reused: {pid}.')
        role = entry.get('role')
        if role == 'protected_display':
            if kind != 'C+G' or current['name'].casefold() not in ('dwm.exe', 'tabtip.exe') or current.get('executable_path') is not None or entry.get('executable_path') is not None or current.get('sha256') is not None or entry.get('sha256') is not None:
                raise RuntimeError('Protected-display exception requires exact approved inaccessible dwm/TabTip identity.')
        else:
            executable = current.get('executable_path'); expected = entry.get('executable_path')
            digest = current.get('sha256')
            if not isinstance(executable, str) or not isinstance(expected, str) or not PureWindowsPath(executable).is_absolute() or '*' in expected or '?' in expected or normalized(executable) != normalized(expected) or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest) or digest != entry.get('sha256'):
                raise RuntimeError(f'GPU executable path/hash is not the exact approved identity: {pid}.')
            if role == 'display':
                if kind not in ('G', 'C+G'): raise RuntimeError('A pure compute context cannot be disguised as an approved display app.')
            elif role == 'idle_original_comfy':
                verified = utc_time(entry.get('verified_at')); issued = utc_time(lease['issued_at'])
                url = urlsplit(str(entry.get('url', '')))
                if kind != 'C' or entry.get('queue_empty') is not True or entry.get('models_unloaded') is not True or entry.get('no_new_jobs_during_lease') is not True or not entry.get('evidence') or not 0 <= (issued-verified).total_seconds() <= 120:
                    raise RuntimeError('Original Comfy context lacks recent idle/unload/reserved-window attestation.')
                if url.scheme != 'http' or url.hostname not in ('127.0.0.1', 'localhost', '::1') or url.username or url.password or not url.port or url.port in (8796, 8798) or url.path not in ('', '/') or url.query or url.fragment:
                    raise RuntimeError('Original context lease must identify its separate exact loopback URL.')
                # Evidence only: never contact the user's original server.
            else: raise RuntimeError('Unknown GPU context lease role.')
        approved.append({'pid': pid, 'name': current['name'], 'created': current['created'], 'role': role, 'xml_type': kind})
    return approved, lease_info


def resource_summary(gpu):
    # Exact executable inventories belong only to the short-lived runtime lease,
    # not a copied permanent benchmark inventory. Retain counts and lease hash.
    result = {key: value for key, value in gpu.items() if key != 'process_contexts'}
    result['observed_context_count'] = len(gpu.get('process_contexts', []))
    return result


def resource_guard(state, proof, initial, lease=None, now=None):
    gpu = state['gpu']
    for key in ('free_vram_gib', 'free_ram_gib', 'utilization'):
        if isinstance(gpu.get(key), bool) or not isinstance(gpu.get(key), (int, float)) or not math.isfinite(gpu[key]):
            raise RuntimeError('Incomplete resource telemetry; refusing GPU submission.')
    approved, lease_info = classify_gpu_contexts(state, proof, lease, now)
    minimum_ram = (lease_info['min_free_ram_gib'] if initial else lease_info['min_followup_ram_gib']) if lease_info else (16 if initial else 2)
    # Immediately after our completed job, the driver's utilization sample can
    # still include that job. Queue idleness and no foreign compute PID remain
    # mandatory; resident weights and that trailing sample are not a new job.
    if (initial and gpu['utilization'] > 5) or gpu['free_ram_gib'] < minimum_ram or (initial and gpu['free_vram_gib'] < 12):
        raise RuntimeError('The approved GPU/RAM resource window is unavailable.')
    return dict(resource_summary(gpu), check='initial_12GiB_VRAM_profile_RAM' if initial else 'same_own_server_cached_weights_allowed',
                approved_contexts=approved, resource_lease=lease_info,
                ram_floor_gib=minimum_ram, ram_floor_basis='operator guard/profile; not official model minimum')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError('Local benchmark requests must never follow a redirect.')


def local_json(url):
    if not (url == PROXY+'/api/v2/health' or url == COMFY+'/queue' or
            re.fullmatch(re.escape(COMFY)+r'/history/[0-9a-f-]{36}', url)):
        raise ValueError('Unapproved benchmark read endpoint.')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(url, timeout=10) as response:
        data = response.read(8 * 1024 * 1024 + 1)
    if len(data) > 8 * 1024 * 1024:
        raise RuntimeError('Local response exceeds benchmark limit.')
    return json.loads(data)


def preflight(initial=True, expected_proof=None):
    state = snapshot()
    proof = process_proof(state)  # Before contacting either server.
    if expected_proof is not None and proof != expected_proof:
        raise RuntimeError('Benchmark server identity changed during this run.')
    resources = resource_guard(state, proof, initial)
    health = local_json(PROXY+'/api/v2/health')
    if health.get('status') != 'healthy' or health.get('upstream', '').rstrip('/') != COMFY:
        raise RuntimeError('Official local proxy is unhealthy or targets another Comfy server.')
    queue = local_json(COMFY+'/queue')
    if not isinstance(queue.get('queue_running'), list) or not isinstance(queue.get('queue_pending'), list):
        raise RuntimeError('Cannot establish dedicated Comfy queue idleness.')
    if queue['queue_running'] or queue['queue_pending']:
        raise RuntimeError('Dedicated Comfy queue is busy; nothing was submitted or cancelled.')
    return {'proof': proof, 'resources': resources, 'health': health, 'queue_idle': True}


def load_provenance():
    spec = importlib.util.spec_from_file_location('_mv_quality_pins', ROOT/'tools/setup_mv_quality.py')
    setup = importlib.util.module_from_spec(spec); spec.loader.exec_module(setup)
    manifest = read_json(ROOT/'.runtime/mv-quality/manifest.json')
    if manifest.get('comfy_revision') != setup.COMFY_REVISION or manifest.get('template_revision') != setup.TEMPLATE_REVISION:
        raise RuntimeError('Pinned model manifest is missing or has different source revisions.')
    verified = [setup.verify_one(asset) for asset in setup.ASSETS]
    return {'models_and_sources': verified, 'comfy_revision': setup.COMFY_REVISION,
            'template_revision': setup.TEMPLATE_REVISION, 'integrity': 'all_pinned_files_sha256_verified_before_submission',
            'workflow_source': read_json(ROOT/'workflows/mv-quality-provenance.json')}


class SubmissionRefused(RuntimeError):
    """A terminal boundary refusal, deliberately not an SDK retryable ApiError."""


def guarded_transport(inner=None):
    """Enforce one wire POST, local origin and explicitly pinned job/artifact IDs.

    SDK 0.5's retry= controls models.run only; submit has an unconditional 429
    loop. Raise our own terminal error before it sees a 429, and refuse a second
    POST before touching the wire. No monkeypatch of installed SDK/global retry.
    """
    import httpx

    class LimitedStream(httpx.AsyncByteStream):
        def __init__(self, stream, maximum): self.stream = stream; self.maximum = maximum
        async def __aiter__(self):
            count = 0
            async for block in self.stream:
                count += len(block)
                if count > self.maximum: raise RuntimeError('HTTP response exceeded its streaming byte limit.')
                yield block
        async def aclose(self): await self.stream.aclose()

    class Guard(httpx.AsyncBaseTransport):
        def __init__(self):
            self.inner = inner if inner is not None else httpx.AsyncHTTPTransport(retries=0)
            self.authorized = {}; self.submissions = {}; self.jobs = set(); self.assets = set()

        def authorize_submission(self, key, graph):
            if key in self.authorized: raise SubmissionRefused('Submission key was already authorized; no retry allowed.')
            self.authorized[key] = canonical(graph)

        def pin_job(self, job_id):
            if str(uuid.UUID(job_id)) != job_id: raise RuntimeError('Expected a canonical SDK workflow ID.')
            self.jobs.add(job_id)

        def pin_asset(self, asset_id, job_id):
            if job_id not in self.jobs or not isinstance(asset_id, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,4096}', asset_id):
                raise RuntimeError('Artifact identity is not pinned to this benchmark job.')
            self.assets.add(asset_id)

        async def handle_async_request(self, request):
            url = request.url
            if url.scheme != 'http' or url.host != '127.0.0.1' or url.port != 8798 or url.username or url.password or url.query or url.fragment:
                raise RuntimeError('SDK request escaped the fixed local benchmark proxy origin.')
            if request.headers.get('authorization'):
                raise RuntimeError('Benchmark transport refuses credentials.')
            request.headers['accept-encoding'] = 'identity'
            asset_id = None
            if request.method == 'POST' and url.path == '/api/v2/jobs':
                key = request.headers.get('idempotency-key')
                body = json.loads(await request.aread())
                if key not in self.authorized or canonical(body.get('workflow')) != self.authorized[key] or 'metadata' in body:
                    raise SubmissionRefused('Only this preflight-approved exact SDK submission is allowed.')
                if self.submissions.get(key, 0):
                    raise SubmissionRefused('A second wire POST is forbidden; no queue retry performed.')
                self.submissions[key] = 1  # Before dispatch, including unknown outcomes.
            elif request.method == 'GET' and url.path.startswith('/api/v2/jobs/'):
                if url.path.removeprefix('/api/v2/jobs/') not in self.jobs:
                    raise RuntimeError('SDK attempted to read a different job.')
            elif request.method == 'GET' and url.path.startswith('/api/v2/assets/') and url.path.endswith('/content'):
                asset_id = url.path[len('/api/v2/assets/'):-len('/content')]
                if asset_id not in self.assets:
                    raise RuntimeError('SDK attempted to download an unpinned artifact.')
            else:
                raise RuntimeError('SDK operation is outside the bounded benchmark API.')
            response = await self.inner.handle_async_request(request)
            if request.method == 'POST' and response.status_code == 429:
                await response.aclose()
                raise SubmissionRefused('Proxy returned HTTP 429; SDK queue retry refused after exactly one wire POST.')
            if 300 <= response.status_code < 400:
                await response.aclose(); raise RuntimeError('Benchmark transport refuses all redirects.')
            maximum = MAX_PNG if asset_id is not None else 2 * 1024 * 1024
            length = response.headers.get('content-length')
            if length is not None and (not length.isdigit() or int(length) > maximum):
                await response.aclose(); raise RuntimeError('HTTP Content-Length exceeds the bounded response limit.')
            if response.headers.get('content-encoding', 'identity').lower() != 'identity':
                await response.aclose(); raise RuntimeError('Encoded responses are forbidden in the bounded local transport.')
            response.stream = LimitedStream(response.stream, maximum)
            return response

        async def aclose(self): await self.inner.aclose()

    return Guard()


async def make_client(*, transport=None):
    if importlib.metadata.version('comfy-sdk') != SDK_VERSION:
        raise RuntimeError('Run in the prepared project .mv-venv with comfy-sdk 0.5.0.')
    from comfy_sdk import AsyncComfy
    names = ('COMFY_BASE_URL', 'COMFY_ROUTER_BASE_URL', 'COMFY_API_KEY', 'NO_PROXY')
    saved = {name: os.environ.get(name) for name in names}
    try:
        os.environ.update(COMFY_BASE_URL=PROXY, COMFY_ROUTER_BASE_URL=PROXY, NO_PROXY='*')
        os.environ.pop('COMFY_API_KEY', None)
        client = AsyncComfy(timeout=30, client_info='voice-motion-quality/0.4-local')
    finally:
        for name, value in saved.items():
            if value is None: os.environ.pop(name, None)
            else: os.environ[name] = value
    # AsyncComfy0.5 has no public HTTP-client argument; its public low-level
    # transport stores this client. Replace the unused default only after closing
    # it, then keep official submit/poll/AsyncOutput.to_stream behavior intact.
    import httpx
    await client._low._client.aclose()
    guard = guarded_transport(transport)
    client._low._client = httpx.AsyncClient(transport=guard, timeout=30, follow_redirects=False, trust_env=False)
    client._quality_transport = guard
    return client


class BoundedWriter:
    """Write-only BinaryIO accepted by official AsyncOutput.to_stream()."""
    def __init__(self, sink, maximum=MAX_PNG):
        self.sink = sink; self.maximum = maximum; self.written = 0

    def write(self, block):
        if self.written + len(block) > self.maximum:
            raise RuntimeError('PNG exceeded the 32 MiB streaming write limit.')
        count = self.sink.write(block)
        if not isinstance(count, int) or not 0 < count <= len(block):
            raise RuntimeError('PNG destination made no valid write progress.')
        self.written += count
        return count


async def download_png(artifact, destination, job_id, client, expected_size):
    declared = artifact.size_bytes
    if type(declared) is not int or not 0 <= declared <= MAX_PNG:
        raise RuntimeError('SDK PNG size metadata exceeds 32 MiB or is invalid.')
    # Proxy0.1.6 reports size_bytes=0 for native history outputs: unknown, not an
    # empty image. Both the HTTP stream and sink retain hard bounds in that case.
    if artifact.node_id != '13' or artifact.job_id != job_id or artifact.type != 'image' or artifact.content_type != 'image/png':
        raise RuntimeError('SDK PNG metadata does not belong to this job and SaveImage node.')
    transport = getattr(client, '_quality_transport', None)
    if transport is not None: transport.pin_asset(artifact.id, job_id)
    partial = destination.with_suffix('.png.partial')
    if destination.exists(): raise RuntimeError('Refusing to overwrite a previous original PNG.')
    with partial.open('xb') as target:
        sink = BoundedWriter(target, MAX_PNG)
        written = await artifact.to_stream(sink)
    if written != sink.written or (declared and written != declared):
        raise RuntimeError('SDK PNG length differs from the actual bounded stream.')
    metadata = inspect_png(partial, expected_size)
    os.link(partial, destination)  # Atomic no-clobber promotion; same directory.
    partial.unlink()
    return dict(metadata, size_metadata='unknown_proxy_size' if declared == 0 else 'matched_actual_bytes',
                download='official-sdk-to_stream-with-32MiB-bounded-writer')


def inspect_png(path, expected_size=None):
    if not path.is_file() or not 8 < path.stat().st_size <= MAX_PNG:
        raise ValueError('PNG is missing or exceeds 32 MiB.')
    with path.open('rb') as source:
        if source.read(8) != b'\x89PNG\r\n\x1a\n':
            raise ValueError('Expected the original PNG artifact.')
    from PIL import Image
    with Image.open(path) as image:
        size = image.size
        if image.format != 'PNG' or size[0]*size[1] > 4*1024*1024 or (expected_size and size != expected_size):
            raise ValueError('PNG dimensions do not match the benchmark.')
        image.verify()
    return {'sha256': sha256(path), 'bytes': path.stat().st_size, 'width': size[0], 'height': size[1]}


async def run_benchmark(stage='hero', count=None, reference=None, resource_approved=False, timeout=600, reference_selection='agent_reviewed'):
    if not resource_approved:
        raise RuntimeError('--resource-approved is required before any probe or GPU submission.')
    candidates = build_plan(stage, count)
    reference_path = None
    if stage == 'edit':
        if not reference:
            raise ValueError('Explicit edit stage requires --reference to the reviewed canonical PNG.')
        reference_path = Path(reference).absolute()
        if not any(reference_path.is_relative_to(ROOT/parent/'mv-quality') for parent in ('input', 'output')):
            raise ValueError('Canonical reference must be this project quality input/output PNG.')
        owned(reference_path, ROOT); inspect_png(reference_path)
    elif reference:
        raise ValueError('--reference is only used in the separate edit stage.')
    if reference_selection not in ('agent_reviewed', 'user_selected'):
        raise ValueError('Reference selection must identify agent review or explicit user selection.')
    run_id = uuid.uuid4().hex
    directory = owned(ROOT/'output/mv-quality/runs'/run_id, ROOT/'output/mv-quality')
    directory.mkdir(parents=True, exist_ok=False)
    report = {'format': 'voice-motion-quality-run', 'version': 1, 'id': run_id, 'stage': stage,
              'success': False, 'status': 'preflight', 'art_review': 'not_performed', 'automatic_retries': 0,
              'retry_policy': {'candidate_regenerations': 0, 'submission_wire_post_limit_per_candidate': 1,
                               'sdk_429_queue_retry': 'terminally_refused_at_transport_boundary'},
              'created': datetime.now(timezone.utc).isoformat(), 'provider': 'comfy-sdk-local-api-v2',
              'proxy': PROXY, 'comfy': COMFY, 'candidates': [],
              'resource_measurement': 'before/after snapshots; peak VRAM/RAM not measured',
              'quality_claim': 'Successful generation does not establish illustration, animation or automatic MV quality.'}
    report_path = directory/'report.json'
    save_json(report_path, report)
    started = time.perf_counter(); active_started = None; client = None
    try:
        first = preflight()
        report['initial_preflight'] = first
        report['provenance'] = load_provenance()
        save_json(directory/'model-provenance.json', report['provenance'])
        if reference_path:
            target = owned(ROOT/'input/mv-quality'/run_id/'reference.png', ROOT/'input/mv-quality')
            target.parent.mkdir(parents=True, exist_ok=False)
            with target.open('xb') as output: output.write(reference_path.read_bytes())
            report['reference'] = dict(inspect_png(target), source=str(reference_path.relative_to(ROOT)),
                                       staged=str(target.relative_to(ROOT)), selection=reference_selection,
                                       human_art_approval=reference_selection == 'user_selected')
        async with await make_client() as client:
            for index, candidate in enumerate(candidates):
                checked = preflight(initial=index == 0, expected_proof=first['proof'])
                item_dir = directory/candidate['id']; item_dir.mkdir()
                graph = candidate['workflow']
                graph['13']['inputs']['filename_prefix'] = f'benchmark/{run_id}/{candidate["id"]}'
                if reference_path: graph['14']['inputs']['image'] = f'{run_id}/reference.png'
                validate_graph(graph, stage)
                save_json(item_dir/'workflow.api.json', graph)
                (item_dir/'prompt.txt').write_text(candidate['prompt']+'\n', encoding='utf-8')
                item = {'id': candidate['id'], 'seed': candidate['seed'], 'success': False,
                        'status': 'submitting', 'preflight': checked, 'workflow_sha256': sha256(item_dir/'workflow.api.json'),
                        'idempotency_key': f'mv-quality-{run_id}-{candidate["id"]}',
                        'job_may_continue': True}
                report['candidates'].append(item); report['status'] = 'running'; save_json(report_path, report)
                tick = time.perf_counter(); active_started = tick
                workflow = client.workflows.from_json(graph)
                boundary = getattr(client, '_quality_transport', None)
                if boundary is not None: boundary.authorize_submission(item['idempotency_key'], graph)
                job = await client.submit(workflow, idempotency_key=item['idempotency_key'])
                item['workflow_id'] = str(uuid.UUID(str(job.id)))
                if boundary is not None: boundary.pin_job(item['workflow_id'])
                item['job_may_continue'] = True; save_json(report_path, report)
                try:
                    await job.wait(timeout=timeout)
                except BaseException:
                    try:
                        save_json(item_dir/'history.json', local_json(COMFY+'/history/'+item['workflow_id']))
                    except Exception as history_error:
                        item['history_error'] = str(history_error)
                    raise
                item['sdk_status'] = job.status
                item['job_may_continue'] = job.status not in ('succeeded', 'failed', 'cancelled')
                history = local_json(COMFY+'/history/'+item['workflow_id'])
                save_json(item_dir/'history.json', history)
                native = history.get(item['workflow_id'])
                if not isinstance(native, dict) or not isinstance(native.get('status'), dict):
                    raise RuntimeError('Actual native Comfy history is absent for the SDK job.')
                native_prompt = native.get('prompt')
                if not isinstance(native_prompt, list) or len(native_prompt) < 3 or native_prompt[1] != item['workflow_id'] or native_prompt[2] != graph:
                    raise RuntimeError('Native Comfy history does not match the submitted API graph.')
                if job.status != 'succeeded' or not native['status'].get('completed') or native['status'].get('status_str') != 'success':
                    raise RuntimeError(f'Candidate failed: SDK={job.status}, native={native["status"]}.')
                artifacts = job.get_outputs('13')
                if len(artifacts) != 1 or not str(artifacts[0].name).lower().endswith('.png'):
                    raise RuntimeError('Expected exactly one original PNG from SaveImage node 13.')
                image_metadata = await download_png(artifacts[0], item_dir/'original.png', item['workflow_id'], client,
                                                    (1024, 1024 if stage == 'hero' else 576))
                item['artifact'] = dict(image_metadata,
                                        path=str((item_dir/'original.png').relative_to(ROOT)), sdk_name=artifacts[0].name)
                after = snapshot()
                if process_proof(after) != first['proof']:
                    raise RuntimeError('Server identity changed before artifact verification completed.')
                item['resources_after'] = resource_summary(after['gpu'])
                item.update(success=True, status='generated_awaiting_art_review', elapsed_seconds=time.perf_counter()-tick)
                save_json(report_path, report)
        report.update(success=True, status='generated_awaiting_art_review', art_review='awaiting_art_review')
        return report_path
    except BaseException as error:
        report.update(success=False, status='interrupted' if isinstance(error, (KeyboardInterrupt, asyncio.CancelledError)) else 'failed',
                      error=f'{type(error).__name__}: {error}')
        for item in report['candidates']:
            if not item['success']:
                item['status'] = report['status']
                if 'workflow_id' not in item:
                    item['submission_outcome'] = 'not_confirmed; preserve idempotency key before any recovery'
        raise
    finally:
        report['elapsed_seconds'] = time.perf_counter()-started
        boundary = getattr(client, '_quality_transport', None)
        if boundary is not None:
            report['actual_submission_wire_posts'] = sum(boundary.submissions.values())
            for item in report['candidates']:
                item['submission_wire_posts'] = boundary.submissions.get(item['idempotency_key'], 0)
        if active_started is not None and report['candidates']:
            last = report['candidates'][-1]
            if 'elapsed_seconds' not in last:
                last['elapsed_seconds'] = time.perf_counter()-active_started
        # An interrupt never calls a global interrupt/kill. In-flight workflow ID
        # remains recorded; the next invocation refuses a non-idle queue.
        save_json(report_path, report)
        print(f'Benchmark report: {report_path}', flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', help='Explicitly submit the bounded benchmark; otherwise show a read-only plan.')
    parser.add_argument('--dry-run', action='store_true', help='No network, SDK import, resource probe or filesystem writes.')
    parser.add_argument('--resource-approved', action='store_true', help='An explicit user-approved resource window, never inferred by this script.')
    parser.add_argument('--stage', choices=('hero', 'edit'), default='hero')
    parser.add_argument('--count', type=int, help='At most 2 hero candidates or 3 reference edits; no retries.')
    parser.add_argument('--reference', type=Path, help='Reviewed canonical PNG under project input/mv-quality or output/mv-quality; edit stage only.')
    parser.add_argument('--reference-selection', choices=('agent_reviewed', 'user_selected'), default='agent_reviewed',
                        help='Record who selected the provisional reference; agent review never implies user art approval.')
    parser.add_argument('--timeout', type=int, default=600, help='Per-candidate wait bound (30..1800 seconds); timeout does not kill jobs.')
    parser.add_argument('--resource-status', action='store_true', help='Read-only XML/CIM/lease check for project setup/start; no SDK or inference.')
    parser.add_argument('--resource-check', action='store_true', help='Same read-only check, exit nonzero if resource policy refuses.')
    args = parser.parse_args(argv)
    if not 30 <= args.timeout <= 1800: parser.error('--timeout must be 30..1800 seconds')
    if args.resource_status or args.resource_check:
        if args.run or args.dry_run: parser.error('Resource inspection cannot be combined with --run or --dry-run.')
        if args.resource_check and not args.resource_approved: parser.error('--resource-check requires --resource-approved.')
        try:
            state = snapshot()
            try:
                value = resource_guard(state, None, True)
                value['allowed'] = True
            except (RuntimeError, ValueError, KeyError, TypeError) as error:
                value = dict(resource_summary(state['gpu']), allowed=False, reason=str(error))
            print(json.dumps(value, ensure_ascii=False, allow_nan=False))
            return 0 if value['allowed'] or args.resource_status else 1
        except Exception as error:
            print(json.dumps({'allowed': False, 'reason': str(error)}, ensure_ascii=False)); return 1
    if args.dry_run or not args.run:
        plan = build_plan(args.stage, args.count)
        print(json.dumps({'dry_run': True, 'stage': args.stage, 'network_used': False, 'gpu_used': False,
                          'candidates': plan, 'edit_requires_reviewed_reference': args.stage == 'edit',
                          'output': 'output/mv-quality/runs/<fresh-id>', 'automatic_retries': 0}, ensure_ascii=False, indent=2))
        return 0
    try:
        asyncio.run(run_benchmark(args.stage, args.count, args.reference, args.resource_approved, args.timeout, args.reference_selection))
        return 0
    except KeyboardInterrupt:
        print('Interrupted. No server or job was globally interrupted; inspect the recorded exact workflow ID.'); return 130
    except Exception as error:
        print(f'Benchmark failed: {error}'); return 1


if __name__ == '__main__':
    raise SystemExit(main())
