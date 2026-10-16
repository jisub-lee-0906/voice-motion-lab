import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { analyze, analyzeSignal, inferModelPitch } from '../../web/play/analysis.mjs';
import { syntheticFixture } from './analysis-fixtures.mjs';
import { fixtureSamples, compareAnalysis } from './analysis-reference.mjs';

const golden = JSON.parse(readFileSync(new URL('./fixtures/browser-analysis-golden.json', import.meta.url)));
for (const fixture of golden.fixtures) test(`full Python signal golden: ${fixture.name}`, () => {
  const result = analyzeSignal(fixtureSamples(fixture), fixture.sampleRate, fixture.options);
  compareAnalysis(result, fixture.reference.signal);
  if (['silence', 'low_noise'].includes(fixture.name)) {
    assert.deepEqual(result.events, []);
    assert.ok(result.frames.every((frame) => frame.energy === 0 && frame.progress === 0 && frame.pitch === .5 && frame.pitch_confidence === 0));
  }
  if (fixture.name === 'tone_holds') {
    const held = result.frames.filter((frame) => frame.time > 1.3 && frame.time < 2.8);
    assert.equal(new Set(held.map((frame) => frame.progress)).size, 1);
    assert.ok(held.every((frame) => frame.energy === 0));
  }
});

test('real signal fallback is explicit; explicit Basic Pitch never pretends success', async () => {
  const samples = syntheticFixture({ kind: 'tone_holds', duration: 5 }, 22050);
  const signal = analyzeSignal(samples, 22050);
  const fallback = await analyze(samples, 22050);
  assert.equal(fallback.engine, 'signal');
  assert.ok(fallback.warnings.some((warning) => warning.includes('AI 모델 분석 결과가 아닙니다')));
  assert.deepEqual(fallback.frames, signal.frames);
  await assert.rejects(analyze(samples, 22050, { engine: 'basic_pitch' }), /Basic Pitch/);
  const invalidModel = async () => ({ pitch: [NaN], confidence: [], onset: [] });
  const invalidFallback = await analyze(samples, 22050, {}, invalidModel);
  assert.deepEqual(invalidFallback.frames, signal.frames);
  const abort = new Error('cancel'); abort.name = 'AbortError';
  await assert.rejects(analyze(samples, 22050, {}, async () => { throw abort; }), { name: 'AbortError' });
});

test('invalid inputs, maximum duration, empty data and clipping preserve contract', () => {
  for (const [samples, rate, options] of [
    [[NaN], 16000, {}], [[Infinity], 16000, {}], [[true], 16000, {}], [[[.1]], 16000, {}],
    [[.1], true, {}], [[.1], 7999, {}], [[.1], 96001, {}], [[.1], 22050.5, {}],
    [[.1], 16000, { sensitivity: 0 }], [[.1], 16000, { smoothing: NaN }],
    [new Float32Array(8000 * 15 + 1), 8000, {}],
  ]) assert.throws(() => analyzeSignal(samples, rate, options));
  const empty = analyzeSignal([], 16000);
  assert.deepEqual(empty.frames, [{ time: 0, energy: 0, pitch: .5, pitch_confidence: 0, onset: 0, progress: 0 }]);
  const maximum = analyzeSignal(new Float32Array(8000 * 15), 8000);
  assert.equal(maximum.frames.length, 901);
  assert.equal(maximum.frames.at(-1).time, 15);
  assert.ok(analyzeSignal([8, -8], 8000).warnings.some((warning) => warning.includes('클리핑')));
});

test('adversarial model note starts cannot turn a smooth glide into repeated impacts', async () => {
  const rate = 22050;
  const samples = syntheticFixture({ kind: 'glide_hits', duration: 5 }, rate);
  const output = await analyze(samples, rate, { engine: 'basic_pitch' }, async (_audio, _rate, times) => ({
    pitch: Float64Array.from(times, (time) => .2 + time * .1),
    confidence: new Float64Array(times.length).fill(.9), onset: new Float64Array(times.length).fill(1),
  }));
  assert.ok(output.events.some((event) => Math.abs(event.time - 1.3) < .04));
  assert.ok(output.events.some((event) => Math.abs(event.time - 4.1) < .04));
  assert.ok(output.events.every((event) => event.time < .4 || Math.abs(event.time - 1.3) < .05 || Math.abs(event.time - 4.1) < .05));
});

test('overlapping model chunks keep their absolute times, pad safely and bound work', async () => {
  const rate = 22050;
  const audio = Float64Array.from({ length: rate * 5 }, (_, index) => index / (rate * 5));
  const times = Float64Array.from({ length: 301 }, (_, index) => index / 60);
  const chunks = [];
  const run = async (chunk) => {
    chunks.push(chunk);
    const note = new Float32Array(172 * 88), onset = new Float32Array(172 * 88);
    for (let frame = 0; frame < 172; frame++) { note[frame * 88 + 27 + (chunks.length - 1) * 6] = .8; onset[frame * 88] = .4; }
    return { 'StatefulPartitionedCall:1': { data: note, dims: [1, 172, 88] }, 'StatefulPartitionedCall:2': { data: onset, dims: [1, 172, 88] } };
  };
  const result = await inferModelPitch(audio, rate, times, run);
  assert.equal(chunks.length, 4);
  assert.ok(chunks[0].subarray(0, 3840).every((sample) => sample === 0));
  assert.equal(chunks[0][3841], Math.fround(audio[1]));
  assert.equal(chunks[1][0], Math.fround(audio[36164 - 3840]));
  assert.ok(result.pitch[120] > result.pitch[60]);
  assert.equal(result.confidence[60], Math.fround(.8));
  assert.equal(result.confidence.at(-1), 0);
  await assert.rejects(inferModelPitch(new Float64Array(rate * 21), rate, times, run), /안전 한도/);
});
