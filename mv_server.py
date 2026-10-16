"""Independent loopback MV creator: local audio -> CPU ASR -> official Comfy SDK -> MP4."""
from __future__ import annotations
import argparse
import asyncio
import ctypes
import json
import mimetypes
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit
from urllib.request import ProxyHandler, Request, build_opener
import uuid
import wave

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'src'))
from voice_motion.mv_lyrics import model_status

MAX_BODY = 24 * 1024 * 1024
JOB_ID = re.compile(r'^[a-f0-9]{32}$')
ARTIFACTS = {'video.mp4': 'video/mp4', 'preview.webm': 'video/webm', 'project.json': 'application/json; charset=utf-8',
             'storyboard.jpg': 'image/jpeg'}


class RequestError(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status


def reject_constant(_):
    raise ValueError('Non-finite number')


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def parse_job(raw):
    import numpy as np
    try:
        data = json.loads(raw.decode('utf-8'), parse_constant=reject_constant, object_pairs_hook=unique_object)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise RequestError('올바른 오디오 JSON 요청이 아닙니다.') from error
    if not isinstance(data, dict) or set(data) - {'audio', 'title', 'theme', 'seed', 'lyrics'}:
        raise RequestError('지원하지 않는 요청 필드입니다.')
    audio = data.get('audio')
    if not isinstance(audio, dict) or set(audio) != {'samples', 'sample_rate'} or audio['sample_rate'] != 16000 or isinstance(audio['sample_rate'], bool):
        raise RequestError('오디오는 16,000Hz 모노 samples 배열이어야 합니다.')
    values = audio['samples']
    if not isinstance(values, list) or not 16000 <= len(values) <= 60 * 16000:
        raise RequestError('노래는 1~60초 구간을 선택해주세요.')
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in values):
        raise RequestError('오디오 샘플은 유한한 숫자여야 합니다.')
    samples = np.asarray(values, dtype=np.float32)
    if not np.isfinite(samples).all() or np.max(np.abs(samples)) > 1.001:
        raise RequestError('오디오 샘플 범위가 올바르지 않습니다.')
    if float(np.sqrt(np.mean(samples**2))) < .0008:
        raise RequestError('소리가 거의 없는 파일입니다. 보컬이 들리는 구간을 선택해주세요.')
    theme = data.get('theme', 'auto')
    seed = data.get('seed', 7)
    if theme not in {'auto', 'bloom', 'fracture', 'orbit'}:
        raise RequestError('지원하지 않는 테마입니다.')
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2147483647:
        raise RequestError('시드는 0~2147483647 사이의 정수여야 합니다.')
    title = data.get('title', 'Untitled')
    lyrics = data.get('lyrics', '')
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120 or any(ord(x) < 32 for x in title):
        raise RequestError('제목은 1~120자로 입력해주세요.')
    if not isinstance(lyrics, str) or len(lyrics) > 6000 or '\x00' in lyrics:
        raise RequestError('선택 가사는 최대 6,000자입니다.')
    return {'samples': samples, 'title': title.strip(), 'theme': theme, 'seed': seed, 'lyrics': lyrics.strip()}


def available_ram_gb():
    class Memory(ctypes.Structure):
        _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [(name, ctypes.c_ulonglong) for name in
            ('total', 'available', 'page_total', 'page_available', 'virtual_total', 'virtual_available', 'extended')]
    if hasattr(ctypes, 'windll'):
        value = Memory(); value.length = ctypes.sizeof(value)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value)):
            return value.available / 2**30
    return None


def comfy_ready():
    # Read only this explicitly isolated local server; no discovery or default cloud.
    try:
        opener = build_opener(ProxyHandler({}))
        with opener.open('http://127.0.0.1:8794/object_info/VoiceMotionMVCreate', timeout=2) as response:
            node = json.load(response)
        with opener.open('http://127.0.0.1:8795/api/v2/health', timeout=2) as response:
            good_proxy = response.status == 200
        return {'available': 'VoiceMotionMVCreate' in node and good_proxy,
                'provider': 'comfy-sdk-local-api-v2'}
    except Exception:
        return {'available': False, 'provider': 'comfy-sdk-local-api-v2'}


