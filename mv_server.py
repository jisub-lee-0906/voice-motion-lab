"""Independent loopback MV creator: local audio -> CPU ASR -> official Comfy SDK -> MP4."""
from __future__ import annotations
import argparse
import asyncio
import ctypes
from email import policy
from email.message import Message
from email.parser import BytesParser
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


def parse_json_object(raw, message='올바른 메타데이터 JSON이 아닙니다.'):
    try:
        data = json.loads(raw.decode('utf-8'), parse_constant=reject_constant, object_pairs_hook=unique_object)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise RequestError(message) from error
    if not isinstance(data, dict):
        raise RequestError(message)
    return data


def validate_metadata(data):
    if not isinstance(data, dict) or set(data) - {'title', 'theme', 'seed', 'lyrics'}:
        raise RequestError('지원하지 않는 메타데이터 필드입니다.')
    theme = data.get('theme', 'auto')
    seed = data.get('seed', 7)
    if not isinstance(theme, str) or theme not in {'auto', 'bloom', 'fracture', 'orbit'}:
        raise RequestError('지원하지 않는 테마입니다.')
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2147483647:
        raise RequestError('시드는 0~2147483647 사이의 정수여야 합니다.')
    title = data.get('title', 'Untitled')
    lyrics = data.get('lyrics', '')
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120 or any(ord(x) < 32 for x in title):
        raise RequestError('제목은 1~120자로 입력해주세요.')
    if not isinstance(lyrics, str) or len(lyrics) > 6000 or '\x00' in lyrics:
        raise RequestError('선택 가사는 최대 6,000자입니다.')
    return {'title': title.strip(), 'theme': theme, 'seed': seed, 'lyrics': lyrics.strip()}


