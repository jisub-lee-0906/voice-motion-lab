"""Music/lyric directing and bounded CPU rendering of original 2D music videos."""
from __future__ import annotations

import hashlib
import json
import math
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
from PIL import Image, ImageDraw

from .mv_art import HEIGHT, WIDTH, MVArtRenderer, font, font_provenance

FPS=24
THEMES=("bloom","fracture","orbit")
LEXICON={
    "bloom":("꽃","피어","자라","봄","새싹","빛","사랑","따뜻","희망","살아","bloom","flower","grow","spring","light","love","hope","alive","rise"),
    "fracture":("깨","부서","균열","아프","눈물","잃","떠나","미워","끝","상처","죽","고독","break","broken","tear","fall","lost","leave","hurt","end","dark","alone"),
    "orbit":("별","달","우주","돌아","시간","기억","꿈","밤","하늘","멀리","star","moon","orbit","space","time","dream","night","sky","remember","away"),
}


def analyze_music(samples: np.ndarray, sample_rate: int) -> dict:
    """Actual STFT/spectral-flux beat evidence; no constant fake BPM grid."""
    mono=np.asarray(samples,dtype=np.float64)
    if mono.ndim!=1 or not len(mono) or not np.all(np.isfinite(mono)):
        raise ValueError("Audio must contain finite mono samples")
    if not 8000<=sample_rate<=192000: raise ValueError("Unsupported audio sample rate")
    # Small resampling is sufficient for control features, not used for output.
    target=16000
    if sample_rate!=target:
        n=max(1,round(len(mono)*target/sample_rate))
        mono=np.interp(np.arange(n)*sample_rate/target,np.arange(len(mono)),mono)
    rate=target; nfft=1024; hop=320
    padded=np.pad(mono,(nfft//2,nfft//2))
    windows=np.lib.stride_tricks.sliding_window_view(padded,nfft)[::hop]
    spectra=np.abs(np.fft.rfft(windows*np.hanning(nfft),axis=1))
    energy=np.sqrt(np.mean(windows**2,axis=1))
    ceiling=float(np.percentile(energy,95))
    energy_norm=np.clip(energy/max(ceiling,1e-7),0,1)
    logged=np.log1p(spectra*8)
    flux=np.r_[0,np.maximum(np.diff(logged,axis=0),0).mean(axis=1)]
    floor=float(np.median(flux)); ceiling_flux=float(np.percentile(flux,98))
    flux_norm=np.clip((flux-floor)/max(ceiling_flux-floor,1e-7),0,1)
    freqs=np.fft.rfftfreq(nfft,1/rate)
    centroid=(spectra*freqs).sum(axis=1)/np.maximum(spectra.sum(axis=1),1e-9)
    brightness=np.clip(centroid/6000,0,1)
    duration=len(samples)/sample_rate
    # Local maxima + adaptive energy threshold prevent beats during silence.
    candidates=[]; min_spacing=.23
    for i in range(1,len(flux_norm)-1):
        if flux_norm[i]>.22 and flux_norm[i]>=flux_norm[i-1] and flux_norm[i]>flux_norm[i+1] and energy_norm[i]>.08:
            time=i*hop/rate
            if time>duration: break
            if candidates and time-candidates[-1][0]<min_spacing:
                if flux_norm[i]>candidates[-1][1]: candidates[-1]=(time,float(flux_norm[i]))
            else: candidates.append((time,float(flux_norm[i])))
    # Flux periodicity supplies a tempo estimate even where not every pulse is
    # detected. Confidence explicitly distinguishes this from exact beat truth.
    centered=flux_norm-float(np.mean(flux_norm))
    tempo=120.; tempo_conf=0.
    if ceiling>1e-6 and len(centered)>100:
        corr=np.correlate(centered,centered,mode="full")[len(centered)-1:]
        lo,hi=round(60/180/.02),min(len(corr)-1,round(60/65/.02))
        if hi>lo:
            lag=int(lo+np.argmax(corr[lo:hi+1])); tempo=60/(lag*.02)
            tempo_conf=float(np.clip(corr[lag]/max(corr[0],1e-9),0,1))
    frames=[{"time":round(i*.02,4),"energy":round(float(e),5),"onset":round(float(o),5),"brightness":round(float(b),5)}
            for i,(e,o,b) in enumerate(zip(energy_norm,flux_norm,brightness)) if i*.02<=duration]
    return {"engine":"numpy_stft_spectral_flux_v1","frame_hop_seconds":.02,
            "tempo_bpm":round(tempo,2),"tempo_confidence":round(tempo_conf,4),
            "beats":[{"time":round(t,4),"strength":round(s,4),"source":"detected_spectral_onset"} for t,s in candidates],
            "frames":frames,"silence":ceiling<=1e-6,
            "timing_note":"Onset candidates and estimated tempo; no claim of perfect downbeat tracking."}


def normalize_transcript(transcript: dict,duration: float) -> dict:
    if not isinstance(transcript,dict): raise ValueError("Transcript must be an object")
    raw=transcript.get("segments",[])
    if not isinstance(raw,list): raise ValueError("Transcript segments must be a list")
    segments=[]
    for source in raw:
        if not isinstance(source,dict): raise ValueError("Invalid transcript segment")
        text=" ".join(str(source.get("text","")).split())
        if not text: continue
        start=float(source.get("start",0)); end=float(source.get("end",duration))
        if not math.isfinite(start) or not math.isfinite(end) or end<=start:
            raise ValueError("Invalid transcript segment timing")
        start=max(0.,min(duration,start)); end=max(0.,min(duration,end))
        if end<=start: continue
        words=[]
        for w in source.get("words",[]) or []:
            ws,we=float(w.get("start",start)),float(w.get("end",end))
            if math.isfinite(ws) and math.isfinite(we) and we>ws:
                words.append({"start":max(start,min(end,ws)),"end":max(start,min(end,we)),"text":str(w.get("text",w.get("word","")))})
        timing=source.get("timing_source",transcript.get("timing_source","asr_model_segment_timestamps"))
        # ASR sometimes emits an entire verse as one segment. Split only long
        # text for readable subtitles and disclose the interior timing estimate.
        # Never insert words that were not supplied by ASR/user correction.
        pieces=[]; rest=text
        while len(rest)>76:
            cut=rest.rfind(" ",20,70)
            if cut<20: cut=60
            pieces.append(rest[:cut].strip()); rest=rest[cut:].strip()
        if rest: pieces.append(rest)
        if len(pieces)>1:
            weight=sum(len(part) for part in pieces); elapsed=start
            for index,part in enumerate(pieces):
                boundary=end if index==len(pieces)-1 else elapsed+(end-start)*len(part)/weight
                segments.append({"start":round(elapsed,4),"end":round(boundary,4),"text":part,
                                 "words":[w for w in words if elapsed<=w["start"]<boundary],
                                 "timing_source":f"estimated_text_split_within_{timing}"})
                elapsed=boundary
        else:
            segments.append({"start":round(start,4),"end":round(end,4),"text":text,"words":words,"timing_source":timing})
    segments.sort(key=lambda s:s["start"])
    return {"engine":str(transcript.get("engine","unknown")),"language":transcript.get("language"),
            "text":str(transcript.get("text", " ".join(s["text"] for s in segments))),"segments":segments,
            "timing_source":transcript.get("timing_source","asr_model_segment_timestamps"),
            "warnings":list(transcript.get("warnings",[]))}


def semantic_theme(text: str, default="orbit") -> tuple[str,dict]:
    text=text.lower()
    scores={theme:sum(text.count(word) for word in words) for theme,words in LEXICON.items()}
    best=max(scores,key=scores.get)
    return (best if scores[best] else default),scores


def direct_project(samples:np.ndarray,sample_rate:int,transcript:dict,theme="auto",seed=7) -> dict:
    duration=len(samples)/sample_rate
    if not .5<=duration<=60.05: raise ValueError("Music video input must be 0.5–60 seconds")
    if theme not in ("auto",)+THEMES: raise ValueError("Unknown MV theme")
    tr=normalize_transcript(transcript,duration); music=analyze_music(samples,sample_rate)
    global_theme,scores=semantic_theme(tr["text"])
    if theme!="auto": global_theme=theme
    # Aim for 2.2–4.5s shots; segment edges and strong musical onsets determine
    # real cut times. Nothing requires user-created cut lists or source images.
    target=max(2.4,min(4.,240/max(65,music["tempo_bpm"])))
    candidates=[(b["time"],b["strength"]+.15) for b in music["beats"]]
    candidates += [(s["start"],1.15) for s in tr["segments"] if s["start"]>.2]
    cuts=[0.]; cursor=target
    while cursor<duration-1.2:
        available=[(t,score) for t,score in candidates if cuts[-1]+1.65<=t<=cuts[-1]+4.5 and abs(t-cursor)<.8 and t<duration-1.0]
        cut=min(available,key=lambda item:abs(item[0]-cursor)-item[1]*.25)[0] if available else cursor
        if cut-cuts[-1]<1.5: cut=cuts[-1]+target
        cuts.append(round(cut,4)); cursor=cut+target
    cuts.append(round(duration,4))
    compositions=("wide","close","dutch","duet","tunnel","close","crown","fall")
    shots=[]; seen={}; segment_repetitions={}; motifs={"bloom":"six-petal seed / flower", "fracture":"torn paper / triangle", "orbit":"crescent / orbital ring"}
    for i,(start,end) in enumerate(zip(cuts,cuts[1:])):
        active=[s for s in tr["segments"] if s["start"]<end and s["end"]>start]
        primary=max(active,key=lambda s:min(s["end"],end)-max(s["start"],start)) if active else None
        lyric=" ".join(s["text"] for s in active)
        local_theme,local_scores=semantic_theme(primary["text"] if primary else "",global_theme)
        if theme!="auto": local_theme=theme
        canonical=primary["text"].lower().strip() if primary else ""
        segment_id=tr["segments"].index(primary) if primary else None
        if segment_id is not None and segment_id not in segment_repetitions:
            segment_repetitions[segment_id]=seen.get(canonical,0)
            seen[canonical]=seen.get(canonical,0)+1
        repeat=segment_repetitions.get(segment_id,0)
        fs=[f for f in music["frames"] if start<=f["time"]<end]
        mean_energy=float(np.mean([f["energy"] for f in fs])) if fs else 0.
        # Repeated lyrics revisit the same symbol at a changed scale and pose.
        comp=compositions[i%len(compositions)]
        if repeat: comp="crown" if mean_energy>.45 else "duet"
        emotion="hurt" if local_theme=="fracture" else "hope" if local_theme=="bloom" else "quiet"
        pose="reach" if comp in ("crown","tunnel") else "fall" if comp=="fall" else "fold" if emotion=="hurt" else "point" if i%3==1 else "sway"
        shots.append({"id":i+1,"start":start,"end":end,"theme":local_theme,"composition":comp,"pose":pose,"emotion":emotion,
                      "lighting":"night" if i%4==2 else "day","transition":"paper-flash" if i and mean_energy>.55 else "cut",
                      "lyric":lyric,"lyric_segment_ids":[tr["segments"].index(s) for s in active],"semantic_scores":local_scores,
                      "mean_energy":round(mean_energy,4),"motif":motifs[local_theme],"motif_state":round((i+1)/len(cuts),4),"lyric_repetition":repeat,
                      "direction_reason":"Recurring lyric revisited with changed staging" if repeat else "Lyric-symbol match with music-aligned cut" if any(local_scores.values()) else "Instrumental/abstract staging guided by measured music"})
    warnings=list(tr["warnings"])
    if not tr["segments"]: warnings.append("No transcribed lyrics: instrumental direction only; no lyric text was invented.")
    if music["silence"]: warnings.append("Input is effectively silent; rhythm/tempo evidence is unavailable.")
    if music["tempo_confidence"]<.15: warnings.append("Tempo confidence is low; cuts also use lyric boundaries and bounded shot lengths.")
    return {"format":"voice-motion-mv-project","version":1,"duration":round(duration,6),"width":WIDTH,"height":HEIGHT,"fps":FPS,"seed":int(seed),
            "theme":global_theme,"theme_source":"lyric_lexicon" if theme=="auto" and any(scores.values()) else "default_abstract" if theme=="auto" else "user_choice",
            "transcript":tr,"audio_analysis":music,"shots":shots,"recurring_motifs":motifs,
            "character":{"id":"crescent-courier-v1","creator":"Voice Motion Lab","description":"Original split-fringe paper character, crescent clip and ribbon coat"},
            "engines":{"audio_analysis":music["engine"],"transcription":tr["engine"],"direction":"lyric_semantic_rules_v1","art":"original_authored_procedural_2d_v1","render":"pillow_pyav_h264_aac_cpu"},
            "font":font_provenance(),"warnings":warnings,
            "editability":{"instructions":"Edit shot start/end, theme, composition, pose, emotion, motif_state, or transcript segments; render with MVArtRenderer.",
                           "image_pack_extension":"Future generated layered artwork may replace the character renderer; current version uses authored procedural illustrations."}}


def _decode(path:Path):
    arrays=[]
    with av.open(str(path)) as container:
        streams=list(container.streams.audio)
        if not streams: raise ValueError("Input has no audio stream")
        stream=streams[0]; stream.thread_type="NONE"
        resampler=av.AudioResampler(format="fltp",layout="stereo",rate=48000)
        length=0
        for frame in container.decode(stream):
            for converted in resampler.resample(frame):
                data=converted.to_ndarray(); arrays.append(data); length+=data.shape[-1]
                if length>48000*60.05: raise ValueError("Music video input exceeds 60 seconds")
        for converted in resampler.resample(None):
            arrays.append(converted.to_ndarray())
    if not arrays: raise ValueError("Audio is empty")
    audio=np.concatenate(arrays,axis=1).astype(np.float32,copy=False)
    if not np.all(np.isfinite(audio)): raise ValueError("Audio contains non-finite samples")
    return audio,48000


def _storyboard(renderer,project,path):
    shots=project["shots"]
    cols=3; thumb=(320,180); rows=math.ceil(len(shots)/cols)
    sheet=Image.new("RGB",(cols*thumb[0],rows*210+58),"#171c2c")
    d=ImageDraw.Draw(sheet)
    d.text((18,14),"NOCTURNE / AUTOMATIC DIRECTING SHEET",font=font(17),fill="#f7eccd")
    for i,shot in enumerate(shots):
        t=shot["start"]+(shot["end"]-shot["start"])*.5
        frame=renderer.render(t).resize(thumb,Image.Resampling.LANCZOS)
        x,y=(i%cols)*thumb[0],58+(i//cols)*210
        sheet.paste(frame,(x,y))
        d.text((x+10,y+185),f"{shot['id']:02}  {shot['start']:05.2f} — {shot['end']:05.2f}   /   {shot['composition'].upper()}",font=font(10),fill="#f7eccd")
    sheet.save(str(path),quality=93)


def create_music_video(audio_path:Path,output_dir:Path,transcript:dict,theme="auto",seed=7,progress=None) -> dict:
    audio_path=Path(audio_path).resolve(); output_dir=Path(output_dir).resolve()
    if not output_dir.is_dir(): raise ValueError("Output directory must already exist")
    report=progress or (lambda value,message:None)
    report(.02,"Decoding original audio")
    audio,rate=_decode(audio_path); duration=audio.shape[-1]/rate
    report(.06,"Analyzing musical changes and lyric symbols")
    project=direct_project(audio.mean(axis=0),rate,transcript,theme,seed)
    digest=hashlib.sha256()
    with audio_path.open("rb") as source:
        for chunk in iter(lambda:source.read(1024*1024),b""): digest.update(chunk)
    project["input"]={"sha256":digest.hexdigest(),"decoded_sample_rate":rate,"channels":audio.shape[0],"duration":duration,
                      "privacy":"Original audio retained only in local input and MP4; samples and host paths excluded from project JSON."}
    video_path=output_dir/"video.mp4"; project_path=output_dir/"project.json"; storyboard_path=output_dir/"storyboard.jpg"
    renderer=MVArtRenderer(project)
    report(.09,"Composing the directing storyboard")
    _storyboard(renderer,project,storyboard_path)
    # Video and audio are multiplexed by timestamp, not accumulated as frames.
    # At most two codec threads, with a single sequential scene renderer.
    report(.12,"Rendering original illustrated scenes")
    total_frames=max(1,math.ceil(duration*FPS)); audio_cursor=0
    with av.open(str(video_path),"w",format="mp4",options={"movflags":"+faststart"}) as container:
        vs=container.add_stream("libx264",rate=FPS)
        vs.width=WIDTH; vs.height=HEIGHT; vs.pix_fmt="yuv420p"
        vs.codec_context.thread_count=2
        vs.options={"preset":"veryfast","crf":"20","threads":"2"}
        aus=container.add_stream("aac",rate=rate)
        aus.layout="stereo"; aus.bit_rate=192000; aus.codec_context.thread_count=1
        for i in range(total_frames):
            frame=av.VideoFrame.from_image(renderer.render(i/FPS))
            frame.pts=i; frame.time_base=Fraction(1,FPS)
            for packet in vs.encode(frame): container.mux(packet)
            audio_until=min(audio.shape[-1],round((i+1)*rate/FPS))
            while audio_cursor<audio_until:
                end=min(audio_until,audio_cursor+1024)
                af=av.AudioFrame.from_ndarray(np.ascontiguousarray(audio[:,audio_cursor:end]),format="fltp",layout="stereo")
                af.sample_rate=rate; af.pts=audio_cursor; af.time_base=Fraction(1,rate)
                for packet in aus.encode(af): container.mux(packet)
                audio_cursor=end
            if i%12==0: report(.12+.84*(i+1)/total_frames,f"Rendering shot frames {i+1}/{total_frames}")
        for packet in vs.encode(None): container.mux(packet)
        for packet in aus.encode(None): container.mux(packet)
    project["output"]={"video":"video.mp4","storyboard":"storyboard.jpg","codec":"h264/aac","frame_count":total_frames,"audio_reencoded":True}
    project_path.write_text(json.dumps(project,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
    report(1.,"Music video and editable directing project are ready")
    return {"video_path":str(video_path),"project_path":str(project_path),"storyboard_path":str(storyboard_path),
            "duration":duration,"width":WIDTH,"height":HEIGHT,"fps":FPS,"shots":len(project["shots"]),
            "warnings":project["warnings"],"engines":project["engines"],"character":project["character"]["id"]}
