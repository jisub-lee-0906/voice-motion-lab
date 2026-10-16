import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { syntheticFixture, pcm16Fixture } from './analysis-fixtures.mjs';

export function fixtureSamples(fixture) {
  return fixture.source.kind === 'published_bop'
    ? pcm16Fixture(readFileSync(new URL('../../web/production/audio/bop-bounce.wav', import.meta.url)))
    : syntheticFixture(fixture.source, fixture.sampleRate);
}

export function compareAnalysis(actual, expected, tolerances = {}) {
  assert.equal(actual.version, 1);
  assert.equal(actual.engine, expected.engine);
  assert.equal(actual.sample_rate, expected.sample_rate);
  assert.equal(actual.duration, expected.duration);
  assert.equal(actual.frames.length, expected.frames.length);
  const maximum = {};
  let lastTime = -1, lastProgress = -1;
  for (let index = 0; index < actual.frames.length; index++) {
    const frame = actual.frames[index];
    assert.ok(frame.time > lastTime);
    assert.ok(frame.progress >= lastProgress);
    lastTime = frame.time; lastProgress = frame.progress;
    for (const key of ['time', 'energy', 'pitch', 'pitch_confidence', 'onset', 'progress']) {
      assert.ok(Number.isFinite(frame[key]), `nonfinite ${key} at ${index}`);
      if (key !== 'time') assert.ok(frame[key] >= 0 && frame[key] <= 1, `range ${key} at ${index}`);
      const error = Math.abs(frame[key] - expected.frames[index][key]);
      maximum[key] = Math.max(maximum[key] || 0, error);
      assert.ok(error <= (tolerances[key] ?? .00000101), `frame ${index} ${key}: ${frame[key]} != ${expected.frames[index][key]} (error ${error})`);
    }
  }
  assert.equal(actual.events.length, expected.events.length, JSON.stringify(actual.events));
  for (let index = 0; index < actual.events.length; index++) {
    assert.equal(actual.events[index].kind, 'impact');
    assert.ok(Math.abs(actual.events[index].time - expected.events[index].time) <= 1 / actual.sample_rate + 1e-6);
    assert.ok(Math.abs(actual.events[index].strength - expected.events[index].strength) <= (tolerances.onset ?? .00000101));
  }
  assert.deepEqual(actual.warnings, expected.warnings);
  return maximum;
}