def parse_upload(raw, content_type):
    """Exactly two ordinary binary multipart parts; filenames never become paths."""
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_BODY:
        raise RequestError('원본 오디오 요청은 최대 24MiB입니다.', 413)
    if not isinstance(content_type, str) or any(x in content_type for x in ('\r', '\n')) or len(content_type) > 200:
        raise RequestError('올바른 multipart 요청 형식이 아닙니다.', 415)
    try:
        content_type.encode('ascii')
    except UnicodeError as error:
        raise RequestError('올바른 multipart 요청 형식이 아닙니다.', 415) from error
    header = Message()
    header['Content-Type'] = content_type
    params = header.get_params() or []
    if header.get_content_type() != 'multipart/form-data' or len(params) != 2 or params[1][0].lower() != 'boundary':
        raise RequestError('metadata와 audio가 있는 multipart/form-data 요청이 필요합니다.', 415)
    boundary = header.get_boundary()
    if not boundary or not re.fullmatch(r"[A-Za-z0-9'()+_,./:=?-]{1,70}", boundary):
        raise RequestError('multipart 경계가 올바르지 않습니다.')
    marker = b'--' + boundary.encode('ascii')
    if not raw.startswith(marker+b'\r\n') or not (raw.endswith(marker+b'--\r\n') or raw.endswith(marker+b'--')):
        raise RequestError('multipart 오디오 전송이 끝나지 않았습니다.')
    # Bound structure before the email parser constructs any MIME object tree.
    # A valid two-part body has one middle delimiter and one closing delimiter.
    if raw.count(b'\r\n'+marker) != 2:
        raise RequestError('metadata와 audio를 각각 한 개씩 보내주세요.')
    starts = [len(marker)+2]
    middle = raw.find(b'\r\n'+marker+b'\r\n')
    if middle < 0:
        raise RequestError('multipart 필드 구분이 올바르지 않습니다.')
    starts.append(middle+len(marker)+4)
    for start in starts:
        end = raw.find(b'\r\n\r\n',start,start+4096)
        if end < 0:
            raise RequestError('multipart 필드 헤더는 최대 4KiB입니다.')
        headers = raw[start:end]
        if any(line.startswith((b' ',b'\t')) for line in headers.split(b'\r\n')):
            raise RequestError('접힌 multipart 헤더는 지원하지 않습니다.')
        compact = b''.join(headers.lower().split())
        if b'multipart/' in compact or b'message/' in compact:
            raise RequestError('중첩된 multipart나 메시지는 허용되지 않습니다.')
    message = BytesParser(policy=policy.HTTP).parsebytes(b'Content-Type: '+content_type.encode('ascii')+b'\r\nMIME-Version: 1.0\r\n\r\n'+raw)
    if message.defects or any(getattr(value, 'defects', ()) for _, value in message.items()) or not message.is_multipart() or message.preamble or message.epilogue:
        raise RequestError('multipart 요청 구조가 올바르지 않습니다.')
    parts = list(message.iter_parts())
    if len(parts) != 2:
        raise RequestError('metadata와 audio를 각각 한 개씩 보내주세요.')
    values = {}
    for part in parts:
        if part.defects or any(getattr(value, 'defects', ()) for _, value in part.items()) or part.is_multipart():
            raise RequestError('중첩되거나 손상된 multipart는 허용되지 않습니다.')
        names = [name.lower() for name in part.keys()]
        if any(name not in {'content-disposition', 'content-type'} for name in names) or len(names) != len(set(names)):
            raise RequestError('지원하지 않거나 중복된 multipart 헤더입니다.')
        if part.get_content_disposition() != 'form-data':
            raise RequestError('multipart form-data가 필요합니다.')
        disposition = part.get_params(header='content-disposition') or []
        disposition_names = [name.lower() for name, _ in disposition[1:]]
        if len(disposition_names) != len(set(disposition_names)) or any(name not in {'name', 'filename'} for name in disposition_names):
            raise RequestError('multipart 필드 정보가 올바르지 않습니다.')
        name = part.get_param('name', header='content-disposition')
        if name not in {'metadata', 'audio'} or name in values:
            raise RequestError('metadata와 audio를 각각 한 개씩 보내주세요.')
        mime = part.get_content_type()
        if name == 'metadata':
            if 'filename' in disposition_names or mime not in {'text/plain', 'application/json'} or part.get_content_charset() not in (None, 'utf-8', 'us-ascii'):
                raise RequestError('metadata는 UTF-8 JSON 문자열이어야 합니다.')
        elif mime != 'application/octet-stream' and not mime.startswith('audio/'):
            raise RequestError('audio 필드에는 음악 파일이 필요합니다.', 415)
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes) or not payload:
            raise RequestError('비어 있는 multipart 필드입니다.')
        values[name] = payload
    if set(values) != {'metadata', 'audio'} or len(values['metadata']) > 64 * 1024:
        raise RequestError('metadata는 최대 64KiB이며 audio 파일이 함께 필요합니다.')
    metadata = validate_metadata(parse_json_object(values['metadata']))
    return {'raw_audio': values['audio'], **metadata}


def parse_job(raw):
    import numpy as np
    data = parse_json_object(raw, '올바른 오디오 JSON 요청이 아닙니다.')
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
    return {'samples': samples, **validate_metadata({key:value for key,value in data.items() if key != 'audio'})}


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


