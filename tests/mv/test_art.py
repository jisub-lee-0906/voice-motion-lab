import unittest

import numpy as np
from PIL import ImageChops

from voice_motion.mv_art import MVArtRenderer, _fit_lines, font_provenance
from voice_motion.mv_engine import direct_project


class ArtTests(unittest.TestCase):
    def test_consistent_deterministic_nonempty_scene_and_motion(self):
        t=np.arange(16000*12)/16000
        audio=(.2*np.sin(t*440*2*np.pi)).astype(np.float32)
        tr={"engine":"provided_lyrics","segments":[{"start":0,"end":6,"text":"꽃이 피어오르는 밤"},{"start":6,"end":12,"text":"깨진 기억 위로 달이 돌아"}]}
        renderer=MVArtRenderer(direct_project(audio,16000,tr))
        a=renderer.render(1.2); b=renderer.render(1.2); c=renderer.render(1.8)
        self.assertEqual(a.size,(960,540))
        self.assertEqual(a.mode,"RGB")
        self.assertIsNone(ImageChops.difference(a,b).getbbox())
        self.assertIsNotNone(ImageChops.difference(a,c).getbbox())
        self.assertGreater(float(np.std(np.asarray(a))),30)

    def test_all_compositions_render_with_original_character(self):
        project=direct_project(np.ones(16000*30,dtype=np.float32)*.01,16000,{"engine":"asr","segments":[]})
        renderer=MVArtRenderer(project)
        for shot in project["shots"]:
            image=renderer.render((shot["start"]+shot["end"])/2)
            self.assertEqual(image.size,(960,540))
            self.assertGreater(len(image.getcolors(1000000)),100)

    def test_korean_typography_wrap_and_provenance(self):
        text="깨진 기억 사이로 작은 꽃이 다시 피어오르는 밤"
        lines,size=_fit_lines(text,max_width=500)
        self.assertEqual("".join(lines).replace(" ",""),text.replace(" ",""))
        self.assertLessEqual(len(lines),2)
        self.assertGreaterEqual(size,20)
        self.assertFalse(font_provenance()["redistributed"])


if __name__=="__main__": unittest.main()
