import test from 'node:test';
import assert from 'node:assert/strict';
import { abortable, frameTiming, normalizeVideoOptions, resampleMono, safeVideoName } from '../../web/play/media-utils.mjs';
import { finalizeWebMBytes } from '../../web/play/webm-finalize.mjs';
import { createMemeVideo, shareVideo } from '../../web/play/export-video.mjs';

const options = extra => ({ renderFrame() {}, samples: new Float32Array(110250), sampleRate: 22050, duration: 5, ...extra });

test('input is copied, trimmed/padded and clipped without mutating recorded PCM', () => {
  const source = new Float32Array([2, -.3, -2]);
  const normalized = normalizeVideoOptions(options({ samples: source }));
  assert.equal(normalized.samples.length, 110250);
  assert.deepEqual([...normalized.samples.slice(0, 4)], [1, source[1], -1, 0]);
  assert.equal(source[0], 2);
  normalized.samples[1] = 0;
  assert.notEqual(source[1], 0);
});

test('invalid dimensions and corrupt audio fail before allocating browser media', () => {
  assert.throws(() => normalizeVideoOptions(options({ width: 721 })), RangeError);
  assert.throws(() => normalizeVideoOptions(options({ duration: Infinity })), RangeError);
  assert.throws(() => normalizeVideoOptions(options({ samples: [0, NaN] })), TypeError);
  assert.throws(() => normalizeVideoOptions(options({ samples: [false] })), TypeError);
});

test('resampling preserves a 440 Hz signal and produces exactly five seconds', () => {
  const input = Float32Array.from({ length: 110250 }, (_, n) => .4 * Math.sin(2 * Math.PI * 440 * n / 22050));
  const output = resampleMono(input, 22050, 48000, 5);
  assert.equal(output.length, 240000);
  let error = 0;
  for (let i = 0; i < output.length - 3; ++i) error += (output[i] - .4 * Math.sin(2 * Math.PI * 440 * i / 48000)) ** 2;
  assert.ok(Math.sqrt(error / output.length) < .001);
});

test('frame timestamps are contiguous and end at exact duration', () => {
  for (const duration of [5, 5.017]) {
    let end = 0;
    for (let n = 0; n < Math.ceil(duration * 24); ++n) {
      const frame = frameTiming(n, 24, duration);
      assert.equal(frame.timestamp, end);
      assert.ok(frame.duration > 0);
      end = frame.timestamp + frame.duration;
    }
    assert.equal(end, Math.round(duration * 1e6));
  }
});

test('pending media operations reject promptly when cancelled', async () => {
  const controller = new AbortController();
  const pending = abortable(new Promise(() => {}), controller.signal);
  controller.abort();
  await assert.rejects(pending, error => error.name === 'AbortError');
  await assert.rejects(createMemeVideo(options({ signal: controller.signal })), error => error.name === 'AbortError');
});

test('file extension always reflects the actual video container', () => {
  assert.equal(safeVideoName('../../내 밈.mp4', 'webm'), '내-밈.webm');
  assert.equal(safeVideoName('BOP!!.webm', 'mp4'), 'BOP.mp4');
});

const join = parts => Uint8Array.from(parts.flatMap(p => [...p]));
const element = (id, payload) => join([id, [128 + payload.length], payload]);
const ascii = text => new TextEncoder().encode(text);
function minimalLiveWebM() {
  const ebml = element([0x1a, 0x45, 0xdf, 0xa3], element([0x42, 0x82], ascii('webm')));
  const info = element([0x15, 0x49, 0xa9, 0x66], element([0x2a, 0xd7, 0xb1], [0x0f, 0x42, 0x40]));
  const track = element([0xae], join([element([0xd7], [1]), element([0x83], [1]), element([0x86], ascii('V_VP8'))]));
  const tracks = element([0x16, 0x54, 0xae, 0x6b], track);
  const block = element([0xa3], [0x81, 0, 0, 0x80, 0xab, 0xcd, 0xef]);
  const clusterPayload = join([element([0xe7], [0]), block]);
  const cluster = join([[0x1f, 0x43, 0xb6, 0x75], [0xff], clusterPayload]);
  return join([ebml, [0x18, 0x53, 0x80, 0x67], [0x01, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff], info, tracks, cluster]);
}