class JobManager:
    def __init__(self):
        self.lock = threading.RLock()
        self.capacity = threading.Semaphore(1)
        self.jobs = {}
        self.created_count = 0

    def health(self):
        return {'status': 'ok', 'version': '0.3.0', 'asr': model_status(), 'comfy': comfy_ready(),
                'limits': {'max_seconds': 60, 'max_bytes': MAX_BODY},
                'processing': 'local CPU; no external audio upload'}

    def get(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
                    raise RequestError('제작 작업을 찾을 수 없습니다.', 404)
                saved = ROOT / 'output/mv' / job_id / 'job.json'
                try:
                    state = json.loads(saved.read_text(encoding='utf-8'))
                    if state.get('id') != job_id or state.get('status') not in {
                        'queued', 'transcribing', 'directing', 'rendering', 'succeeded', 'failed'
                    }:
                        raise ValueError('Invalid saved job')
                    if state['status'] == 'succeeded':
                        if not isinstance(state.get('result'), dict):
                            raise ValueError('Missing saved result')
                        if not all((saved.parent / name).is_file() for name in ('video.mp4', 'project.json', 'storyboard.jpg')):
                            raise ValueError('Missing completed artifact')
                        if (saved.parent / 'preview.webm').is_file():
                            state['result']['preview_url'] = f'/api/mv/jobs/{job_id}/artifacts/preview.webm'
                    elif state['status'] != 'failed':
                        state.update(status='failed', message='앱이 종료되어 이전 제작이 중단됐습니다. 음악을 선택해 다시 만들어주세요.',
                                     error='이전 실행에서 중단된 작업입니다.')
                    self.jobs[job_id] = state
                except (OSError, ValueError, AttributeError, TypeError):
                    raise RequestError('제작 작업을 찾을 수 없습니다.', 404) from None
            return json.loads(json.dumps(self.jobs[job_id]))

    def update(self, job_id, **changes):
        with self.lock:
            self.jobs[job_id].update(changes)
            self.jobs[job_id]['updated_at'] = time.time()
            # Only product metadata, never model traces/absolute host paths.
            out = ROOT / 'output/mv' / job_id / 'job.json'
            out.write_text(json.dumps(self.jobs[job_id], ensure_ascii=False, indent=2), encoding='utf-8')

    def create(self, payload):
        if not model_status()['available']:
            raise RequestError('전사 모델을 준비한 뒤 다시 시도해주세요. tools/setup_mv_models.py', 503)
        if not comfy_ready()['available']:
            raise RequestError('독립 Comfy와 공식 API 프록시가 준비되지 않았습니다. 시작 안내를 확인해주세요.', 503)
        free_ram = available_ram_gb()
        if free_ram is not None and free_ram < 5.0:
            raise RequestError('다른 작업과 메모리가 겹칩니다. 여유 RAM 5GB 이상일 때 다시 시도해주세요.', 503)
        if not self.capacity.acquire(blocking=False):
            raise RequestError('다른 MV를 제작 중입니다. 완료 후 다시 눌러주세요.', 429)
        try:
            if self.created_count >= 20:
                raise RequestError('이 실행 세션의 최대 작업 수에 도달했습니다. 결과를 보존한 뒤 앱을 다시 시작해주세요.', 429)
            job_id = uuid.uuid4().hex
            source_dir = ROOT / 'input/mv' / job_id
            output_dir = ROOT / 'output/mv' / job_id
            source_dir.mkdir(parents=True, exist_ok=False)
            output_dir.mkdir(parents=True, exist_ok=False)
            samples = payload.pop('samples')
            with wave.open(str(source_dir / 'source.wav'), 'wb') as out:
                out.setnchannels(1); out.setsampwidth(2); out.setframerate(16000)
                out.writeframes((samples.clip(-1, 1)*32767).astype('<i2').tobytes())
            if payload['lyrics']:
                (source_dir / 'provided-lyrics.txt').write_text(payload['lyrics'], encoding='utf-8')
            self.jobs[job_id] = {'id': job_id, 'status': 'queued', 'progress': 0,
                'message': '로컬 제작 대기 중', 'title': payload['title'], 'created_at': time.time()}
            self.created_count += 1
            self.update(job_id)
            threading.Thread(target=self.run, args=(job_id, payload), daemon=True, name=f'mv-{job_id[:8]}').start()
            return self.get(job_id)
        except Exception:
            self.capacity.release()
            raise

    def run(self, job_id, payload):
        source_dir = ROOT / 'input/mv' / job_id
        output_dir = ROOT / 'output/mv' / job_id
        try:
            self.update(job_id, status='transcribing', progress=.02, message='노래의 가사를 분석하는 중')
            worker_python = ROOT / '.comfy-venv/Scripts/python.exe'
            if not worker_python.is_file():
                raise RuntimeError('프로젝트 CPU 모델 환경을 찾을 수 없습니다. MV 설치 안내를 확인해주세요.')
            command = [str(worker_python), str(ROOT / 'src/voice_motion/mv_lyrics.py'),
                str(source_dir / 'source.wav'), '--out', str(output_dir / 'transcript.json')]
            if payload['lyrics']:
                command.extend(['--lyrics-file', str(source_dir / 'provided-lyrics.txt')])
            env = os.environ.copy()
            env.update({'CUDA_VISIBLE_DEVICES': '', 'HF_HOME': str(ROOT / '.cache/huggingface'),
                        'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'PYTHONNOUSERSITE': '1',
                        'OMP_NUM_THREADS': '2', 'MKL_NUM_THREADS': '2', 'PYTHONIOENCODING': 'utf-8',
                        'TEMP': str(ROOT / 'temp'), 'TMP': str(ROOT / 'temp')})
            with (output_dir / 'asr.log').open('w', encoding='utf-8') as errors:
                child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors, text=True,
                    encoding='utf-8', cwd=ROOT, env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                watchdog = threading.Timer(900, child.kill)
                watchdog.start()
                try:
                    for line in child.stdout:
                        try:
                            value = json.loads(line)
                            if 'progress' in value:
                                self.update(job_id, progress=.02 + .43*float(value['progress']), message=str(value['message'])[:250])
                            elif 'error' in value:
                                errors.write(str(value['error']) + '\n')
                        except (ValueError, KeyError):
                            continue
                    code = child.wait()
                finally:
                    watchdog.cancel()
                    if child.poll() is None:
                        child.kill(); child.wait(timeout=5)
                if code != 0:
                    raise RuntimeError('가사 모델 분석에 실패했습니다. 보컬이 잘 들리는 구간으로 재시도하거나 선택 가사를 입력해주세요.')
            transcript = json.loads((output_dir / 'transcript.json').read_text(encoding='utf-8'))
            self.update(job_id, status='directing', progress=.46, message='공식 Comfy SDK로 장면 제작을 시작하는 중')
            from voice_motion.mv_sdk import run_mv_workflow
            def report(value, message):
                value = max(0.0, min(1.0, float(value)))
                self.update(job_id, status='rendering' if value >= .15 else 'directing',
                            progress=.46 + .53*value, message=str(message)[:250])
            result = asyncio.run(run_mv_workflow(source_dir / 'source.wav', transcript, output_dir,
                theme=payload['theme'], seed=payload['seed'], progress=report))
            self.update(job_id, status='rendering', progress=.99, message='브라우저에서 재생할 미리보기를 준비하는 중')
            from voice_motion.mv_preview import create_web_preview
            create_web_preview(output_dir / 'video.mp4', output_dir / 'preview.webm')
            for filename in ARTIFACTS:
                path = output_dir / filename
                if not path.is_file() or path.stat().st_size == 0:
                    raise RuntimeError('완성 파일을 확인하지 못했습니다. 제작 로그를 확인해주세요.')
            safe_result = {key: result[key] for key in ('duration', 'width', 'height', 'fps', 'shots', 'engines', 'warnings') if key in result}
            safe_result.update({key: f'/api/mv/jobs/{job_id}/artifacts/{filename}' for key, filename in
                (('video_url', 'video.mp4'), ('preview_url', 'preview.webm'), ('project_url', 'project.json'), ('storyboard_url', 'storyboard.jpg'))})
            safe_result['transcription'] = {'engine': transcript['engine'], 'language': transcript.get('language'),
                'timing_source': transcript.get('timing_source'), 'text': transcript['text']}
            safe_result['provider'] = 'comfy-sdk-local-api-v2'
            self.update(job_id, status='succeeded', progress=1, message='가사로 연출한 MV가 완성됐어요.', result=safe_result)
        except Exception as error:
            with (output_dir / 'failure.log').open('w', encoding='utf-8') as trace:
                import traceback
                traceback.print_exc(file=trace)
            # Do not leak host filesystem paths or untrusted upstream traceback into UI.
            message = str(error)
            if re.search(r'[A-Za-z]:[\\/]|Traceback|https?://', message) or len(message) > 300:
                message = '로컬 제작 단계에서 오류가 발생했습니다. 프로젝트의 작업 로그를 확인해주세요.'
            self.update(job_id, status='failed', message=message, error=message)
        finally:
            self.capacity.release()


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    manager = JobManager()
    def log_message(self, fmt, *args):
        # Never put audio, lyrics or request body into server logs.
        super().log_message(fmt, *args)

    def local(self):
        hosts = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        if self.headers.get('Host') not in hosts:
            raise RequestError('로컬 앱 요청만 허용됩니다.', 403)
        if self.headers.get('Origin') not in (None, *('http://' + host for host in hosts)):
            raise RequestError('다른 웹사이트의 요청은 허용되지 않습니다.', 403)

    def send_headers(self, status, content_type, length, extra=None):
        self.send_response(status)
        for key, value in {'Content-Type': content_type, 'Content-Length': str(length),
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'Referrer-Policy': 'no-referrer', 'Permissions-Policy': 'camera=(),microphone=(),geolocation=()',
            'Content-Security-Policy': "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
            **(extra or {})}.items():
            self.send_header(key, value)
        self.end_headers()

    def json(self, status, data):
        encoded = json.dumps(data, ensure_ascii=False, allow_nan=False).encode('utf-8')
        self.send_headers(status, 'application/json; charset=utf-8', len(encoded))
        if self.command != 'HEAD':
            self.wfile.write(encoded)

    def file(self, path, mime):
        size = path.stat().st_size
        start, end, code = 0, size - 1, 200
        extra = {'Accept-Ranges': 'bytes'}
        requested = self.headers.get('Range')
        if requested:
            match = re.fullmatch(r'bytes=(\d*)-(\d*)', requested)
            if not match or not any(match.groups()):
                raise RequestError('잘못된 파일 범위 요청입니다.', 416)
            first, last = match.groups()
            if not first:
                start = max(0, size-int(last))
            else:
                start = int(first)
                if last: end = min(end, int(last))
            if not 0 <= start <= end < size:
                self.send_headers(416, mime, 0, {'Content-Range': f'bytes */{size}'})
                return
            code = 206
            extra['Content-Range'] = f'bytes {start}-{end}/{size}'
        self.send_headers(code, mime, end-start+1, extra)
        if self.command == 'HEAD':
            return
        with path.open('rb') as source:
            source.seek(start)
            remaining = end-start+1
            while remaining:
                chunk = source.read(min(65536, remaining))
                if not chunk: break
                self.wfile.write(chunk); remaining -= len(chunk)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        try:
            self.local()
            route = urlsplit(self.path).path
            if route == '/api/mv/health':
                return self.json(200, self.manager.health())
            if route == '/api/mv/demo.wav':
                path = ROOT / 'tests/fixtures/mv-original-demo.wav'
                if not path.is_file(): raise RequestError('합성 데모 파일이 준비되지 않았습니다.', 404)
                return self.file(path, 'audio/wav')
            match = re.fullmatch(r'/api/mv/jobs/([a-f0-9]{32})(?:/artifacts/([a-z0-9.]+))?', route)
            if match:
                job_id, filename = match.groups()
                state = self.manager.get(job_id)
                if not filename:
                    return self.json(200, state)
                if filename not in ARTIFACTS or state['status'] != 'succeeded':
                    raise RequestError('완성 파일을 찾을 수 없습니다.', 404)
                path = ROOT / 'output/mv' / job_id / filename
                if not path.is_file() or path.is_symlink():
                    raise RequestError('완성 파일을 찾을 수 없습니다.', 404)
                return self.file(path, ARTIFACTS[filename])
            if route.startswith('/api/'):
                raise RequestError('API 경로를 찾을 수 없습니다.', 404)
            decoded = unquote(route).replace('\\', '/')
            if '\x00' in decoded or '..' in decoded.split('/'):
                raise RequestError('허용되지 않는 파일 경로입니다.', 403)
            relative = 'index.html' if decoded in ('/', '/mv/', '/mv/index.html') else decoded.removeprefix('/mv/').lstrip('/')
            base = (ROOT / 'web/mv').resolve()
            path = (base / relative).resolve()
            if not path.is_relative_to(base) or not path.is_file() or path.suffix not in {'.html', '.mjs', '.js', '.css', '.svg', '.png', '.jpg'}:
                raise RequestError('파일을 찾을 수 없습니다.', 404)
            mime = 'text/javascript' if path.suffix in {'.js', '.mjs'} else mimetypes.guess_type(str(path))[0] or 'application/octet-stream'
            return self.file(path, mime)
        except RequestError as error:
            self.json(error.status, {'error': error.message})
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        try:
            self.local()
            if urlsplit(self.path).path != '/api/mv/jobs':
                raise RequestError('API 경로를 찾을 수 없습니다.', 404)
            if self.headers.get('Transfer-Encoding') or not re.fullmatch(r'\d{1,9}', self.headers.get('Content-Length', '')):
                raise RequestError('Content-Length가 필요합니다.', 411)
            size = int(self.headers['Content-Length'])
            if size > MAX_BODY:
                self.close_connection = True
                raise RequestError('오디오 요청은 최대24MiB입니다.', 413)
            if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
                raise RequestError('JSON 오디오 요청이 필요합니다.', 415)
            self.connection.settimeout(30)
            raw = self.rfile.read(size)
            if len(raw) != size: raise RequestError('오디오 전송이 끝나지 않았습니다.')
            job = self.manager.create(parse_job(raw))
            self.json(202, {'id': job['id'], 'status': job['status']})
        except RequestError as error:
            self.close_connection = True
            self.json(error.status, {'error': error.message})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self.close_connection = True
            self.json(500, {'error': '로컬 제작 요청을 처리하지 못했습니다.'})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8797, choices=[8797])
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.daemon_threads = True
    print(f'BOP MV Creator: http://127.0.0.1:{args.port}/ (project-local CPU)', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
