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
      part = part.replace(/[\x00-\x1f<>:"|?*]/g, '_').replace(/[. ]+$/, '');
      if (!part) part = '_';
      if (/^(CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?$/i.test(part)) part = `_${part}`;
      parts.push(part.slice(0, 200));
    }
    const name = parts.join('/').slice(0, 2000).replace(/[. ]+$/, '');
    if (!name) throw new Error(`Unsafe file path: ${input}`);
    return name;
  }

  function portableMemberKey(name) {
    return String(name).normalize('NFC').toLowerCase();
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

  function reportProgress(callback, percent, label) {
    if (typeof callback !== 'function') return;
    callback({ percent: Math.max(0, Math.min(100, Math.round(Number(percent) || 0))), label });
  }

  function throwIfAborted(signal) {
    if (!signal?.aborted) return;
    const error = new Error('Operation cancelled.');
    error.name = 'AbortError';
    throw error;
  }

  function yieldToBrowser() {
    return new Promise((resolve) => setTimeout(resolve, 0));
  }

  async function crc32Chunked(data, onProgress, signal) {
    let crc = 0xffffffff;
    const chunkSize = 2 * 1024 * 1024;
    if (!data.length) {
      onProgress?.(0, 0);
      return 0;
    }
    for (let start = 0; start < data.length; start += chunkSize) {
      throwIfAborted(signal);
      const end = Math.min(data.length, start + chunkSize);
      for (let i = start; i < end; i += 1) crc = crcTable[(crc ^ data[i]) & 0xff] ^ (crc >>> 8);
      onProgress?.(end, data.length);
      if (end < data.length) await yieldToBrowser();
    }
    throwIfAborted(signal);
    return (crc ^ 0xffffffff) >>> 0;
  }

  function prepareStoredZip(entries) {
    if (!Array.isArray(entries) || entries.length > MAX_FILES + 1) {
      throw new Error('This bundle has too many files for the browser-only edition.');
    }
    const prepared = [];
    const names = new Set();
    let localSize = 0;
    let centralSize = 0;

    for (const entry of entries) {
      const name = safeMemberName(entry.name);
      if (names.has(name)) throw new Error(`Duplicate ZIP member: ${name}`);
      names.add(name);
      const nameBytes = encoder.encode(name);
      const data = entry.data instanceof Uint8Array ? entry.data : new Uint8Array(entry.data);
      if (nameBytes.length > 65535 || data.length > 0xffffffff) throw new Error(`File is too large for a browser ZIP: ${name}`);
      const localOffset = localSize;
      localSize += 30 + nameBytes.length + data.length;
      centralSize += 46 + nameBytes.length;
      if (!Number.isSafeInteger(localSize) || localSize > 0xffffffff || centralSize > 0xffffffff) {
        throw new Error('This archive exceeds the ZIP32 limits supported by the browser-only edition.');
      }
      prepared.push({
        name,
        nameBytes,
        data,
        localOffset,
        checksum: 0,
        stamp: dosDateTime(entry.mtime),
        mode: Number(entry.mode) || 0o644,
      });
    }

    const eocdOffset = localSize + centralSize;
    const totalSize = eocdOffset + 22;
    if (entries.length > 65535 || totalSize > 0xffffffff) {
      throw new Error('This archive exceeds the ZIP32 limits supported by the browser-only edition.');
    }
    return { entries: prepared, localSize, centralSize, eocdOffset, totalSize };
  }

  async function checksumZipEntries(layout, { signal, onProgress } = {}) {
    const total = layout.entries.reduce((sum, entry) => sum + entry.data.length, 0);
    let processed = 0;
    for (let index = 0; index < layout.entries.length; index += 1) {
      throwIfAborted(signal);
      const entry = layout.entries[index];
      entry.checksum = await crc32Chunked(entry.data, (current) => {
        onProgress?.(total ? (processed + current) / total * 100 : 100, entry.name);
      }, signal);
      processed += entry.data.length;
      onProgress?.(total ? processed / total * 100 : 100, entry.name);
      if ((index + 1) % 64 === 0 && index + 1 < layout.entries.length) await yieldToBrowser();
    }
  }

  async function copyBytesChunked(target, source, targetOffset, { signal, onProgress } = {}) {
    const chunkSize = 2 * 1024 * 1024;
    if (!source.length) {
      onProgress?.(100);
      return;
    }
    for (let start = 0; start < source.length; start += chunkSize) {
      throwIfAborted(signal);
      const end = Math.min(source.length, start + chunkSize);
      target.set(source.subarray(start, end), targetOffset + start);
      onProgress?.(end / source.length * 100);
      if (end < source.length) await yieldToBrowser();
    }
    throwIfAborted(signal);
  }

  async function writeStoredZip(target, baseOffset, layout, { signal, onProgress } = {}) {
    const view = new DataView(target.buffer, target.byteOffset, target.byteLength);
    let cursor = baseOffset;
    let localCompleted = 0;
    let centralWritten = 0;
    const yieldEvery = 64;

    for (let index = 0; index < layout.entries.length; index += 1) {
      throwIfAborted(signal);
      const entry = layout.entries[index];
      const nameLength = entry.nameBytes.length;
      const dataLength = entry.data.length;
      const stamp = entry.stamp;
      const flag = 0x0800; // UTF-8 filename
      view.setUint32(cursor, 0x04034b50, true);
      view.setUint16(cursor + 4, 20, true);
      view.setUint16(cursor + 6, flag, true);
      view.setUint16(cursor + 8, 0, true); // stored, no compression
      view.setUint16(cursor + 10, stamp.time, true);
      view.setUint16(cursor + 12, stamp.date, true);
      view.setUint32(cursor + 14, entry.checksum, true);
      view.setUint32(cursor + 18, dataLength, true);
      view.setUint32(cursor + 22, dataLength, true);
      view.setUint16(cursor + 26, nameLength, true);
      view.setUint16(cursor + 28, 0, true);
      target.set(entry.nameBytes, cursor + 30);
      const dataOffset = cursor + 30 + nameLength;
      localCompleted += 30 + nameLength;
      for (let start = 0; start < dataLength; start += 2 * 1024 * 1024) {
        throwIfAborted(signal);
        const end = Math.min(dataLength, start + 2 * 1024 * 1024);
        target.set(entry.data.subarray(start, end), dataOffset + start);
        const percent = layout.localSize ? (localCompleted + end) / layout.localSize * 85 : 85;
        onProgress?.(percent, `Writing ${entry.name}…`);
        if (end < dataLength) await yieldToBrowser();
      }
      localCompleted += dataLength;
      onProgress?.(layout.localSize ? localCompleted / layout.localSize * 85 : 85, `Wrote ${entry.name}`);
      cursor += 30 + nameLength + dataLength;
      if ((index + 1) % yieldEvery === 0 && index + 1 < layout.entries.length) await yieldToBrowser();
    }
    if (cursor !== baseOffset + layout.localSize) throw new Error('ZIP local data size mismatch.');

    for (let index = 0; index < layout.entries.length; index += 1) {
      throwIfAborted(signal);
      const entry = layout.entries[index];
      const nameLength = entry.nameBytes.length;
      const stamp = entry.stamp;
      const flag = 0x0800;
      view.setUint32(cursor, 0x02014b50, true);
      view.setUint16(cursor + 4, 0x0314, true); // Unix creator, ZIP 2.0
      view.setUint16(cursor + 6, 20, true);
      view.setUint16(cursor + 8, flag, true);
      view.setUint16(cursor + 10, 0, true);
      view.setUint16(cursor + 12, stamp.time, true);
      view.setUint16(cursor + 14, stamp.date, true);
      view.setUint32(cursor + 16, entry.checksum, true);
      view.setUint32(cursor + 20, entry.data.length, true);
      view.setUint32(cursor + 24, entry.data.length, true);
      view.setUint16(cursor + 28, nameLength, true);
      view.setUint16(cursor + 30, 0, true);
      view.setUint16(cursor + 32, 0, true);
      view.setUint16(cursor + 34, 0, true);
      view.setUint16(cursor + 36, 0, true);
      view.setUint32(cursor + 38, (entry.mode & 0xffff) << 16, true);
      view.setUint32(cursor + 42, entry.localOffset, true);
      target.set(entry.nameBytes, cursor + 46);
      cursor += 46 + nameLength;
      centralWritten += 46 + nameLength;
      const percent = layout.centralSize ? 85 + centralWritten / layout.centralSize * 15 : 100;
      onProgress?.(percent, 'Writing ZIP directory…');
      if ((index + 1) % yieldEvery === 0 && index + 1 < layout.entries.length) await yieldToBrowser();
    }
    if (cursor !== baseOffset + layout.eocdOffset) throw new Error('ZIP central directory size mismatch.');

    throwIfAborted(signal);
    const eocd = cursor;
    view.setUint32(eocd, 0x06054b50, true);
    view.setUint16(eocd + 4, 0, true);
    view.setUint16(eocd + 6, 0, true);
    view.setUint16(eocd + 8, layout.entries.length, true);
    view.setUint16(eocd + 10, layout.entries.length, true);
    view.setUint32(eocd + 12, layout.centralSize, true);
    view.setUint32(eocd + 16, layout.localSize, true);
    view.setUint16(eocd + 20, 0, true);
    onProgress?.(100, 'ZIP records written.');
    return eocd;
  }

  async function buildStoredZip(entries, { signal, onProgress } = {}) {
    const layout = prepareStoredZip(entries);
    if (layout.totalSize > MAX_BYTES) throw new Error(`The ZIP download exceeds the ${Math.round(MAX_BYTES / 1024 / 1024)} MiB browser limit.`);
    await checksumZipEntries(layout, {
      signal,
      onProgress: (percent, name) => onProgress?.(percent * 0.65, `Checking ${name}…`),
    });
    throwIfAborted(signal);
    const bytes = new Uint8Array(layout.totalSize);
    const eocdOffset = await writeStoredZip(bytes, 0, layout, {
      signal,
      onProgress: (percent, label) => onProgress?.(65 + percent * 0.35, label),
    });
    return { bytes, eocdOffset };
  }

  async function buildPolyglotZip(entries, hostBytes, manifestBytes, { signal, onProgress } = {}) {
    const layout = prepareStoredZip(entries);
    reportProgress(onProgress, 0, 'Preparing ZIP records…');
    await checksumZipEntries(layout, {
      signal,
      onProgress: (percent, name) => reportProgress(onProgress, percent * 0.62, `Checking ZIP data for ${name}…`),
    });
    throwIfAborted(signal);

    const commentPrefix = encoder.encode('PolyglotMaker v1\u0000');
    const commentLength = commentPrefix.length + BOOTSTRAP_LENGTH;
    const zipLength = layout.totalSize + commentLength;
    if (hostBytes.length + zipLength > MAX_BYTES) {
      throw new Error(`The completed container exceeds the ${Math.round(MAX_BYTES / 1024 / 1024)} MiB browser limit, including ZIP overhead.`);
    }
    const result = new Uint8Array(hostBytes.length + zipLength);
    await copyBytesChunked(result, hostBytes, 0, {
      signal,
      onProgress: (percent) => reportProgress(onProgress, 62 + percent * 0.05, 'Copying host bytes…'),
    });
    const eocdOffset = await writeStoredZip(result, hostBytes.length, layout, {
      signal,
      onProgress: (percent, label) => reportProgress(onProgress, 67 + percent * 0.11, label),
    });

    reportProgress(onProgress, 78, 'Hashing ZIP archive…');
    const archiveHash = await sha256(result.subarray(hostBytes.length, hostBytes.length + layout.totalSize));
    throwIfAborted(signal);
    const manifestHash = await sha256(manifestBytes);
    throwIfAborted(signal);
    const fields = concat([
      new Uint8Array([1]), u64(hostBytes.length), u64(zipLength),
      manifestHash, archiveHash, u16(0),
    ]);
    const footer = encoder.encode(`${BOOTSTRAP_MAGIC}${toHex(fields)}`);
    const comment = concat([commentPrefix, footer]);
    if (comment.length > 65535) throw new Error('The ZIP bootstrap comment exceeds the format limit.');
    const view = new DataView(result.buffer, result.byteOffset, result.byteLength);
    view.setUint16(eocdOffset + 20, comment.length, true);
    result.set(comment, hostBytes.length + layout.totalSize);
    reportProgress(onProgress, 100, 'Integrity footer ready.');
    return { bytes: result, length: zipLength, bundleSha256: toHex(archiveHash) };
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
    const cleanKey = portableMemberKey(clean);
    if (!seen.has(cleanKey)) { seen.add(cleanKey); return clean; }
    const slash = clean.lastIndexOf('/');
    const dir = slash >= 0 ? clean.slice(0, slash + 1) : '';
    const base = slash >= 0 ? clean.slice(slash + 1) : clean;
    const dot = base.lastIndexOf('.');
    const stem = dot > 0 ? base.slice(0, dot) : base;
    const ext = dot > 0 ? base.slice(dot) : '';
    let n = 1;
    let candidate = safeMemberName(`${dir}${stem} (${n})${ext}`);
    while (seen.has(portableMemberKey(candidate))) candidate = safeMemberName(`${dir}${stem} (${++n})${ext}`);
    seen.add(portableMemberKey(candidate));
    return candidate;
  }

  async function readInput(file, dataOverride = null) {
    const content = dataOverride instanceof Uint8Array
      ? dataOverride
      : (file.data instanceof Uint8Array
        ? file.data
        : (file.data instanceof ArrayBuffer ? new Uint8Array(file.data) : new Uint8Array(await file.arrayBuffer())));
    return {
      name: safeMemberName(file.webkitRelativePath || file.name || 'payload.bin'),
      data: content,
      mtime: Math.max(315532800, Number(file.lastModified || file.mtime * 1000 || Date.now()) / 1000),
      mode: Number(file.mode) || 0o644,
      mime: file.type || file.mime || 'application/octet-stream',
      role: file.role || 'payload',
    };
  }

  async function createPolyglot({ payloads = [], hostFile = null, includeHost = true, onProgress, signal } = {}) {
    throwIfAborted(signal);
    reportProgress(onProgress, 1, hostFile ? 'Reading custom host…' : 'Preparing built-in PNG shell…');
    const hostBytes = hostFile ? new Uint8Array(await hostFile.arrayBuffer()) : pngShell();
    throwIfAborted(signal);
    reportProgress(onProgress, 3, 'Preparing payload list…');
    const seen = new Set();
    const inputs = [];
    if (hostFile && includeHost) inputs.push({ file: hostFile, role: 'host' });
    for (const file of payloads) inputs.push({ file, role: 'payload' });
    if (!inputs.length) throw new Error('Add at least one payload, or include the selected host file.');
    if (inputs.length > MAX_FILES) throw new Error(`The browser-only edition supports up to ${MAX_FILES.toLocaleString()} payloads.`);
    const hostAndPayloadSize = hostBytes.length + inputs.reduce((sum, item) => sum + Number(item.file.size || item.file.data?.byteLength || 0), 0);
    if (hostAndPayloadSize > MAX_BYTES) throw new Error(`Selected data exceeds the ${Math.round(MAX_BYTES / 1024 / 1024)} MiB browser limit.`);

    const files = [];
    const renamedFiles = [];
    for (let index = 0; index < inputs.length; index += 1) {
      throwIfAborted(signal);
      const input = inputs[index];
      const requestedName = input.file.webkitRelativePath || input.file.name || 'payload.bin';
      const file = await readInput(input.file, input.role === 'host' && input.file === hostFile ? hostBytes : null);
      file.name = uniqueName(file.name, seen);
      if (file.name !== requestedName) renamedFiles.push({ from: String(requestedName), to: file.name });
      file.role = input.role;
      files.push(file);
      reportProgress(onProgress, 4 + (index + 1) / inputs.length * 10, `Reading ${file.name}…`);
      if ((index + 1) % 64 === 0 && index + 1 < inputs.length) await yieldToBrowser();
    }

    const records = [];
    const totalPayloadBytes = files.reduce((sum, file) => sum + file.data.length, 0);
    let hashedBytes = 0;
    for (let index = 0; index < files.length; index += 1) {
      throwIfAborted(signal);
      const file = files[index];
      reportProgress(onProgress, 15 + (totalPayloadBytes ? hashedBytes / totalPayloadBytes : index / files.length) * 34, `Checking ${file.name}…`);
      const digest = await sha256Hex(file.data);
      throwIfAborted(signal);
      records.push({
        name: file.name,
        size: file.data.length,
        sha256: digest,
        mtime: file.mtime,
        mode: file.mode,
        mime: file.mime,
        role: file.role,
        store: 'zip',
        entry: `${PAYLOAD_PREFIX}${file.name}`,
      });
      hashedBytes += file.data.length;
      const ratio = totalPayloadBytes ? hashedBytes / totalPayloadBytes : (index + 1) / files.length;
      reportProgress(onProgress, 15 + ratio * 34, `Checked ${file.name}`);
    }

    throwIfAborted(signal);
    reportProgress(onProgress, 50, 'Writing bundle manifest…');
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
    const bundle = await buildPolyglotZip(zipEntries, hostBytes, manifestBytes, {
      signal,
      onProgress: (progress) => reportProgress(onProgress, 52 + progress.percent * 0.45, progress.label),
    });
    const bytes = bundle.bytes;
    const hostExt = hostFile ? extensionOf(hostFile.name) : 'png';
    const fileName = `polyglotmaker${hostExt ? `.${hostExt}` : '.polyglot'}`;
    reportProgress(onProgress, 100, 'Bundle ready to download.');
    return {
      bytes,
      fileName,
      host: manifest.container.host,
      hostBytes: hostBytes.length,
      bundleBytes: bundle.length,
      totalBytes: bytes.length,
      bundleSha256: bundle.bundleSha256,
      files: records.map(({ name, size, sha256: digest, mime, role }) => ({ name, size, sha256: digest, mime, role })),
      renamedFiles,
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

  async function inflateRaw(data, expectedSize, signal, onProgress) {
    if (typeof DecompressionStream === 'undefined') throw new Error('This browser cannot inflate DEFLATE ZIP entries. Try a current version of Chrome, Edge, Firefox, or Safari.');
    let decompressor;
    try { decompressor = new DecompressionStream('deflate-raw'); }
    catch { throw new Error('This browser does not support raw DEFLATE ZIP entries. Try a current browser.'); }
    const output = new Uint8Array(expectedSize);
    const reader = new Blob([data]).stream().pipeThrough(decompressor).getReader();
    let length = 0;
    try {
      while (true) {
        throwIfAborted(signal);
        const { done, value } = await reader.read();
        if (done) break;
        const nextLength = length + value.byteLength;
        if (nextLength > expectedSize || nextLength > MAX_BYTES) {
          await reader.cancel();
          throw new Error('A ZIP entry exceeds its declared size or the browser-only extraction limit.');
        }
        output.set(value, length);
        length = nextLength;
        onProgress?.(length, expectedSize, 'inflate');
      }
      if (length !== expectedSize) throw new Error('The inflated ZIP entry does not match its declared size.');
      throwIfAborted(signal);
      return output;
    } catch (error) {
      try { await reader.cancel(); } catch { /* The stream may already be closed. */ }
      throw error;
    }
  }

  async function extractEntry(data, archive, entry, limit, { signal, onProgress } = {}) {
    throwIfAborted(signal);
    const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
    const local = archive.start + entry.localOffset;
    const centralStart = archive.start + archive.cdOffset;
    if (local + 30 > centralStart || readU32(view, local) !== 0x04034b50) throw new Error(`Missing local ZIP record for ${entry.name}.`);
    const localFlags = readU16(view, local + 6);
    const localMethod = readU16(view, local + 8);
    if ((entry.flags & 1) || (localFlags & 1)) throw new Error('Encrypted ZIP members are not supported in the offline edition.');
    if (localMethod !== entry.method) throw new Error(`ZIP compression method does not match for ${entry.name}.`);
    const nameLength = readU16(view, local + 26);
    const extraLength = readU16(view, local + 28);
    const nameEnd = local + 30 + nameLength;
    const bodyStart = nameEnd + extraLength;
    const bodyEnd = bodyStart + entry.compressedSize;
    if (nameEnd > centralStart || bodyStart < local || bodyEnd > centralStart) throw new Error(`ZIP entry is truncated or overlaps the central directory: ${entry.name}`);
    const localName = safeMemberName(decoder.decode(data.subarray(local + 30, nameEnd)));
    if (localName !== entry.name) throw new Error(`ZIP filename records disagree for ${entry.name}.`);
    if (entry.size > limit || entry.size > MAX_BYTES) throw new Error(`ZIP entry is too large for this browser: ${entry.name}`);
    const compressed = data.subarray(bodyStart, bodyEnd);
    let output;
    if (entry.method === 0) output = compressed;
    else if (entry.method === 8) output = await inflateRaw(compressed, entry.size, signal, onProgress);
    else throw new Error(`Compression method ${entry.method} is not supported in the offline edition.`);
    if (output.length !== entry.size) throw new Error(`Size check failed for ${entry.name}.`);
    const checksum = await crc32Chunked(output, (current, total) => onProgress?.(current, total, 'crc'), signal);
    if (checksum !== entry.checksum) throw new Error(`ZIP CRC check failed for ${entry.name}.`);
    return output;
  }

  function equalBytes(a, b) {
    if (!a || !b || a.length !== b.length) return false;
    let diff = 0;
    for (let i = 0; i < a.length; i += 1) diff |= a[i] ^ b[i];
    return diff === 0;
  }

  async function unpackPolyglot(input, { onProgress, signal } = {}) {
    const data = input instanceof Uint8Array ? input : new Uint8Array(input);
    if (data.length > MAX_BYTES) throw new Error(`The container exceeds the ${Math.round(MAX_BYTES / 1024 / 1024)} MiB browser limit.`);
    throwIfAborted(signal);
    reportProgress(onProgress, 1, 'Scanning for an appended ZIP…');
    const archive = locateArchive(data);
    if (archive.entryCount > MAX_FILES + 1) throw new Error('The ZIP directory contains too many entries for this browser.');
    const entries = readDirectory(data, archive);
    const manifestEntry = entries.get(MANIFEST_PATH);
    if (!manifestEntry) throw new Error('No PolyglotMaker manifest was found in the appended ZIP.');
    if (manifestEntry.size > 4 * 1024 * 1024) throw new Error('The manifest is larger than the browser-only safety limit.');
    const manifestBytes = await extractEntry(data, archive, manifestEntry, 4 * 1024 * 1024, { signal });
    let manifest;
    try { manifest = JSON.parse(decoder.decode(manifestBytes)); }
    catch { throw new Error('The PolyglotMaker manifest is invalid JSON.'); }
    if (!manifest || manifest.tool !== 'PolyglotMaker' || manifest.format_version !== 1 || !Array.isArray(manifest.files)) {
      throw new Error('This is not a supported PolyglotMaker manifest.');
    }
    if (archive.footer) {
      reportProgress(onProgress, 5, 'Verifying bundle integrity…');
      const manifestHash = await sha256(manifestBytes);
      throwIfAborted(signal);
      if (!equalBytes(manifestHash, archive.footer.manifestHash)) throw new Error('The manifest SHA-256 check failed.');
      const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
      const commentLength = readU16(view, archive.eocd + 20);
      let archiveHash;
      view.setUint16(archive.eocd + 20, 0, true);
      try {
        archiveHash = await sha256(data.subarray(archive.start, archive.eocd + 22));
      } finally {
        view.setUint16(archive.eocd + 20, commentLength, true);
      }
      throwIfAborted(signal);
      if (!equalBytes(archiveHash, archive.footer.archiveHash)) throw new Error('The ZIP bundle SHA-256 check failed.');
    }
    if (manifest.files.length > MAX_FILES) throw new Error('The manifest contains too many files for this browser.');
    for (const record of manifest.files) {
      if (!record || typeof record !== 'object') throw new Error('A file record in the manifest is malformed.');
      const size = Number(record.size);
      if (!Number.isSafeInteger(size) || size < 0) throw new Error('A file size in the manifest is invalid.');
    }
    const declaredTotal = manifest.files.reduce((sum, item) => sum + Number(item.size), 0);
    if (!Number.isSafeInteger(declaredTotal) || declaredTotal < 0 || declaredTotal > MAX_BYTES) throw new Error('The payload set exceeds the browser-only extraction size limit.');

    const files = [];
    const renamedFiles = [];
    const seen = new Set();
    const seenEntries = new Set();
    let completedBytes = 0;
    for (let index = 0; index < manifest.files.length; index += 1) {
      throwIfAborted(signal);
      const record = manifest.files[index];
      const requestedName = String(record.name || '');
      const originalName = safeMemberName(requestedName);
      const name = uniqueName(originalName, seen);
      if (name !== requestedName) renamedFiles.push({ from: requestedName, to: name });
      const expectedDigest = String(record.sha256 || '');
      if (!/^[0-9a-f]{64}$/i.test(expectedDigest)) throw new Error(`A valid SHA-256 digest is missing for ${name}.`);
      const entryName = safeMemberName(record.entry || `${PAYLOAD_PREFIX}${originalName}`);
      const entryKey = portableMemberKey(entryName);
      if (!entryName.startsWith(PAYLOAD_PREFIX) || seenEntries.has(entryKey)) throw new Error(`The manifest points multiple files at an unsafe or repeated ZIP entry: ${entryName}`);
      seenEntries.add(entryKey);
      const zipEntry = entries.get(entryName);
      if (!zipEntry) throw new Error(`Payload data is missing: ${name}`);
      if (Number(record.size) !== zipEntry.size) throw new Error(`Manifest size does not match for ${name}.`);
      const size = Number(record.size);
      const entryBase = declaredTotal ? completedBytes / declaredTotal : index / manifest.files.length;
      reportProgress(onProgress, 10 + entryBase * 80, `Restoring ${name}…`);
      const bytes = await extractEntry(data, archive, zipEntry, size, {
        signal,
        onProgress: (current, total, phase) => {
          const withinEntry = total
            ? (zipEntry.method === 8 && phase === 'inflate' ? current / total * 0.45 : 0.45 + current / total * 0.55)
            : 1;
          const fraction = declaredTotal
            ? (completedBytes + size * withinEntry) / declaredTotal
            : (index + withinEntry) / manifest.files.length;
          reportProgress(onProgress, 10 + fraction * 80, `${phase === 'crc' ? 'Checking' : 'Restoring'} ${name}…`);
        },
      });
      const verifiedFraction = declaredTotal
        ? (completedBytes + size) / declaredTotal
        : (index + 1) / manifest.files.length;
      reportProgress(onProgress, 10 + verifiedFraction * 80, `Verifying ${name}…`);
      const digest = await sha256Hex(bytes);
      throwIfAborted(signal);
      if (digest.toLowerCase() !== expectedDigest.toLowerCase()) throw new Error(`SHA-256 check failed for ${name}.`);
      files.push({ name, size: bytes.length, sha256: digest, mime: record.mime || 'application/octet-stream', role: record.role || 'payload', data: bytes });
      completedBytes += size;
      const progress = declaredTotal ? completedBytes / declaredTotal : (index + 1) / manifest.files.length;
      reportProgress(onProgress, 10 + progress * 80, `Verified ${name}`);
    }
    if (!files.length) throw new Error('The manifest contains no recoverable payload files.');
    reportProgress(onProgress, 100, 'All payload checksums verified.');
    return {
      files,
      source: archive.footer ? 'ZIP bundle · integrity footer verified' : 'ZIP bundle · payload checks verified',
      bundleSha256: archive.footer ? toHex(archive.footer.archiveHash) : null,
      renamedFiles,
    };
  }

  async function downloadZip(files, { onProgress, signal } = {}) {
    const seen = new Set();
    const entries = files.map((file) => ({ name: uniqueName(file.name, seen), data: file.data, mtime: Date.now() / 1000, mode: 0o644 }));
    const result = await buildStoredZip(entries, {
      signal,
      onProgress: (percent, name) => reportProgress(onProgress, percent, `Packing ${name}…`),
    });
    return result.bytes;
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
