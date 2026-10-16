import http.client
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("voice_motion_http_test", ROOT / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class QuietHandler(server.Handler):
    def log_message(self, *args):
        pass


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="backend-tests-", dir=ROOT / "temp")
        web = Path(cls.temp.name) / "web"
        web.mkdir()
        (web / "index.html").write_text("Voice Motion test", encoding="utf-8")
        (Path(cls.temp.name) / "private.txt").write_text("must not be served", encoding="utf-8")
        QuietHandler.web_root = web
        cls.httpd = server.VoiceMotionHTTPServer(("127.0.0.1", 0), QuietHandler)
        cls.port = cls.httpd.server_port
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=3)
        cls.temp.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        data = response.read()
        result = (response.status, dict(response.getheaders()), data)
        connection.close()
        return result

    def post(self, payload, path="/api/analyze"):
        return self.request("POST", path, json.dumps(payload), {"Content-Type": "application/json"})

    def test_health_model_and_static(self):
        status, headers, data = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(data)["engines"]["signal"])
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertEqual(self.request("GET", "/api/model")[0], 200)
        self.assertEqual(self.request("GET", "/")[2], b"Voice Motion test")

    def test_analysis_success_does_not_write_audio(self):
        before = sorted(Path(self.temp.name).rglob("*"))
        status, _, data = self.post({"samples": [0.] * 800, "sample_rate": 8000, "engine": "signal"})
        self.assertEqual(status, 200, data)
        result = json.loads(data)
        self.assertEqual(result["engine"], "signal")
        self.assertEqual(result["events"], [])
        self.assertEqual(before, sorted(Path(self.temp.name).rglob("*")))

    def test_invalid_json_nonfinite_and_missing_fields(self):
        for raw in (b"{bad}", b"[]", b'{"samples":[NaN],"sample_rate":8000}',
                    b'{"samples":[],"sample_rate":8000,"sample_rate":16000}', b'{"sample_rate":8000}'):
            status, _, _ = self.request("POST", "/api/analyze", raw, {"Content-Type": "application/json"})
            self.assertEqual(status, 400)
        self.assertEqual(self.post({"samples": [0.], "sample_rate": 16000.5})[0], 400)
        self.assertEqual(self.post({"samples": [0.], "sample_rate": 16000, "url": "http://example.com"})[0], 400)

    def test_json_size_and_content_type_limits(self):
        status, _, _ = self.request("POST", "/api/analyze", b"x" * (server.MAX_BODY + 1), {"Content-Type": "application/json"})
        self.assertEqual(status, 413)
        self.assertEqual(self.request("POST", "/api/analyze", b"{}", {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.post({"samples": [0] * 120001, "sample_rate": 8000, "engine": "signal"})[0], 400)

    def test_cross_origin_host_and_path_traversal_are_rejected(self):
        self.assertEqual(self.request("GET", "/api/health", headers={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.request("GET", "/api/health", headers={"Host": "evil.example"})[0], 403)
        for path in ("/../private.txt", "/%2e%2e/private.txt", "/%2e%2e%5cprivate.txt", "/C:/Windows/win.ini"):
            self.assertIn(self.request("GET", path)[0], (403, 404))

    def test_explicit_model_error_and_comfy_opt_in(self):
        with patch.dict(os.environ, {"VOICE_MOTION_MODEL": str(ROOT / "nonexistent-test.onnx")}):
            self.assertEqual(self.post({"samples": [0.], "sample_rate": 8000, "engine": "basic_pitch"})[0], 503)
        with patch.dict(os.environ, {"VOICE_MOTION_COMFY_URL": ""}):
            self.assertEqual(self.post({"samples": [0.], "sample_rate": 8000}, "/api/analyze-comfy")[0], 503)
        for url in ("http://127.0.0.1:8188", "https://127.0.0.1:8794", "http://localhost:8794", "http://127.0.0.1:8794@evil.example"):
            with patch.dict(os.environ, {"VOICE_MOTION_COMFY_URL": url}):
                with self.assertRaises(server.ApiError):
                    server.comfy_config()

    def test_comfy_queues_only_named_project_graph_and_polls_its_id(self):
        payload = {"samples": [0.], "sample_rate": 8000, "engine": "signal"}
        result = {"version": 1, "frames": [], "events": [], "engine": "signal"}
        replies = [{"VoiceMotionAnalyze": {}}, {"prompt_id": "our-job-1"},
                   {"our-job-1": {"outputs": {"voice_motion_analysis": {"text": [json.dumps(result)]}}}}]
        with patch.dict(os.environ, {"VOICE_MOTION_COMFY_URL": "http://127.0.0.1:8794"}), patch.object(server, "_upstream_json", side_effect=replies) as upstream:
            self.assertEqual(server.analyze_with_comfy(payload), result)
        calls = upstream.call_args_list
        self.assertEqual(calls[0].args[0], "http://127.0.0.1:8794/object_info/VoiceMotionAnalyze")
        graph = calls[1].args[1]["prompt"]
        self.assertEqual(list(graph), ["voice_motion_analysis"])
        self.assertEqual(graph["voice_motion_analysis"]["class_type"], "VoiceMotionAnalyze")
        self.assertEqual(calls[2].args[0], "http://127.0.0.1:8794/history/our-job-1")


if __name__ == "__main__":
    unittest.main()
