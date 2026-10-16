"""Boundary tests for the independent output checker; no inference or server."""
import importlib.util
from pathlib import Path
import unittest

import numpy as np

SPEC=importlib.util.spec_from_file_location('inspect_mv_video',Path(__file__).resolve().parents[2]/'tools/inspect_mv_video.py')
CHECKER=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


class InspectOutputTests(unittest.TestCase):
    def test_alignment_recovers_positive_and_negative_delays(self):
        source=np.random.default_rng(7).normal(0,.1,32000)
        for lag in (-1300,0,700):
            if lag>=0: output=np.r_[np.zeros(lag),source]
            else: output=np.r_[source[-lag:],np.zeros(-lag)]
            result=CHECKER.compare_audio(source,output)
            self.assertEqual(result['best_lag_samples'],lag)
            self.assertGreater(result['correlation'],.999)

    def test_unrelated_audio_is_not_a_source_match(self):
        rng=np.random.default_rng(3)
        result=CHECKER.compare_audio(rng.normal(size=16000),rng.normal(size=16000))
        self.assertLess(result['correlation'],.1)

    def test_timestamps_contribute_to_actual_offset(self):
        source=np.random.default_rng(3).normal(size=16000)
        result=CHECKER.compare_audio(source,source,output_start=.4)
        self.assertAlmostEqual(result['timestamp_adjusted_offset_seconds'],.4)

    def test_shot_coverage_rejects_gaps_overlaps_and_host_paths(self):
        project={'format':'voice-motion-mv-project','duration':3,'shots':[{'start':0,'end':1},{'start':1,'end':3}]}
        failures=[]
        CHECKER.inspect_project(project,failures)
        self.assertEqual(failures,[])
        project['shots'][1]['start']=1.2
        CHECKER.inspect_project(project,failures)
        self.assertIn('Shot coverage contains gaps',failures)
        project['shots'][1]['start']=.8
        project['font']={'path':'C:\\Users\\private\\font.ttf'}
        failures=[]
        CHECKER.inspect_project(project,failures)
        self.assertIn('Shots are out of order or overlap',failures)
        self.assertIn('Directing JSON contains absolute host filesystem paths',failures)

    def test_host_path_scanner_keeps_relative_artifacts_and_normal_lyrics(self):
        self.assertEqual(CHECKER.host_path_locations({'video':'video.mp4','lyric':'Round / round'}),[])
        self.assertEqual(CHECKER.host_path_locations({'file':'/home/me/private.wav'}),['$.file'])


if __name__=='__main__':
    unittest.main()
