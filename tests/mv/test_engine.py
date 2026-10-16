import json
import math
import tempfile
import unittest
import wave
from pathlib import Path

import av
import numpy as np

from voice_motion.mv_engine import analyze_music, create_music_video, direct_project, normalize_transcript, semantic_theme


def pulse_audio(duration=12,rate=16000,bpm=120):
    samples=np.zeros(round(duration*rate),dtype=np.float32)
    for beat in np.arange(.25,duration,60/bpm):
        start=round(beat*rate); n=min(round(.08*rate),len(samples)-start)
        t=np.arange(n)/rate
        samples[start:start+n]+=.7*np.sin(2*np.pi*180*t)*np.exp(-t*45)
    return samples


class MusicDirectingTests(unittest.TestCase):
    def test_detects_actual_pulses_and_silence(self):
        music=analyze_music(pulse_audio(),16000)
        detected=[b["time"] for b in music["beats"]]
        self.assertGreater(len(detected),15)
        self.assertTrue(all(min(abs(t-beat) for t in detected)<.07 for beat in (1.25,2.25,3.25)))
        self.assertAlmostEqual(music["tempo_bpm"],120,delta=12)
        silence=analyze_music(np.zeros(32000),16000)
        self.assertTrue(silence["silence"])
        self.assertEqual(silence["beats"],[])
        self.assertTrue(all(f["energy"]==0 for f in silence["frames"]))

    def test_lyrics_change_direction_without_canned_text(self):
        audio=pulse_audio()
        tr={"engine":"test_real_transcript","text":"the flowers bloom, my heart is broken, remember the moon",
            "segments":[{"start":0,"end":4,"text":"the flowers bloom"},
                        {"start":4,"end":8,"text":"my heart is broken"},
                        {"start":8,"end":12,"text":"remember the moon"}]}
        project=direct_project(audio,16000,tr)
        self.assertEqual(project["transcript"]["segments"][0]["text"],"the flowers bloom")
        self.assertIn("bloom",{s["theme"] for s in project["shots"]})
        self.assertIn("fracture",{s["theme"] for s in project["shots"]})
        self.assertIn("orbit",{s["theme"] for s in project["shots"]})
        self.assertGreaterEqual(len({s["composition"] for s in project["shots"]}),3)
        self.assertEqual(project["shots"][0]["start"],0)
        self.assertEqual(project["shots"][-1]["end"],12)
        self.assertTrue(all(a["end"]==b["start"] for a,b in zip(project["shots"],project["shots"][1:])))
        self.assertEqual(project["engines"]["art"],"original_authored_procedural_2d_v1")
        json.dumps(project,allow_nan=False)

    def test_unknown_and_empty_lyrics_are_not_fabricated(self):
        project=direct_project(pulse_audio(6),16000,{"engine":"asr","segments":[]})
        self.assertEqual(project["transcript"]["segments"],[])
        self.assertTrue(all(s["lyric"]=="" for s in project["shots"]))
        self.assertTrue(any("No transcribed lyrics" in w for w in project["warnings"]))
        theme,scores=semantic_theme("unseen words xyz qwerty")
        self.assertEqual(theme,"orbit")
        self.assertFalse(any(scores.values()))

    def test_timing_and_audio_validation(self):
        with self.assertRaises(ValueError):
            normalize_transcript({"segments":[{"text":"hello","start":math.nan,"end":2}]},3)
        with self.assertRaises(ValueError):
            analyze_music(np.array([0,float("nan")]),16000)
        with self.assertRaises(ValueError):
            direct_project(np.ones(100),16000,{"segments":[]})
        with self.assertRaises(ValueError):
            direct_project(pulse_audio(1),16000,{"segments":[]},theme="missing")

    def test_determinism_and_unseen_korean_lyrics(self):
        tr={"engine":"provided_lyrics","timing_source":"estimated","segments":[
            {"start":0,"end":3,"text":"차가운 유리 위로 꽃이 피어"},{"start":3,"end":6,"text":"깨진 기억 속 작은 달"}]}
        a=direct_project(pulse_audio(6),16000,tr,seed=19)
        b=direct_project(pulse_audio(6),16000,tr,seed=19)
        self.assertEqual(a,b)
        self.assertEqual(a["transcript"]["timing_source"],"estimated")
        self.assertTrue(all("timing_source" in segment for segment in a["transcript"]["segments"]))

    def test_refrain_requires_a_new_lyric_occurrence_and_long_timing_is_disclosed(self):
        text="작은 꽃이 피어나"
        tr={"engine":"asr","segments":[{"start":0,"end":8,"text":text},
            {"start":8,"end":10,"text":"검은 밤을 건너"},{"start":10,"end":16,"text":text}]}
        project=direct_project(pulse_audio(16),16000,tr)
        self.assertTrue(all(s["lyric_repetition"]==0 for s in project["shots"] if s["end"]<=8))
        self.assertTrue(any(s["lyric_repetition"]>0 for s in project["shots"] if s["start"]>=10))
        long_text="꽃이 피어나 별이 돌아오는 밤 "*16
        normalized=normalize_transcript({"engine":"asr","segments":[{"start":0,"end":16,"text":long_text}]},16)
        self.assertGreater(len(normalized["segments"]),1)
        self.assertTrue(all(s["timing_source"].startswith("estimated_text_split") for s in normalized["segments"]))
        self.assertEqual("".join(s["text"] for s in normalized["segments"]).replace(" ",""),long_text.replace(" ",""))
        self.assertEqual(normalized["segments"][-1]["end"],16)

    def test_real_tiny_mp4_contains_original_audio_and_safe_project(self):
        temp_root=Path(__file__).resolve().parents[2]/".runtime"/"temp"
        temp_root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as temp:
            folder=Path(temp); source=folder/"private-input.wav"
            rate=16000; t=np.arange(9600)/rate
            samples=(np.sin(2*np.pi*440*t)*.3*32767).astype("<i2")
            with wave.open(str(source),"wb") as wav:
                wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(rate); wav.writeframes(samples.tobytes())
            events=[]
            result=create_music_video(source,folder,{"engine":"test","segments":[{"start":0,"end":.6,"text":"별이 피어"}]},progress=lambda p,m:events.append(p))
            self.assertTrue(Path(result["video_path"]).is_file())
            self.assertEqual(events[-1],1)
            self.assertEqual(events,sorted(events))
            with av.open(result["video_path"]) as clip:
                self.assertEqual(clip.streams.video[0].width,960)
                self.assertEqual(clip.streams.video[0].height,540)
                self.assertAlmostEqual(clip.duration/av.time_base,.625,delta=.08)
                frames=list(clip.decode(audio=0))
                decoded=np.concatenate([f.to_ndarray() for f in frames],axis=1).mean(axis=0)
                self.assertGreater(float(np.std(decoded)),.1)
            text=Path(result["project_path"]).read_text(encoding="utf-8")
            self.assertNotIn(str(folder),text)
            self.assertNotIn("private-input.wav",text)
            self.assertNotIn('"samples"',text)
            self.assertFalse(json.loads(text)["font"]["redistributed"])


if __name__=="__main__": unittest.main()