test('live WebM gains finite Duration, SeekHead and Cues; payload stays intact', () => {
  const original = minimalLiveWebM();
  const finalized = finalizeWebMBytes(original, 5);
  const buffer = Buffer.from(finalized);
  const durationPos = buffer.indexOf(Buffer.from([0x44, 0x89, 0x88]));
  assert.ok(durationPos > 0);
  assert.equal(buffer.readDoubleBE(durationPos + 3), 5000);
  assert.ok(buffer.indexOf(Buffer.from([0x11, 0x4d, 0x9b, 0x74])) > 0);
  assert.ok(buffer.indexOf(Buffer.from([0x1c, 0x53, 0xbb, 0x6b])) > 0);
  assert.ok(buffer.includes(Buffer.from([0x81, 0, 0, 0x80, 0xab, 0xcd, 0xef])));
  assert.deepEqual(finalizeWebMBytes(finalized, 5), finalized, 'Finalizing twice should not duplicate metadata');
});

test('broken WebM is rejected rather than returned with fake MP4 metadata', () => {
  assert.throws(() => finalizeWebMBytes(new Uint8Array([0, 1, 2]), 5));
});

function browserMocks() {
  const saved = new Map();
  const install = (key, value) => { saved.set(key, Object.getOwnPropertyDescriptor(globalThis, key)); Object.defineProperty(globalThis, key, { configurable: true, writable: true, value }); };
  const resources = { tracks: [], contexts: [], sources: [], canvases: [] };
  const track = kind => { const t = { kind, stopped: false, stop() { this.stopped = true; } }; resources.tracks.push(t); return t; };
  class Stream { constructor(tracks) { this.tracks = tracks; } getTracks() { return this.tracks; } getVideoTracks() { return this.tracks.filter(t => t.kind === 'video'); } getAudioTracks() { return this.tracks.filter(t => t.kind === 'audio'); } }
  class Context extends EventTarget {
    constructor() { super(); this.state = 'running'; this.created = performance.now(); this.closedByExporter = false; resources.contexts.push(this); }
    get currentTime() { return (performance.now() - this.created) / 1000; }
    createBuffer(channels, length, rate) { return { duration: length / rate, copyToChannel() {} }; }
    createMediaStreamDestination() { return { stream: new Stream([track('audio')]), disconnect() {} }; }
    createBufferSource() { const source = { connected: null, disconnectCalled: false, timer: null,
      connect(destination) { this.connected = destination; }, disconnect() { this.disconnectCalled = true; },
      start() { this.timer = setTimeout(() => this.onended?.(), this.buffer.duration * 1000); }, stop() { clearTimeout(this.timer); } };
      resources.sources.push(source); return source; }
    async resume() { this.state = 'running'; }
    async close() { this.closedByExporter = true; this.state = 'closed'; }
  }
  class Recorder {
    static isTypeSupported(type) { return type.startsWith('video/mp4'); }
    constructor(stream, options) { this.stream = stream; this.mimeType = options.mimeType; this.state = 'inactive'; }
    start() { this.state = 'recording'; }
    stop() { this.state = 'inactive'; queueMicrotask(() => { this.ondataavailable?.({ data: new Blob(['mock MP4 test payload']) }); this.onstop?.(); }); }
  }
  const doc = new EventTarget(); doc.hidden = false;
  doc.createElement = kind => { assert.equal(kind, 'canvas'); const canvas = { getContext() { return {}; }, captureStream() { return new Stream([track('video')]); } }; resources.canvases.push(canvas); return canvas; };
  for (const [key, value] of Object.entries({ document: doc, AudioContext: Context, MediaRecorder: Recorder, MediaStream: Stream, VideoEncoder: undefined, AudioEncoder: undefined, navigator: {} })) install(key, value);
  return { resources, Context, restore() { for (const [key, descriptor] of saved) { if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key]; } } };
}

