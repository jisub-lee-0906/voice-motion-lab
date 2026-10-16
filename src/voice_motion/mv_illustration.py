"""Optional, real local OpenVINO LCM illustration experiment.

No model or package is downloaded by this module. Run the explicit project setup
tool first. The procedural MV engine remains independent of this optional path.
LCM equations follow the Apache-2.0 Diffusers v0.32.1 reference scheduler; model
provenance includes LCM MIT and inherited CreativeML OpenRAIL-M conditions.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import json
import math
import time
from pathlib import Path

import numpy as np
from PIL import Image

PROJECT_ROOT=Path(__file__).resolve().parents[2]
MODEL_DIR=PROJECT_ROOT/".runtime"/"mv-models"/"illustration"
MODEL_ID="OpenVINO/LCM_Dreamshaper_v7-int8-ov"
MODEL_REVISION="340a03e5488f36f7f222e6efa29819850ad7e4be"
TOKENIZER_ID="openai/clip-vit-large-patch14"
TOKENIZER_REVISION="32bd64288804d66eefd0ccbe215aa642df71cc41"
ENGINE="openvino_lcm_dreamshaper_v7_int8_cpu_numpy_v1"
MODEL_FILES=("text_encoder/openvino_model.xml","text_encoder/openvino_model.bin",
             "unet/openvino_model.xml","unet/openvino_model.bin",
             "vae_decoder/openvino_model.xml","vae_decoder/openvino_model.bin",
             "scheduler/scheduler_config.json","vae_decoder/config.json","unet/config.json",
             "text_encoder/config.json","tokenizer/tokenizer.json")


def _sha256(path):
    digest=hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda:source.read(1024*1024),b""): digest.update(block)
    return digest.hexdigest()


def model_status() -> dict:
    """Fast availability only; full weight hashes are checked before inference."""
    missing=[]; manifest={}
    manifest_path=MODEL_DIR/"manifest.json"
    if manifest_path.is_file():
        try: manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError,ValueError): missing.append("valid manifest.json")
    else: missing.append("manifest.json")
    if manifest and (manifest.get("model_id")!=MODEL_ID or manifest.get("revision")!=MODEL_REVISION):
        missing.append("pinned model revision")
    for relative in MODEL_FILES:
        path=MODEL_DIR/relative; record=manifest.get("files",{}).get(relative,{})
        if not path.is_file() or path.is_symlink() or path.stat().st_size!=record.get("bytes"):
            missing.append(relative)
    deps={name:importlib.util.find_spec(name) is not None for name in ("openvino","tokenizers")}
    return {"available":not missing and all(deps.values()),"engine":ENGINE,"device":"CPU","threads":2,
            "model_id":MODEL_ID,"revision":MODEL_REVISION,"missing":missing,"dependencies":deps,
            "integrity":"size_checked; sha256_checked_before_inference",
            "safety_checker":"not installed or claimed; fixed fully clothed adult illustration prompt only"}


def _verified_manifest():
    status=model_status()
    if not status["available"]:
        raise RuntimeError("Optional illustration model unavailable; run tools/setup_mv_illustration.py in the project environment")
    manifest=json.loads((MODEL_DIR/"manifest.json").read_text(encoding="utf-8"))
    for relative in MODEL_FILES:
        path=MODEL_DIR/relative
        if path.resolve().is_relative_to(MODEL_DIR.resolve()) is False:
            raise ValueError("Illustration model path escapes project directory")
        if _sha256(path)!=manifest["files"][relative].get("sha256"):
            raise ValueError(f"Illustration model integrity mismatch: {relative}")
    return manifest


def character_prompt(theme="orbit") -> str:
    details={"bloom":"small white flower hair clip", "fracture":"long ribbon scarf", "orbit":"small crescent hair clip"}
    if theme=="auto": theme="orbit"
    if theme not in details: raise ValueError("Unknown illustration theme")
    # Fixed adult, clothed, original character. No arbitrary user prompt and no
    # artist name/known character imitation is accepted by this first experiment.
    return ("flat vector poster, 2D paper cutout cartoon, bold black contour, solid flat colors, geometric shapes, "
            "original fully clothed adult character, full body frontal pose, round face, dot eyes, "
            "black bob hair, coral raincoat, black leggings, black boots, standing straight, "
            "isolated solid mint background, graphic illustration, "+details[theme])


def guidance_embedding(value=7.,dimension=256):
    """LCM distilled guidance embedding (guidance_scale - 1), no CFG doubling."""
    if dimension%2 or dimension<4: raise ValueError("Guidance dimension must be even")
    half=dimension//2
    frequencies=np.exp(-math.log(10000)*np.arange(half,dtype=np.float32)/(half-1))
    angles=np.float32(value*1000)*frequencies
    return np.concatenate((np.sin(angles),np.cos(angles)))[None,:].astype(np.float32)


class NumpyLCMScheduler:
    """The narrowly supported epsilon/scaled-linear LCM scheduler, not a stub.

    Mathematical algorithm reproduced from the official Diffusers LCM scheduler:
    https://github.com/huggingface/diffusers/blob/v0.32.1/src/diffusers/schedulers/scheduling_lcm.py
    Copyright Hugging Face; reference implementation Apache License 2.0.
    """
    def __init__(self,config,steps=4):
        expected={"beta_schedule":"scaled_linear","prediction_type":"epsilon","num_train_timesteps":1000,
                  "original_inference_steps":50,"timestep_scaling":10.0,"clip_sample":False,"thresholding":False}
        for key,value in expected.items():
            if config.get(key)!=value: raise ValueError(f"Unsupported LCM scheduler option: {key}")
        if config.get("trained_betas") is not None or config.get("rescale_betas_zero_snr",False):
            raise ValueError("Unsupported LCM beta configuration")
        if steps!=4: raise ValueError("This bounded illustration experiment uses exactly four LCM steps")
        betas=np.linspace(math.sqrt(config["beta_start"]),math.sqrt(config["beta_end"]),1000,dtype=np.float32)**2
        self.alphas=np.cumprod(1-betas,dtype=np.float32)
        original=(np.arange(1,51,dtype=np.int64)*20-1)[::-1]
        self.timesteps=original[np.floor(np.linspace(0,50,num=steps,endpoint=False)).astype(np.int64)]

    def step(self,model_output,sample,index,rng):
        timestep=int(self.timesteps[index]); alpha=float(self.alphas[timestep])
        x0=(sample-math.sqrt(1-alpha)*model_output)/math.sqrt(alpha)
        scaled=timestep*10.; sigma=.5
        skip=sigma*sigma/(scaled*scaled+sigma*sigma)
        out=scaled/math.sqrt(scaled*scaled+sigma*sigma)
        denoised=(out*x0+skip*sample).astype(np.float32)
        if index==len(self.timesteps)-1: return denoised
        previous_alpha=float(self.alphas[int(self.timesteps[index+1])])
        noise=rng.standard_normal(sample.shape,dtype=np.float32)
        return (math.sqrt(previous_alpha)*denoised+math.sqrt(1-previous_alpha)*noise).astype(np.float32)


def _token_ids(path,prompt):
    from tokenizers import Tokenizer
    tokenizer=Tokenizer.from_file(str(path))
    tokenizer.enable_truncation(max_length=77)
    tokenizer.enable_padding(length=77,pad_id=49407,pad_token="<|endoftext|>")
    encoding=tokenizer.encode(prompt)
    ids=np.asarray([encoding.ids],dtype=np.int64)
    if ids.shape!=(1,77) or ids[0,0]!=49406 or 49407 not in ids:
        raise ValueError("Unexpected CLIP tokenizer output")
    return ids


def _compile(core,part,shape):
    model=core.read_model(str(MODEL_DIR/part/"openvino_model.xml"))
    model.reshape(shape)
    compiled=core.compile_model(model,"CPU",{
        "INFERENCE_NUM_THREADS":2,"NUM_STREAMS":1,"PERFORMANCE_HINT":"LATENCY",
        "ENABLE_CPU_PINNING":False,"INFERENCE_PRECISION_HINT":"f32",
    })
    return compiled


def generate_character(output_dir:Path,theme="orbit",seed=7,progress=None) -> dict:
    """Generate one actual 512px image. Caller controls subprocess/time/RAM budget.

    This function does not remove backgrounds, claim rigged poses, or declare an
    image safe based on its prompt. Quality must be reviewed before integration.
    """
    output_dir=Path(output_dir).resolve()
    if not output_dir.is_dir() or not output_dir.is_relative_to(PROJECT_ROOT.resolve()):
        raise ValueError("Illustration output must be an existing project-owned directory")
    prompt=character_prompt(theme)
    seed=int(seed)
    if not 0<=seed<=2**32-1: raise ValueError("Seed out of range")
    report=progress or (lambda fraction,message:None)
    start=time.monotonic(); report(.01,"Verifying optional illustration weights")
    manifest=_verified_manifest()
    import openvino as ov
    core=ov.Core()
    rng=np.random.default_rng(seed)
    config=json.loads((MODEL_DIR/"scheduler/scheduler_config.json").read_text(encoding="utf-8"))
    scheduler=NumpyLCMScheduler(config)
    report(.05,"Encoding original character description on CPU")
    ids=_token_ids(MODEL_DIR/"tokenizer/tokenizer.json",prompt)
    text_model=_compile(core,"text_encoder",{"input_ids":[1,77]})
    text_result=text_model({"input_ids":ids})
    # The text encoder also returns a pooled vector; use only the token states.
    hidden=next((np.array(value,copy=True) for value in text_result.values() if np.asarray(value).shape==(1,77,768)),None)
    if hidden is None: raise ValueError("CLIP hidden-state output shape mismatch")
    del text_result,text_model; gc.collect()
    report(.12,"Compiling the bounded CPU illustration network")
    unet=_compile(core,"unet",{"sample":[1,4,64,64],"timestep":[1],"encoder_hidden_states":[1,77,768],"timestep_cond":[1,256]})
    latent=rng.standard_normal((1,4,64,64),dtype=np.float32)
    cond=guidance_embedding(7.)
    for index,timestep in enumerate(scheduler.timesteps):
        report(.18+index*.16,f"Generating original illustration, LCM step {index+1}/4")
        result=unet({"sample":latent,"timestep":np.asarray([timestep],dtype=np.int64),
                     "encoder_hidden_states":hidden,"timestep_cond":cond})
        predicted=np.array(next(iter(result.values())),copy=True)
        if predicted.shape!=latent.shape or not np.all(np.isfinite(predicted)):
            raise ValueError("Invalid LCM model output")
        latent=scheduler.step(predicted,latent,index,rng)
    del result,predicted,unet,hidden; gc.collect()
    report(.85,"Decoding the generated illustration")
    decoder=_compile(core,"vae_decoder",{"latent_sample":[1,4,64,64]})
    vae_config=json.loads((MODEL_DIR/"vae_decoder/config.json").read_text(encoding="utf-8"))
    scaling=float(vae_config.get("scaling_factor",.18215))
    if not math.isclose(scaling,.18215,rel_tol=1e-6): raise ValueError("Unexpected VAE latent scale")
    decoded=next(iter(decoder({"latent_sample":latent/scaling}).values()))
    if np.asarray(decoded).shape!=(1,3,512,512) or not np.all(np.isfinite(decoded)):
        raise ValueError("Invalid illustration image output")
    pixels=(np.clip(decoded[0].transpose(1,2,0)/2+.5,0,1)*255).round().astype(np.uint8)
    image_path=output_dir/"character.png"
    Image.fromarray(pixels).save(image_path)
    del decoder,core; gc.collect()
    provenance={"engine":ENGINE,"model_id":MODEL_ID,"revision":MODEL_REVISION,"device":"CPU","threads":2,
                "steps":4,"resolution":[512,512],"scheduler":"numpy_lcm_epsilon_scaled_linear_v1",
                "timesteps":scheduler.timesteps.tolist(),"guidance_scale":8.,"seed":seed,"theme":theme,"prompt":prompt,
                "tokenizer":{"model_id":TOKENIZER_ID,"revision":TOKENIZER_REVISION,"max_tokens":77},
                "model_files":{name:{"sha256":record["sha256"],"bytes":record["bytes"]} for name,record in manifest["files"].items()},
                "license_notice":"LCM/OpenVINO metadata: MIT. Base DreamShaper7/StableDiffusion1.5: inherited CreativeML OpenRAIL-M. CLIP tokenizer: MIT. Scheduler reference: Apache-2.0.",
                "safety_checker":"not executed; generated output is not automatically safety-rated",
                "artwork_status":"unreviewed optional single generated illustration; not a layered or rigged character pack",
                "seconds":round(time.monotonic()-start,3),"image_sha256":_sha256(image_path)}
    (output_dir/"illustration-provenance.json").write_text(json.dumps(provenance,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
    report(1.,"Optional illustration generated; visual review required")
    return {"image_path":str(image_path),"engine":ENGINE,"provenance":provenance}


def main(argv=None):
    parser=argparse.ArgumentParser(description="Project-local bounded CPU illustration experiment; no downloads")
    parser.add_argument("--status",action="store_true")
    parser.add_argument("--output-dir",type=Path)
    parser.add_argument("--theme",choices=["bloom","fracture","orbit"],default="orbit")
    parser.add_argument("--seed",type=int,default=7)
    args=parser.parse_args(argv)
    if args.status:
        print(json.dumps(model_status(),ensure_ascii=False)); return 0
    if not args.output_dir: parser.error("--output-dir is required for generation")
    result=generate_character(args.output_dir,args.theme,args.seed,
                              progress=lambda value,message:print(json.dumps({"progress":value,"message":message}),flush=True))
    print(json.dumps(result,ensure_ascii=False),flush=True); return 0


if __name__=="__main__": raise SystemExit(main())
