"""Loopback-only Voice Motion Lab HTTP service. No uploaded audio is persisted."""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
from pathlib import Path
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import uuid

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
from voice_motion.analysis import AnalysisError, analyze, validate_input
from voice_motion.model import ModelUnavailableError, model_info

MAX_BODY = 3 * 1024 * 1024
MAX_UPSTREAM_BODY = 8 * 1024 * 1024
_analysis_lock = threading.Semaphore(1)


class ApiError(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status


def _reject_constant(value):
    raise ValueError("Non-finite JSON number")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def parse_payload(raw: bytes) -> dict:
    try:
        value = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant, object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ApiError("올바른 UTF-8 JSON 요청이 아닙니다.") from exc
    if not isinstance(value, dict):
        raise ApiError("요청은 JSON 객체여야 합니다.")
    allowed = {"samples", "sample_rate", "engine", "sensitivity", "smoothing"}
    if set(value) - allowed:
        raise ApiError("지원하지 않는 요청 필드가 있습니다.")
    if "samples" not in value or "sample_rate" not in value:
        raise ApiError("samples와 sample_rate가 필요합니다.")
    return value


def comfy_config() -> str | None:
    value = os.environ.get("VOICE_MOTION_COMFY_URL", "").strip()
    if not value:
        return None
    try:
        parsed = urlsplit(value)
        valid = parsed.scheme == "http" and parsed.hostname == "127.0.0.1" and parsed.port == 8794
        valid &= not parsed.username and not parsed.password and parsed.path in ("", "/")
        valid &= not parsed.query and not parsed.fragment
        if valid:
            return "http://127.0.0.1:8794"
    except ValueError:
        pass
    raise ApiError("Comfy 연결은 프로젝트 전용 http://127.0.0.1:8794만 설정할 수 있습니다.", 503)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _upstream_json(url: str, payload=None, timeout=5):
    encoded = None if payload is None else json.dumps(payload, allow_nan=False).encode("utf-8")
    request = Request(url, data=encoded, headers={"Content-Type": "application/json"})
    try:
        with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=timeout) as response:
            raw = response.read(MAX_UPSTREAM_BODY + 1)
        if len(raw) > MAX_UPSTREAM_BODY:
            raise ApiError("프로젝트 Comfy 응답이 너무 큽니다.", 502)
        return json.loads(raw, parse_constant=_reject_constant)
    except ApiError:
        raise
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        raise ApiError("프로젝트 Comfy 서버 응답을 확인할 수 없습니다.", 502) from exc


