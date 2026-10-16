import importlib.util
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from voice_motion import mv_illustration as illustration


CONFIG={"beta_schedule":"scaled_linear","prediction_type":"epsilon","num_train_timesteps":1000,
        "original_inference_steps":50,"timestep_scaling":10.0,"clip_sample":False,"thresholding":False,
        "beta_start":.00085,"beta_end":.012,"trained_betas":None,"rescale_betas_zero_snr":False}


class IllustrationTests(unittest.TestCase):
    def test_pinned_lcm_schedule_and_guidance(self):
        scheduler=illustration.NumpyLCMScheduler(CONFIG)
        self.assertEqual(scheduler.timesteps.tolist(),[999,759,499,259])
        self.assertTrue(np.all(np.diff(scheduler.alphas)<0))
        self.assertAlmostEqual(float(scheduler.alphas[0]),.99915,places=6)
        guidance=illustration.guidance_embedding(0.)
        self.assertEqual(guidance.shape,(1,256))
        self.assertTrue(np.all(guidance[0,:128]==0))
        self.assertTrue(np.all(guidance[0,128:]==1))
        nonzero=illustration.guidance_embedding(7.)
        self.assertEqual(nonzero.dtype,np.float32)
        self.assertTrue(np.all(np.isfinite(nonzero)))

    def test_exactly_four_steps_and_no_unknown_scheduler_silently_accepted(self):
        with self.assertRaises(ValueError): illustration.NumpyLCMScheduler(CONFIG,steps=8)
        with self.assertRaises(ValueError): illustration.NumpyLCMScheduler({**CONFIG,"prediction_type":"v_prediction"})
        with self.assertRaises(ValueError): illustration.NumpyLCMScheduler({**CONFIG,"clip_sample":True})

    def test_lcm_stochastic_steps_are_seeded_and_final_step_adds_no_noise(self):
        scheduler=illustration.NumpyLCMScheduler(CONFIG)
        sample=np.full((1,4,4,4),.3,dtype=np.float32); predicted=np.full_like(sample,.1)
        a=scheduler.step(predicted,sample,0,np.random.default_rng(7))
        b=scheduler.step(predicted,sample,0,np.random.default_rng(7))
        self.assertTrue(np.array_equal(a,b))
        final_a=scheduler.step(predicted,sample,3,np.random.default_rng(7))
        final_b=scheduler.step(predicted,sample,3,np.random.default_rng(8))
        self.assertTrue(np.array_equal(final_a,final_b))
        self.assertTrue(np.all(np.isfinite(final_a)))

    def test_fixed_original_prompt_and_unavailable_status_are_honest(self):
        prompt=illustration.character_prompt("orbit")
        self.assertIn("fully clothed adult",prompt)
        self.assertIn("full body frontal pose",prompt)
        self.assertTrue(prompt.startswith("flat vector poster"))
        self.assertIn("dot eyes",prompt)
        self.assertNotIn("courier",prompt)
        self.assertNotIn("artist",prompt)
        with self.assertRaises(ValueError): illustration.character_prompt("arbitrary user prompt")
        temp_root=illustration.PROJECT_ROOT/".runtime"/"temp"
        temp_root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as temp:
            with patch.object(illustration,"MODEL_DIR",Path(temp)):
                status=illustration.model_status()
                self.assertFalse(status["available"])
                self.assertIn("manifest.json",status["missing"])
                self.assertIn("not installed",status["safety_checker"])
                with self.assertRaises(RuntimeError): illustration._verified_manifest()

    def test_pipeline_contract_uses_mock_models_not_a_claim_of_real_inference(self):
        temp_root=illustration.PROJECT_ROOT/".runtime"/"temp"
        temp_root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as temp:
            folder=Path(temp); model=folder/"model"; output=folder/"output"; output.mkdir()
            (model/"scheduler").mkdir(parents=True); (model/"vae_decoder").mkdir()
            (model/"scheduler/scheduler_config.json").write_text(json.dumps(CONFIG))
            (model/"vae_decoder/config.json").write_text('{"scaling_factor":0.18215}')
            calls=[]
            def fake_compile(core,part,shape):
                calls.append((part,shape))
                def infer(inputs):
                    if part=="text_encoder": return {0:np.zeros((1,77,768),np.float32),1:np.zeros((1,768),np.float32)}
                    if part=="unet":
                        self.assertEqual(inputs["timestep_cond"].shape,(1,256))
                        return {0:np.ones((1,4,64,64),np.float32)*.1}
                    return {0:np.zeros((1,3,512,512),np.float32)}
                return infer
            events=[]
            with patch.object(illustration,"MODEL_DIR",model), \
                 patch.object(illustration,"_verified_manifest",return_value={"files":{}}), \
                 patch.object(illustration,"_token_ids",return_value=np.zeros((1,77),np.int64)), \
                 patch.object(illustration,"_compile",side_effect=fake_compile), \
                 patch.dict("sys.modules",{"openvino":types.SimpleNamespace(Core=lambda:object())}):
                result=illustration.generate_character(output,"orbit",7,lambda p,m:events.append(p))
            self.assertEqual([c[0] for c in calls],["text_encoder","unet","vae_decoder"])
            with Image.open(result["image_path"]) as generated:
                self.assertEqual(generated.size,(512,512))
            self.assertEqual(result["provenance"]["steps"],4)
            self.assertEqual(result["provenance"]["threads"],2)
            self.assertIn("not executed",result["provenance"]["safety_checker"])
            self.assertIn("unreviewed",result["provenance"]["artwork_status"])
            self.assertEqual(events,sorted(events)); self.assertEqual(events[-1],1.)
            self.assertNotIn(str(folder),json.dumps(result["provenance"]))

    def test_setup_verifies_pinned_hash_and_rejects_existing_corruption(self):
        spec=importlib.util.spec_from_file_location("setup_mv_illustration",illustration.PROJECT_ROOT/"tools/setup_mv_illustration.py")
        setup=importlib.util.module_from_spec(spec); spec.loader.exec_module(setup)
        temp_root=illustration.PROJECT_ROOT/".runtime"/"temp"
        temp_root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as temp:
            path=Path(temp)/"test.bin"; path.write_bytes(b"original model fixture")
            hashes=setup._hashes(path)
            record={"size":path.stat().st_size,"blobId":hashes["git_blob"]}
            self.assertEqual(setup.verify(path,record)["sha256"],hashes["sha256"])
            with self.assertRaises(ValueError): setup.verify(path,record,"f"*64)
            with self.assertRaises(ValueError): setup.download("https://example.invalid",path,{"size":1,"blobId":"0"*40})


if __name__=="__main__": unittest.main()