def normalize_upload_subprocess(source_path, target, timeout=45):
    """Kill/wait only our single native-decoder Python process on a deadline.

    PyAV performs decoding in this process; no ffmpeg CLI or child process is
    launched. Native stdout/stderr are deliberately discarded instead of buffering
    attacker-controlled log volume. A small structured result is the sole output.
    """
    source_path, target = Path(source_path), Path(target)
    result_path = source_path.parent / 'normalization-result.json'
    log_path = source_path.parent / 'normalization.log'
    env = dict(os.environ)
    env.update({'CUDA_VISIBLE_DEVICES':'', 'OMP_NUM_THREADS':'2', 'MKL_NUM_THREADS':'2',
                'OPENBLAS_NUM_THREADS':'2', 'NUMEXPR_NUM_THREADS':'2', 'PYTHONNOUSERSITE':'1',
                'PYTHONIOENCODING':'utf-8', 'TEMP':str(ROOT/'temp'), 'TMP':str(ROOT/'temp')})
    command = [sys.executable, str(ROOT/'src/voice_motion/mv_audio.py'),
               '--source',str(source_path),'--target',str(target),'--result',str(result_path)]
    begun = time.monotonic()
    child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, cwd=ROOT, env=env,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try:
        try:
            code = child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)
            log_path.write_text(f'normalization timed out after {timeout:g} seconds; child killed and reaped\n',encoding='utf-8')
            raise RequestError('음악 파일을 읽는 시간이 제한을 넘었습니다. 다른 WAV·MP3 구간으로 다시 시도해주세요.',408) from None
        if not result_path.is_file() or result_path.is_symlink() or result_path.stat().st_size > 64*1024:
            raise RequestError('음악 정규화 결과를 확인할 수 없습니다. 다른 파일로 다시 시도해주세요.')
        record = parse_json_object(result_path.read_bytes())
        if code != 0 or record.get('ok') is not True:
            message = record.get('error')
            if not isinstance(message,str) or not message or len(message)>300:
                message = '음악 파일을 안전하게 읽을 수 없습니다.'
            raise RequestError(message)
        metadata = record.get('audio')
        if not isinstance(metadata,dict) or not target.is_file() or target.is_symlink():
            raise RequestError('정규화된 음악 파일을 확인할 수 없습니다.')
        log_path.write_text(f'normalization succeeded in {time.monotonic()-begun:.3f} seconds; native output discarded\n',encoding='utf-8')
        return metadata
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