def analyze_with_comfy(payload: dict) -> dict:
    # Validate locally before probing or queuing any graph.
    validate_input(payload["samples"], payload["sample_rate"], payload.get("engine", "auto"),
                   payload.get("sensitivity", 1.0), payload.get("smoothing", .35))
    url = comfy_config()
    if url is None:
        raise ApiError("독립 Comfy 연결이 설정되지 않았습니다. 로컬 분석을 사용하세요.", 503)
    schema = _upstream_json(url + "/object_info/VoiceMotionAnalyze")
    if not isinstance(schema, dict) or "VoiceMotionAnalyze" not in schema:
        raise ApiError("프로젝트 VoiceMotionAnalyze 노드를 찾을 수 없어 작업을 제출하지 않았습니다.", 503)
    node = "voice_motion_analysis"
    inputs = {"samples_json": json.dumps(payload["samples"], allow_nan=False),
              "sample_rate": payload["sample_rate"], "engine": payload.get("engine", "auto"),
              "sensitivity": payload.get("sensitivity", 1.0), "smoothing": payload.get("smoothing", .35)}
    queued = _upstream_json(url + "/prompt", {"prompt": {node: {"class_type": "VoiceMotionAnalyze", "inputs": inputs}},
                                               "client_id": "voice-motion-" + uuid.uuid4().hex})
    prompt_id = queued.get("prompt_id") if isinstance(queued, dict) else None
    if not isinstance(prompt_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", prompt_id):
        raise ApiError("프로젝트 Comfy 작업 번호를 확인할 수 없습니다.", 502)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        history = _upstream_json(url + "/history/" + quote(prompt_id, safe=""))
        item = history.get(prompt_id) if isinstance(history, dict) else None
        if isinstance(item, dict):
            status = item.get("status", {})
            if status.get("status_str") == "error":
                raise ApiError("프로젝트 Comfy 오디오 분석 작업에 실패했습니다.", 502)
            text = item.get("outputs", {}).get(node, {}).get("text", [])
            if text:
                try:
                    result = json.loads(text[0], parse_constant=_reject_constant)
                    if not isinstance(result, dict) or result.get("version") != 1 or not isinstance(result.get("frames"), list):
                        raise ValueError("Bad analysis contract")
                    json.dumps(result, allow_nan=False)
                    return result
                except (TypeError, ValueError) as exc:
                    raise ApiError("프로젝트 Comfy 분석 결과 형식이 올바르지 않습니다.", 502) from exc
            if status.get("completed"):
                raise ApiError("프로젝트 Comfy 작업에 분석 출력이 없습니다.", 502)
        time.sleep(.15)
    raise ApiError("프로젝트 Comfy 분석 대기 시간이 초과되었습니다. 제출한 프로젝트 작업만 확인했습니다.", 504)


class VoiceMotionHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class Handler(BaseHTTPRequestHandler):
    server_version = "VoiceMotion/0.1"
    protocol_version = "HTTP/1.1"
    web_root = ROOT / "web"

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def _headers(self, status, content_type, length):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(self), geolocation=()")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; script-src 'self' 'wasm-unsafe-eval'; connect-src 'self'; worker-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()

    def _json(self, status, data):
        encoded = json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(encoded))
        self.wfile.write(encoded)

    def _local_request(self):
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host", "") not in hosts:
            raise ApiError("로컬 호스트 요청만 허용됩니다.", 403)
        origin = self.headers.get("Origin")
        if origin is not None and origin not in {"http://" + host for host in hosts}:
            raise ApiError("다른 웹사이트의 요청은 허용되지 않습니다.", 403)

    def do_GET(self):
        try:
            self._local_request()
            route = urlsplit(self.path).path
            if route == "/api/health":
                model = model_info()
                try:
                    comfy = comfy_config()
                    comfy_meta = {"configured": comfy is not None, "url": comfy}
                except ApiError as exc:
                    comfy_meta = {"configured": False, "url": None, "error": exc.message}
                self._json(200, {"status": "ok", "version": "0.1.0", "engines": {"signal": True, "basic_pitch": model["available"]},
                                 "model": model, "comfy": comfy_meta})
            elif route == "/api/model":
                self._json(200, model_info())
            elif route.startswith("/api/"):
                raise ApiError("API 경로를 찾을 수 없습니다.", 404)
            else:
                decoded = unquote(route).replace("\\", "/")
                if "\x00" in decoded or ".." in decoded.split("/"):
                    raise ApiError("허용되지 않는 파일 경로입니다.", 403)
                relative = "index.html" if decoded == "/" else decoded.lstrip("/")
                root = self.web_root.resolve()
                target = (root / relative).resolve()
                if not target.is_relative_to(root):
                    raise ApiError("허용되지 않는 파일 경로입니다.", 403)
                if not target.is_file():
                    raise ApiError("파일을 찾을 수 없습니다.", 404)
                data = target.read_bytes()
                content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
                if target.suffix in (".js", ".mjs"):
                    content_type = "text/javascript"
                self._headers(200, content_type, len(data))
                self.wfile.write(data)
        except ApiError as exc:
            self._json(exc.status, {"error": exc.message})
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        try:
            self._local_request()
            route = urlsplit(self.path).path
            if route not in ("/api/analyze", "/api/analyze-comfy"):
                raise ApiError("API 경로를 찾을 수 없습니다.", 404)
            if self.headers.get("Transfer-Encoding"):
                raise ApiError("청크 전송은 지원하지 않습니다.", 400)
            length = self.headers.get("Content-Length", "")
            if not re.fullmatch(r"[0-9]{1,9}", length):
                raise ApiError("Content-Length가 필요합니다.", 411)
            length = int(length)
            if length > MAX_BODY:
                # An immediate close while a browser is still uploading can reset
                # the socket on Windows, hiding the useful 413 response. Drain
                # only one bounded request's worth, never an arbitrary declared
                # length, and do not wait on headers-only oversized requests.
                self.connection.settimeout(.5)
                remaining = min(length, MAX_BODY + 1)
                try:
                    while remaining:
                        chunk = self.rfile.read(min(65536, remaining))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                except (TimeoutError, OSError):
                    pass
                finally:
                    self.connection.settimeout(15)
                raise ApiError("JSON 요청은 최대 3MB입니다.", 413)
            if self.headers.get("Content-Type", "").split(";", 1)[0].lower().strip() != "application/json":
                raise ApiError("Content-Type은 application/json이어야 합니다.", 415)
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ApiError("요청 본문이 완전하지 않습니다.")
            payload = parse_payload(raw)
            if not _analysis_lock.acquire(blocking=False):
                raise ApiError("다른 프로젝트 분석이 진행 중입니다. 잠시 후 다시 시도하세요.", 429)
            try:
                result = analyze(**payload) if route == "/api/analyze" else analyze_with_comfy(payload)
            finally:
                _analysis_lock.release()
            self._json(200, result)
        except AnalysisError as exc:
            self._json(400, {"error": str(exc)})
        except ModelUnavailableError as exc:
            self._json(503, {"error": str(exc)})
        except ApiError as exc:
            self.close_connection = True
            self._json(exc.status, {"error": exc.message})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except TimeoutError:
            self.close_connection = True
        except Exception:
            self.close_connection = True
            self._json(500, {"error": "로컬 분석 처리 중 오류가 발생했습니다."})


def main():
    parser = argparse.ArgumentParser(description="Voice Motion Lab (isolated, CPU only)")
    parser.add_argument("--port", type=int, default=8793)
    args = parser.parse_args()
    if args.port != 8793:
        parser.error("프로젝트 앱 포트는 8793으로 고정되어 있습니다.")
    server = VoiceMotionHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Voice Motion Lab: http://127.0.0.1:{args.port} (CPU only)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
