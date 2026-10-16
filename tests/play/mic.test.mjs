import test from 'node:test';
import assert from 'node:assert/strict';
import { BrowserMicrophone, fiveSecondMono, microphoneError } from '../../web/play/mic.mjs';

const flush = () => new Promise(resolve => setImmediate(resolve));
const node = () => ({ connect() {}, disconnect() { this.disconnected = true; } });
function fixture({ sampleRate = 44100, media, resumeError } = {}) {
  const track = { readyState: 'live', onended: null, stop() { this.readyState = 'ended'; } }, stream = { getTracks: () => [track] };
  let context;
  class Audio {
    constructor() { context = this; this.sampleRate = sampleRate; this.state = 'suspended'; this.destination = node(); }
    resume() { if (resumeError) return Promise.reject(resumeError); this.state = 'running'; return Promise.resolve(); }
    close() { this.state = 'closed'; return Promise.resolve(); }
    createMediaStreamSource() { return node(); }
    createScriptProcessor() { this.processor = { ...node(), onaudioprocess: null }; return this.processor; }
    createGain() { return { ...node(), gain: { value: 1 } }; }
  }
  const recorder = new BrowserMicrophone({ mediaDevices: { getUserMedia: media || (() => Promise.resolve(stream)) }, AudioContextClass: Audio, secure: true });
  return { recorder, Audio, stream, track, get context() { return context; } };
}

test('five seconds of real input frames are trimmed exactly, resampled, then all tracks stop', async () => {
  const f = fixture(); let lastElapsed = 0, started = 0;
  const pending = f.recorder.record({ onStarted: () => started++, onTick: elapsed => { lastElapsed = elapsed; } });
  await flush();
  assert.equal(started, 1);
  let sent = 0;
  while (f.context.processor.onaudioprocess) { const input = new Float32Array(2048).fill(.2); f.context.processor.onaudioprocess({ inputBuffer: { getChannelData: () => input } }); sent += input.length; }
  const output = await pending;
  assert.ok(sent >= 44100 * 5 && sent < 44100 * 5 + 2048);
  assert.equal(output.samples.length, 110250); assert.equal(output.sampleRate, 22050);
  assert.equal(lastElapsed, 5); assert.equal(f.track.readyState, 'ended'); assert.equal(f.context.state, 'closed'); assert.equal(f.recorder.session, null);
  assert.ok(output.samples.every(value => Math.abs(value - .2) < 1e-6));
});

test('cancel while browser permission is pending rejects promptly and stops a late stream', async () => {
  let permit; const f = fixture({ media: () => new Promise(resolve => { permit = resolve; }) });
  const pending = f.recorder.record(); const rejected = assert.rejects(pending, { name: 'AbortError' });
  f.recorder.cancel(); await rejected; assert.equal(f.context.state, 'closed');
  permit(f.stream); await flush(); assert.equal(f.track.readyState, 'ended');
});

test('permission denial closes audio and exposes actionable recovery', async () => {
  const error = new DOMException('Denied', 'NotAllowedError'); const f = fixture({ media: () => Promise.reject(error) });
  await assert.rejects(f.recorder.record(), { name: 'NotAllowedError' }); assert.equal(f.context.state, 'closed'); assert.match(microphoneError(error), /권한/);
});

test('audio resume failure also stops a stream that was already granted', async () => {
  const f = fixture({ resumeError: new Error('resume failed') });
  await assert.rejects(f.recorder.record(), /resume failed/); assert.equal(f.track.readyState, 'ended'); assert.equal(f.context.state, 'closed');
});

test('track interruption stops remaining capture without returning a short fake take', async () => {
  const f = fixture(); const pending = f.recorder.record(); await flush();
  f.track.onended(); await assert.rejects(pending, /끊겼/); assert.equal(f.context.state, 'closed'); assert.equal(f.track.readyState, 'ended');
});

test('external unlocked AudioContext remains caller owned on cancellation', async () => {
  const f = fixture(); const audioContext = new f.Audio(); const pending = f.recorder.record({ audioContext }); await flush();
  const rejected = assert.rejects(pending, { name: 'AbortError' }); f.recorder.cancel(); await rejected;
  assert.equal(audioContext.state, 'running'); assert.equal(f.track.readyState, 'ended'); await audioContext.close();
});

test('insecure contexts never request microphone permission', async () => {
  let called = false; const recorder = new BrowserMicrophone({ secure: false, mediaDevices: { getUserMedia: () => { called = true; } } });
  await assert.rejects(recorder.record(), { name: 'SecurityError' }); assert.equal(called, false);
});

test('synthetic/file normalization pads short audio and clamps unsafe samples', () => {
  const source = new Float32Array([2, -2, NaN, .5]); const result = fiveSecondMono(source, 22050);
  assert.equal(result.length, 110250); assert.deepEqual([...result.slice(0, 5)], [1, -1, 0, .5, 0]);
  assert.ok(result.slice(4).every(value => value === 0)); assert.throws(() => fiveSecondMono(source, 0));
});
