import sys
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from voice_motion.mv_lyrics import group_words


class LyricTimingTests(unittest.TestCase):
    def test_alignment_preserves_short_recognized_words(self):
        segments=group_words([{'text':'a','start_time':1,'end_time':1},
                              {'text':'light','start_time':1.1,'end_time':1.5}],3)
        self.assertEqual(segments[0]['text'],'a light')
        self.assertGreater(segments[0]['words'][0]['end'],1)
    def test_actual_long_pause_becomes_separate_line(self):
        segments=group_words([{'text':'빛','start':.1,'end':.3},
                              {'text':'돌아와','start':2,'end':2.8}],3)
        self.assertEqual(len(segments),2)
        self.assertEqual(segments[-1]['end'],2.8)


if __name__=='__main__':unittest.main()
