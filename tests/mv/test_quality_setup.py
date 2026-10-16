"""No network, package install, Torch import or GPU use in these source checks."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('_quality_setup_test',ROOT/'tools/setup_mv_quality.py')
setup=importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class QualityPreparationTests(unittest.TestCase):
    def test_status_never_writes_or_downloads(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'temp') as directory:
            missing=Path(directory)/'not-created'
            with patch.object(setup,'RUNTIME',missing),patch.object(setup.urllib.request,'urlopen',side_effect=AssertionError('network')):
                value=setup.status()
            self.assertFalse(missing.exists())
            self.assertFalse(value['all_assets_present'])
            self.assertEqual(value['weights_bytes'],12451817860)
            self.assertEqual(value['benchmark_hero_seeds'],[7,19])

    def test_approved_paths_refuse_traversal(self):
        with self.assertRaises(RuntimeError):setup.destination('../../outside')

    def test_api_and_gui_graphs_match_real_link_wiring(self):
        for mode in ('t2i','edit'):
            api=json.loads((ROOT/f'workflows/mv-quality-{mode}.api.json').read_text())
            gui=json.loads((ROOT/f'workflows/mv-quality-{mode}.gui.json').read_text())
            nodes={str(node['id']):node for node in gui['nodes']}
            links={link[0]:link for link in gui['links']}
            self.assertEqual(set(api),set(nodes))
            for node_id,node in api.items():
                self.assertEqual(node['class_type'],nodes[node_id]['type'])
                for name,value in node['inputs'].items():
                    if isinstance(value,list):
                        entry=next(item for item in nodes[node_id]['inputs'] if item['name']==name)
                        link=links[entry['link']]
                        self.assertEqual((str(link[1]),link[2],str(link[3])),(value[0],value[1],node_id))
            self.assertEqual(api['9']['inputs']['steps'],4)
            self.assertEqual(api['7']['inputs']['cfg'],1)
            self.assertEqual(api['10']['inputs']['batch_size'],1)
            self.assertEqual(api['6']['inputs']['noise_seed'],7)
            self.assertFalse(gui['extra']['voice_motion_quality']['gpu_tested'])
        self.assertEqual(api['7']['inputs']['positive'],['16',0])
        self.assertEqual(api['7']['inputs']['negative'],['17',0])
        self.assertEqual(api['16']['class_type'],'ReferenceLatent')
        self.assertEqual(api['17']['class_type'],'ReferenceLatent')

    def test_benchmark_is_bounded_and_has_no_automatic_gpu_execution(self):
        plan=json.loads((ROOT/'workflows/mv-quality-benchmark.json').read_text())
        self.assertEqual([item['seed'] for item in plan['hero_candidates']],[7,19])
        self.assertEqual(plan['max_hero_candidates'],2)
        self.assertEqual(plan['automatic_retries'],0)
        self.assertEqual(plan['edit']['max_shots'],3)
        self.assertFalse(plan['resource_gate']['existing_processes_may_be_stopped'])
        self.assertTrue(plan['resource_gate']['explicit_approval'])


if __name__=='__main__':unittest.main()
