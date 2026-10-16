// Source recipes shared with tools/generate_browser_analysis_refs.py; no recordings.
export function syntheticFixture(source, sampleRate) {
  const samples = new Float32Array(Math.round(source.duration * sampleRate));
  let seed = source.kind === 'low_noise' ? 8 : 191;
  const noise = () => { seed = (Math.imul(1664525, seed) + 1013904223) >>> 0; return seed / 4294967296 * 2 - 1; };
  if (source.kind === 'silence') return samples;
  if (source.kind === 'low_noise') { for (let index = 0; index < samples.length; index++) samples[index] = noise() * .002; return samples; }
  if (source.kind === 'impulses') { for (const hit of [.4, .43, 2.1, 4.6]) samples[Math.round(hit * sampleRate)] = 1; return samples; }
  if (source.kind === 'tone_holds') {
    for (let index = 0; index < samples.length; index++) {
      const time = index / sampleRate;
      for (const [start, stop, frequency] of [[.25, 1.1, 220], [3, 4.2, 330]]) if (time >= start && time < stop) samples[index] = .3 * Math.sin(2 * Math.PI * frequency * time);
    }
    return samples;
  }
  if (source.kind === 'glide_hits') {
    const double = new Float64Array(samples.length);
    const clamp = (value) => Math.min(1, Math.max(0, value));
    for (let index = 0; index < double.length; index++) {
      const time = index / sampleRate;
      double[index] = .13 * clamp((time - .2) / .06) * clamp((3.3 - time) / .1) * Math.sin(2 * Math.PI * (170 * time + 35 * time ** 2));
    }
    for (const hit of [1.3, 4.1]) for (let index = 0; index < double.length; index++) {
      const age = index / sampleRate - hit;
      if (age >= 0 && age < .08) double[index] += .65 * Math.exp(-age * 55) * noise();
    }
    return Float32Array.from(double);
  }
  throw new Error(`Unknown synthetic fixture ${source.kind}`);
}

export function pcm16Fixture(buffer, sampleCount = 22050 * 5) {
  const bytes = buffer instanceof ArrayBuffer ? buffer : buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength);
  const view = new DataView(bytes);
  const text = (at) => String.fromCharCode(...new Uint8Array(bytes, at, 4));
  if (text(0) !== 'RIFF' || text(8) !== 'WAVE') throw new Error('Expected WAV.');
  let data;
  for (let at = 12; at + 8 <= view.byteLength;) {
    const size = view.getUint32(at + 4, true), start = at + 8;
    if (start + size > view.byteLength) throw new Error('Truncated WAV.');
    if (text(at) === 'fmt ' && (view.getUint16(start, true) !== 1 || view.getUint16(start + 2, true) !== 1 || view.getUint32(start + 4, true) !== 22050 || view.getUint16(start + 14, true) !== 16)) throw new Error('Expected 22050 Hz mono PCM16.');
    if (text(at) === 'data') data = start;
    at = start + size + (size % 2);
  }
  if (data === undefined || data + sampleCount * 2 > view.byteLength) throw new Error('Short WAV.');
  return Float32Array.from({ length: sampleCount }, (_, index) => view.getInt16(data + index * 2, true) / 32768);
}