test('realtime fallback includes original audio, avoids speakers, releases owned context', async () => {
  const mocks = browserMocks();
  try {
    const video = await createMemeVideo(options({ duration: .06, samples: new Float32Array(1323) }));
    assert.equal(video.method, 'mediarecorder-mp4');
    assert.ok(video.blob.size > 0);
    assert.ok(mocks.resources.tracks.every(t => t.stopped));
    assert.ok(mocks.resources.contexts.every(c => c.closedByExporter));
    assert.ok(mocks.resources.sources.every(s => s.connected?.stream?.getAudioTracks().length === 1 && s.disconnectCalled));
  } finally { mocks.restore(); }
});

test('cancelling realtime recording settles and preserves caller-owned AudioContext', async () => {
  const mocks = browserMocks(), controller = new AbortController();
  try {
    const context = new mocks.Context();
    await assert.rejects(createMemeVideo(options({ audioContext: context, signal: controller.signal,
      onProgress({ message }) { if (message.includes('5초 동안')) controller.abort(); } })), error => error.name === 'AbortError');
    assert.equal(context.closedByExporter, false);
    assert.ok(mocks.resources.tracks.every(t => t.stopped));
    assert.ok(mocks.resources.sources.every(s => s.disconnectCalled));
  } finally { mocks.restore(); }
});

test('cancelling fast encoding closes codecs, AudioData and already rendered VideoFrames', async () => {
  const saved = new Map(), resources = { encoders: [], audio: [], frames: [] };
  const install = (key, value) => { saved.set(key, Object.getOwnPropertyDescriptor(globalThis, key)); Object.defineProperty(globalThis, key, { configurable: true, writable: true, value }); };
  class Encoder {
    static async isConfigSupported() { return { supported: true }; }
    constructor() { this.state = 'unconfigured'; this.encodeQueueSize = 0; resources.encoders.push(this); }
    configure() { this.state = 'configured'; }
    encode() { this.encodeQueueSize++; }
    async flush() { this.encodeQueueSize = 0; }
    close() { this.state = 'closed'; }
  }
  class Frame { constructor() { this.closed = false; resources.frames.push(this); } close() { this.closed = true; } }
  class Audio { constructor() { this.closed = false; resources.audio.push(this); } close() { this.closed = true; } }
  install('VideoEncoder', Encoder); install('AudioEncoder', Encoder); install('VideoFrame', Frame); install('AudioData', Audio);
  install('document', { createElement() { return { getContext() { return {}; } }; } });
  const controller = new AbortController();
  try {
    await assert.rejects(createMemeVideo(options({ duration: .15, samples: new Float32Array(3308), signal: controller.signal,
      renderFrame(_context, time) { if (time > 0) controller.abort(); } })), error => error.name === 'AbortError');
    assert.equal(resources.encoders.length, 2);
    assert.ok(resources.encoders.every(encoder => encoder.state === 'closed'));
    assert.ok(resources.audio.length > 0 && resources.audio.every(data => data.closed));
    assert.equal(resources.frames.length, 1);
    assert.equal(resources.frames[0].closed, true);
  } finally { for (const [key, descriptor] of saved) { if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key]; } }
});

test('native file share cancellation is a normal result and does not download', async () => {
  const mocks = browserMocks();
  try {
    navigator.canShare = data => data.files?.[0]?.type === 'video/webm';
    navigator.share = async () => { throw new DOMException('Cancelled', 'AbortError'); };
    const result = await shareVideo({ blob: new Blob(['video'], { type: 'video/webm' }), mimeType: 'video/webm', extension: 'webm' });
    assert.equal(result.method, 'cancelled');
    assert.equal(result.filename, 'bop-meme.webm');
  } finally { mocks.restore(); }
});