class JobAdmission:
    """Idempotent token: the request owns the semaphore until worker handoff."""
    def __init__(self,manager):
        self.manager, self.held = manager, True

    def validate(self,manager):
        if self.manager is not manager or not self.held:
            raise RequestError('제작 요청의 실행 권한이 올바르지 않습니다.',500)

    def release(self):
        if self.held:
            self.held = False
            self.manager.capacity.release()

    def transfer_to_worker(self):
        self.validate(self.manager)
        self.held = False


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

    def reserve_upload(self):
        """Called before upload body read/parser, so at most one copy tree exists."""
        if not model_status()['available']:
            raise RequestError('전사 모델을 준비한 뒤 다시 시도해주세요. tools/setup_mv_models.py', 503)
        if not comfy_ready()['available']:
            raise RequestError('독립 Comfy와 공식 API 프록시가 준비되지 않았습니다. 시작 안내를 확인해주세요.', 503)
        free_ram = available_ram_gb()
        if free_ram is not None and free_ram < 5.0:
            raise RequestError('다른 작업과 메모리가 겹칩니다. 여유 RAM 5GB 이상일 때 다시 시도해주세요.', 503)
        if not self.capacity.acquire(blocking=False):
            raise RequestError('다른 MV를 제작 중입니다. 완료 후 다시 눌러주세요.', 429)
        return JobAdmission(self)

    def create(self, payload, admission=None):
        # Legacy JSON callers acquire here. Multipart requests already hold one
        # token before body read/parse and pass it through without reacquiring.
        admission = admission or self.reserve_upload()
        admission.validate(self)
        try:
            if self.created_count >= 20:
                raise RequestError('이 실행 세션의 최대 작업 수에 도달했습니다. 결과를 보존한 뒤 앱을 다시 시작해주세요.', 429)
            # Rejected uploaded media also consumes one admission: retained private
            # upload files cannot grow without bound during a single server run.
            self.created_count += 1
            payload = dict(payload)
            job_id = uuid.uuid4().hex
            source_dir = ROOT / 'input/mv' / job_id
            output_dir = ROOT / 'output/mv' / job_id
            approved_root = ROOT.resolve()
            if source_dir.resolve() != approved_root / 'input/mv' / job_id or output_dir.resolve() != approved_root / 'output/mv' / job_id:
                raise RequestError('프로젝트 입출력 경로가 외부 위치로 연결되어 있습니다. 격리 경로를 확인해주세요.', 503)
            source_dir.mkdir(parents=True, exist_ok=False)
            output_dir.mkdir(parents=True, exist_ok=False)
            if 'raw_audio' in payload:
                raw_audio = payload.pop('raw_audio')
                # The client's filename/path is ignored. Retain only this job's
                # original bytes and canonical WAV under fixed private names.
                (source_dir / 'upload.audio').write_bytes(raw_audio)
                audio_metadata = normalize_upload_subprocess(source_dir/'upload.audio',source_dir/'source.wav')
            else:
                samples = payload.pop('samples')
                with wave.open(str(source_dir / 'source.wav'), 'wb') as out:
                    out.setnchannels(1); out.setsampwidth(2); out.setframerate(16000)
                    out.writeframes((samples.clip(-1, 1)*32767).astype('<i2').tobytes())
                audio_metadata = {'duration':len(samples)/16000, 'sample_rate':16000, 'channels':1,
                                  'codec':'pcm_s16le', 'input_kind':'legacy_samples_json'}
            (source_dir / 'audio-metadata.json').write_text(json.dumps(audio_metadata, ensure_ascii=False, indent=2), encoding='utf-8')
            if payload['lyrics']:
                (source_dir / 'provided-lyrics.txt').write_text(payload['lyrics'], encoding='utf-8')
            self.jobs[job_id] = {'id': job_id, 'status': 'queued', 'progress': 0,
                'message': '로컬 제작 대기 중', 'title': payload['title'], 'created_at': time.time(),
                'audio': audio_metadata}
            self.update(job_id)
            threading.Thread(target=self.run, args=(job_id, payload), daemon=True, name=f'mv-{job_id[:8]}').start()
            admission.transfer_to_worker()
            return self.get(job_id)
        except Exception:
            admission.release()
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
        admission = None
        try:
            self.local()
            route = urlsplit(self.path).path
            if route not in {'/api/mv/jobs', '/api/mv/uploads'}:
                raise RequestError('API 경로를 찾을 수 없습니다.', 404)
            if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length') or []) != 1 or not re.fullmatch(r'\d{1,9}', self.headers.get('Content-Length', '')):
                raise RequestError('Content-Length가 필요합니다.', 411)
            size = int(self.headers['Content-Length'])
            if size > MAX_BODY:
                self.close_connection = True
                raise RequestError('오디오 요청은 최대24MiB입니다.', 413)
            if len(self.headers.get_all('Content-Type') or []) != 1:
                raise RequestError('요청 Content-Type을 하나만 지정해주세요.', 415)
            content_type = self.headers.get('Content-Type', '')
            mime = content_type.split(';')[0].strip().lower()
            if route == '/api/mv/jobs' and mime != 'application/json':
                raise RequestError('JSON 오디오 요청이 필요합니다.', 415)
            if route == '/api/mv/uploads' and mime != 'multipart/form-data':
                raise RequestError('원본 음악은 multipart/form-data로 보내주세요.', 415)
            if route == '/api/mv/uploads':
                admission = self.manager.reserve_upload()
            self.connection.settimeout(30)
            raw = self.rfile.read(size)
            if len(raw) != size: raise RequestError('오디오 전송이 끝나지 않았습니다.')
            payload = parse_upload(raw, content_type) if route == '/api/mv/uploads' else parse_job(raw)
            job = self.manager.create(payload,admission=admission) if admission else self.manager.create(payload)
            self.json(202, {'id': job['id'], 'status': job['status']})
        except RequestError as error:
            self.close_connection = True
            self.json(error.status, {'error': error.message})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self.close_connection = True
            self.json(500, {'error': '로컬 제작 요청을 처리하지 못했습니다.'})
        finally:
            if admission:
                admission.release()


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
