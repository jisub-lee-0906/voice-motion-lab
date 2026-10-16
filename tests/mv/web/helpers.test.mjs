import test from 'node:test';
import assert from 'node:assert/strict';
import {formatTime, validateDuration, downmix, validateSamples, artifactUrl, validateJob, completedElapsedSeconds, healthMessage, waveformPeaks} from '../../../web/mv/helpers.mjs';

test('audio duration is bounded before sending audio to the local server', () => {
  assert.doesNotThrow(() => validateDuration(60));
  for (const duration of [0,-1,NaN,Infinity,60.1]) assert.throws(() => validateDuration(duration));
  assert.equal(formatTime(65), '1:05');
});

test('downmix averages stereo into finite bounded PCM without mutating source', () => {
  const left = new Float32Array([1,.5,-1]);
  const right = new Float32Array([-1,.5,-1]);
  assert.deepEqual(Array.from(downmix([left,right])), [0,.5,-1]);
  assert.deepEqual(Array.from(left), [1,.5,-1]);
  assert.throws(() => downmix([left,new Float32Array([1])]));
  assert.throws(() => downmix([new Float32Array([NaN])]));
});

test('outbound audio only accepts the 16kHz finite mono contract', () => {
  assert.doesNotThrow(() => validateSamples(new Float32Array([0,.2,-1])));
  assert.throws(() => validateSamples(new Float32Array([1.1])));
  assert.throws(() => validateSamples(new Float32Array([NaN])));
  assert.throws(() => validateSamples(new Float32Array(60 * 16000 + 1)));
  assert.throws(() => validateSamples(new Float32Array([0]),44100));
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
