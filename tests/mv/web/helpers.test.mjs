import test from 'node:test';
import assert from 'node:assert/strict';
import {MAX_UPLOAD_BYTES, MULTIPART_RESERVE_BYTES, formatTime, validateDuration, maxAudioFileBytes, validateAudioFile, inspectDecodedAudio, uploadMetadata, createUploadBody, artifactUrl, validateJob, completedElapsedSeconds, healthMessage, waveformPeaks} from '../../../web/mv/helpers.mjs';

test('audio duration is bounded before sending audio to the local server', () => {
  assert.doesNotThrow(() => validateDuration(60));
  for (const duration of [0,.9,-1,NaN,Infinity,60.1]) assert.throws(() => validateDuration(duration));
  assert.equal(formatTime(65), '1:05');
});

test('original file limits reserve room for multipart metadata and boundaries', () => {
  const limit = maxAudioFileBytes();
  assert.equal(limit, MAX_UPLOAD_BYTES - MULTIPART_RESERVE_BYTES);
  assert.equal(maxAudioFileBytes(1024 * 1024), 1024 * 1024 - MULTIPART_RESERVE_BYTES);
  assert.equal(maxAudioFileBytes(MAX_UPLOAD_BYTES * 2),limit);
  assert.doesNotThrow(() => validateAudioFile({size:limit}));
  for (const size of [0,-1,NaN,Infinity,1.5,limit+1]) assert.throws(() => validateAudioFile({size}));
});

test('decode preflight preserves anti-phase stereo and checks all channels without mixing', () => {
  const left = new Float32Array(48000).fill(.1);
  const right = new Float32Array(48000).fill(-.1);
  const audio = {duration:1,sampleRate:48000,numberOfChannels:2,length:48000,getChannelData:index => [left,right][index]};
  const result = inspectDecodedAudio(audio);
  assert.equal(result.sample_rate,48000);
  assert.equal(result.channels,2);
  assert.ok(result.rms > .099);
  assert.equal(left[0],Math.fround(.1));
  assert.equal(right[0],Math.fround(-.1));
  right[47999]=NaN;
  assert.throws(() => inspectDecodedAudio(audio));
  left.fill(0); right.fill(0);
  assert.throws(() => inspectDecodedAudio(audio),/소리가 거의 없는/);
  assert.throws(() => inspectDecodedAudio({...audio,numberOfChannels:0}));
  assert.throws(() => inspectDecodedAudio({...audio,sampleRate:Infinity}));
});

test('decode preflight matches server 8–96kHz mono or stereo limits', () => {
  const buffer = (rate, channels) => ({duration:1,sampleRate:rate,numberOfChannels:channels,length:rate,
    getChannelData:() => new Float32Array(rate).fill(.1)});
  for (const rate of [8000,44100,96000]) {
    for (const channels of [1,2]) assert.doesNotThrow(() => inspectDecodedAudio(buffer(rate,channels)));
  }
  for (const rate of [7999,96001,192000]) assert.throws(() => inspectDecodedAudio(buffer(rate,2)),/8–96kHz/);
  for (const channels of [0,3,6,32]) assert.throws(() => inspectDecodedAudio(buffer(48000,channels)),/모노 또는 스테레오/);
});

test('multipart uploads carry the unchanged original File and explicit metadata only', async () => {
  const bytes = new Uint8Array([82,73,70,70,0,255,128,47]);
  const file = new File([bytes], '../../my music.wav', {type:'audio/wav'});
  const body = createUploadBody(file,{title:' 나의 음악 ',theme:'orbit',seed:7,lyrics:'봄이 와요'});
  assert.deepEqual([...body.keys()],['metadata','audio']);
  assert.deepEqual(JSON.parse(body.get('metadata')),{title:'나의 음악',theme:'orbit',seed:7,lyrics:'봄이 와요'});
  const uploaded=body.get('audio');
  assert.equal(uploaded.name,'source-audio');
  assert.equal(uploaded.type,'audio/wav');
  assert.deepEqual(new Uint8Array(await uploaded.arrayBuffer()),bytes);
  assert.equal(file.name,'../../my music.wav');
  const request = new Request('http://127.0.0.1:8797/api/mv/uploads',{method:'POST',body});
  assert.match(request.headers.get('content-type'),/^multipart\/form-data; boundary=/);
});

