"""MV HTTP boundaries and private artifact range serving, without model substitutes."""
import http.client
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('mv_http_test', ROOT / 'mv_server.py')
app = importlib.util.module_from_spec(spec); spec.loader.exec_module(app)


class ParseTests(unittest.TestCase):
    def payload(self, **updates):
        value = {'audio': {'samples': [.05]*16000, 'sample_rate': 16000}, 'title': 'test'}
        value.update(updates)
        return json.dumps(value).encode()
    def test_normal_audio_and_optional_lyrics(self):
        result = app.parse_job(self.payload(lyrics='작은 빛', theme='bloom'))
        self.assertEqual(len(result['samples']), 16000)
        self.assertEqual(result['lyrics'], '작은 빛')
    def test_nonfinite_duplicate_and_unbounded_fields(self):
        for raw in (b'{"audio":NaN}', b'{"title":"a","title":"b"}', b'[]', self.payload(url='https://example.com')):
            with self.assertRaises(app.RequestError): app.parse_job(raw)
        for update in ({'seed': True}, {'theme': 'javascript'}, {'lyrics':'a'*6001}, {'title':'../secret\n'}):
            with self.assertRaises(app.RequestError): app.parse_job(self.payload(**update))
    def test_boolean_silence_and_out_of_bounds_audio_rejected(self):
        for values in ([True]*16000, [0.]*16000, [1.1]*16000, [.2]*15):
            with self.assertRaises(app.RequestError): app.parse_job(self.payload(audio={'samples':values,'sample_rate':16000}))


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class Quiet(app.Handler):
            def log_message(self, *args): pass
        cls.server = app.ThreadingHTTPServer(('127.0.0.1',0), Quiet)
        cls.port = cls.server.server_port
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True); cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join(timeout=3)
    def request(self, path='/', method='GET', headers=None, body=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.port,timeout=10)
        conn.request(method,path,body=body,headers=headers or {}); response=conn.getresponse(); data=response.read()
        value=response.status,dict(response.getheaders()),data; conn.close(); return value
    def test_local_static_and_video_style_range(self):
        status,headers,data=self.request('/app.mjs',headers={'Range':'bytes=0-31'})
        self.assertEqual(status,206); self.assertEqual(len(data),32)
        self.assertEqual(headers['Content-Range'].split('/')[0],'bytes 0-31')
        self.assertEqual(headers['Cache-Control'],'no-store')
        self.assertEqual(self.request('/app.mjs','HEAD')[2],b'')
        self.assertEqual(self.request('/app.mjs',headers={'Range':'bytes=99999999-'})[0],416)
    def test_other_origins_hosts_and_private_paths_rejected(self):
        self.assertEqual(self.request(headers={'Host':'evil.example'})[0],403)
        self.assertEqual(self.request(headers={'Origin':'https://evil.example'})[0],403)
        for path in ('/../mv_server.py','/%2e%2e/mv_server.py','/.runtime/processes.json','/api/mv/jobs/../../x','/api/unknown'):
            self.assertIn(self.request(path)[0],(403,404))
    def test_unknown_job_never_exposes_output_tree(self):
        ident='a'*32
        self.assertEqual(self.request('/api/mv/jobs/'+ident)[0],404)
        self.assertEqual(self.request('/api/mv/jobs/'+ident+'/artifacts/video.mp4')[0],404)

    def test_upload_endpoint_parses_original_bytes_without_model_execution(self):
        # Boundary-only test: create is mocked, never represented as inference.
        boundary='mv-http-upload'
        audio=b'original\x00\x80\xffaudio'
        body=(b'--'+boundary.encode()+b'\r\nContent-Disposition: form-data; name="metadata"\r\n\r\n{"title":"Upload","theme":"bloom"}\r\n'
              b'--'+boundary.encode()+b'\r\nContent-Disposition: form-data; name="audio"; filename="../../ignored.wav"\r\nContent-Type: audio/wav\r\n\r\n'+audio+b'\r\n--'+boundary.encode()+b'--\r\n')
        with patch.object(app,'model_status',return_value={'available':True}), patch.object(app,'comfy_ready',return_value={'available':True}), patch.object(app,'available_ram_gb',return_value=8), patch.object(self.server.RequestHandlerClass.manager,'create',return_value={'id':'e'*32,'status':'queued'}) as create:
            status,_,data=self.request('/api/mv/uploads','POST',{'Content-Type':'multipart/form-data; boundary='+boundary},body)
            self.assertEqual(status,202)
            self.assertEqual(json.loads(data),{'id':'e'*32,'status':'queued'})
            self.assertEqual(create.call_args.args[0]['raw_audio'],audio)
            self.assertNotIn('filename',create.call_args.args[0])
            self.assertEqual(self.request('/api/mv/uploads','POST',{'Content-Type':'application/json'},b'{}')[0],415)
            self.assertEqual(self.request('/api/mv/uploads','POST',{'Content-Type':'multipart/form-data; boundary=x'},b'broken')[0],400)
            # Parse failure returned the admission; a retry can acquire it exactly once.
            admission=self.server.RequestHandlerClass.manager.reserve_upload()
            admission.release(); admission.release()

    def test_busy_upload_is_rejected_before_reading_or_parsing_body(self):
        manager=self.server.RequestHandlerClass.manager
        with patch.object(app,'model_status',return_value={'available':True}), patch.object(app,'comfy_ready',return_value={'available':True}), patch.object(app,'available_ram_gb',return_value=8), patch.object(app,'parse_upload') as parse:
            admission=manager.reserve_upload()
            try:
                # Deliberately send no promised body: a read-first implementation
                # would wait 30 seconds rather than immediately returning 429.
                status,_,_=self.request('/api/mv/uploads','POST',{'Content-Type':'multipart/form-data; boundary=x','Content-Length':'1000'})
                self.assertEqual(status,429)
                parse.assert_not_called()
            finally:
                admission.release()

    def test_all_named_artifacts_and_ranges_include_numeric_mp4_extension(self):
        ident = 'd'*32
        with tempfile.TemporaryDirectory() as directory, patch.object(app, 'ROOT', Path(directory)):
            out = Path(directory)/'output/mv'/ident
            out.mkdir(parents=True)
            state = {'id':ident,'status':'succeeded','result':{}}
            with patch.dict(self.server.RequestHandlerClass.manager.jobs, {ident:state}):
                for name, mime in app.ARTIFACTS.items():
                    content = b'actual-artifact-bytes-'+name.encode()
                    (out/name).write_bytes(content)
                    route = '/api/mv/jobs/'+ident+'/artifacts/'+name
                    status, headers, data = self.request(route)
                    self.assertEqual(status, 200, name)
                    self.assertEqual(data, content, name)
                    self.assertEqual(headers['Content-Type'], mime)
                    status, headers, data = self.request(route, headers={'Range':'bytes=1-4'})
                    self.assertEqual((status,data), (206,content[1:5]), name)


