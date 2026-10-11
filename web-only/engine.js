/* Browser-only PolyglotMaker bundle engine. No imports, network calls, or server APIs. */
(function (root, factory) {
  const engine = factory();
  root.PolyglotEngine = engine;
  if (typeof module !== 'undefined' && module.exports) module.exports = engine;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const encoder = new TextEncoder();
  const decoder = new TextDecoder('utf-8');
  const MANIFEST_PATH = 'POLYGLOTMAKER/manifest.json';
  const PAYLOAD_PREFIX = 'payloads/';
  const BOOTSTRAP_MAGIC = 'PLGMK1';
  const BOOTSTRAP_LENGTH = 172;
  const MAX_BYTES = 128 * 1024 * 1024;
  const MAX_FILES = 10000;
  const PNG_BASE64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNQKN/xHwAEWQJPVTAhPAAAAABJRU5ErkJggg==';

  const crcTable = new Uint32Array(256);
  for (let n = 0; n < 256; n += 1) {
    let value = n;
    for (let bit = 0; bit < 8; bit += 1) value = (value & 1) ? (0xedb88320 ^ (value >>> 1)) : (value >>> 1);
    crcTable[n] = value >>> 0;
  }

  function crc32(data) {
    let crc = 0xffffffff;
    for (let i = 0; i < data.length; i += 1) crc = crcTable[(crc ^ data[i]) & 0xff] ^ (crc >>> 8);
    return (crc ^ 0xffffffff) >>> 0;
  }

  function concat(parts) {
    const arrays = parts.map((part) => part instanceof Uint8Array ? part : new Uint8Array(part));
    const length = arrays.reduce((total, part) => total + part.byteLength, 0);
    const output = new Uint8Array(length);
    let offset = 0;
    for (const part of arrays) { output.set(part, offset); offset += part.byteLength; }
    return output;
  }

  function u16(value) {
    const output = new Uint8Array(2);
    new DataView(output.buffer).setUint16(0, value, true);
    return output;
  }

  function u32(value) {
    const output = new Uint8Array(4);
    new DataView(output.buffer).setUint32(0, value >>> 0, true);
    return output;
  }

  function u64(value) {
    const output = new Uint8Array(8);
    new DataView(output.buffer).setBigUint64(0, BigInt(value), true);
    return output;
  }

  function toHex(data) {
    let hex = '';
    for (const byte of data) hex += byte.toString(16).padStart(2, '0');
    return hex;
  }

  function fromHex(text) {
    if (text.length % 2 || !/^[0-9a-f]*$/i.test(text)) throw new Error('Invalid ZIP integrity footer.');
    const bytes = new Uint8Array(text.length / 2);
    for (let i = 0; i < bytes.length; i += 1) bytes[i] = Number.parseInt(text.slice(i * 2, i * 2 + 2), 16);
    return bytes;
  }

  function safeMemberName(input) {
    const raw = String(input || '').trim().replace(/\\/g, '/');
    if (!raw || raw.startsWith('/') || /^[a-z]:/i.test(raw)) throw new Error(`Unsafe file path: ${input}`);
    const parts = [];
    for (let part of raw.split('/')) {
      if (!part || part === '.') continue;
      if (part === '..') throw new Error(`Unsafe file path: ${input}`);
      part = part.replace(/[\x00-\x1f<>:"|?*]/g, '_');
      if (/^(CON|PRN|AUX|NUL|COM\d|LPT\d)$/i.test(part)) part = `_${part}`;
      parts.push(part.slice(0, 200));
    }
    const name = parts.join('/').slice(0, 2000);
    if (!name) throw new Error(`Unsafe file path: ${input}`);
    return name;
  }

  function sortedJson(value) {
    if (Array.isArray(value)) return value.map(sortedJson);
    if (value && typeof value === 'object') {
      const output = {};
      for (const key of Object.keys(value).sort()) output[key] = sortedJson(value[key]);
      return output;
    }
    return value;
  }

  function canonicalJson(value) {
    return JSON.stringify(sortedJson(value));
  }

  async function sha256(data) {
    const cryptoApi = typeof globalThis !== 'undefined' ? globalThis.crypto : null;
    if (!cryptoApi || !cryptoApi.subtle) throw new Error('This browser needs Web Crypto. Open this page from file:// or a secure static website.');
    const bytes = data instanceof Uint8Array ? data : new Uint8Array(data);
    return new Uint8Array(await cryptoApi.subtle.digest('SHA-256', bytes));
  }

  async function sha256Hex(data) {
    return toHex(await sha256(data));
  }

  function dosDateTime(epochSeconds) {
    const date = new Date(Math.max(315532800, Number(epochSeconds) || Date.now() / 1000) * 1000);
    const year = Math.min(2107, Math.max(1980, date.getFullYear()));
    const dateBits = ((year - 1980) << 9) | ((date.getMonth() + 1) << 5) | date.getDate();
    const timeBits = (date.getHours() << 11) | (date.getMinutes() << 5) | (Math.floor(date.getSeconds() / 2));
    return { date: dateBits, time: timeBits };
  }

  function buildStoredZip(entries) {
    if (entries.length > MAX_FILES + 1) throw new Error('This bundle has too many files for the browser-only edition.');
    const localParts = [];
    const centralParts = [];
    const names = new Set();
    let localOffset = 0;

    for (const entry of entries) {
      const name = safeMemberName(entry.name);
      if (names.has(name)) throw new Error(`Duplicate ZIP member: ${name}`);
      names.add(name);
      const nameBytes = encoder.encode(name);
      const data = entry.data instanceof Uint8Array ? entry.data : new Uint8Array(entry.data);
      if (nameBytes.length > 65535 || data.length > 0xffffffff) throw new Error(`File is too large for a browser ZIP: ${name}`);
      const checksum = crc32(data);
      const stamp = dosDateTime(entry.mtime);
      const flag = 0x0800; // UTF-8 filename

      const localHeader = new Uint8Array(30);
      const localView = new DataView(localHeader.buffer);
      localView.setUint32(0, 0x04034b50, true);
      localView.setUint16(4, 20, true);
      localView.setUint16(6, flag, true);
      localView.setUint16(8, 0, true); // stored, no compression
      localView.setUint16(10, stamp.time, true);
      localView.setUint16(12, stamp.date, true);
      localView.setUint32(14, checksum, true);
      localView.setUint32(18, data.length, true);
      localView.setUint32(22, data.length, true);
      localView.setUint16(26, nameBytes.length, true);
      localView.setUint16(28, 0, true);
      localParts.push(localHeader, nameBytes, data);

      const centralHeader = new Uint8Array(46);
      const centralView = new DataView(centralHeader.buffer);
      centralView.setUint32(0, 0x02014b50, true);
      centralView.setUint16(4, 0x0314, true); // Unix creator, ZIP 2.0
      centralView.setUint16(6, 20, true);
      centralView.setUint16(8, flag, true);
      centralView.setUint16(10, 0, true);
      centralView.setUint16(12, stamp.time, true);
      centralView.setUint16(14, stamp.date, true);
      centralView.setUint32(16, checksum, true);
      centralView.setUint32(20, data.length, true);
      centralView.setUint32(24, data.length, true);
      centralView.setUint16(28, nameBytes.length, true);
      centralView.setUint16(30, 0, true);
      centralView.setUint16(32, 0, true);
      centralView.setUint16(34, 0, true);
      centralView.setUint16(36, 0, true);
      centralView.setUint32(38, ((Number(entry.mode) || 0o644) & 0xffff) << 16, true);
      centralView.setUint32(42, localOffset, true);
      centralParts.push(centralHeader, nameBytes);
      localOffset += localHeader.length + nameBytes.length + data.length;
    }

    const localData = concat(localParts);
    const centralData = concat(centralParts);
    if (entries.length > 65535 || centralData.length > 0xffffffff || localData.length > 0xffffffff) {
      throw new Error('This archive exceeds the ZIP32 limits supported by the browser-only edition.');
    }
    const endRecord = new Uint8Array(22);
    const endView = new DataView(endRecord.buffer);
    endView.setUint32(0, 0x06054b50, true);
    endView.setUint16(4, 0, true);
    endView.setUint16(6, 0, true);
    endView.setUint16(8, entries.length, true);
    endView.setUint16(10, entries.length, true);
    endView.setUint32(12, centralData.length, true);
    endView.setUint32(16, localData.length, true);
    endView.setUint16(20, 0, true);
    const eocdOffset = localData.length + centralData.length;
    return { bytes: concat([localData, centralData, endRecord]), eocdOffset };
  }

  async function buildPolyglotZip(entries, absoluteOffset, manifestBytes) {
    const draft = buildStoredZip(entries);
    const archiveHash = await sha256(draft.bytes);
    const manifestHash = await sha256(manifestBytes);
    const commentPrefix = encoder.encode('PolyglotMaker v1\u0000');
    const finalLength = draft.bytes.length + commentPrefix.length + BOOTSTRAP_LENGTH;
    const fields = concat([
      new Uint8Array([1]), u64(absoluteOffset), u64(finalLength),
      manifestHash, archiveHash, u16(0),
    ]);
    const footer = encoder.encode(`${BOOTSTRAP_MAGIC}${toHex(fields)}`);
    const comment = concat([commentPrefix, footer]);
    if (comment.length > 65535) throw new Error('The ZIP bootstrap comment exceeds the format limit.');
    const result = new Uint8Array(draft.bytes.length + comment.length);
    result.set(draft.bytes);
    new DataView(result.buffer).setUint16(draft.eocdOffset + 20, comment.length, true);
    result.set(comment, draft.bytes.length);
    return result;
  }

  function pngShell() {
    const binary = atob(PNG_BASE64);
    return Uint8Array.from(binary, (character) => character.charCodeAt(0));
  }

  function extensionOf(name) {
    const base = String(name || '').replace(/\\/g, '/').split('/').pop();
    const dot = base.lastIndexOf('.');
    return dot > 0 ? base.slice(dot + 1).toLowerCase().replace(/[^a-z0-9]/g, '').slice(0, 10) : '';
  }

  function hostKeyFor(name, fallback = 'png') {
    const ext = extensionOf(name);
    const aliases = {
      jpg: 'jpeg', jpeg: 'jpeg', htm: 'html', md: 'markdown', yml: 'yaml',
      exe: 'exe', dll: 'exe', doc: 'docx', xls: 'xlsx', ppt: 'pptx',
    };
    const known = new Set(['png', 'jpeg', 'gif', 'bmp', 'webp', 'tiff', 'ico', 'pdf', 'mp3', 'wav', 'flac', 'aiff', 'mp4', 'avi', 'midi', 'zip', 'tar', 'gz', 'bz2', 'xz', 'html', 'json', 'xml', 'svg', 'csv', 'txt', 'markdown', 'javascript', 'typescript', 'python', 'rust', 'go', 'c', 'css', 'yaml', 'toml', 'ini', 'rtf', 'wasm', 'exe', 'elf', 'macho', 'class', 'docx', 'xlsx', 'pptx', 'odt', 'ods', 'odp', 'epub', 'jar', 'apk', 'cbz']);
    const key = aliases[ext] || ext;
    return known.has(key) ? key : (key || fallback);
  }

  function uniqueName(name, seen) {
    const clean = safeMemberName(name);
    if (!seen.has(clean)) { seen.add(clean); return clean; }
    const slash = clean.lastIndexOf('/');
    const dir = slash >= 0 ? clean.slice(0, slash + 1) : '';
    const base = slash >= 0 ? clean.slice(slash + 1) : clean;
    const dot = base.lastIndexOf('.');
    const stem = dot > 0 ? base.slice(0, dot) : base;
    const ext = dot > 0 ? base.slice(dot) : '';
    let n = 1;
    let candidate = `${dir}${stem} (${n})${ext}`;
    while (seen.has(candidate)) candidate = `${dir}${stem} (${++n})${ext}`;
    seen.add(candidate);
    return candidate;
  }

  async function readInput(file) {
    const content = file.data instanceof Uint8Array
      ? file.data
      : (file.data instanceof ArrayBuffer ? new Uint8Array(file.data) : new Uint8Array(await file.arrayBuffer()));
    return {
      name: safeMemberName(file.name || 'payload.bin'),
      data: content,
      mtime: Math.max(315532800, Number(file.lastModified || file.mtime * 1000 || Date.now()) / 1000),
      mode: Number(file.mode) || 0o644,
      mime: file.type || file.mime || 'application/octet-stream',
      role: file.role || 'payload',
    };
  }

  async function createPolyglot({ payloads = [], hostFile = null, includeHost = true } = {}) {
    const hostBytes = hostFile ? new Uint8Array(await hostFile.arrayBuffer()) : pngShell();
    const seen = new Set();
    const inputs = [];
    if (hostFile && includeHost) inputs.push({ file: hostFile, role: 'host' });
    for (const file of payloads) inputs.push({ file, role: 'payload' });
    if (!inputs.length) throw new Error('Add at least one payload, or include the selected host file.');
    if (inputs.length > MAX_FILES) throw new Error(`The browser-only edition supports up to ${MAX_FILES.toLocaleString()} payloads.`);
    const hostAndPayloadSize = hostBytes.length + inputs.reduce((sum, item) => sum + Number(item.file.size || item.file.data?.byteLength || 0), 0);
    if (hostAndPayloadSize > MAX_BYTES) throw new Error(`Selected data exceeds the ${Math.round(MAX_BYTES / 1024 / 1024)} MiB browser limit.`);

    const files = [];
    for (const input of inputs) {
      const file = await readInput(input.file);
      file.name = uniqueName(file.name, seen);
      file.role = input.role;
      files.push(file);
    }
    const records = [];
    for (const file of files) {
      records.push({
        name: file.name,
        size: file.data.length,
        sha256: await sha256Hex(file.data),
        mtime: file.mtime,
        mode: file.mode,
        mime: file.mime,
        role: file.role,
        store: 'zip',
        entry: `${PAYLOAD_PREFIX}${file.name}`,
      });
    }
    const fileSet = records.map(({ name, size, sha256: digest }) => ({ name, size, sha256: digest }));
    const bundleHash = await sha256Hex(encoder.encode(canonicalJson(fileSet)));
    const hostName = hostFile ? hostFile.name : 'web-only.png';
    const manifest = {
      tool: 'PolyglotMaker',
      format_version: 1,
      created_utc: new Date().toISOString().replace(/\.\d{3}Z$/, 'Z'),
      container: {
        host: hostKeyFor(hostName),
        host_mode: hostFile ? 'original' : 'generated',
        embed_mode: 'append',
        store: 'zip',
        compression: 'none',
        obfuscated: false,
        layers: [],
      },
      files: records,
      bundle: {
        sha256: bundleHash,
        size: null,
        offset: hostBytes.length,
        sha256_scope: 'payload-set (footer separately hashes normalized ZIP bytes)',
      },
      options: {},
    };
    const manifestBytes = encoder.encode(canonicalJson(manifest));
    const zipEntries = [{ name: MANIFEST_PATH, data: manifestBytes, mtime: Date.now() / 1000, mode: 0o644 }];
    for (const file of files) zipEntries.push({ name: `${PAYLOAD_PREFIX}${file.name}`, data: file.data, mtime: file.mtime, mode: file.mode });
    const bundle = await buildPolyglotZip(zipEntries, hostBytes.length, manifestBytes);
    const bytes = concat([hostBytes, bundle]);
    const hostExt = hostFile ? extensionOf(hostFile.name) : 'png';
    const fileName = `polyglotmaker${hostExt ? `.${hostExt}` : '.polyglot'}`;
    return {
      bytes,
      fileName,
      host: manifest.container.host,
      hostBytes: hostBytes.length,
      bundleBytes: bundle.length,
      totalBytes: bytes.length,
      files: records.map(({ name, size, sha256: digest }) => ({ name, size, sha256: digest })),
    };
  }

  function readU16(view, offset) { return view.getUint16(offset, true); }
  function readU32(view, offset) { return view.getUint32(offset, true); }

  function parseFooter(data, eocd, commentLength) {
    const comment = decoder.decode(data.subarray(eocd + 22, eocd + 22 + commentLength));
    const start = comment.lastIndexOf(BOOTSTRAP_MAGIC);
    if (start < 0) return null;
    const hexLength = BOOTSTRAP_LENGTH - BOOTSTRAP_MAGIC.length;
    const hex = comment.slice(start + BOOTSTRAP_MAGIC.length, start + BOOTSTRAP_MAGIC.length + hexLength);
    if (hex.length !== hexLength) return null;
    let fields;
    try { fields = fromHex(hex); } catch { return null; }
    if (fields.length !== 83 || fields[0] !== 1) return null;
    const view = new DataView(fields.buffer, fields.byteOffset, fields.byteLength);
    const offset = Number(view.getBigUint64(1, true));
    const length = Number(view.getBigUint64(9, true));
    return {
      offset,
      length,
      manifestHash: fields.slice(17, 49),
      archiveHash: fields.slice(49, 81),
    };
  }

  function locateArchive(data) {
    const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
    const lowerBound = Math.max(0, data.length - 22 - 65535);
    for (let eocd = data.length - 22; eocd >= lowerBound; eocd -= 1) {
      if (readU32(view, eocd) !== 0x06054b50) continue;
      const commentLength = readU16(view, eocd + 20);
      const end = eocd + 22 + commentLength;
      if (end > data.length) continue;
      const disk = readU16(view, eocd + 4);
      const cdDisk = readU16(view, eocd + 6);
      if (disk !== 0 || cdDisk !== 0) continue;
      const entryCount = readU16(view, eocd + 10);
      const cdSize = readU32(view, eocd + 12);
      const cdOffset = readU32(view, eocd + 16);
      if (entryCount === 0xffff || cdSize === 0xffffffff || cdOffset === 0xffffffff) continue;
      const footer = parseFooter(data, eocd, commentLength);
      const start = footer && footer.offset < eocd ? footer.offset : eocd - cdSize - cdOffset;
      if (start < 0 || start + 4 > data.length) continue;
      if (readU32(view, start) !== 0x04034b50) continue;
      const length = end - start;
      if (footer && footer.length !== length) continue;
      return { start, end, eocd, entryCount, cdSize, cdOffset, footer };
    }
    throw new Error('No appended ZIP bundle was found. This offline edition supports PolyglotMaker ZIP bundles, not raw, obfuscated, or native-only stores.');
  }

  function readDirectory(data, archive) {
    const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
    const start = archive.start + archive.cdOffset;
    const end = start + archive.cdSize;
    if (start < 0 || end > archive.eocd) throw new Error('The ZIP directory is outside the file.');
    const entries = new Map();
    let cursor = start;
    for (let i = 0; i < archive.entryCount; i += 1) {
      if (cursor + 46 > end || readU32(view, cursor) !== 0x02014b50) throw new Error('The ZIP directory is malformed.');
      const flags = readU16(view, cursor + 8);
      const method = readU16(view, cursor + 10);
      const checksum = readU32(view, cursor + 16);
      const compressedSize = readU32(view, cursor + 20);
      const size = readU32(view, cursor + 24);
      const nameLength = readU16(view, cursor + 28);
      const extraLength = readU16(view, cursor + 30);
      const commentLength = readU16(view, cursor + 32);
      const localOffset = readU32(view, cursor + 42);
      const afterName = cursor + 46 + nameLength;
      if (afterName + extraLength + commentLength > end) throw new Error('The ZIP directory entry is truncated.');
      const rawName = decoder.decode(data.subarray(cursor + 46, afterName));
      const name = safeMemberName(rawName);
      if (compressedSize === 0xffffffff || size === 0xffffffff || localOffset === 0xffffffff) throw new Error('ZIP64 bundles are not supported by this browser-only edition.');
      if (entries.has(name)) throw new Error(`Duplicate ZIP member: ${name}`);
      entries.set(name, { name, flags, method, checksum, compressedSize, size, localOffset });
      cursor = afterName + extraLength + commentLength;
    }
    return entries;
  }

  async function inflateRaw(data, limit) {
    if (typeof DecompressionStream === 'undefined') throw new Error('This browser cannot inflate DEFLATE ZIP entries. Try a current version of Chrome, Edge, Firefox, or Safari.');
    let decompressor;
    try { decompressor = new DecompressionStream('deflate-raw'); }
    catch { throw new Error('This browser does not support raw DEFLATE ZIP entries. Try a current browser.'); }
    const reader = new Blob([data]).stream().pipeThrough(decompressor).getReader();
    const chunks = [];
    let length = 0;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      length += value.byteLength;
      if (length > limit || length > MAX_BYTES) {
        await reader.cancel();
        throw new Error('A ZIP entry exceeds the browser-only extraction size limit.');
      }
      chunks.push(value);
    }
    return concat(chunks);
  }

  async function extractEntry(data, archive, entry, limit) {
    if (entry.flags & 1) throw new Error('Encrypted ZIP members are not supported in the offline edition.');
    const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
    const local = archive.start + entry.localOffset;
    if (local + 30 > archive.end || readU32(view, local) !== 0x04034b50) throw new Error(`Missing local ZIP record for ${entry.name}.`);
    const nameLength = readU16(view, local + 26);
    const extraLength = readU16(view, local + 28);
    const bodyStart = local + 30 + nameLength + extraLength;
    const bodyEnd = bodyStart + entry.compressedSize;
    if (bodyStart < local || bodyEnd > archive.end) throw new Error(`ZIP entry is truncated: ${entry.name}`);
    if (entry.size > limit || entry.size > MAX_BYTES) throw new Error(`ZIP entry is too large for this browser: ${entry.name}`);
    const compressed = data.subarray(bodyStart, bodyEnd);
    let output;
    if (entry.method === 0) output = Uint8Array.from(compressed);
    else if (entry.method === 8) output = await inflateRaw(compressed, Math.min(limit, entry.size));
    else throw new Error(`Compression method ${entry.method} is not supported in the offline edition.`);
    if (output.length !== entry.size) throw new Error(`Size check failed for ${entry.name}.`);
    if (crc32(output) !== entry.checksum) throw new Error(`ZIP CRC check failed for ${entry.name}.`);
    return output;
  }

  function equalBytes(a, b) {
    if (!a || !b || a.length !== b.length) return false;
    let diff = 0;
    for (let i = 0; i < a.length; i += 1) diff |= a[i] ^ b[i];
    return diff === 0;
  }

  async function unpackPolyglot(input) {
    const data = input instanceof Uint8Array ? input : new Uint8Array(input);
    if (data.length > MAX_BYTES) throw new Error(`The container exceeds the ${Math.round(MAX_BYTES / 1024 / 1024)} MiB browser limit.`);
    const archive = locateArchive(data);
    const entries = readDirectory(data, archive);
    const manifestEntry = entries.get(MANIFEST_PATH);
    if (!manifestEntry) throw new Error('No PolyglotMaker manifest was found in the appended ZIP.');
    if (manifestEntry.size > 4 * 1024 * 1024) throw new Error('The manifest is larger than the browser-only safety limit.');
    const manifestBytes = await extractEntry(data, archive, manifestEntry, 4 * 1024 * 1024);
    let manifest;
    try { manifest = JSON.parse(decoder.decode(manifestBytes)); }
    catch { throw new Error('The PolyglotMaker manifest is invalid JSON.'); }
    if (!manifest || manifest.tool !== 'PolyglotMaker' || manifest.format_version !== 1 || !Array.isArray(manifest.files)) {
      throw new Error('This is not a supported PolyglotMaker manifest.');
    }
    if (archive.footer) {
      if (!equalBytes(await sha256(manifestBytes), archive.footer.manifestHash)) throw new Error('The manifest SHA-256 check failed.');
      const zipBytes = data.subarray(archive.start, archive.end);
      const eocdInZip = archive.eocd - archive.start;
      const normalized = concat([zipBytes.subarray(0, eocdInZip + 20), u16(0)]);
      if (!equalBytes(await sha256(normalized), archive.footer.archiveHash)) throw new Error('The ZIP bundle SHA-256 check failed.');
    }
    if (manifest.files.length > MAX_FILES) throw new Error('The manifest contains too many files for this browser.');
    const declaredTotal = manifest.files.reduce((sum, item) => sum + Number(item.size || 0), 0);
    if (!Number.isSafeInteger(declaredTotal) || declaredTotal < 0 || declaredTotal > MAX_BYTES) throw new Error('The payload set exceeds the browser-only extraction size limit.');

    const files = [];
    const seen = new Set();
    for (const record of manifest.files) {
      if (!record || typeof record !== 'object') throw new Error('A file record in the manifest is malformed.');
      const name = safeMemberName(record.name);
      if (seen.has(name)) throw new Error(`The manifest contains duplicate filenames: ${name}`);
      seen.add(name);
      const entryName = safeMemberName(record.entry || `${PAYLOAD_PREFIX}${name}`);
      const zipEntry = entries.get(entryName);
      if (!zipEntry) throw new Error(`Payload data is missing: ${name}`);
      if (Number(record.size) !== zipEntry.size) throw new Error(`Manifest size does not match for ${name}.`);
      const bytes = await extractEntry(data, archive, zipEntry, Number(record.size));
      const digest = await sha256Hex(bytes);
      if (record.sha256 && digest.toLowerCase() !== String(record.sha256).toLowerCase()) throw new Error(`SHA-256 check failed for ${name}.`);
      files.push({ name, size: bytes.length, sha256: digest, mime: record.mime || 'application/octet-stream', data: bytes });
    }
    if (!files.length) throw new Error('The manifest contains no recoverable payload files.');
    return { files, source: archive.footer ? 'ZIP bundle · integrity footer verified' : 'ZIP bundle · payload checks verified' };
  }

  function downloadZip(files) {
    const entries = files.map((file) => ({ name: safeMemberName(file.name), data: file.data, mtime: Date.now() / 1000, mode: 0o644 }));
    return buildStoredZip(entries).bytes;
  }

  return {
    MAX_BYTES,
    MAX_FILES,
    crc32,
    sha256Hex,
    makePngShell: pngShell,
    createPolyglot,
    unpackPolyglot,
    downloadZip,
  };
});
