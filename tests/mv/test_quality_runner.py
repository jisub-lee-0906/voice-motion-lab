"""Mock-only benchmark checks: no server, GPU, model download or inference."""
import asyncio
from contextlib import redirect_stdout
import copy
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import uuid

PROJECT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('_quality_runner_test', PROJECT/'tools/run_mv_quality.py')
runner = importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)


def fake_state(root):
    def quote(value): return '"'+str(value)+'"'
    gpu = ' '.join([quote(root/'.gpu-venv/Scripts/python.exe'), quote(root/'.runtime/ComfyUI/main.py'),
                    '--listen 127.0.0.1 --port 8796',
                    '--models-directory', quote(root/'.runtime/mv-quality/models'),
                    '--input-directory', quote(root/'input/mv-quality'),
                    '--output-directory', quote(root/'output/mv-quality'),
                    '--user-directory', quote(root/'user/gpu'), '--temp-directory', quote(root/'temp/gpu'),
                    '--base-directory', quote(root/'.runtime/mv-quality/comfy-state')])
    proxy = ' '.join([quote(root/'.mv-venv/Scripts/python.exe'), '-m comfy_api_proxy.cli run',
                      '--host 127.0.0.1 --port 8798 --comfyui http://127.0.0.1:8796',
                      '--state-dir', quote(root/'.runtime/mv-quality/proxy-state')])
    return {'listeners': [{'port': 8796, 'address': '127.0.0.1', 'pid': 101},
                           {'port': 8798, 'address': '127.0.0.1', 'pid': 201}],
            'processes': [
                {'pid': 101, 'parent': 100, 'name': 'python.exe', 'command': gpu, 'created': '2026-10-17T01:00:02Z'},
                {'pid': 100, 'parent': 1, 'name': 'pwsh.exe', 'command': 'pwsh.exe -File '+quote(root/'start-mv-gpu.ps1'), 'created': '2026-10-17T01:00:01Z'},
                {'pid': 201, 'parent': 200, 'name': 'python.exe', 'command': proxy, 'created': '2026-10-17T01:00:04Z'},
                {'pid': 200, 'parent': 1, 'name': 'powershell.exe', 'command': 'powershell.exe -File '+quote(root/'start-mv-quality-proxy.ps1'), 'created': '2026-10-17T01:00:03Z'}],
            'gpu': {'name': 'test-only GPU', 'driver': 'mock', 'free_vram_gib': 13., 'free_ram_gib': 20.,
                    'utilization': 0., 'compute_pids': [101]}}


class FakeJob:
    def __init__(self, graph, status='succeeded', failure=None):
        self.id = str(uuid.uuid4()); self.graph = graph; self.status = status; self.failure = failure

    async def wait(self, timeout):
        if self.failure: raise self.failure
        return self

    def get_outputs(self, node):
        if node != '13': raise AssertionError('wrong output node')
        async def stream(sink):
            from PIL import Image
            buffer = io.BytesIO()
            Image.new('RGB', (self.graph['10']['inputs']['width'], self.graph['10']['inputs']['height']), '#d96d62').save(buffer, format='PNG')
            return sink.write(buffer.getvalue())
        return [SimpleNamespace(name='server-original.png', to_stream=stream, size_bytes=0,
                                id='test-asset', node_id='13', job_id=self.id, type='image', content_type='image/png')]


class FakeClient:
    def __init__(self, status='succeeded', failure=None):
        self.submitted = []; self.status = status; self.failure = failure
        self.workflows = SimpleNamespace(from_json=lambda graph: SimpleNamespace(json=graph))

    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False

    async def submit(self, workflow, **kwargs):
        if 'nodes' in workflow.json: raise AssertionError('GUI graph')
        if 'metadata' in kwargs: raise AssertionError('proxy rejects mapping metadata')
        job = FakeJob(copy.deepcopy(workflow.json), self.status, self.failure)
        self.submitted.append((job, kwargs)); return job

    def history(self, url):
        job = next(job for job, _ in self.submitted if url.endswith(job.id))
        return {job.id: {'prompt': [1, job.id, job.graph, {}, ['13']],
                         'status': {'completed': True, 'status_str': 'success' if job.status == 'succeeded' else 'error'},
                         'outputs': {'13': {'images': [{'filename': 'server-original.png', 'type': 'output'}]}}}}


