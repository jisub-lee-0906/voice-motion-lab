import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { analyze, inferModelPitch } from '../../web/play/analysis.mjs';
import { fixtureSamples, compareAnalysis } from './analysis-reference.mjs';

const vendor = new URL('../../web/browser-proof/vendor/', import.meta.url);
const golden = JSON.parse(readFileSync(new URL('./fixtures/browser-analysis-golden.json', import.meta.url)));
const available = existsSync(new URL('ort.wasm.min.mjs', vendor)) && existsSync(new URL('basic_pitch.onnx', vendor));

test('actual single-thread WASM: all full five-second Python Basic Pitch goldens', { skip: !available, timeout: 60000 }, async (context) => {
  const ort = await import(new URL('ort.wasm.min.mjs', vendor));
  ort.env.wasm.numThreads = 1;
  ort.env.wasm.proxy = false;
  ort.env.wasm.wasmPaths = vendor.href;
  ort.env.logLevel = 'error';
  const session = await ort.InferenceSession.create(new Uint8Array(readFileSync(new URL('basic_pitch.onnx', vendor))), { executionProviders: ['wasm'] });
  let inputHashes = [];
  const run = async (chunk) => {
    inputHashes.push(createHash('sha256').update(new Uint8Array(chunk.buffer, chunk.byteOffset, chunk.byteLength)).digest('hex'));
    const input = new ort.Tensor('float32', chunk, [1, 43844, 1]);
    let raw;
    try {
      raw = await session.run({ [session.inputNames[0]]: input });
      return Object.fromEntries(Object.entries(raw).map(([name, tensor]) => [name, { data: Float32Array.from(tensor.data), dims: tensor.dims }]));
    } finally { input.dispose(); if (raw) for (const tensor of Object.values(raw)) tensor.dispose(); }
  };
  try {
    for (const fixture of golden.fixtures) await context.test(fixture.name, async () => {
      inputHashes = [];
      const samples = fixtureSamples(fixture);
      const model = (audio, rate, times) => inferModelPitch(audio, rate, times, run);
      const result = await analyze(samples, fixture.sampleRate, { ...fixture.options, engine: 'basic_pitch' }, model);
      assert.deepEqual(inputHashes, fixture.model_input_sha256, 'Every actual model input must match Python byte-for-byte.');
      // CPU and WASM have different floating-point kernels. Confidence may drift
      // by <0.0005; pitch, timing, energy and progress retain strict precision.
      const maximum = compareAnalysis(result, fixture.reference.basic_pitch, { pitch_confidence: .0005, onset: .00005 });
      context.diagnostic(`${fixture.name}: maximum errors ${JSON.stringify(maximum)}`);
      assert.equal(result.engine, 'basic_pitch');
      if (fixture.name === 'glide_hits') assert.ok(result.events.every((event) => event.time < .4 || Math.abs(event.time - 1.3) < .05 || Math.abs(event.time - 4.1) < .05));
    });
  } finally { await session.release(); }
});
