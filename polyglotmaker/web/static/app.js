(() => {
  'use strict';

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const state = { files: [], formats: [], toastTimer: null, unpackFile: null, inspectFile: null };
  const MAX_UPLOAD_BYTES = 256 * 1024 * 1024;
  const colors = { host: '#a6f4d6', embed: '#8ad6e7', layer: '#b6a6ff', bundle: '#ffd08a', footer: '#ff9bb0', padding: '#56647d', meta: '#7086a1' };
  const byte = (value) => {
    if (value < 1024) return `${value} B`;
    if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
    if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MiB`;
    return `${(value / 1024 ** 3).toFixed(2)} GiB`;
  };
  const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const extOf = (name) => {
    const idx = name.lastIndexOf('.');
    return idx >= 0 ? name.slice(idx + 1).toUpperCase().slice(0, 5) : 'FILE';
  };

  function toast(message, error = false) {
    const node = $('#toast');
    node.textContent = message;
    node.classList.toggle('error', error);
    node.classList.add('visible');
    clearTimeout(state.toastTimer);
    state.toastTimer = setTimeout(() => node.classList.remove('visible'), 3600);
  }

  async function api(path, formData, method = 'POST') {
    const response = await fetch(path, { method, body: formData || undefined });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.error) throw new Error(payload.error || `Request failed (${response.status})`);
    return payload;
  }

  function switchView(view) {
    $$('.nav-item').forEach((button) => button.classList.toggle('active', button.dataset.view === view));
    $$('.view').forEach((section) => section.classList.toggle('active', section.id === `view-${view}`));
    const labels = { make: 'Create', unpack: 'Unpack', inspect: 'Inspect', formats: 'Formats' };
    $('#crumbCurrent').textContent = labels[view] || 'Create';
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  function addFiles(files) {
    const incoming = [...files];
    if (incoming.some((file) => file.size > MAX_UPLOAD_BYTES)) {
      toast('A file is larger than the 256 MiB request limit.', true);
      return;
    }
    for (const file of incoming) {
      if (!state.files.some((old) => old.name === file.name && old.size === file.size && old.lastModified === file.lastModified)) {
        state.files.push(file);
      }
    }
    drawFiles();
  }

  function drawFiles() {
    $('#makeResult').hidden = true;
    const list = $('#fileList');
    $('#fileCount').textContent = `${state.files.length} FILE${state.files.length === 1 ? '' : 'S'}`;
    $('#statFiles').textContent = String(state.files.length);
    $('#statTypes').textContent = String(new Set(state.files.map((file) => extOf(file.name))).size);
    if (!state.files.length) {
      list.innerHTML = '<div class="empty-files">Your selected files will appear here.</div>';
      updateHostPreview();
      return;
    }
    list.innerHTML = state.files.map((file, index) => `<div class="file-row">
      <span class="file-type-icon">${escapeHtml(extOf(file.name))}</span>
      <span class="file-meta"><strong title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</strong><small>${byte(file.size)} · ${escapeHtml(file.type || 'unknown type')}</small></span>
      <button class="file-remove" aria-label="Remove ${escapeHtml(file.name)}" data-remove="${index}">×</button>
    </div>`).join('');
    $$('[data-remove]', list).forEach((button) => button.addEventListener('click', () => {
      state.files.splice(Number(button.dataset.remove), 1);
      drawFiles();
    }));
    updateHostPreview();
  }

  function updateHostPreview() {
    const selected = $('#hostSelect').value;
    let format = state.formats.find((item) => item.key === selected);
    if (selected === 'auto' && state.files.length) {
      const ext = state.files[0].name.split('.').pop().toLowerCase();
      format = state.formats.find((item) => item.extension.replace(/^\./, '').toLowerCase() === ext || item.aliases?.includes(ext));
    }
    $('#previewFormat').textContent = format ? format.label : (state.files.length ? 'Auto-selected' : 'Auto-selected');
    $('#previewExtension').textContent = format ? `${format.extension} · ${format.category}` : (state.files.length ? `from ${state.files[0].name}` : 'Waiting for files');
  }

  function drawFormatControls() {
    const host = $('#hostSelect');
    const layer = $('#alsoSelect');
    const categories = new Set();
    const groups = new Map();
    for (const format of state.formats) {
      categories.add(format.category);
      if (!groups.has(format.category)) groups.set(format.category, []);
      groups.get(format.category).push(format);
    }
    for (const [category, formats] of groups) {
      const group = document.createElement('optgroup');
      group.label = category[0].toUpperCase() + category.slice(1);
      for (const format of formats) {
        const option = document.createElement('option');
        option.value = format.key;
        option.textContent = `${format.label} (${format.extension})`;
        group.append(option);
      }
      host.append(group);
    }
    for (const format of state.formats) {
      const option = document.createElement('option');
      option.value = format.key;
      option.textContent = `${format.key} · ${format.extension} — ${format.label}`;
      layer.append(option);
    }
    const categorySelect = $('#categoryFilter');
    [...categories].sort().forEach((category) => {
      const option = document.createElement('option');
      option.value = category;
      option.textContent = category[0].toUpperCase() + category.slice(1);
      categorySelect.append(option);
    });
    $('#formatCount').textContent = String(state.formats.length);
  }

  function drawCatalog() {
    const query = $('#formatSearch').value.trim().toLowerCase();
    const category = $('#categoryFilter').value;
    const shown = state.formats.filter((format) => {
      const haystack = `${format.key} ${format.label} ${format.extension} ${format.category} ${(format.aliases || []).join(' ')}`.toLowerCase();
      return (!query || haystack.includes(query)) && (!category || format.category === category);
    });
    $('#formatGrid').innerHTML = shown.map((format) => `<article class="format-card">
      <div class="format-card-head"><strong>${escapeHtml(format.label)}</strong><span class="format-ext">${escapeHtml(format.extension)}</span></div>
      <p>${escapeHtml(format.notes || 'Lossless arbitrary payload support.')}</p><span class="format-category">${escapeHtml(format.category)} · ${escapeHtml(format.key)}</span>
    </article>`).join('') || '<div class="inspect-empty">No formats match that search.</div>';
  }

  function setBusy(button, label) {
    button.disabled = true;
    const original = button.dataset.original || button.innerHTML;
    button.dataset.original = original;
    button.innerHTML = `${escapeHtml(label)} <span class="loading-dots"><i></i><i></i><i></i></span>`;
    return () => { button.disabled = false; button.innerHTML = original; };
  }

  function formForFile(file, field = 'file') {
    const form = new FormData();
    form.append(field, file, file.name);
    return form;
  }

  function createSvgMap(container, segments, totalSize) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 1000 68');
    svg.setAttribute('preserveAspectRatio', 'none');
    svg.setAttribute('class', 'byte-map');
    const base = document.createElementNS(svg.namespaceURI, 'rect');
    base.setAttribute('x', '4'); base.setAttribute('y', '19'); base.setAttribute('width', '992'); base.setAttribute('height', '27');
    base.setAttribute('rx', '5'); base.setAttribute('fill', '#182238'); svg.append(base);
    const sorted = [...(segments || [])].filter((item) => item.length > 0).sort((a, b) => a.offset - b.offset || b.length - a.length);
    for (const segment of sorted) {
      const rect = document.createElementNS(svg.namespaceURI, 'rect');
      const start = Math.max(0, Number(segment.offset) || 0);
      const end = Math.min(totalSize, start + Math.max(0, Number(segment.length) || 0));
      const x = 4 + (start / Math.max(1, totalSize)) * 992;
      const width = Math.max(1.2, ((end - start) / Math.max(1, totalSize)) * 992);
      rect.setAttribute('x', Math.min(996, x).toFixed(2)); rect.setAttribute('y', '19');
      rect.setAttribute('width', Math.max(0, Math.min(width, 996 - x)).toFixed(2)); rect.setAttribute('height', '27');
      rect.setAttribute('rx', '3'); rect.setAttribute('fill', colors[segment.kind] || colors.meta);
      rect.setAttribute('opacity', segment.kind === 'padding' ? '.6' : '.9');
      const title = document.createElementNS(svg.namespaceURI, 'title');
      title.textContent = `${segment.name || segment.kind} · ${segment.kind} · offset ${start.toLocaleString()} · ${byte(Number(segment.length) || 0)}`;
      rect.append(title); svg.append(rect);
    }
    const axis = document.createElementNS(svg.namespaceURI, 'text');
    axis.setAttribute('x', '4'); axis.setAttribute('y', '61'); axis.setAttribute('fill', '#75839a');
    axis.setAttribute('font-size', '9'); axis.textContent = `0x00000000  →  ${totalSize.toLocaleString()} bytes`;
    svg.append(axis);
    container.replaceChildren(svg);
  }

  function renderMapLegend(container) {
    container.innerHTML = '<div class="map-legend"><span><i class="legend-dot legend-host"></i>Host</span><span><i class="legend-dot legend-embed"></i>Embedded-legal</span><span><i class="legend-dot legend-layer"></i>Separate layer</span><span><i class="legend-dot legend-bundle"></i>Payload bundle</span><span><i class="legend-dot legend-meta"></i>Metadata</span></div>';
  }

  async function makePolyglot() {
    if (!state.files.length) return toast('Add at least one file first.', true);
    const button = $('#makeButton');
    const restore = setBusy(button, 'Building');
    const form = new FormData();
    for (const file of state.files) form.append('files', file, file.name);
    if ($('#useFirstAsHost').checked && state.files[0]) {
      form.append('host_file', state.files[0], state.files[0].name);
    }
    form.append('host', $('#hostSelect').value);
    form.append('embed_mode', $('#embedMode').value);
    form.append('store', $('#storeSelect').value);
    form.append('compress', $('#compressSelect').value);
    form.append('width', '8'); form.append('height', '8');
    for (const option of $('#alsoSelect').selectedOptions) form.append('also', option.value);
    if ($('#obfuscateToggle').checked) {
      const key = $('#obfuscationKey').value;
      if (!key) { restore(); return toast('Enter an obfuscation key, or turn the option off.', true); }
      form.append('obfuscate', key);
    }
    try {
      const result = await api('/api/make', form);
      const output = $('#makeResult');
      output.hidden = false;
      const warnings = result.warnings?.length ? `<div class="warning-list">${result.warnings.map((warning) => `<p>⚠ ${escapeHtml(warning)}</p>`).join('')}</div>` : '';
      output.innerHTML = `<div class="result-heading"><div class="success-label"><span class="success-check">✓</span> Polyglot file created</div><div class="result-actions"><a class="secondary-button" href="${escapeHtml(result.download_url)}" download="${escapeHtml(result.download_name)}">Download ${escapeHtml(result.extension)} <span>↓</span></a></div></div>
        <p class="result-subtitle">${escapeHtml(result.host)} host · ${byte(result.size)} · ${result.manifest.files.length} payload file${result.manifest.files.length === 1 ? '' : 's'}</p>
        ${warnings}<div class="result-map"><div class="map-caption"><span>BYTE MAP · HOVER SEGMENTS FOR DETAILS</span><span>${byte(result.size)}</span></div><div id="makeMap"></div><div id="makeLegend"></div></div>
        <details class="payload-details"><summary>View bundled file checksums <span>${result.manifest.files.length} files</span></summary><div class="payload-table">${result.manifest.files.map((file) => `<div><span>${escapeHtml(file.name)}</span><code>${escapeHtml(file.sha256.slice(0, 16))}…</code></div>`).join('')}</div></details>`;
      createSvgMap($('#makeMap', output), result.segments || [], result.size);
      renderMapLegend($('#makeLegend', output));
      toast('Your container is ready to download.');
      output.scrollIntoView({ behavior: 'smooth', block: 'center' });
    } catch (error) {
      toast(error.message || 'Could not create this container.', true);
    } finally { restore(); }
  }

  function downloadData(name, encoded, mime = 'application/octet-stream') {
    const binary = atob(encoded);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
    const url = URL.createObjectURL(new Blob([bytes], { type: mime }));
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = name;
    document.body.append(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 1500);
  }

  async function unpackContainer() {
    const file = state.unpackFile || $('#unpackFile').files[0];
    if (!file) return toast('Choose a container file first.', true);
    const button = $('#unpackButton'); const restore = setBusy(button, 'Reading bundle');
    const form = formForFile(file); if ($('#unpackKey').value) form.append('key', $('#unpackKey').value);
    try {
      const result = await api('/api/unpack', form);
      const target = $('#unpackResult'); target.hidden = false;
      if (!result.files.length) {
        target.innerHTML = '<div class="inspect-empty">The manifest was found, but no payload store was recoverable.</div>';
        return;
      }
      target.innerHTML = `<div class="success-label"><span class="success-check">✓</span> ${result.files.length} file${result.files.length === 1 ? '' : 's'} verified · ${escapeHtml(result.source_store)}</div>
        <div class="unpacked-list">${result.files.map((item, index) => `<div class="unpacked-row"><span class="file-type-icon">${escapeHtml(extOf(item.name))}</span><div><strong title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</strong><small>${byte(item.size)} · SHA-256 ${escapeHtml(item.sha256.slice(0, 12))}…</small></div><button class="download-one" data-download="${index}">Save ↓</button></div>`).join('')}</div>`;
      $$('[data-download]', target).forEach((button) => button.addEventListener('click', () => {
        const item = result.files[Number(button.dataset.download)];
        downloadData(item.name, item.data_base64, item.mime || 'application/octet-stream');
      }));
      toast('All payload checksums verified.');
    } catch (error) { toast(error.message || 'Could not unpack this file.', true); }
    finally { restore(); }
  }

  function renderInspection(target, report) {
    target.hidden = false;
    const detections = report.detections || [];
    const chips = detections.length ? detections.map((item) => `<div class="detect-chip"><span class="grade ${escapeHtml(item.validity)}">${escapeHtml(item.validity)}</span><strong>${escapeHtml(item.label)}</strong><small>${escapeHtml(item.fmt)} · offset 0x${Number(item.offset).toString(16).padStart(8, '0')}</small></div>`).join('') : '<div class="inspect-empty">No known signatures detected.</div>';
    const segments = (report.byte_map || []).slice(0, 40);
    const rows = segments.map((item) => `<tr><td>0x${Number(item.offset).toString(16).padStart(8, '0')}</td><td>${byte(item.length)}</td><td>${escapeHtml(item.kind)}</td><td>${escapeHtml(item.name)}</td></tr>`).join('');
    target.innerHTML = `<div class="inspect-head"><h2>${escapeHtml(report.host || 'Unknown host')} · ${byte(report.size)}</h2><span class="inspect-host-pill">${detections.length} detections</span></div>
      <div class="detect-grid">${chips}</div><div class="inspect-map-wrap"><div class="map-caption"><span>STRUCTURAL BYTE MAP</span><span>COLOR = REGION TYPE</span></div><div id="inspectMap"></div><div id="inspectLegend"></div>
      <table class="byte-table"><thead><tr><th>OFFSET</th><th>SIZE</th><th>KIND</th><th>SEGMENT</th></tr></thead><tbody>${rows}</tbody></table></div>`;
    createSvgMap($('#inspectMap', target), report.byte_map || [], report.size);
    renderMapLegend($('#inspectLegend', target));
  }

  async function inspectContainer(verify = false) {
    const file = state.inspectFile || $('#inspectFile').files[0];
    if (!file) return toast('Choose a file to inspect first.', true);
    const button = verify ? $('#verifyButton') : $('#inspectButton');
    const restore = setBusy(button, verify ? 'Verifying' : 'Scanning');
    const form = formForFile(file);
    if (verify && $('#inspectKey').value) form.append('key', $('#inspectKey').value);
    try {
      const report = await api(verify ? '/api/verify' : '/api/inspect', form);
      if (verify) {
        const target = $('#inspectResult'); target.hidden = false;
        const checks = report.checks || [];
        target.innerHTML = `<div class="inspect-head"><h2>${report.valid ? 'Integrity checks passed' : 'Verification reported issues'}</h2><span class="inspect-host-pill">${report.valid ? 'VALID' : 'CHECK'}</span></div><div class="verify-list">${checks.map((check) => `<div><span class="verify-mark ${check.ok ? 'ok' : 'fail'}">${check.ok ? '✓' : '×'}</span><strong>${escapeHtml(check.name)}</strong><small>${escapeHtml(check.detail)}</small></div>`).join('')}</div>`;
        if (!report.valid) toast((report.errors || ['Verification failed']).join('; '), true);
      } else {
        renderInspection($('#inspectResult'), report);
        toast('Byte map ready.');
      }
    } catch (error) { toast(error.message || 'Could not inspect this file.', true); }
    finally { restore(); }
  }

  function bindDropZone() {
    const zone = $('#dropZone');
    zone.addEventListener('dragover', (event) => { event.preventDefault(); zone.classList.add('dragover'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
    zone.addEventListener('drop', (event) => { event.preventDefault(); zone.classList.remove('dragover'); addFiles(event.dataTransfer.files); });
    $('#payloadFiles').addEventListener('change', (event) => { addFiles(event.target.files); event.target.value = ''; });

    const unpackZone = $('.small-drop');
    unpackZone.addEventListener('dragover', (event) => { event.preventDefault(); unpackZone.classList.add('dragover'); });
    unpackZone.addEventListener('dragleave', () => unpackZone.classList.remove('dragover'));
    unpackZone.addEventListener('drop', (event) => {
      event.preventDefault();
      unpackZone.classList.remove('dragover');
      const file = event.dataTransfer.files[0];
      if (!file) return;
      if (file.size > MAX_UPLOAD_BYTES) return toast('This file is larger than the 256 MiB request limit.', true);
      state.unpackFile = file;
      $('#unpackFileName').textContent = file.name;
      $('#unpackResult').hidden = true;
    });
  }

  async function init() {
    $$('.nav-item').forEach((button) => button.addEventListener('click', () => switchView(button.dataset.view)));
    bindDropZone();
    $('#hostSelect').addEventListener('change', updateHostPreview);
    $('#makeButton').addEventListener('click', makePolyglot);
    $('#unpackButton').addEventListener('click', unpackContainer);
    $('#inspectButton').addEventListener('click', () => inspectContainer(false));
    $('#verifyButton').addEventListener('click', () => inspectContainer(true));
    $('#unpackFile').addEventListener('change', (event) => {
      const file = event.target.files[0] || null;
      event.target.value = '';
      if (file && file.size > MAX_UPLOAD_BYTES) {
        state.unpackFile = null;
        $('#unpackFileName').textContent = 'Drop or choose a container';
        return toast('This file is larger than the 256 MiB request limit.', true);
      }
      state.unpackFile = file;
      $('#unpackFileName').textContent = file?.name || 'Drop or choose a container';
      $('#unpackResult').hidden = true;
    });
    $('#inspectFile').addEventListener('change', (event) => {
      const file = event.target.files[0] || null;
      event.target.value = '';
      if (file && file.size > MAX_UPLOAD_BYTES) {
        state.inspectFile = null;
        $('#inspectFileName').textContent = 'Choose file to inspect';
        return toast('This file is larger than the 256 MiB request limit.', true);
      }
      state.inspectFile = file;
      $('#inspectFileName').textContent = file?.name || 'Choose file to inspect';
      $('#inspectResult').hidden = true;
    });
    $('#obfuscateToggle').addEventListener('change', (event) => { $('#keyRow').hidden = !event.target.checked; });
    $('#formatSearch').addEventListener('input', drawCatalog);
    $('#categoryFilter').addEventListener('change', drawCatalog);
    $('#alsoSelect').addEventListener('change', () => {
      if ($('#alsoSelect').selectedOptions.length > 4) {
        $('#alsoSelect').selectedOptions[$('#alsoSelect').selectedOptions.length - 1].selected = false;
        toast('Select up to four extra format layers.');
      }
    });
    try {
      const data = await api('/api/formats', null, 'GET');
      state.formats = data.formats || [];
      $('#versionLabel').textContent = `v${data.version}`;
      drawFormatControls(); drawCatalog(); updateHostPreview();
    } catch (error) {
      toast(`Could not load format catalog: ${error.message}`, true);
    }
  }

  document.addEventListener('DOMContentLoaded', init);
})();
