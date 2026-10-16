"""Actual bounded media normalization and multipart boundary tests; no inference."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch
import wave

import av
import numpy as np
from PIL import Image

from voice_motion.mv_audio import normalize_uploaded_audio, OPEN_OPTIONS

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('mv_upload_app', ROOT/'mv_server.py')
app=importlib.util.module_from_spec(spec); spec.loader.exec_module(app)


def wav_bytes(duration=1.1, rate=44100, channels=2, silent=False):
    time=np.arange(round(duration*rate))/rate
    signal=np.zeros(len(time)) if silent else .25*np.sin(2*np.pi*330*time)
    values=np.column_stack([signal if channel%2==0 else -signal for channel in range(channels)])
    pcm=(values*32767).astype('<i2')
    output=io.BytesIO()
    with wave.open(output,'wb') as target:
        target.setnchannels(channels); target.setsampwidth(2); target.setframerate(rate)
        target.writeframes(pcm.tobytes())
    return output.getvalue(),pcm


def multipart(parts=None, boundary='bop-safe-boundary'):
    if parts is None:
        parts=[('metadata',b'{"title":"My song","theme":"bloom","seed":7}',None),
               ('audio',b'\x00\x80\xfforiginal\r\nbytes','audio/wav')]
    pieces=[]
    for name,payload,mime in parts:
        pieces.append(b'--'+boundary.encode()+b'\r\n')
        disposition=f'Content-Disposition: form-data; name="{name}"'
        if name=='audio': disposition+='; filename="../../ignored-client-name.wav"'
        pieces.append(disposition.encode()+b'\r\n')
        if mime: pieces.append(b'Content-Type: '+mime.encode()+b'\r\n')
        pieces.append(b'\r\n'+payload+b'\r\n')
    pieces.append(b'--'+boundary.encode()+b'--\r\n')
    return b''.join(pieces),'multipart/form-data; boundary='+boundary


class AudioNormalizationTests(unittest.TestCase):
    def test_actual_stereo_rate_and_float_samples_survive_canonical_wav(self):
        raw,pcm=wav_bytes()
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'source.wav'
            original_open=av.open
            with patch('voice_motion.mv_audio.av.open', wraps=original_open) as opened:
                result=normalize_uploaded_audio(raw,target)
            self.assertEqual(opened.call_args_list[0].kwargs['options'],OPEN_OPTIONS)
            self.assertEqual(OPEN_OPTIONS['protocol_whitelist'],'pipe')
            self.assertEqual(OPEN_OPTIONS['enable_drefs'],'0')
            self.assertNotIn('hls',OPEN_OPTIONS['format_whitelist'])
            self.assertEqual(result['sample_rate'],44100)
            self.assertEqual(result['channels'],2)
            self.assertFalse(result['resampled']); self.assertFalse(result['downmixed'])
            self.assertEqual(result['source_sha256'],hashlib.sha256(raw).hexdigest())
            self.assertEqual(result['normalized_codec'],'pcm_f32le')
            with av.open(str(target)) as output:
                self.assertEqual(output.streams.audio[0].codec_context.name,'pcm_f32le')
                converter=av.AudioResampler(format='fltp',layout='stereo',rate=44100)
                frames=[]
                for frame in output.decode(audio=0): frames.extend(converter.resample(frame))
                frames.extend(converter.resample(None))
                actual=np.concatenate([frame.to_ndarray() for frame in frames],axis=1)
            expected=pcm.T.astype(np.float32)/32768
            np.testing.assert_array_equal(actual,expected)
            self.assertGreater(result['channel_rms'][0],.1)
            self.assertGreater(result['channel_rms'][1],.1)

    def test_invalid_duration_silence_rate_channels_and_corruption_leave_no_wav(self):
        invalid=[wav_bytes(duration=.5)[0],wav_bytes(duration=60.1,rate=16000)[0],
                 wav_bytes(silent=True)[0],wav_bytes(rate=4000)[0],wav_bytes(channels=3)[0],b'not audio']
        for raw in invalid:
            with self.subTest(size=len(raw)), tempfile.TemporaryDirectory() as directory:
                target=Path(directory)/'source.wav'
                with self.assertRaises(ValueError): normalize_uploaded_audio(raw,target)
                self.assertFalse(target.exists())
                self.assertFalse(target.with_name('source.wav.normalizing').exists())

    def test_playlists_are_rejected_before_any_decoder_or_network_open(self):
        with tempfile.TemporaryDirectory() as directory, patch('voice_motion.mv_audio.av.open') as opened:
            target=Path(directory)/'source.wav'
            for raw in [b'#EXTM3U\nhttps://example.com/private.aac',b'[playlist]\nFile1=file:///secret.wav',b'<?xml version="1.0"?><MPD/>']:
                with self.assertRaises(ValueError): normalize_uploaded_audio(raw,target)
            opened.assert_not_called()

    def test_existing_target_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'source.wav'; target.write_bytes(b'keep')
            with self.assertRaises(ValueError): normalize_uploaded_audio(wav_bytes()[0],target)
            self.assertEqual(target.read_bytes(),b'keep')

    def test_nonfinite_float_audio_rejected(self):
        raw=io.BytesIO()
        with av.open(raw,'w',format='wav') as output:
            stream=output.add_stream('pcm_f32le',rate=16000); stream.layout='mono'
            values=np.full((1,16000),.1,dtype=np.float32); values[0,1234]=np.nan
            frame=av.AudioFrame.from_ndarray(values,format='flt',layout='mono'); frame.sample_rate=16000
            for packet in stream.encode(frame): output.mux(packet)
            for packet in stream.encode(None): output.mux(packet)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError): normalize_uploaded_audio(raw.getvalue(),Path(directory)/'source.wav')

    def test_actual_aac_m4a_and_mp3_remain_stereo_at_native_rate(self):
        rate=48000
        time=np.arange(round(1.2*rate))/rate
        samples=np.array([.2*np.sin(2*np.pi*330*time),.15*np.sin(2*np.pi*550*time)],dtype=np.float32)
        for container_format,codec in [('mp4','aac'),('mp3','libmp3lame')]:
            with self.subTest(codec=codec), tempfile.TemporaryDirectory() as directory:
                raw=io.BytesIO()
                with av.open(raw,'w',format=container_format) as output:
                    stream=output.add_stream(codec,rate=rate); stream.layout='stereo'
                    frame=av.AudioFrame.from_ndarray(samples,format='fltp',layout='stereo'); frame.sample_rate=rate
                    for packet in stream.encode(frame): output.mux(packet)
                    for packet in stream.encode(None): output.mux(packet)
                metadata=normalize_uploaded_audio(raw.getvalue(),Path(directory)/'source.wav')
                self.assertEqual(metadata['sample_rate'],48000)
                self.assertEqual(metadata['channels'],2)
                self.assertAlmostEqual(metadata['duration'],1.2,delta=.05)

    def test_actual_mp3_id3_album_cover_is_ignored_without_changing_music(self):
        rate=44100
        time=np.arange(round(1.2*rate))/rate
        samples=np.array([.2*np.sin(2*np.pi*330*time),.15*np.sin(2*np.pi*550*time)],dtype=np.float32)
        raw=io.BytesIO()
        with av.open(raw,'w',format='mp3') as output:
            stream=output.add_stream('libmp3lame',rate=rate); stream.layout='stereo'
            frame=av.AudioFrame.from_ndarray(samples,format='fltp',layout='stereo'); frame.sample_rate=rate
            for packet in stream.encode(frame): output.mux(packet)
            for packet in stream.encode(None): output.mux(packet)
        original=raw.getvalue()
        music=original
        if music.startswith(b'ID3'):
            size=sum((byte & 127) << (7*(3-index)) for index,byte in enumerate(music[6:10]))
            music=music[10+size:]
        jpeg=io.BytesIO()
        Image.new('RGB',(12,12),(253,78,139)).save(jpeg,format='JPEG')
        picture=b'\x00image/jpeg\x00\x03\x00'+jpeg.getvalue()
        apic=b'APIC'+len(picture).to_bytes(4,'big')+b'\x00\x00'+picture
        size=len(apic)
        synchsafe=bytes((size >> shift) & 127 for shift in (21,14,7,0))
        with_cover=b'ID3\x03\x00\x00'+synchsafe+apic+music
        with av.open(io.BytesIO(with_cover),'r') as probe:
            self.assertEqual(len(probe.streams.audio),1)
            self.assertEqual(len(probe.streams.video),1)
            self.assertTrue(probe.streams.video[0].disposition & av.stream.Disposition.attached_pic)
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            base_path=Path(first)/'source.wav'; cover_path=Path(second)/'source.wav'
            base=normalize_uploaded_audio(original,base_path)
            covered=normalize_uploaded_audio(with_cover,cover_path)
            self.assertEqual(covered['ignored_attached_pictures'],1)
            self.assertEqual((covered['sample_rate'],covered['channels']),(44100,2))
            self.assertEqual(covered['normalized_samples_per_channel'],base['normalized_samples_per_channel'])
            self.assertEqual(covered['normalized_sha256'],base['normalized_sha256'])
            self.assertEqual(cover_path.read_bytes(),base_path.read_bytes())


class MultipartTests(unittest.TestCase):
    def test_binary_bytes_and_korean_metadata_are_unchanged_and_filename_ignored(self):
        audio=b'\x00\xff\x80RIFF\r\nraw\r\n'
        raw,content_type=multipart([('metadata',json.dumps({'title':'나의 노래','lyrics':'작은 빛'},ensure_ascii=False).encode(),None),('audio',audio,'audio/wav')])
        result=app.parse_upload(raw,content_type)
        self.assertEqual(result['raw_audio'],audio)
        self.assertEqual(result['title'],'나의 노래')
        self.assertEqual(set(result),{'raw_audio','title','theme','seed','lyrics'})

    def test_duplicate_extra_missing_and_truncated_parts_rejected(self):
        audio=('audio',b'raw','audio/wav'); metadata=('metadata',b'{"title":"song"}',None)
        for parts in [[metadata,audio,audio],[metadata,metadata],[audio],[metadata,('other',b'x',None)]]:
            with self.assertRaises(app.RequestError): app.parse_upload(*multipart(parts))
        raw,content_type=multipart()
        with self.assertRaises(app.RequestError): app.parse_upload(raw[:-5],content_type)

    def test_content_types_duplicate_json_and_headers_rejected(self):
        for parts in [[('metadata',b'{"seed":true}',None),('audio',b'x','audio/wav')],
                      [('metadata',b'{"title":"a","title":"b"}',None),('audio',b'x','audio/wav')],
                      [('metadata',b'{}','application/xml'),('audio',b'x','audio/wav')],
                      [('metadata',b'{}',None),('audio',b'x','video/mp4')]]:
            with self.assertRaises(app.RequestError): app.parse_upload(*multipart(parts))
        raw,content_type=multipart()
        for changed in [raw.replace(b'name="metadata"',b'name="metadata"; name="audio"'),
                        raw.replace(b'Content-Type: audio/wav',b'Content-Type: audio/wav\r\nContent-Type: audio/mpeg'),
                        raw.replace(b'Content-Type: audio/wav',b'Content-Transfer-Encoding: base64\r\nContent-Type: audio/wav')]:
            with self.assertRaises(app.RequestError): app.parse_upload(changed,content_type)
        for changed_type in ['application/json',content_type+'; boundary=second',content_type+'\r\nX-Test: injected']:
            with self.assertRaises(app.RequestError): app.parse_upload(raw,changed_type)

    def test_nested_or_large_mime_headers_rejected_before_object_tree_parsing(self):
        raw,content_type=multipart()
        changed=[raw.replace(b'Content-Type: audio/wav',b'Content-Type: multipart / mixed; boundary=nested'),
                 raw.replace(b'Content-Type: audio/wav',b'Content-Type: message/rfc822'),
                 raw.replace(b'filename="../../ignored-client-name.wav"',b'filename="'+b'x'*5000+b'"')]
        with patch.object(app,'BytesParser') as parser:
            for body in changed:
                with self.assertRaises(app.RequestError): app.parse_upload(body,content_type)
            parser.assert_not_called()


class AdmissionTests(unittest.TestCase):
    def test_normalization_is_after_health_capacity_and_ram_guards(self):
        manager=app.JobManager()
        payload={'raw_audio':b'bad','title':'Song','theme':'auto','seed':7,'lyrics':''}
        with patch.object(app,'model_status',return_value={'available':True}), patch.object(app,'comfy_ready',return_value={'available':True}), patch.object(app,'available_ram_gb',return_value=4), patch('voice_motion.mv_audio.normalize_uploaded_audio') as normalize:
            with self.assertRaises(app.RequestError): manager.create(payload)
            normalize.assert_not_called()
        with patch.object(app,'model_status',return_value={'available':True}), patch.object(app,'comfy_ready',return_value={'available':True}), patch.object(app,'available_ram_gb',return_value=8), patch('voice_motion.mv_audio.normalize_uploaded_audio') as normalize:
            manager.capacity.acquire()
            with self.assertRaises(app.RequestError): manager.create(payload)
            normalize.assert_not_called(); manager.capacity.release()

    def test_real_normalization_staged_without_running_model_or_sdk(self):
        raw,_=wav_bytes()
        payload={'raw_audio':raw,'title':'Song','theme':'orbit','seed':7,'lyrics':''}
        def normalize(source,target): return normalize_uploaded_audio(source.read_bytes(),target)
        with tempfile.TemporaryDirectory() as directory, patch.object(app,'ROOT',Path(directory)), patch.object(app,'model_status',return_value={'available':True}), patch.object(app,'comfy_ready',return_value={'available':True}), patch.object(app,'available_ram_gb',return_value=8), patch.object(app.threading,'Thread') as thread, patch.object(app,'normalize_upload_subprocess',side_effect=normalize):
            manager=app.JobManager(); job=manager.create(payload)
            source=Path(directory)/'input/mv'/job['id']
            self.assertEqual((source/'upload.audio').read_bytes(),raw)
            self.assertTrue((source/'source.wav').is_file())
            self.assertEqual(json.loads((source/'audio-metadata.json').read_text())['channels'],2)
            self.assertEqual(job['status'],'queued')
            self.assertEqual(job['audio']['sample_rate'],44100)
            thread.return_value.start.assert_called_once()
            self.assertIn('raw_audio',payload)  # Caller input was not mutated.

    def test_admission_release_is_idempotent_and_success_transfers_to_worker(self):
        raw,_=wav_bytes()
        payload={'raw_audio':raw,'title':'Song','theme':'auto','seed':7,'lyrics':''}
        def normalize(source,target): return normalize_uploaded_audio(source.read_bytes(),target)
        with tempfile.TemporaryDirectory() as directory, patch.object(app,'ROOT',Path(directory)), patch.object(app,'model_status',return_value={'available':True}), patch.object(app,'comfy_ready',return_value={'available':True}), patch.object(app,'available_ram_gb',return_value=8), patch.object(app.threading,'Thread'), patch.object(app,'normalize_upload_subprocess',side_effect=normalize):
            manager=app.JobManager()
            admission=manager.reserve_upload()
            with self.assertRaises(app.RequestError): manager.reserve_upload()
            manager.create(payload,admission=admission)
            admission.release(); admission.release()  # HTTP finally must not release worker's slot.
            self.assertFalse(manager.capacity.acquire(blocking=False))
            manager.capacity.release()  # Simulate the worker's one finally release.
            self.assertTrue(manager.capacity.acquire(blocking=False))
            self.assertFalse(manager.capacity.acquire(blocking=False))
            manager.capacity.release()

    def test_normalization_failure_releases_exactly_once_for_retry(self):
        payload={'raw_audio':b'bad','title':'Song','theme':'auto','seed':7,'lyrics':''}
        with tempfile.TemporaryDirectory() as directory, patch.object(app,'ROOT',Path(directory)), patch.object(app,'model_status',return_value={'available':True}), patch.object(app,'comfy_ready',return_value={'available':True}), patch.object(app,'available_ram_gb',return_value=8), patch.object(app,'normalize_upload_subprocess',side_effect=app.RequestError('timeout',408)):
            manager=app.JobManager(); admission=manager.reserve_upload()
            with self.assertRaises(app.RequestError): manager.create(payload,admission=admission)
            admission.release()
            self.assertTrue(manager.capacity.acquire(blocking=False))
            self.assertFalse(manager.capacity.acquire(blocking=False))
            manager.capacity.release()


class SubprocessNormalizationTests(unittest.TestCase):
    def test_actual_isolated_normalizer_child_finishes_and_is_reaped(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'temp') as directory:
            source=Path(directory)/'upload.audio'; target=Path(directory)/'source.wav'
            source.write_bytes(wav_bytes()[0])
            spawned=[]; real_spawn=subprocess.Popen
            def spawn(*args,**kwargs):
                process=real_spawn(*args,**kwargs); spawned.append(process); return process
            with patch.object(app.subprocess,'Popen',side_effect=spawn):
                result=app.normalize_upload_subprocess(source,target)
            self.assertEqual((result['sample_rate'],result['channels']),(44100,2))
            self.assertEqual(len(spawned),1)
            self.assertEqual(spawned[0].poll(),0)
            self.assertLess((Path(directory)/'normalization.log').stat().st_size,1024)

    def test_watchdog_kills_and_reaps_only_its_hung_child_with_bounded_logs(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'temp') as directory:
            source=Path(directory)/'upload.audio'; target=Path(directory)/'source.wav'
            source.write_bytes(b'private test fixture')
            spawned=[]; real_spawn=subprocess.Popen
            def hung(*args,**kwargs):
                process=real_spawn([sys.executable,'-c','import sys,time; sys.stderr.write("x"*1000000); sys.stderr.flush(); time.sleep(30)'],**kwargs)
                spawned.append(process); return process
            with patch.object(app.subprocess,'Popen',side_effect=hung):
                with self.assertRaises(app.RequestError) as caught:
                    app.normalize_upload_subprocess(source,target,timeout=.1)
            self.assertEqual(caught.exception.status,408)
            self.assertEqual(len(spawned),1)
            self.assertIsNotNone(spawned[0].poll())
            self.assertLess((Path(directory)/'normalization.log').stat().st_size,1024)
            self.assertEqual(source.read_bytes(),b'private test fixture')


if __name__=='__main__': unittest.main()
