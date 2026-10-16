// Finalize local WebM metadata without decoding or changing any media packets.
// Element IDs/positions follow https://www.matroska.org/technical/elements.html.
// MediaRecorder may omit Duration and Cues; live files then report Infinity.
const ID = { segment: 0x18538067, info: 0x1549a966, tracks: 0x1654ae6b, cluster: 0x1f43b675, cues: 0x1c53bb6b, seek: 0x114d9b74, tags: 0x1254c367, attachments: 0x1941a469, chapters: 0x1043a770 };
const TOP = new Set(Object.values(ID));
const concat = parts => { const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0)); let pos = 0; for (const p of parts) { out.set(p, pos); pos += p.length; } return out; };
function unsigned(value, width) {
  let number = BigInt(Math.round(value));
  if (!width) { width = 1; while (number >= (1n << BigInt(width * 8))) width++; }
  const bytes = new Uint8Array(width);
  for (let index = width - 1; index >= 0; index--) { bytes[index] = Number(number & 255n); number >>= 8n; }
  return bytes;
}
function size(value) {
  let width = 1;
  while (BigInt(value) >= (1n << BigInt(width * 7)) - 1n) width++;
  const bytes = unsigned(value, width); bytes[0] |= 1 << (8 - width); return bytes;
}
function element(id, data) { return concat([unsigned(id), size(data.length), data]); }
function uintElement(id, value, width) { return element(id, unsigned(value, width)); }
function readVint(bytes, offset, id = false) {
  if (offset >= bytes.length || bytes[offset] === 0) throw new Error('올바르지 않은 WebM 헤더예요.');
  let length = 1, mask = 128;
  while (!(bytes[offset] & mask)) { length++; mask >>= 1; }
  if (length > (id ? 4 : 8) || offset + length > bytes.length) throw new Error('잘린 WebM 헤더예요.');
  let value = BigInt(id ? bytes[offset] : bytes[offset] & (mask - 1));
  for (let index = 1; index < length; ++index) value = value * 256n + BigInt(bytes[offset + index]);
  const unknown = !id && value === (1n << BigInt(length * 7)) - 1n;
  if (!unknown && value > BigInt(Number.MAX_SAFE_INTEGER)) throw new Error('WebM 파일이 너무 커요.');
  return { value: Number(value), length, unknown };
}
function header(bytes, offset, end) {
  const id = readVint(bytes, offset, true), length = readVint(bytes, offset + id.length);
  const data = offset + id.length + length.length;
  const stop = length.unknown ? end : data + length.value;
  if (stop > end || stop < data) throw new Error('잘린 WebM 요소예요.');
  return { id: id.value, start: offset, data, end: stop, unknown: length.unknown };
}
function children(bytes, start, end, topLevel = false) {
  const result = [];
  for (let position = start; position < end;) {
    const item = header(bytes, position, end);
    if (item.unknown) {
      if (!topLevel || item.id !== ID.cluster) throw new Error('지원되지 않는 WebM 구조예요.');
      let stop = item.data;
      while (stop < end) {
        const child = header(bytes, stop, end);
        if (TOP.has(child.id)) break;
        if (child.unknown) throw new Error('지원되지 않는 WebM 블록이에요.');
        stop = child.end;
      }
      item.end = stop;
    }
    result.push(item);
    position = item.end;
  }
  return result;
}
function readUnsigned(bytes, entry) { let n = 0; for (let p = entry.data; p < entry.end; ++p) n = n * 256 + bytes[p]; return n; }
function seekHead(info, tracks, cues) {
  return element(ID.seek, [[ID.info, info], [ID.tracks, tracks], [ID.cues, cues]].map(([id, position]) => element(0x4dbb, concat([element(0x53ab, unsigned(id)), uintElement(0x53ac, position, 8)]))).reduce((all, value) => concat([all, value]), new Uint8Array()));
}

