"""Behavior checks for real timing, silence, pitch and bounded input handling."""
import json
import math
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import numpy as np
from voice_motion.analysis import AnalysisError, analyze
from voice_motion.model import ModelUnavailableError, model_info


class AnalysisTests(unittest.TestCase):
    rate = 16000

    def assert_contract(self, result):
        frames = result["frames"]
        self.assertTrue(frames)
        self.assertEqual(result["version"], 1)
        self.assertAlmostEqual(frames[-1]["time"], result["duration"], places=5)
        previous_time = previous_progress = -1
        for frame in frames:
            for name, value in frame.items():
                self.assertTrue(math.isfinite(value), (name, value))
                if name != "time":
                    self.assertGreaterEqual(value, 0)
                    self.assertLessEqual(value, 1)
            self.assertGreater(frame["time"], previous_time)
            self.assertGreaterEqual(frame["progress"], previous_progress)
            previous_time, previous_progress = frame["time"], frame["progress"]
        json.dumps(result, allow_nan=False)

    def test_silence_empty_and_low_noise_do_not_move(self):
        rng = np.random.default_rng(8)
        for samples in ([], [0.0] * self.rate, (rng.normal(0, .002, self.rate)).tolist()):
            with self.subTest(length=len(samples)):
                result = analyze(samples, self.rate, engine="signal")
                self.assert_contract(result)
                self.assertEqual(result["events"], [])
                self.assertTrue(all(f["energy"] == f["progress"] == 0 for f in result["frames"]))
                self.assertTrue(all(f["pitch"] == .5 and f["pitch_confidence"] == 0 for f in result["frames"]))

    def test_tone_pitch_and_silence_holds(self):
        time = np.arange(self.rate * 3) / self.rate
        samples = np.zeros_like(time)
        for start, end in ((.25, .9), (1.7, 2.35)):
            mask = (time >= start) & (time < end)
            samples[mask] = .3 * np.sin(2 * np.pi * 220 * time[mask])
        result = analyze(samples, self.rate, engine="signal")
        self.assert_contract(result)
        voiced = [f for f in result["frames"] if .4 < f["time"] < .8]
        self.assertGreater(np.mean([f["pitch_confidence"] for f in voiced]), .7)
        expected = math.log2(220 / 70) / math.log2(1000 / 70)
        self.assertAlmostEqual(np.mean([f["pitch"] for f in voiced]), expected, delta=.025)
        held = [f for f in result["frames"] if 1.1 < f["time"] < 1.5]
        self.assertGreater(held[0]["progress"], 0)
        self.assertEqual(len(set(f["progress"] for f in held)), 1)
        self.assertTrue(all(f["energy"] == 0 for f in held))
        last_voiced = [f for f in result["frames"] if f["energy"] > 0][-1]
        self.assertEqual(last_voiced["progress"], 1)

    def test_impulse_times_and_minimum_spacing(self):
        samples = np.zeros(self.rate * 2)
        for time in (.4, .43, 1.2):
            samples[round(time * self.rate)] = 1
        result = analyze(samples, self.rate, engine="signal", smoothing=0)
        self.assert_contract(result)
        self.assertEqual(len(result["events"]), 2)
        self.assertAlmostEqual(result["events"][0]["time"], .4, delta=.04)
        self.assertAlmostEqual(result["events"][1]["time"], 1.2, delta=.025)
        self.assertGreaterEqual(result["events"][1]["time"] - result["events"][0]["time"], .14)

    def test_sample_rate_preserves_timing(self):
        for rate in (8000, 22050, 48000):
            samples = np.zeros(rate)
            samples[round(rate * .6)] = .9
            result = analyze(samples, rate, engine="signal")
            self.assertAlmostEqual(result["events"][0]["time"], .6, delta=.02)

    def test_adaptive_floor_rejects_quiet_noise_but_detects_voice(self):
        rng = np.random.default_rng(12)
        time = np.arange(self.rate * 2) / self.rate
        samples = rng.normal(0, .012, len(time))
        active = (time > .6) & (time < 1.4)
        samples[active] += .22 * np.sin(2 * np.pi * 280 * time[active])
        result = analyze(samples, self.rate, engine="signal")
        outside = [f for f in result["frames"] if f["time"] < .4 or f["time"] > 1.6]
        self.assertTrue(all(f["energy"] == 0 for f in outside))
        self.assertTrue(any(f["energy"] > .5 for f in result["frames"]))

    def test_invalid_inputs_and_clipping(self):
        invalid = [([float("nan")], 16000, {}), ([float("inf")], 16000, {}),
                   ([True], 16000, {}), ([[.1]], 16000, {}), ([.1], True, {}),
                   ([.1], 16000.0, {}), ([.1], 7999, {}), ([.1], 96001, {}),
                   ([.1], 16000, {"engine": "remote"}), ([.1], 16000, {"sensitivity": float("nan")}),
                   ([.1], 16000, {"smoothing": 1.01}), ([0] * 120001, 8000, {})]
        for samples, rate, kwargs in invalid:
            with self.subTest(rate=rate, kwargs=kwargs):
                with self.assertRaises(AnalysisError):
                    analyze(samples, rate, engine=kwargs.pop("engine", "signal"), **kwargs)
        result = analyze([5, -5] * 100, 8000, engine="signal")
        self.assertTrue(result["warnings"])
        self.assert_contract(result)

    def test_missing_model_auto_is_explicit_and_explicit_model_fails(self):
        with patch.dict(os.environ, {"VOICE_MOTION_MODEL": str(ROOT / ".runtime/models/nonexistent-test.onnx")}):
            result = analyze([0.] * 1000, 16000, engine="auto")
            self.assertEqual(result["engine"], "signal")
            self.assertTrue(result["warnings"])
            with self.assertRaises(ModelUnavailableError):
                analyze([0.] * 1000, 16000, engine="basic_pitch")

    def test_note_onsets_without_energy_attacks_cannot_create_impacts(self):
        # Deliberately adversarial model: every frame claims a note onset. This
        # must not produce impacts while the physical envelope stays steady.
        duration = 3
        time = np.arange(self.rate * duration) / self.rate
        frequency = 130 + 160 * time
        phase = 2 * np.pi * np.cumsum(frequency) / self.rate
        envelope = np.clip((time - .2) / .06, 0, 1) * np.clip((2.7 - time) / .10, 0, 1)
        audio = .3 * envelope * (np.sin(phase) + .15 * np.sin(2 * phase))
        frames = duration * 60 + 1
        fake_output = (np.linspace(.2, .8, frames), np.ones(frames) * .9, np.ones(frames))
        with patch("voice_motion.analysis.infer_pitch", return_value=fake_output):
            result = analyze(audio, self.rate, engine="basic_pitch")
        self.assertEqual(result["engine"], "basic_pitch")
        self.assertLessEqual(len(result["events"]), 1)
        self.assertTrue(all(event["time"] < .35 for event in result["events"]))
        voiced = [frame["pitch"] for frame in result["frames"] if .5 < frame["time"] < 2.4]
        self.assertGreater(max(voiced) - min(voiced), .3)
        self.assert_contract(result)

    @unittest.skipUnless(model_info()["available"], "optional real model not installed")
    def test_real_model_glissando_does_not_turn_note_changes_into_impacts(self):
        for rate, amplitude, start_pitch, end_pitch in ((16000, .12, 110, 540), (22050, .42, 480, 145)):
            with self.subTest(rate=rate, amplitude=amplitude):
                time = np.arange(rate * 3) / rate
                frequency = start_pitch + (end_pitch - start_pitch) * np.clip((time - .2) / 2.5, 0, 1)
                phase = 2 * np.pi * np.cumsum(frequency) / rate
                envelope = np.clip((time - .2) / .07, 0, 1) * np.clip((2.7 - time) / .10, 0, 1)
                audio = amplitude * envelope * (np.sin(phase) + .2 * np.sin(2 * phase))
                result = analyze(audio, rate, engine="basic_pitch")
                self.assertLessEqual(len(result["events"]), 1)
                self.assertTrue(all(event["time"] < .4 for event in result["events"]))
                voiced = [f["pitch"] for f in result["frames"] if .6 < f["time"] < 2.3 and f["pitch_confidence"] > .2]
                self.assertTrue(voiced)
                self.assertGreater(max(voiced) - min(voiced), .15)
                self.assert_contract(result)

    @unittest.skipUnless(model_info()["available"], "optional real model not installed")
    def test_model_glide_with_percussive_hits_preserves_real_hit_times(self):
        # Hits have different times, levels and envelopes from the integration
        # fixture, and include one superimposed on a sustained pitched signal.
        rate = 22050
        time = np.arange(rate * 4) / rate
        phase = 2 * np.pi * (170 * time + 35 * time ** 2)
        envelope = np.clip((time - .2) / .06, 0, 1) * np.clip((2.5 - time) / .1, 0, 1)
        audio = .13 * envelope * np.sin(phase)
        rng = np.random.default_rng(191)
        for hit in (1.3, 3.1):
            age = time - hit
            mask = (age >= 0) & (age < .08)
            audio[mask] += .65 * np.exp(-age[mask] * 55) * rng.uniform(-1, 1, mask.sum())
        result = analyze(audio, rate, engine="basic_pitch")
        self.assertTrue(any(abs(e["time"] - 1.3) < .035 for e in result["events"]))
        self.assertTrue(any(abs(e["time"] - 3.1) < .035 for e in result["events"]))
        self.assertTrue(all(e["time"] < .4 or abs(e["time"] - 1.3) < .05 or abs(e["time"] - 3.1) < .05 for e in result["events"]))
        self.assert_contract(result)

    @unittest.skipUnless(model_info()["available"], "optional real model not installed")
    def test_installed_open_model_runs_real_cpu_inference(self):
        time = np.arange(22050) / 22050
        result = analyze(.3 * np.sin(2 * np.pi * 440 * time), 22050, engine="basic_pitch")
        self.assertEqual(result["engine"], "basic_pitch")
        self.assert_contract(result)
        self.assertTrue(any(f["pitch_confidence"] > 0 for f in result["frames"]))
        self.assertEqual(model_info()["provider"], "CPUExecutionProvider")


if __name__ == "__main__":
    unittest.main()
