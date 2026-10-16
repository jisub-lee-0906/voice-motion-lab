"""Prepare pinned FLUX.2 klein assets; default/--status is strictly read-only.

No inference or server startup. --download --resource-approved is an explicit
future opt-in. --verify hashes installed assets without downloading anything.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / '.runtime' / 'mv-quality'
TEMPLATE_REVISION = '04a508b31bec1818e48122ca0761c1de4140a234'
COMFY_REVISION = 'bdc63e72f5ab29fde448c161db3057827148c0ef'
ASSETS = [
    {'path':'models/diffusion_models/flux-2-klein-4b-fp8.safetensors', 'bytes':4070624520,
     'sha256':'97ed34fe0567e436200f2faee3939b88f2b5d99f8af2a4dc16532c4245c0ccb6',
     'url':'https://huggingface.co/black-forest-labs/FLUX.2-klein-4b-fp8/resolve/5b4408e59397a4a37ccb46afe426d8ed86379441/flux-2-klein-4b-fp8.safetensors', 'license':'Apache-2.0'},
    {'path':'models/text_encoders/qwen_3_4b.safetensors','bytes':8044982048,
     'sha256':'6c671498573ac2f7a5501502ccce8d2b08ea6ca2f661c458e708f36b36edfc5a',
     'url':'https://huggingface.co/Comfy-Org/flux2-klein-4B/resolve/5f526678002e43af5551dadb73ce2e8c91b43afe/split_files/text_encoders/qwen_3_4b.safetensors','license':'Apache-2.0'},
    {'path':'models/vae/flux2-vae.safetensors','bytes':336211292,
     'sha256':'868fe7b343cc8f3a19dbcfcafbc3d5f888802be3f89bd81b65b3621a066ce8f3',
     'url':'https://huggingface.co/Comfy-Org/flux2-klein-4B/resolve/5f526678002e43af5551dadb73ce2e8c91b43afe/split_files/vae/flux2-vae.safetensors','license':'Apache-2.0'},
    {'path':'licenses/BFL_APACHE2.md','bytes':9584,
     'sha256':'ca02bc51900ab07789d1b70283329e7137f5af98f5161c23a1c81fc38a4af1fe',
     'url':'https://huggingface.co/black-forest-labs/FLUX.2-klein-4b-fp8/resolve/5b4408e59397a4a37ccb46afe426d8ed86379441/LICENSE.md','license':'Apache-2.0'},
    {'path':'licenses/QWEN3_APACHE2.txt','bytes':11343,
     'sha256':'832dd9e00a68dd83b3c3fb9f5588dad7dcf337a0db50f7d9483f310cd292e92e',
     'url':'https://huggingface.co/Qwen/Qwen3-4B/resolve/1cfa9a7208912126459214e8b04321603b3df60c/LICENSE','license':'Apache-2.0'},
    {'path':'licenses/ComfyWorkflowTemplates.LICENSE','bytes':1074,
     'sha256':'5dccd61c80c847cd6b22fa98124010163ab8646e573557a16a5cae31122f2056',
     'url':f'https://raw.githubusercontent.com/Comfy-Org/workflow_templates/{TEMPLATE_REVISION}/LICENSE','license':'MIT'},
    {'path':'upstream/image_flux2_klein_text_to_image.json',
     'sha256':'cd4a29b0be6f71a7391f2f023e69dfc3b47d7da9f2aa215b4c8b8e17c2eea107',
     'url':f'https://raw.githubusercontent.com/Comfy-Org/workflow_templates/{TEMPLATE_REVISION}/templates/image_flux2_klein_text_to_image.json','license':'MIT'},
    {'path':'upstream/image_flux2_klein_image_edit_4b_distilled.json',
     'sha256':'7f1ead62d1456d485e95fb432fb492d2783f5ec9cfeefefe4ef71bfb448ba1a3',
     'url':f'https://raw.githubusercontent.com/Comfy-Org/workflow_templates/{TEMPLATE_REVISION}/templates/image_flux2_klein_image_edit_4b_distilled.json','license':'MIT'},
]


def destination(relative):
    path = RUNTIME / relative
    if path.resolve() != path or not path.is_relative_to(RUNTIME):
        raise RuntimeError('Only project-owned, non-symlink quality runtime paths are allowed')
    return path


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as source:
        while block := source.read(1024 * 1024):
            value.update(block)
    return value.hexdigest()


def verify_one(asset):
    path = destination(asset['path'])
    if not path.is_file() or ('bytes' in asset and path.stat().st_size != asset['bytes']) or digest(path) != asset['sha256']:
        raise RuntimeError(f"Missing or failed pinned asset integrity: {asset['path']}")
    return {'path':asset['path'], 'sha256':asset['sha256'], 'bytes':path.stat().st_size,
            'mtime_ns':path.stat().st_mtime_ns, 'url':asset['url'], 'license':asset['license']}


def status():
    files=[]
    for asset in ASSETS:
        path=destination(asset['path'])
        files.append({'path':asset['path'], 'present':path.is_file(),
                      'bytes':path.stat().st_size if path.is_file() else None,
                      'expected_bytes':asset.get('bytes'), 'expected_sha256':asset['sha256']})
    return {'phase':'prepared; GPU execution untested', 'network_used':False,
            'weights_bytes':sum(asset['bytes'] for asset in ASSETS if asset['path'].endswith('.safetensors')),
            'all_assets_present':all(file['present'] for file in files), 'files':files,
            'before_inference':'Obtain explicit resource approval; run --verify and the GPU resource guard.',
            'comfy_port':8796, 'optional_proxy_port':8798, 'benchmark_hero_seeds':[7,19]}


def download():
    if shutil.disk_usage(ROOT).free < 40 * 1024**3:
        raise RuntimeError('At least 40 GiB project-drive free space is required for this setup')
    for asset in ASSETS:
        path=destination(asset['path'])
        if path.exists():
            verify_one(asset)  # Never overwrite an existing, changed asset.
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary=path.with_name(path.name+'.partial-'+uuid.uuid4().hex)
        maximum=asset.get('bytes', 2*1024*1024)
        value=hashlib.sha256(); count=0
        print(f"Downloading pinned {asset['path']}", flush=True)
        request=urllib.request.Request(asset['url'],headers={'User-Agent':'VoiceMotionLabQuality/0.4'})
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open('xb') as target:
            while block := response.read(2*1024*1024):
                count+=len(block)
                if count>maximum: raise RuntimeError('Pinned asset exceeded its expected size')
                value.update(block); target.write(block)
        if ('bytes' in asset and count!=asset['bytes']) or value.hexdigest()!=asset['sha256']:
            raise RuntimeError(f"Pinned asset verification failed; owned partial retained: {temporary.name}")
        # Atomic no-clobber placement. Never replace an existing model/reference.
        import os
        os.link(temporary,path)
        temporary.unlink()
    verified=[verify_one(asset) for asset in ASSETS]
    destination('manifest.json').write_text(json.dumps({'format':'voice-motion-quality-assets','version':1,
        'comfy_revision':COMFY_REVISION,'template_revision':TEMPLATE_REVISION,
        'assets':verified,'gpu_tested':False},indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--status',action='store_true')
    mode.add_argument('--download',action='store_true')
    mode.add_argument('--verify',action='store_true')
    parser.add_argument('--resource-approved',action='store_true',help='Explicit approval for this setup attempt; never persisted')
    args=parser.parse_args()
    if args.download:
        if not args.resource_approved: parser.error('--download requires explicit --resource-approved')
        download()
    elif args.verify:
        print(json.dumps({'verified':[verify_one(asset) for asset in ASSETS],'gpu_tested':False},indent=2))
    else:
        print(json.dumps(status(),indent=2))