export function finalizeWebMBytes(input, duration) {
  if (!Number.isFinite(duration) || duration <= 0) throw new TypeError('WebM 길이가 올바르지 않아요.');
  const bytes = input instanceof Uint8Array ? input : new Uint8Array(input);
  // Header is finite; Segment itself is normally unknown-sized in live output.
  const first = header(bytes, 0, bytes.length);
  const segment = header(bytes, first.end, bytes.length);
  if (first.id !== 0x1a45dfa3 || segment.id !== ID.segment) throw new Error('WebM 파일이 아니에요.');
  const entries = children(bytes, segment.data, segment.end, true);
  const info = entries.find(e => e.id === ID.info), tracks = entries.find(e => e.id === ID.tracks);
  if (!info || !tracks) throw new Error('WebM 미디어 정보가 없어요.');
  const infoParts = children(bytes, info.data, info.end);
  const scaleEntry = infoParts.find(e => e.id === 0x2ad7b1);
  const scale = scaleEntry ? readUnsigned(bytes, scaleEntry) : 1e6;
  if (!(scale > 0)) throw new Error('WebM 시간 단위가 올바르지 않아요.');
  const durationBytes = new Uint8Array(8); new DataView(durationBytes.buffer).setFloat64(0, duration * 1e9 / scale, false);
  const newInfo = element(ID.info, concat([...infoParts.filter(e => ![0x4489, 0xbf].includes(e.id)).map(e => bytes.slice(e.start, e.end)), element(0x4489, durationBytes)]));
  let videoTrack = 1;
  for (const entry of children(bytes, tracks.data, tracks.end).filter(e => e.id === 0xae)) {
    const fields = children(bytes, entry.data, entry.end);
    const type = fields.find(e => e.id === 0x83), number = fields.find(e => e.id === 0xd7);
    if (type && number && readUnsigned(bytes, type) === 1) videoTrack = readUnsigned(bytes, number);
  }
  const newTracks = bytes.slice(tracks.start, tracks.end);
  const other = entries.filter(e => ![ID.info, ID.tracks, ID.cluster, ID.seek, ID.cues, 0xbf].includes(e.id)).map(e => bytes.slice(e.start, e.end));
  const clusters = entries.filter(e => e.id === ID.cluster);
  if (!clusters.length) throw new Error('WebM 영상 프레임이 없어요.');
  const dummySeek = seekHead(0, 0, 0);
  const infoPosition = dummySeek.length, tracksPosition = infoPosition + newInfo.length;
  let position = tracksPosition + newTracks.length + other.reduce((n, p) => n + p.length, 0);
  const cues = [], clusterParts = [];
  for (const cluster of clusters) {
    const fields = children(bytes, cluster.data, cluster.end);
    const timestampEntry = fields.find(e => e.id === 0xe7);
    const timestamp = timestampEntry ? readUnsigned(bytes, timestampEntry) : 0;
    for (const block of fields.filter(e => e.id === 0xa3)) {
      const track = readVint(bytes, block.data), p = block.data + track.length;
      if (p + 3 > block.end) throw new Error('잘린 WebM 프레임이에요.');
      const relativeTime = new DataView(bytes.buffer, bytes.byteOffset + p, 2).getInt16(0, false);
      if (track.value === videoTrack && (bytes[p + 2] & 128)) {
        cues.push(element(0xbb, concat([uintElement(0xb3, Math.max(0, timestamp + relativeTime)), element(0xb7, concat([uintElement(0xf7, videoTrack), uintElement(0xf1, position, 8), uintElement(0xf0, block.start - cluster.data, 8)]))])));
      }
    }
    // Preserve payload bytes, normalize unknown cluster size to a finite size.
    const clusterBytes = element(ID.cluster, bytes.slice(cluster.data, cluster.end));
    clusterParts.push(clusterBytes); position += clusterBytes.length;
  }
  const newCues = element(ID.cues, concat(cues));
  const newSeek = seekHead(infoPosition, tracksPosition, position);
  const payload = concat([newSeek, newInfo, newTracks, ...other, ...clusterParts, newCues]);
  return concat([bytes.slice(0, first.end), element(ID.segment, payload)]);
}

export async function finalizeWebM(blob, duration) {
  const bytes = finalizeWebMBytes(await blob.arrayBuffer(), duration);
  return new Blob([bytes], { type: blob.type || 'video/webm' });
}