test('upload metadata rejects malformed fields and fits the reserved envelope', () => {
  assert.deepEqual(uploadMetadata({title:'Song'}),{title:'Song',theme:'auto',seed:7});
  assert.equal('lyrics' in uploadMetadata({title:'Song',lyrics:'  '}),false);
  for (const patch of [{title:''},{title:'a\nb'},{theme:'bad'},{seed:NaN},{seed:-1},{lyrics:'a'.repeat(6001)},{lyrics:'a\0b'}]) {
    assert.throws(() => uploadMetadata({title:'Song',...patch}));
  }
  const largest = JSON.stringify(uploadMetadata({title:'음'.repeat(100),lyrics:'음'.repeat(6000)}));
  assert.ok(new TextEncoder().encode(largest).byteLength < MULTIPART_RESERVE_BYTES / 2);
});

test('only fixed same-origin job artifacts can become video or download URLs', () => {
  const origin = 'http://127.0.0.1:8797';
  const path = '/api/mv/jobs/abc-123/artifacts/video.mp4';
  assert.equal(artifactUrl(path,'abc-123','video.mp4',origin),origin+path);
  const preview = '/api/mv/jobs/abc-123/artifacts/preview.webm';
  assert.equal(artifactUrl(preview,'abc-123','preview.webm',origin),origin+preview);
  assert.throws(() => artifactUrl('https://evil.test'+preview,'abc-123','preview.webm',origin));
  assert.throws(() => artifactUrl(path,'abc-123','preview.webm',origin));
  for (const value of ['https://evil.test'+path,'javascript:alert(1)',path+'?redirect=1',path+'#x','/api/mv/jobs/other/artifacts/video.mp4','/private.wav']) {
    assert.throws(() => artifactUrl(value,'abc-123','video.mp4',origin));
  }
  assert.throws(() => artifactUrl(path,'../bad','video.mp4',origin));
  assert.throws(() => artifactUrl(path,'abc-123','anything.exe',origin));
});

test('polling refuses another job or unsupported status', () => {
  assert.equal(validateJob({id:'abc',status:'rendering'},'abc').status,'rendering');
  assert.throws(() => validateJob({id:'abc',status:'rendering'},'other'));
  assert.throws(() => validateJob({id:'abc',status:'made_up'}));
  assert.throws(() => validateJob({id:'../abc',status:'queued'}));
});

test('restored completed jobs use server execution time rather than time since page creation', () => {
  assert.equal(completedElapsedSeconds({status:'succeeded',created_at:1000,updated_at:1046}),46);
  assert.equal(completedElapsedSeconds({status:'failed',created_at:1000,updated_at:1002}),2);
  assert.equal(completedElapsedSeconds({status:'rendering',created_at:1000,updated_at:1046}),null);
  assert.equal(completedElapsedSeconds({status:'succeeded',created_at:Infinity,updated_at:1046}),null);
  assert.equal(completedElapsedSeconds({status:'succeeded',created_at:1047,updated_at:1046}),null);
  assert.equal(completedElapsedSeconds({status:'succeeded'}),null);
});

test('engine health has clear local setup errors and no cloud fallback', () => {
  assert.match(healthMessage(null),/로컬 서버/);
  assert.match(healthMessage({asr:{available:false}}),/음성 인식/);
  assert.match(healthMessage({asr:{available:true},comfy:{available:false}}),/영상 제작/);
  assert.equal(healthMessage({asr:{available:true},comfy:{available:true}}),'');
});

test('waveform bins cover short inputs and preserve transient peaks', () => {
  assert.deepEqual(waveformPeaks(new Float32Array([.5,-1,0])),[.5,1,0]);
  assert.deepEqual(waveformPeaks(new Float32Array([0,1,0,0]),2),[1,0]);
});
