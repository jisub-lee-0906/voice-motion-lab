"""Download pinned, Apache-2.0 Qwen CPU ASR/alignment assets into this project only."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / '.runtime/mv-models'
MODELS = {
    'asr': ('Qwen/Qwen3-ASR-0.6B-hf', '7f1569a48a89f3e3f4dc3a5c9d28bddd903bc76c'),
    'aligner': ('Qwen/Qwen3-ForcedAligner-0.6B-hf', 'c07281df297b9905d24a508279258cccf987a064'),
}
ALLOW = {'README.md', 'chat_template.jinja', 'config.json', 'generation_config.json',
         'model.safetensors', 'processor_config.json', 'tokenizer.json', 'tokenizer_config.json'}
# The pinned HF repos declare Apache-2.0 but contain no LICENSE sibling. Preserve
# the actual unmodified license from Qwen's own upstream project alongside each
# model, with its independent source/revision/hash recorded explicitly.
LICENSE_SOURCE = {
    'repo': 'QwenLM/Qwen3-ASR',
    'revision': '7c6daf77a2421100f5fb066495372c00129d39ff',
    'url': 'https://raw.githubusercontent.com/QwenLM/Qwen3-ASR/7c6daf77a2421100f5fb066495372c00129d39ff/LICENSE',
    'sha256': 'a44a6081c73ad75f0255bb2bb5cab74ef1829565a895a24e53a4f11290ab7655',
    'bytes': 11343,
}


def request(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'VoiceMotionLabMV/0.3'}), timeout=60)


def preserve_license(target, manifest):
    path = target / 'LICENSE'
    if path.is_file():
        contents = path.read_bytes()
    else:
        with request(LICENSE_SOURCE['url']) as response:
            contents = response.read(64 * 1024 + 1)
    if len(contents) != LICENSE_SOURCE['bytes'] or hashlib.sha256(contents).hexdigest() != LICENSE_SOURCE['sha256']:
        raise RuntimeError('Official Qwen Apache-2.0 license integrity mismatch')
    if not path.exists():
        path.write_bytes(contents)
    manifest['license_file'] = {**LICENSE_SOURCE, 'path': 'LICENSE',
        'notice': 'Copyright 2026 Alibaba Cloud',
        'reason': 'The pinned HF model card declares Apache-2.0; no LICENSE file exists in that model revision. This is the original upstream project license.'}


def install(name, licenses_only=False):
    repo, revision = MODELS[name]
    with request(f'https://huggingface.co/api/models/{repo}/revision/{revision}?blobs=true') as response:
        info = json.load(response)
    if info['sha'] != revision or info.get('cardData', {}).get('license') != 'apache-2.0':
        raise RuntimeError('Unexpected model revision/license')
    target = DEST / name
    if target.resolve() != target:
        raise RuntimeError('Model installation must stay in the project-owned runtime directory; symlink targets are refused')
    target.mkdir(parents=True, exist_ok=True)
    manifest_path = target / 'manifest.json'
    if licenses_only:
        if not manifest_path.is_file():
            raise RuntimeError('Install the model first; --licenses-only never downloads weights')
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if manifest.get('repo') != repo or manifest.get('revision') != revision:
            raise RuntimeError('Installed model provenance does not match the pinned model')
        preserve_license(target, manifest)
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        print(json.dumps({'model': name, 'license_saved': True, 'weights_untouched': True}), flush=True)
        return
    manifest = {'repo': repo, 'revision': revision, 'license': 'Apache-2.0', 'files': {}}
    preserve_license(target, manifest)
    for item in info['siblings']:
        filename = item['rfilename']
        if filename not in ALLOW:
            continue
        path = target / filename
        if not path.exists():
            print(f'Downloading {name}/{filename}', flush=True)
            temporary = path.with_name(path.name + '.partial')
            with request(f'https://huggingface.co/{repo}/resolve/{revision}/{filename}') as response, temporary.open('wb') as out:
                total = 0
                while chunk := response.read(2 * 1024 * 1024):
                    total += len(chunk)
                    if total > 2 * 1024**3:
                        raise RuntimeError('Unexpected model asset over 2GiB')
                    out.write(chunk)
            temporary.replace(path)
        digest = hashlib.sha256()
        git_digest = hashlib.sha1(f'blob {path.stat().st_size}\0'.encode())
        with path.open('rb') as source:
            while chunk := source.read(2 * 1024 * 1024):
                digest.update(chunk)
                git_digest.update(chunk)
        if item.get('lfs'):
            expected = item['lfs'].get('sha256') or item['lfs'].get('oid')
            if digest.hexdigest() != expected or path.stat().st_size != item['lfs']['size']:
                raise RuntimeError(f'Official LFS integrity mismatch: {filename}')
        elif item.get('blobId') != git_digest.hexdigest():
            raise RuntimeError(f'Official Git blob mismatch: {filename}')
        manifest['files'][filename] = {'bytes': path.stat().st_size, 'sha256': digest.hexdigest()}
    if not (target / 'model.safetensors').is_file():
        raise RuntimeError('Model weights missing')
    (target / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps({'model': name, 'files': len(manifest['files']),
                      'bytes': sum(v['bytes'] for v in manifest['files'].values())}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--asr-only', action='store_true')
    parser.add_argument('--licenses-only', action='store_true', help='Add original LICENSE and provenance to installed models without reading or downloading weights')
    args = parser.parse_args()
    for model in ('asr',) if args.asr_only else ('asr', 'aligner'):
        install(model, licenses_only=args.licenses_only)
