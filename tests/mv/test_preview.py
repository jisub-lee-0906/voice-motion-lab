import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

import av
import numpy as np

from voice_motion.mv_preview import PROJECT_ROOT, create_web_preview


def tiny_master(path):
    rate=48000; duration=.75
    with av.open(str(path),"w") as output:
        video=output.add_stream("libx264",rate=24); video.width=160; video.height=90; video.pix_fmt="yuv420p"
        video.codec_context.thread_count=2
        audio=output.add_stream("aac",rate=rate); audio.layout="stereo"; audio.codec_context.thread_count=1
        samples=(np.sin(np.arange(round(rate*duration))/rate*440*2*np.pi)*.2).astype(np.float32)
        for index in range(18):
            pixels=np.zeros((90,160,3),np.uint8); pixels[:,index*7:index*7+22,:]=[220,70,90]
            frame=av.VideoFrame.from_ndarray(pixels,format="rgb24"); frame.pts=index; frame.time_base=Fraction(1,24)
            for packet in video.encode(frame): output.mux(packet)
        for index in range(0,len(samples),1024):
            data=np.ascontiguousarray(np.tile(samples[index:index+1024],(2,1)))
            frame=av.AudioFrame.from_ndarray(data,format="fltp",layout="stereo"); frame.sample_rate=rate
            frame.pts=index; frame.time_base=Fraction(1,rate)
            for packet in audio.encode(frame): output.mux(packet)
        for packet in video.encode(None): output.mux(packet)
        for packet in audio.encode(None): output.mux(packet)


class PreviewTests(unittest.TestCase):
    def test_real_streaming_webm_preserves_motion_audio_duration_and_seek(self):
        root=PROJECT_ROOT/".runtime"/"temp"; root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temp:
            folder=Path(temp); source=folder/"master.mp4"; target=folder/"preview.webm"
            tiny_master(source); original=source.read_bytes(); progress=[]
            result=create_web_preview(source,target,lambda value,message:progress.append(value))
            self.assertEqual(source.read_bytes(),original)
            self.assertEqual(result["video_codec"],"vp9"); self.assertEqual(result["audio_codec"],"opus")
            self.assertEqual(result["frames"],18); self.assertEqual(result["audio_samples"],36000)
            self.assertEqual(progress[-1],1.)
            with av.open(str(target)) as clip:
                self.assertEqual(clip.streams.video[0].codec_context.name,"vp9")
                self.assertEqual(clip.streams.audio[0].codec_context.name,"opus")
                self.assertAlmostEqual(clip.duration/av.time_base,.75,delta=.06)
                decoded=list(clip.decode(video=0))
                self.assertEqual(len(decoded),18)
                self.assertGreater(float(np.mean(np.abs(decoded[0].to_ndarray(format="rgb24").astype(float)-decoded[-1].to_ndarray(format="rgb24").astype(float)))),5)
            with av.open(str(target)) as clip:
                data=np.concatenate([f.to_ndarray() for f in clip.decode(audio=0)],axis=1)
                self.assertGreater(float(np.std(data)),.08)
            with av.open(str(target)) as clip:
                clip.seek(round(.4*av.time_base))
                self.assertTrue(any(True for frame in clip.decode(video=0)))

    def test_invalid_target_rejected_before_touching_master(self):
        root=PROJECT_ROOT/".runtime"/"temp"; root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temp:
            source=Path(temp)/"master.mp4"; tiny_master(source)
            with self.assertRaises(ValueError): create_web_preview(source,source)
            with self.assertRaises(ValueError): create_web_preview(source,Path(temp)/"bad.mp4")


if __name__=="__main__": unittest.main()
