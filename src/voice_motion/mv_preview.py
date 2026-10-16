"""Browser-compatible VP9/Opus preview from the actual generated MV artifact.

Single-pass streaming decode/encode, no GPU and no generated replacement frames.
The original H.264/AAC MP4 remains the master/download artifact.
"""
from __future__ import annotations

import math
import os
import uuid
from fractions import Fraction
from pathlib import Path

import av
import numpy as np

PROJECT_ROOT=Path(__file__).resolve().parents[2]


def create_web_preview(video_path:Path,target_path:Path,progress=None) -> dict:
    source_path=Path(video_path).resolve(); target=Path(target_path).resolve()
    root=PROJECT_ROOT.resolve()
    if not source_path.is_file() or not source_path.is_relative_to(root):
        raise ValueError("Preview source must be an existing project-owned video")
    if not target.parent.is_dir() or not target.is_relative_to(root) or target==source_path or target.suffix.lower()!=".webm":
        raise ValueError("Preview target must be a distinct project-owned .webm path")
    report=progress or (lambda value,message:None)
    partial=target.with_name(target.name+"."+uuid.uuid4().hex+".part")
    frames_written=0; audio_samples=0; width=height=0; fps=Fraction(24,1); duration=0.
    try:
        with av.open(str(source_path)) as source:
            if not source.streams.video: raise ValueError("Preview source has no video")
            input_video=source.streams.video[0]
            input_audio=source.streams.audio[0] if source.streams.audio else None
            width,height=input_video.width,input_video.height
            fps=input_video.average_rate or Fraction(24,1)
            if not math.isfinite(float(fps)) or not 1<=float(fps)<=120:
                raise ValueError("Invalid preview source frame rate")
            duration=float(input_video.duration*input_video.time_base) if input_video.duration is not None else (source.duration or 0)/av.time_base
            if not 0<duration<=61: raise ValueError("Preview source must be at most 60 seconds")
            origin=(source.start_time or 0)/av.time_base
            input_video.codec_context.thread_count=2
            if input_audio: input_audio.codec_context.thread_count=1
            report(0.,"Preparing browser-compatible video preview")
            with av.open(str(partial),"w",format="webm") as output:
                video=output.add_stream("libvpx-vp9",rate=fps)
                video.width=width; video.height=height; video.pix_fmt="yuv420p"; video.bit_rate=1800000
                video.codec_context.thread_count=2
                video.options={"deadline":"realtime","cpu-used":"6","row-mt":"1","tile-columns":"1","lag-in-frames":"0","threads":"2"}
                video.codec_context.gop_size=max(1,round(float(fps)*2))
                audio=None; resampler=None
                if input_audio:
                    audio=output.add_stream("libopus",rate=48000)
                    audio.layout="stereo"; audio.bit_rate=128000; audio.codec_context.thread_count=1
                    resampler=av.AudioResampler(format="fltp",layout="stereo",rate=48000)

                def encode_audio(converted):
                    nonlocal audio_samples
                    # Generated master audio starts at t=0. Retain any explicit
                    # positive offset rather than moving the soundtrack earlier.
                    frame_time=float(converted.pts*converted.time_base)-origin if converted.pts is not None else audio_samples/48000
                    start=max(0,round(frame_time*48000))
                    data=converted.to_ndarray()
                    remaining=max(0,round(duration*48000)-start)
                    if not remaining: return
                    data=np.ascontiguousarray(data[:,:remaining])
                    if not data.shape[-1]: return
                    af=av.AudioFrame.from_ndarray(data,format="fltp",layout="stereo")
                    af.sample_rate=48000; af.pts=start; af.time_base=Fraction(1,48000)
                    for encoded in audio.encode(af): output.mux(encoded)
                    audio_samples+=data.shape[-1]

                selected=[input_video]+([input_audio] if input_audio else [])
                for packet in source.demux(selected):
                    for frame in packet.decode():
                        if packet.stream.type=="video":
                            timestamp=float(frame.pts*frame.time_base)-origin if frame.pts is not None else frames_written/float(fps)
                            frame.pts=max(0,round(timestamp*float(fps))); frame.time_base=1/fps
                            for encoded in video.encode(frame): output.mux(encoded)
                            frames_written+=1
                            if frames_written%24==0:
                                report(min(.98,timestamp/duration),"Encoding browser-compatible VP9/Opus preview")
                        elif resampler:
                            for converted in resampler.resample(frame): encode_audio(converted)
                if resampler:
                    for converted in resampler.resample(None): encode_audio(converted)
                for encoded in video.encode(None): output.mux(encoded)
                if audio:
                    for encoded in audio.encode(None): output.mux(encoded)
        os.replace(partial,target)
    except Exception:
        # Only this call's UUID-named partial inside the verified target folder.
        if partial.is_file(): partial.unlink()
        raise
    report(1.,"Browser-compatible preview is ready")
    return {"preview_path":str(target),"mime_type":"video/webm","video_codec":"vp9","audio_codec":"opus" if audio_samples else None,
            "duration":duration,"width":width,"height":height,"fps":float(fps),"frames":frames_written,
            "audio_samples":audio_samples,"engine":"pyav_streaming_vp9_opus_cpu","threads":2,"bytes":target.stat().st_size}