class RecoveryTests(unittest.TestCase):
    def test_completed_result_survives_app_restart_and_missing_artifacts_do_not(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(app, 'ROOT', Path(directory)):
            ident = 'b'*32
            out = Path(directory) / 'output/mv' / ident
            out.mkdir(parents=True)
            state = {'id':ident, 'status':'succeeded', 'result':{'duration':2.0}}
            (out/'job.json').write_text(json.dumps(state), encoding='utf-8')
            for name in ('video.mp4', 'project.json', 'storyboard.jpg'):
                (out/name).write_bytes(b'fixture')
            self.assertEqual(app.JobManager().get(ident), state)
            (out/'video.mp4').unlink()
            with self.assertRaises(app.RequestError): app.JobManager().get(ident)

    def test_interrupted_job_is_not_resumed_or_reported_as_complete(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(app, 'ROOT', Path(directory)):
            ident = 'c'*32
            out = Path(directory) / 'output/mv' / ident
            out.mkdir(parents=True)
            (out/'job.json').write_text(json.dumps({'id':ident, 'status':'rendering'}), encoding='utf-8')
            self.assertEqual(app.JobManager().get(ident)['status'], 'failed')
            with self.assertRaises(app.RequestError): app.JobManager().get('../outside')


if __name__ == '__main__': unittest.main()