class QualityRunnerTests(unittest.TestCase):
    def test_dry_run_and_default_do_not_probe_write_or_import_sdk(self):
        for argv in ([], ['--dry-run'], ['--dry-run', '--stage', 'edit']):
            with patch.object(runner, 'snapshot', side_effect=AssertionError('resource probe')), \
                 patch.object(runner, 'local_json', side_effect=AssertionError('network')), \
                 patch.object(runner, 'make_client', side_effect=AssertionError('SDK')), \
                 patch.object(runner, 'save_json', side_effect=AssertionError('write')), redirect_stdout(io.StringIO()) as output:
                self.assertEqual(runner.main(argv), 0)
            value = json.loads(output.getvalue())
            self.assertTrue(value['dry_run']); self.assertFalse(value['gpu_used'])

    def test_budgets_gui_rejection_and_new_art_bible_match(self):
        heroes = runner.build_plan()
        self.assertEqual([item['seed'] for item in heroes], [7, 19])
        edits = runner.build_plan('edit')
        self.assertEqual([item['id'] for item in edits], ['S01_extract', 'S02_release', 'S03_door'])
        for item in heroes+edits:
            for token in ('black bob', 'ivory crescent', 'muted coral raincoat', 'ivory paper star'):
                self.assertIn(token, item['prompt'])
            self.assertNotIn('crimson ribbon', item['prompt'])
        for stage, count in (('hero', 3), ('hero', 0), ('edit', 4)):
            with self.assertRaises(ValueError): runner.build_plan(stage, count)
        with self.assertRaisesRegex(ValueError, 'flattened'):
            runner.validate_graph({'nodes': []}, 'hero')
        for mode in ('t2i', 'edit'):
            api = runner.read_json(PROJECT/f'workflows/mv-quality-{mode}.api.json')
            gui = runner.read_json(PROJECT/f'workflows/mv-quality-{mode}.gui.json')
            self.assertEqual(api['4']['inputs']['text'], next(node for node in gui['nodes'] if node['id'] == 4)['widgets_values'][0])
        self.assertEqual(edits[0]['prompt'], runner.read_json(PROJECT/'workflows/mv-quality-edit.api.json')['4']['inputs']['text'])

    def test_exact_listener_proof_rejects_wrong_owner_paths_upstream_and_pid_reuse(self):
        state = fake_state(PROJECT)
        proof = runner.process_proof(state)
        self.assertEqual(proof['8796']['python_pids'], [101])
        mutations = [lambda s: s['listeners'][0].update(address='0.0.0.0'),
                     lambda s: s['listeners'][0].update(pid=999),
                     lambda s: s['processes'][0].update(command=s['processes'][0]['command'].replace('--port 8796', '--port 8188')),
                     lambda s: s['processes'][0].update(command=s['processes'][0]['command'].replace(str(PROJECT/'user/gpu'), str(PROJECT/'user/other'))),
                     lambda s: s['processes'][2].update(command=s['processes'][2]['command'].replace('127.0.0.1:8796', '127.0.0.1:8188')),
                     lambda s: s['processes'][3].update(command='pwsh.exe -File C:/other/start-mv-quality-proxy.ps1'),
                     lambda s: s['processes'][1].update(created='2026-10-17T03:00:00Z'),
                     lambda s: s['processes'][0].update(created='2026-10-17T01:00:02')]
        for index, mutate in enumerate(mutations):
            altered = copy.deepcopy(state); mutate(altered)
            with self.subTest(mutation=index), self.assertRaises(RuntimeError): runner.process_proof(altered)

    def test_exact_graph_rejects_review_bypass_and_gui_drift(self):
        graph = runner.read_json(PROJECT/'workflows/mv-quality-t2i.api.json')
        def extra_scheduler(value):
            value['19'] = {'class_type': 'Flux2Scheduler', 'inputs': {'steps': 1000, 'width': 1024, 'height': 1024}}
            value['11']['inputs']['sigmas'] = ['19', 0]
        mutations = [extra_scheduler,
                     lambda value: value['11']['inputs'].update(sigmas=['10', 0]),
                     lambda value: value['1'].update(class_type='CLIPLoader'),
                     lambda value: value['9']['inputs'].update(hidden_extra_steps=1000),
                     lambda value: value['10']['inputs'].update(batch_size=True)]
        for index, mutate in enumerate(mutations):
            altered = copy.deepcopy(graph); mutate(altered)
            with self.subTest(mutation=index), self.assertRaisesRegex(ValueError, 'exact approved'):
                runner.validate_graph(altered, 'hero')
        edit = runner.read_json(PROJECT/'workflows/mv-quality-edit.api.json')
        edit['16']['inputs']['latent'] = ['10', 0]
        with self.assertRaisesRegex(ValueError, 'exact approved'): runner.validate_graph(edit, 'edit')
        original_read = runner.read_json
        def altered_gui(path):
            value = original_read(path)
            if str(path).endswith('.gui.json'): value['nodes'][8]['widgets_values'][0] = 1000
            return value
        with patch.object(runner, 'read_json', side_effect=altered_gui), self.assertRaisesRegex(ValueError, 'GUI widget'):
            runner.validate_gui_matches(graph, 'hero')

    def test_resources_allow_only_proven_own_cuda_pid_and_initial_free_window(self):
        state = fake_state(PROJECT); proof = runner.process_proof(state)
        runner.resource_guard(state, proof, True)
        state['gpu']['compute_pids'].append(999)
        with self.assertRaisesRegex(RuntimeError, 'Another GPU'): runner.resource_guard(state, proof, False)
        state['gpu']['compute_pids'] = [101]; state['gpu']['free_vram_gib'] = 2.
        with self.assertRaises(RuntimeError): runner.resource_guard(state, proof, True)
        runner.resource_guard(state, proof, False)  # Own resident weights, same proven idle server.
        state['gpu']['free_ram_gib'] = float('nan')
        with self.assertRaisesRegex(RuntimeError, 'telemetry'): runner.resource_guard(state, proof, False)

    def test_snapshot_is_bounded_utf8_read_only_process_inspection(self):
        state = fake_state(PROJECT)
        result = SimpleNamespace(returncode=0, stdout=json.dumps(state))
        with patch.object(runner.subprocess, 'run', return_value=result) as process:
            self.assertEqual(runner.snapshot(), state)
        args, kwargs = process.call_args
        self.assertEqual(args[0][:3], ['powershell.exe', '-NoProfile', '-NonInteractive'])
        self.assertEqual(kwargs['timeout'], 30); self.assertEqual(kwargs['encoding'], 'utf-8')
        with patch.object(runner.subprocess, 'run', return_value=SimpleNamespace(returncode=1)):
            with self.assertRaisesRegex(RuntimeError, 'inspection failed'): runner.snapshot()

    def test_proof_precedes_network_and_queue_busy_is_rejected(self):
        state = fake_state(PROJECT); state['listeners'][0]['pid'] = 777
        with patch.object(runner, 'snapshot', return_value=state), patch.object(runner, 'local_json') as network:
            with self.assertRaises(RuntimeError): runner.preflight()
            network.assert_not_called()
        with patch.object(runner, 'snapshot', return_value=fake_state(PROJECT)), \
             patch.object(runner, 'local_json', side_effect=[{'status': 'healthy', 'upstream': runner.COMFY}, {'queue_running': [[1]], 'queue_pending': []}]):
            with self.assertRaisesRegex(RuntimeError, 'busy'): runner.preflight()

    def test_direct_endpoint_and_reference_escape_rejected(self):
        with self.assertRaises(ValueError): runner.local_json('http://127.0.0.1:8188/queue')
        with self.assertRaises(ValueError): runner.owned(PROJECT/'output/mv-quality/../../outside', PROJECT/'output/mv-quality')


class QualityRunnerAsyncTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=PROJECT/'temp')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root/'workflows').mkdir()
        for path in (PROJECT/'workflows').glob('mv-quality*.json'):
            shutil.copyfile(path, self.root/'workflows'/path.name)
        root_patch = patch.object(runner, 'ROOT', self.root); root_patch.start(); self.addCleanup(root_patch.stop)
        self.state = fake_state(self.root)
        self.proof = runner.process_proof(self.state)
        self.checked = {'proof': self.proof, 'resources': self.state['gpu'], 'queue_idle': True}

    async def run_fake(self, client, stage='hero', reference=None, preflight=None):
        with patch.object(runner, 'preflight', side_effect=preflight or (lambda **kwargs: self.checked)), \
             patch.object(runner, 'load_provenance', return_value={'integrity': 'mock-only-no-model-executed'}), \
             patch.object(runner, 'make_client', new=AsyncMock(return_value=client)), \
             patch.object(runner, 'snapshot', return_value=self.state), \
             patch.object(runner, 'local_json', side_effect=getattr(client, 'history', lambda url: {})), redirect_stdout(io.StringIO()):
            return await runner.run_benchmark(stage=stage, reference=reference, resource_approved=True)

    def report(self):
        paths = list((self.root/'output/mv-quality/runs').glob('*/report.json'))
        self.assertEqual(len(paths), 1)
        return runner.read_json(paths[0]), paths[0].parent

    async def test_real_candidate_artifacts_history_hashes_and_two_seed_budget(self):
        client = FakeClient()
        await self.run_fake(client)
        report, directory = self.report()
        self.assertTrue(report['success']); self.assertEqual(len(client.submitted), 2)
        self.assertEqual([job.graph['6']['inputs']['noise_seed'] for job, _ in client.submitted], [7, 19])
        self.assertEqual(report['art_review'], 'awaiting_art_review')
        for item in report['candidates']:
            path = directory/item['id']
            graph = runner.read_json(path/'workflow.api.json')
            native = runner.read_json(path/'history.json')[item['workflow_id']]
            self.assertEqual(native['prompt'][2], graph)
            self.assertEqual(item['artifact']['sha256'], runner.sha256(path/'original.png'))
            self.assertEqual((item['artifact']['width'], item['artifact']['height']), (1024, 1024))
            self.assertTrue((path/'prompt.txt').is_file()); self.assertGreaterEqual(item['elapsed_seconds'], 0)

    async def test_failure_and_interruption_never_claim_success_or_cancel_any_job(self):
        client = FakeClient(failure=asyncio.CancelledError('unit-test interruption'))
        with self.assertRaises(asyncio.CancelledError): await self.run_fake(client)
        report, directory = self.report()
        self.assertFalse(report['success']); self.assertEqual(report['status'], 'interrupted')
        self.assertEqual(len(client.submitted), 1)
        self.assertTrue(report['candidates'][0]['job_may_continue'])
        self.assertTrue((directory/report['candidates'][0]['id']/'history.json').is_file())

    async def test_busy_preflight_preserves_failed_report_and_never_constructs_sdk(self):
        client = FakeClient()
        with self.assertRaisesRegex(RuntimeError, 'busy'):
            await self.run_fake(client, preflight=lambda **kwargs: (_ for _ in ()).throw(RuntimeError('busy')))
        report, _ = self.report()
        self.assertFalse(report['success']); self.assertEqual(client.submitted, [])

    async def test_failed_native_history_is_preserved_and_not_retried(self):
        client = FakeClient(status='failed')
        with self.assertRaisesRegex(RuntimeError, 'Candidate failed'): await self.run_fake(client)
        report, directory = self.report()
        self.assertFalse(report['success']); self.assertEqual(len(client.submitted), 1)
        self.assertFalse(report['candidates'][0]['job_may_continue'])
        self.assertTrue((directory/report['candidates'][0]['id']/'history.json').is_file())

    async def test_edit_is_separate_stage_and_uses_one_reviewed_reference_for_all_shots(self):
        from PIL import Image
        reference = self.root/'input/mv-quality/chosen.png'; reference.parent.mkdir(parents=True)
        Image.new('RGB', (1024, 1024), '#151a2a').save(reference)
        original_hash = runner.sha256(reference)
        client = FakeClient(); await self.run_fake(client, 'edit', reference)
        report, _ = self.report()
        self.assertEqual(len(client.submitted), 3); self.assertTrue(report['success'])
        references = {job.graph['14']['inputs']['image'] for job, _ in client.submitted}
        self.assertEqual(len(references), 1); self.assertEqual(runner.sha256(reference), original_hash)
        self.assertEqual(report['reference']['sha256'], original_hash)
        self.assertEqual([item['artifact']['height'] for item in report['candidates']], [576]*3)

    async def test_approval_and_edit_reference_are_required_before_any_resource_probe(self):
        with patch.object(runner, 'snapshot', side_effect=AssertionError('probe')):
            with self.assertRaisesRegex(RuntimeError, 'resource-approved'):
                await runner.run_benchmark()
            with self.assertRaisesRegex(ValueError, 'reference'):
                await runner.run_benchmark('edit', resource_approved=True)
            with self.assertRaisesRegex(ValueError, 'quality input/output'):
                await runner.run_benchmark('edit', reference=PROJECT/'README.md', resource_approved=True)

    @unittest.skipUnless(importlib.util.find_spec('comfy_sdk'), 'Pinned SDK unavailable')
    async def test_actual_sdk_wire_fixed_8798_api_graph_no_key_or_metadata(self):
        import httpx
        from comfy_sdk import ComfyError
        seen = []
        def terminal(request):
            seen.append((str(request.url), dict(request.headers), json.loads(request.content)))
            return httpx.Response(422, json={'error': {'code': 'invalid_request', 'message': 'test-terminal-no-real-submit'}})
        client = await runner.make_client(transport=httpx.MockTransport(terminal))
        self.assertEqual(client._low.base_url, runner.PROXY)
        self.assertFalse(client._low.authenticated)
        with self.assertRaises(ComfyError): await self.run_fake(client)
        self.assertEqual(len(seen), 1)
        url, headers, body = seen[0]
        self.assertEqual(url, runner.PROXY+'/api/v2/jobs')
        self.assertNotIn('metadata', body); self.assertNotIn('authorization', headers)
        self.assertEqual(body['workflow']['13']['class_type'], 'SaveImage')
        self.assertNotIn('nodes', body['workflow'])
        report, _ = self.report(); self.assertFalse(report['success'])

    @unittest.skipUnless(importlib.util.find_spec('comfy_sdk'), 'Pinned SDK unavailable')
    async def test_actual_sdk_429_cannot_retry_or_send_another_post(self):
        import httpx
        seen = []
        def full_queue(request):
            seen.append(request)
            return httpx.Response(429, headers={'Retry-After': '0'}, json={'error': {'code': 'queue_full', 'message': 'mock queue full'}})
        client = await runner.make_client(transport=httpx.MockTransport(full_queue))
        with patch('asyncio.sleep', new=AsyncMock(side_effect=AssertionError('SDK retry sleep forbidden'))):
            with self.assertRaisesRegex(runner.SubmissionRefused, '429'): await self.run_fake(client)
        self.assertEqual(len(seen), 1)
        report, _ = self.report()
        self.assertFalse(report['success']); self.assertEqual(report['actual_submission_wire_posts'], 1)
        self.assertEqual(report['candidates'][0]['submission_wire_posts'], 1)
        self.assertEqual(report['retry_policy']['submission_wire_post_limit_per_candidate'], 1)

    @unittest.skipUnless(importlib.util.find_spec('comfy_sdk'), 'Pinned SDK unavailable')
    async def test_actual_sdk_bounded_stream_and_fixed_artifact_origin(self):
        import httpx
        from comfy_low.models import Output
        from comfy_sdk.outputs import AsyncOutput
        from PIL import Image
        job_id = str(uuid.uuid4()); asset_id = 'pinned-test-asset'
        png = io.BytesIO(); Image.new('RGB', (4, 4), 'red').save(png, format='PNG')
        payload = png.getvalue(); requests = []; mode = ['normal']; streamed = []
        class Chunks(httpx.AsyncByteStream):
            async def __aiter__(self):
                for index in range(100):
                    streamed.append(index); yield b'x'*32
            async def aclose(self): pass
        def serve(request):
            requests.append(str(request.url))
            if mode[0] == 'large': return httpx.Response(200, stream=Chunks())
            if mode[0] == 'redirect': return httpx.Response(302, headers={'Location': 'http://127.0.0.1:8188/view'})
            return httpx.Response(200, content=payload, headers={'Content-Type': 'image/png'})
        client = await runner.make_client(transport=httpx.MockTransport(serve))
        client._quality_transport.pin_job(job_id)
        output = AsyncOutput(Output.model_validate({'node_id': '13', 'name': 'original.png', 'type': 'image',
                             'content_type': 'image/png', 'size_bytes': 0, 'id': asset_id, 'job_id': job_id,
                             'hash': None, 'url_expires_at': '2099-01-01T00:00:00Z',
                             'url': runner.PROXY+'/api/v2/assets/'+asset_id+'/content'}), client._low)
        folder = self.root/'output/mv-quality'; folder.mkdir(parents=True)
        try:
            result = await runner.download_png(output, folder/'original.png', job_id, client, (4, 4))
            self.assertEqual((folder/'original.png').read_bytes(), payload)
            self.assertEqual(result['bytes'], len(payload)); self.assertEqual(result['size_metadata'], 'unknown_proxy_size')
            mode[0] = 'large'
            with patch.object(runner, 'MAX_PNG', 64), self.assertRaisesRegex(RuntimeError, 'streaming byte limit'):
                await runner.download_png(output, folder/'large.png', job_id, client, (4, 4))
            self.assertLessEqual((folder/'large.png.partial').stat().st_size, 64)
            self.assertEqual(len(streamed), 3)  # Stops reading immediately, not at EOF.
            self.assertFalse((folder/'large.png').exists())
            output._model.size_bytes = runner.MAX_PNG+1
            count = len(requests)
            with self.assertRaisesRegex(RuntimeError, 'size metadata'):
                await runner.download_png(output, folder/'declared-large.png', job_id, client, (4, 4))
            self.assertEqual(len(requests), count)
            with self.assertRaisesRegex(RuntimeError, 'origin'):
                await client._low._client.get('http://127.0.0.1:8188/view')
            self.assertEqual(len(requests), count)
            with self.assertRaisesRegex(RuntimeError, 'unpinned'):
                await client._low._client.get(runner.PROXY+'/api/v2/assets/another-asset/content')
            self.assertEqual(len(requests), count)
            mode[0] = 'redirect'
            with self.assertRaisesRegex(RuntimeError, 'redirects'):
                await client._low._client.get(runner.PROXY+'/api/v2/assets/'+asset_id+'/content')
            self.assertEqual(len(requests), count+1)
            sink = io.BytesIO(); writer = runner.BoundedWriter(sink, 8); writer.write(b'12345678')
            with self.assertRaisesRegex(RuntimeError, 'streaming write limit'): writer.write(b'9')
            self.assertEqual(sink.getvalue(), b'12345678')
        finally:
            await client.aclose()


if __name__ == '__main__': unittest.main()
