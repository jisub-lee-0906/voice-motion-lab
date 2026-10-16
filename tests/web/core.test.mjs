import test from 'node:test';
import assert from 'node:assert/strict';
import { motionAt, sampleKeyframes, animatedSvg, validateSvgSource, validateSvgAttributeValue, validateAnalysis, validateProject, downsampleMono, normalizeSettings, assetBox, DEFAULT_SETTINGS } from '../../web/core.mjs';

const frame = (time, progress, extras = {}) => ({ time, energy: .5, pitch: .5, pitch_confidence: 1, onset: 0, progress, ...extras });
const analysis = { version: 1, duration: 4, sample_rate: 8000, engine: 'signal', frames: [frame(0, 0), frame(1, .4), frame(2, .4, { energy: 0, pitch_confidence: 0 }), frame(3, .4, { energy: 0, pitch_confidence: 0 }), frame(4, 1)], events: [{ time: 1, kind: 'impact', strength: .8 }], warnings: [] };
const svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100"><path fill="#abc" d="M0 0H200V100H0Z"/></svg>';

test('silence holds path progress rather than advancing on elapsed time', () => {
  const quiet = { ...analysis, events: [] };
  for (const preset of ['fly', 'bounce', 'orbit']) assert.deepEqual(motionAt(2, quiet, { ...DEFAULT_SETTINGS, preset }), motionAt(3, quiet, { ...DEFAULT_SETTINGS, preset }));
  assert.notEqual(motionAt(0, quiet).x, motionAt(4, quiet).x);
});
test('an all-silent take has no movement or impacts', () => {
  const silent = { ...analysis, frames: [frame(0, 0, { energy: 0, pitch_confidence: 0 }), frame(4, 0, { energy: 0, pitch_confidence: 0 })], events: [] };
  assert.deepEqual(motionAt(0, silent), motionAt(4, silent)); assert.equal(motionAt(2, silent).scaleX, 1);
});
test('the impact is local in time, squashes proportionately and then settles', () => {
  assert.equal(motionAt(.9, analysis).scaleX, 1); assert.ok(motionAt(1.07, analysis).scaleX > 1); assert.ok(motionAt(1.07, analysis).scaleY < 1);
  assert.equal(motionAt(2, analysis).scaleX, 1); assert.equal(motionAt(1.07, analysis, { intensity: 0 }).scaleX, 1);
});
test('default preset bounds contain a 196px asset throughout the trajectory', () => {
  const take = { ...analysis, frames: [frame(0, 0), frame(4, 1)], events: [] };
  for (const preset of ['fly', 'bounce', 'orbit']) for (const key of sampleKeyframes(take, { ...DEFAULT_SETTINGS, preset })) {
    assert.ok(key.x >= 98 && key.x <= 862, `${preset}: x=${key.x}`); assert.ok(key.y >= 98 && key.y <= 442, `${preset}: y=${key.y}`);
    for (const value of Object.values(key)) assert.ok(Number.isFinite(value));
  }
});
function rotatedBounds(m) {
  const radians = m.rotation * Math.PI / 180, c = Math.abs(Math.cos(radians)), s = Math.abs(Math.sin(radians));
  const hx = 98 * (Math.abs(m.scaleX) * c + Math.abs(m.scaleY) * s), hy = 98 * (Math.abs(m.scaleX) * s + Math.abs(m.scaleY) * c);
  return [m.x - hx, m.y - hy, m.x + hx, m.y + hy];
}
test('all advertised intensities keep rotated impact-scaled art inside the stage', () => {
  for (const preset of ['fly', 'bounce', 'orbit']) for (const intensity of [0, .5, 1, 1.01, 1.2, 1.5, 1.8]) for (const pitch of [0, .5, 1]) for (const smoothing of [0, 1]) {
    const take = { ...analysis, duration: 5, frames: Array.from({ length: 301 }, (_, n) => frame(n / 60, n / 300, { pitch })), events: Array.from({ length: 46 }, (_, n) => ({ time: n * .11, kind: 'impact', strength: 1 })) };
    for (let n = 0; n <= 1200; n++) {
      const bounds = rotatedBounds(motionAt(n / 240, take, { ...DEFAULT_SETTINGS, preset, intensity, smoothing }));
      assert.ok(bounds[0] >= 0 && bounds[1] >= 0 && bounds[2] <= 960 && bounds[3] <= 540, `${preset}/${intensity}/pitch ${pitch}/smooth ${smoothing}/t ${n / 240}: ${bounds}`);
    }
  }
});
test('100% trajectories retain their original positions and rotations', () => {
  const take = { ...analysis, frames: [frame(0, 0, { pitch: 1 }), frame(4, 1, { pitch: 1 })], events: [] };
  assert.deepEqual(motionAt(1, take), { x: 340, y: 230, scaleX: 1, scaleY: 1, rotation: 7, opacity: 1 });
  const bounce = motionAt(1, take, { ...DEFAULT_SETTINGS, preset: 'bounce' });
  assert.ok(Math.abs(bounce.x - 350) < 1e-9); assert.ok(Math.abs(bounce.y - (330 - Math.sin(.75 * Math.PI) * 138 - 17.5)) < 1e-9);
  const orbit = motionAt(1, take, { ...DEFAULT_SETTINGS, preset: 'orbit' });
  assert.deepEqual(orbit, { x: 660, y: 258, scaleX: 1, scaleY: 1, rotation: 0, opacity: 1 });
});
test('180% remains expressive without clamped edge dwell, and silence still holds', () => {
  const take = { ...analysis, frames: [frame(0, 0), frame(4, 1)], events: [] };
  const high = { ...DEFAULT_SETTINGS, intensity: 1.8 };
  for (const preset of ['fly', 'bounce', 'orbit']) {
    const base = sampleKeyframes(take, { ...DEFAULT_SETTINGS, preset }), strong = sampleKeyframes(take, { ...high, preset });
    const span = frames => Math.max(...frames.map(f => f.x)) - Math.min(...frames.map(f => f.x));
    assert.ok(span(strong) > span(base) * 1.05, `${preset} horizontal expression remains stronger`);
    const quiet = { ...analysis, events: [] };
    assert.deepEqual(motionAt(2, quiet, { ...high, preset }), motionAt(3, quiet, { ...high, preset }));
  }
  const first = motionAt(0, take, high).x, second = motionAt(.1, take, high).x, third = motionAt(.2, take, high).x;
  assert.ok(second > first); assert.ok(Math.abs((third - second) - (second - first)) < 1e-9);
  const low = motionAt(0, take, { ...high, preset: 'orbit' });
  assert.ok(Math.abs(low.rotation) > 13, 'high-intensity rotation is retained');
});
test('exported SVG uses exactly the sampled preview trajectory and retains the original path', () => {
  const settings = { ...DEFAULT_SETTINGS, preset: 'orbit', intensity: .8 }, output = animatedSvg(svg, analysis, settings, '<test>');
  assert.match(output, /&lt;test&gt;/); assert.ok(output.includes('M0 0H200V100H0Z')); assert.ok(!output.includes('<script'));
  const values = output.match(/type="translate"[^>]*values="([^"]+)"/)[1].split(';'), sampled = sampleKeyframes(analysis, settings);
  assert.equal(values.length, sampled.length);
  sampled.forEach((actual, index) => { const [x, y] = values[index].split(' ').map(Number); assert.ok(Math.abs(x - actual.x) <= .000051); assert.ok(Math.abs(y - actual.y) <= .000051); });
  assert.equal(sampled.at(-1).time, analysis.duration); assert.match(output, /dur="4\.0000s"/);
});
test('transparent SVG export omits the background while keeping stage dimensions', () => {
  const output = animatedSvg(svg, analysis, { ...DEFAULT_SETTINGS, transparent: true }); assert.ok(!output.includes('<rect width="960"')); assert.match(output, /viewBox="0 0 960 540"/);
});
test('audio downsampling averages stereo channels and prevents out-of-range samples', () => {
  const mono = downsampleMono([new Float32Array([1, 1, -1, -1]), new Float32Array([0, 0, 0, 0])], 16000, 8000); assert.deepEqual([...mono], [.5, -.5]);
  assert.deepEqual([...downsampleMono([new Float32Array([NaN, Infinity, 2, -2])], 8000, 8000)], [0, 0, 1, -1]);
});
test('SVG lexical boundary rejects code, document entities and external resources', () => {
  for (const hostile of ['<svg><script>alert(1)</script></svg>', '<svg onload="alert(1)"/>', '<svg><foreignObject><div>html</div></foreignObject></svg>', '<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///secret">]><svg/>', '<svg><use href="https://example.com/a.svg#x"/></svg>', '<svg><image href="data:image/svg+xml,x"/></svg>', '<svg><style>@import "https://example.com"</style></svg>', '<svg><animate attributeName="href" to="javascript:1"/></svg>']) assert.throws(() => validateSvgSource(hostile));
  assert.equal(validateSvgSource(svg), svg); assert.doesNotThrow(() => validateSvgSource('<svg><use href="#local"/></svg>'));
});
test('decoded presentation attributes reject CSS escapes and keep local references', () => {
  for (const name of ['fill', 'stroke', 'filter', 'clip-path', 'mask', 'marker-start', 'style']) {
    // These are the actual post-XML-decoding values, including &#92;/&#x5c; input.
    for (const value of ['u\\72l(https://example.invalid/resource.svg)', '\\75rl(https://example.invalid/resource.svg)', 'u\\000072l(https://example.invalid/resource.svg)', 'u\\\nrl(https://example.invalid/resource.svg)', 'u/**/rl(https://example.invalid/resource.svg)']) assert.throws(() => validateSvgAttributeValue(name, value));
  }
  assert.equal(validateSvgAttributeValue('fill', 'url(#gradient)'), 'url(#gradient)');
  assert.equal(validateSvgAttributeValue('filter', 'url("#shadow")'), 'url("#shadow")');
  assert.equal(validateSvgAttributeValue('style', 'fill:url(#gradient);stroke:#fff'), 'fill:url(#gradient);stroke:#fff');
  assert.throws(() => validateSvgAttributeValue('fill', 'url(https://example.invalid/x)'));
});
test('project import validates finite audio, timeline order and source/analysis duration agreement', () => {
  const project = { format: 'voice-motion-project', version: 1, asset: { name: 'x.svg', svg }, audio: { sample_rate: 8000, samples: Array(32000).fill(0) }, analysis, settings: DEFAULT_SETTINGS };
  assert.equal(validateProject(project).format, 'voice-motion-project'); assert.throws(() => validateProject({ ...project, audio: { ...project.audio, samples: [NaN] } }));
  assert.throws(() => validateProject({ ...project, analysis: { ...analysis, duration: 2 } })); assert.throws(() => validateAnalysis({ ...analysis, frames: [frame(2, .8), frame(1, .9)] }));
  assert.throws(() => validateAnalysis({ ...analysis, frames: [frame(0, .8), frame(1, .3)] })); assert.throws(() => validateAnalysis({ ...analysis, events: [{ time: 1, kind: 'impact', strength: Infinity }] }));
});
test('settings constrain user input and source aspect ratio is preserved', () => {
  assert.equal(normalizeSettings({ intensity: Infinity, preset: 'injected', background: '<script>' }).background, '#10151f'); assert.equal(normalizeSettings({ intensity: 500 }).intensity, 1.8);
  assert.equal(normalizeSettings({ preset: 'injected' }).preset, 'fly'); assert.deepEqual(assetBox(svg), { width: 196, height: 98 });
});
