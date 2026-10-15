(() => {
  'use strict';

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const state = {
    payloads: [],
    payloadSearch: '',
    payloadSort: 'name-asc',
    hostFile: null,
    containerFile: null,
    unpackedFiles: [],
    selectedUnpacked: new Set(),
    unpackedSearch: '',
    unpackedSort: 'name-asc',
    unpackSource: '',
    unpackBundleSha256: null,
    toastTimer: 0,
    createUrl: null,
    createResult: null,
    createController: null,
    unpackController: null,
    zipController: null,
  };
  const LIMIT = window.PolyglotEngine?.MAX_BYTES || 128 * 1024 * 1024;
  const MAX_FILES = window.PolyglotEngine?.MAX_FILES || 10000;
  const MAX_VISIBLE_PAYLOADS = 150;
  const MAX_VISIBLE_UNPACKED = 150;

  const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[character]);

  function payloadName(file) {
    return file.webkitRelativePath || file.name || 'payload.bin';
  }

  function extension(name) {
    const base = String(name || '').replace(/\\/g, '/').split('/').pop();
    const dot = base.lastIndexOf('.');
    return dot > 0 ? base.slice(dot + 1).toUpperCase().slice(0, 5) : 'FILE';
  }

  function prettyBytes(value) {
    if (value < 1024) return `${value} B`;
    if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KiB`;
    if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MiB`;
    return `${(value / 1024 ** 3).toFixed(2)} GiB`;
  }

  function totalSourceBytes(files = state.payloads, hostFile = state.hostFile, includeHost = $('#includeHost')?.checked) {
    const payloadBytes = files.reduce((total, file) => total + file.size, 0);
    return payloadBytes + (hostFile ? hostFile.size * (includeHost ? 2 : 1) : 0);
  }

  const fileNameCollator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });

  function compareFileRows(left, right, sort, getName) {
    const nameLeft = getName(left.file);
    const nameRight = getName(right.file);
    const direction = sort.endsWith('desc') ? -1 : 1;
    const primary = sort.startsWith('size')
      ? left.file.size - right.file.size
      : fileNameCollator.compare(nameLeft, nameRight);
    return direction * (primary || fileNameCollator.compare(nameLeft, nameRight));
  }

  function normalizePath(name) {
    return String(name || '').split(String.fromCharCode(92)).join('/');
  }

  function getOutputExtension(name) {
    const base = normalizePath(name).split('/').pop();
    const dot = base.lastIndexOf('.');
    const extension = dot > 0 ? base.slice(dot + 1).toLowerCase().replace(/[^a-z0-9]/g, '').slice(0, 10) : '';
    return extension || 'polyglot';
  }

  function outputFilename(stem, fallback) {
    const fallbackBase = normalizePath(fallback || 'polyglotmaker.polyglot').split('/').pop();
    const dot = fallbackBase.lastIndexOf('.');
    const extension = dot > 0 ? fallbackBase.slice(dot) : '.polyglot';
    const fallbackStem = dot > 0 ? fallbackBase.slice(0, dot) : fallbackBase;
    const safeStem = [...String(stem || '').trim()]
      .map((character) => {
        const code = character.charCodeAt(0);
        return '<>:\"|?*'.includes(character) || code < 32 || code === 127 ? '_' : character;
      })
      .join('')
      .split(String.fromCharCode(92)).join('_')
      .replaceAll('/', '_')
      .replace(/^[. ]+/, '')
      .replace(/[. ]+$/, '')
      .slice(0, 100)
      .replace(/[. ]+$/, '');
    const normalizedStem = safeStem.toLowerCase().endsWith(extension.toLowerCase())
      ? safeStem.slice(0, -extension.length)
      : safeStem;
    const selectedStem = normalizedStem || fallbackStem;
    const portableStem = /^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)/i.test(selectedStem) ? `_${selectedStem}` : selectedStem;
    return `${portableStem}${extension}`;
  }

  function updatePayloadBudget() {
    const bytes = totalSourceBytes();
    const mib = Math.min(LIMIT / (1024 ** 2), bytes / (1024 ** 2));
    const fileCount = state.payloads.length + (state.hostFile && $('#includeHost')?.checked ? 1 : 0);
    $('#payloadBudgetText').textContent = `${prettyBytes(bytes)} of ${prettyBytes(LIMIT)} estimated`;
    $('#payloadFileTotal').textContent = `${fileCount.toLocaleString()} file${fileCount === 1 ? '' : 's'}`;
    const track = $('.capacity-track');
    track.setAttribute('aria-valuemax', String(LIMIT / (1024 ** 2)));
    track.setAttribute('aria-valuenow', String(Number(mib.toFixed(2))));
    track.firstElementChild.style.width = `${Math.min(100, bytes / LIMIT * 100)}%`;
  }

  function notify(message, error = false) {
    const toast = $('#toast');
    toast.textContent = message;
    toast.classList.toggle('error', error);
    toast.classList.add('visible');
    clearTimeout(state.toastTimer);
    state.toastTimer = setTimeout(() => toast.classList.remove('visible'), 3400);
  }

  function setBusy(button, label) {
    const original = button.dataset.original || button.innerHTML;
    button.dataset.original = original;
    button.disabled = true;
    button.innerHTML = `${escapeHtml(label)} <span class="loading-dots" aria-hidden="true"><i></i><i></i><i></i></span>`;
    return () => { button.innerHTML = original; };
  }

  function updateProgress(id, progress = {}) {
    const root = $(`#${id}`);
    if (!root) return;
    root.hidden = false;
    const percent = Math.max(0, Math.min(100, Math.round(Number(progress.percent) || 0)));
    $('[data-progress-label]', root).textContent = progress.label || 'Working locally…';
    $('[data-progress-value]', root).textContent = `${percent}%`;
    const track = $('.progress-track', root);
    track.setAttribute('aria-valuenow', String(percent));
    track.firstElementChild.style.width = `${percent}%`;
  }

  function hideProgress(id) {
    const root = $(`#${id}`);
    if (root) root.hidden = true;
  }

  function clearCreateResult() {
    state.createController?.abort();
    $('#createResult').hidden = true;
    if (state.createUrl) URL.revokeObjectURL(state.createUrl);
    state.createUrl = null;
    state.createResult = null;
  }

  function clearUnpackedResult() {
    state.unpackedFiles = [];
    state.selectedUnpacked.clear();
    state.unpackedSearch = '';
    state.unpackSource = '';
    state.unpackBundleSha256 = null;
    const target = $('#unpackResult');
    target.replaceChildren();
    target.hidden = true;
    hideProgress('unpackProgress');
  }

  function switchTab(name) {
    $$('.mode-tab').forEach((tab) => {
      const active = tab.dataset.tab === name;
      tab.classList.toggle('active', active);
      tab.setAttribute('aria-pressed', String(active));
    });
    $$('.tool-view').forEach((view) => view.classList.toggle('active', view.id === `view-${name}`));
    if (!window.matchMedia('(prefers-reduced-motion: reduce)').matches) window.scrollTo({ top: 0, behavior: 'smooth' });
    else window.scrollTo({ top: 0, behavior: 'auto' });
  }

  function updateCreateButton() {
    const includesHost = state.hostFile && $('#includeHost').checked;
    const busy = state.createController || state.unpackController || state.zipController;
    $('#createButton').disabled = Boolean(busy) || (!state.payloads.length && !includesHost);
  }

  function updateUnpackButton() {
    const busy = state.createController || state.unpackController || state.zipController;
    $('#unpackButton').disabled = Boolean(busy) || !state.containerFile;
  }

  function updateOperationButtons() {
    updateCreateButton();
    updateUnpackButton();
    updateUnpackedSelection();
  }

  function drawPayloads() {
    const list = $('#payloadList');
    const toolbar = $('#payloadListToolbar');
    const count = state.payloads.length;
    $('#payloadCount').textContent = `${count} FILE${count === 1 ? '' : 'S'}`;
    updatePayloadBudget();
    toolbar.hidden = count === 0;
    if (!count) {
      $('#payloadListSummary').textContent = '';
      list.innerHTML = '<div class="empty-list">Your payloads will appear here.</div>';
      updateOperationButtons();
      return;
    }

    const query = state.payloadSearch.trim().toLocaleLowerCase();
    const matching = [];
    state.payloads.forEach((file, index) => {
      const searchable = `${payloadName(file)} ${file.type || ''}`.toLocaleLowerCase();
      if (!query || searchable.includes(query)) matching.push({ file, index });
    });
    matching.sort((left, right) => compareFileRows(left, right, state.payloadSort, payloadName));
    const visible = matching.slice(0, MAX_VISIBLE_PAYLOADS);
    $('#payloadListSummary').textContent = query ? `${matching.length} match${matching.length === 1 ? '' : 'es'}` : `${count} total`;
    if (!visible.length) {
      list.innerHTML = '<div class="empty-list">No payloads match that filter.</div>';
    } else {
      list.innerHTML = visible.map(({ file, index }) => `<div class="file-row">
        <span class="file-chip-icon">${escapeHtml(extension(payloadName(file)))}</span>
        <span class="file-meta"><strong title="${escapeHtml(payloadName(file))}">${escapeHtml(payloadName(file))}</strong><small>${prettyBytes(file.size)} · ${escapeHtml(file.type || 'unknown file type')}</small></span>
        <button class="remove-file" type="button" data-remove="${index}" aria-label="Remove ${escapeHtml(payloadName(file))}">×</button>
      </div>`).join('');
      if (matching.length > visible.length) {
        list.insertAdjacentHTML('beforeend', `<div class="render-limit">Showing ${visible.length} of ${matching.length}. Filter to find more files.</div>`);
      }
    }
    updateOperationButtons();
  }

  function addPayloads(fileList) {
    const additions = [...fileList];
    if (!additions.length) return;

    const includesHost = Boolean(state.hostFile && $('#includeHost').checked);
    if (state.payloads.length + additions.length + (includesHost ? 1 : 0) > MAX_FILES) {
      return notify(`The browser edition supports up to ${MAX_FILES.toLocaleString()} files per bundle.`, true);
    }
    const nextTotal = totalSourceBytes() + additions.reduce((sum, file) => sum + file.size, 0);
    if (nextTotal > LIMIT) return notify(`Selected data exceeds the ${Math.round(LIMIT / 1024 / 1024)} MiB browser limit.`, true);
    state.payloads.push(...additions);
    clearCreateResult();
    drawPayloads();
  }

  function removePayload(index) {
    if (!Number.isInteger(index) || index < 0 || index >= state.payloads.length) return;
    state.payloads.splice(index, 1);
    clearCreateResult();
    drawPayloads();
  }

  function clearPayloads() {
    if (!state.payloads.length) return;
    state.payloads = [];
    state.payloadSearch = '';
    $('#payloadSearch').value = '';
    clearCreateResult();
    drawPayloads();
    notify('Payload list cleared.');
  }

  function setHostFile(file) {
    if (file && $('#includeHost').checked && state.payloads.length + 1 > MAX_FILES) {
      return notify(`The browser edition supports up to ${MAX_FILES.toLocaleString()} files per bundle.`, true);
    }
    if (file && totalSourceBytes(state.payloads, file, $('#includeHost').checked) > LIMIT) {
      return notify(`Selected data exceeds the ${Math.round(LIMIT / 1024 / 1024)} MiB browser limit.`, true);
    }
    state.hostFile = file || null;
    $('#hostSelected').hidden = !file;
    $('#hostPickerTitle').textContent = file ? 'Custom host selected' : 'Choose an existing host';
    $('#hostPickerHint').textContent = file ? 'The host bytes will remain untouched' : 'Optional · keep its original bytes';
    if (file) {
      $('#hostExt').textContent = extension(file.name);
      $('#hostName').textContent = file.name;
      $('#hostSize').textContent = `${prettyBytes(file.size)} · ${file.type || 'unknown file type'}`;
    }
    $('#outputExtension').textContent = `.${getOutputExtension(file?.name || 'web-only.png')}`;
    updatePayloadBudget();
    clearCreateResult();
    updateOperationButtons();
  }

  function setContainerFile(file) {
    if (file && file.size > LIMIT) return notify(`This file exceeds the ${Math.round(LIMIT / 1024 / 1024)} MiB browser limit.`, true);
    if (state.containerFile !== file) {
      state.unpackController?.abort();
      state.zipController?.abort();
      clearUnpackedResult();
    }
    state.containerFile = file || null;
    $('#containerSelected').hidden = !file;
    $('#containerPrompt').textContent = file ? 'Container selected' : 'Drop a PolyglotMaker file here';
    if (file) {
      $('#containerExt').textContent = extension(file.name);
      $('#containerName').textContent = file.name;
      $('#containerSize').textContent = `${prettyBytes(file.size)} · ready to scan locally`;
    } else {
      $('#containerName').textContent = '';
      $('#containerSize').textContent = '';
    }
    updateOperationButtons();
  }

  function clickDownload(blob, name) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = name;
    link.hidden = true;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
  }

  async function copyText(value, label) {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(value);
      } else {
        const field = document.createElement('textarea');
        field.value = value;
        field.setAttribute('readonly', '');
        field.style.position = 'fixed';
        field.style.opacity = '0';
        document.body.append(field);
        field.select();
        let copied = false;
        try { copied = document.execCommand('copy'); }
        finally { field.remove(); }
        if (!copied) throw new Error('Clipboard access is unavailable in this browser.');
      }
      notify(`${label} copied to clipboard.`);
    } catch (error) {
      notify(error.message || 'Could not copy to the clipboard.', true);
    }
  }

  function reportFilename(name) {
    const base = normalizePath(name || 'polyglotmaker').split('/').pop();
    const dot = base.lastIndexOf('.');
    const stem = dot > 0 ? base.slice(0, dot) : base;
    return outputFilename(`${stem || 'polyglotmaker'}-verification`, 'polyglotmaker-verification.json');
  }

  function downloadVerificationReport(files, metadata = {}) {
    const report = {
      tool: 'PolyglotMaker',
      format_version: 1,
      generated_utc: new Date().toISOString(),
      source: metadata.source || 'locally generated in the browser',
      zip_archive_sha256: metadata.bundleSha256 || null,
      host: metadata.host || null,
      files: files.map(({ name, size, sha256, mime, role }) => ({ name, size, sha256, mime: mime || 'application/octet-stream', role: role || 'payload' })),
    };
    const json = `${JSON.stringify(report, null, 2)}\n`;
    clickDownload(new Blob([json], { type: 'application/json' }), metadata.filename || reportFilename(metadata.name));
  }

  function downloadFile(file) {
    clickDownload(new Blob([file.data], { type: file.mime || 'application/octet-stream' }), normalizePath(file.name).split('/').pop() || 'payload.bin');
  }

  function bindDropZone(id, handler) {
    const zone = $(`#${id}`);
    zone.addEventListener('dragover', (event) => { event.preventDefault(); zone.classList.add('dragover'); });
    zone.addEventListener('dragleave', (event) => {
      if (!zone.contains(event.relatedTarget)) zone.classList.remove('dragover');
    });
    zone.addEventListener('drop', (event) => {
      event.preventDefault();
      zone.classList.remove('dragover');
      handler([...event.dataTransfer.files]);
    });
  }

  async function buildContainer() {
    if (state.createController || state.unpackController || state.zipController) return;
    if (!state.payloads.length && !(state.hostFile && $('#includeHost').checked)) {
      return notify('Add at least one payload file first.', true);
    }
    clearCreateResult();
    const payloads = [...state.payloads];
    const hostFile = state.hostFile;
    const includeHost = $('#includeHost').checked;
    const button = $('#createButton');
    const restore = setBusy(button, 'Building locally');
    const controller = new AbortController();
    state.createController = controller;
    updateOperationButtons();
    updateProgress('createProgress', { percent: 0, label: 'Starting local build…' });
    try {
      const result = await window.PolyglotEngine.createPolyglot({
        payloads,
        hostFile,
        includeHost,
        signal: controller.signal,
        onProgress: (progress) => updateProgress('createProgress', progress),
      });
      if (state.createUrl) URL.revokeObjectURL(state.createUrl);
      const blob = new Blob([result.bytes], { type: 'application/octet-stream' });
      state.createUrl = URL.createObjectURL(blob);
      const finalName = outputFilename($('#outputFilename').value, result.fileName);
      state.createResult = {
        files: result.files,
        metadata: { bundleSha256: result.bundleSha256, host: result.host, name: finalName, filename: reportFilename(finalName) },
      };
      const output = $('#createResult');
      output.hidden = false;
      output.innerHTML = `<div class="result-summary"><span class="success-check" aria-hidden="true">✓</span><span class="result-copy"><strong>Polyglot built in this browser</strong><small>${result.files.length} payload${result.files.length === 1 ? '' : 's'} · ${prettyBytes(result.totalBytes)} · ${escapeHtml(result.host.toUpperCase())} host</small></span></div><div class="bundle-digest"><span>ZIP integrity SHA-256</span><code>${escapeHtml(result.bundleSha256)}</code><button class="copy-digest" type="button" data-copy-value="${escapeHtml(result.bundleSha256)}">Copy</button></div><div class="result-actions"><a class="download-link" href="${escapeHtml(state.createUrl)}" download="${escapeHtml(finalName)}">Download ${escapeHtml(finalName)} <span aria-hidden="true">↓</span></a><button class="report-download" type="button" data-create-report>Save report</button></div>`;
      output.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'nearest' });
      notify('Container created. Your files stayed on this device.');
    } catch (error) {
      if (error.name === 'AbortError') notify('Build cancelled. No container was created.');
      else notify(error.message || 'Could not create this container.', true);
    } finally {
      state.createController = null;
      hideProgress('createProgress');
      restore();
      updateOperationButtons();
    }
  }

  function unpackFilename(name) {
    const base = String(name || 'polyglot').replace(/\\/g, '/').split('/').pop();
    const dot = base.lastIndexOf('.');
    const stem = dot > 0 ? base.slice(0, dot) : base;
    return `${stem || 'polyglot'}-unpacked.zip`;
  }

  function updateUnpackedSelection() {
    const target = $('#unpackResult');
    const total = state.unpackedFiles.length;
    const selected = state.selectedUnpacked.size;
    const selectAll = $('#selectAllFiles', target);
    const button = $('#downloadSelectedZip', target);
    const summary = $('#selectionSummary', target);
    if (!selectAll || !button || !summary) return;
    const busy = Boolean(state.createController || state.unpackController || state.zipController);
    selectAll.checked = total > 0 && selected === total;
    selectAll.indeterminate = selected > 0 && selected < total;
    selectAll.disabled = busy;
    $('#selectNoneFiles', target).disabled = busy;
    $('#invertFiles', target).disabled = busy;
    $('#unpackedSearch', target).disabled = busy;
    $('#unpackedSort', target).disabled = busy;
    $$('[data-file-select]', target).forEach((checkbox) => { checkbox.disabled = busy; });
    summary.textContent = `${selected} of ${total} selected`;
    button.disabled = selected === 0 || busy;
    button.textContent = selected === total
      ? `Download all as ZIP (${selected}) ↓`
      : `Download selected as ZIP (${selected}) ↓`;
  }

  function renderUnpackedRows() {
    const target = $('#unpackResult');
    const results = $('.file-results', target);
    const query = state.unpackedSearch.trim().toLocaleLowerCase();
    const matching = [];
    state.unpackedFiles.forEach((file, index) => {
      const searchable = `${file.name} ${file.mime || ''}`.toLocaleLowerCase();
      if (!query || searchable.includes(query)) matching.push({ file, index });
    });
    matching.sort((left, right) => compareFileRows(left, right, state.unpackedSort, (file) => file.name));
    const visible = matching.slice(0, MAX_VISIBLE_UNPACKED);
    if (!visible.length) {
      results.innerHTML = '<div class="empty-list">No restored files match that filter.</div>';
      return;
    }
    results.innerHTML = visible.map(({ file, index }) => `<div class="unpack-file-row" data-file-name="${escapeHtml(file.name.toLocaleLowerCase())}"><label class="file-select" title="Select ${escapeHtml(file.name)}"><input type="checkbox" data-file-select="${index}" ${state.selectedUnpacked.has(index) ? 'checked' : ''} aria-label="Include ${escapeHtml(file.name)} in ZIP download"><span aria-hidden="true"></span></label><span class="file-chip-icon">${escapeHtml(extension(file.name))}</span><span><strong title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</strong><small>${prettyBytes(file.size)} · SHA-256 ${escapeHtml(file.sha256.slice(0, 12))}…</small></span><div class="file-actions"><button class="copy-digest compact-copy" type="button" data-copy-hash="${index}" aria-label="Copy SHA-256 for ${escapeHtml(file.name)}">Hash</button><button class="save-one" type="button" data-save="${index}" aria-label="Save ${escapeHtml(file.name)}">Save ↓</button></div></div>`).join('');
    if (matching.length > visible.length) {
      results.insertAdjacentHTML('beforeend', `<div class="render-limit">Showing ${visible.length} of ${matching.length}. Filter to find more files.</div>`);
    }
  }

  function filterUnpackedFiles() {
    const target = $('#unpackResult');
    state.unpackedSearch = $('#unpackedSearch', target).value;
    renderUnpackedRows();
    updateUnpackedSelection();
  }

  function renderUnpacked(result) {
    const target = $('#unpackResult');
    state.unpackedFiles = result.files;
    state.selectedUnpacked = new Set(result.files.map((_, index) => index));
    state.unpackedSearch = '';
    state.unpackSource = result.source;
    state.unpackBundleSha256 = result.bundleSha256 || null;
    const total = result.files.reduce((sum, item) => sum + item.size, 0);
    target.hidden = false;
    target.innerHTML = `<div class="unpacked-result-head"><div class="unpack-success"><span class="success-check" aria-hidden="true">✓</span><span><strong>${result.files.length} file${result.files.length === 1 ? '' : 's'} verified</strong><small>${prettyBytes(total)} total · ${escapeHtml(result.source)}</small></span></div><div class="unpacked-result-actions"><button class="report-download" id="downloadReport" type="button">Save verification report</button><button class="download-all" id="downloadSelectedZip" type="button">Download all as ZIP (${result.files.length}) ↓</button></div></div>${result.bundleSha256 ? `<div class="bundle-digest"><span>ZIP integrity SHA-256</span><code>${escapeHtml(result.bundleSha256)}</code><button class="copy-digest" type="button" data-copy-value="${escapeHtml(result.bundleSha256)}">Copy</button></div>` : ''}
      <div class="unpacked-tools"><div class="selection-controls"><label class="select-all-control"><input id="selectAllFiles" type="checkbox" checked> Select all</label><button id="selectNoneFiles" type="button">None</button><button id="invertFiles" type="button">Invert</button></div><span class="selection-summary" id="selectionSummary">${result.files.length} of ${result.files.length} selected</span><input class="unpacked-search" id="unpackedSearch" type="search" placeholder="Filter names or MIME types…" aria-label="Filter restored files by name or type"><select id="unpackedSort" class="sort-select" aria-label="Sort restored files"><option value="name-asc">Name A–Z</option><option value="name-desc">Name Z–A</option><option value="size-asc">Smallest</option><option value="size-desc">Largest</option></select></div>
      <div class="file-results"></div>`;
    $('#unpackedSort', target).value = state.unpackedSort;
    renderUnpackedRows();
    updateUnpackedSelection();
  }

  async function downloadSelectedZip() {
    if (state.createController || state.unpackController || state.zipController) return;
    const files = [...state.selectedUnpacked]
      .sort((left, right) => left - right)
      .map((index) => state.unpackedFiles[index])
      .filter(Boolean);
    if (!files.length) return notify('Select at least one file to create a ZIP.', true);
    const controller = new AbortController();
    state.zipController = controller;
    updateOperationButtons();
    updateProgress('unpackProgress', { percent: 0, label: 'Preparing selected files…' });
    try {
      const zipBytes = await window.PolyglotEngine.downloadZip(files, {
        signal: controller.signal,
        onProgress: (progress) => updateProgress('unpackProgress', progress),
      });
      if (controller.signal.aborted) throw Object.assign(new Error('Operation cancelled.'), { name: 'AbortError' });
      clickDownload(new Blob([zipBytes], { type: 'application/zip' }), unpackFilename(state.containerFile?.name));
      notify(`${files.length} file${files.length === 1 ? '' : 's'} added to the ZIP download.`);
    } catch (error) {
      if (error.name === 'AbortError') notify('ZIP creation cancelled.');
      else notify(error.message || 'Could not create the ZIP download.', true);
    } finally {
      state.zipController = null;
      hideProgress('unpackProgress');
      updateOperationButtons();
    }
  }

  async function unpackContainer() {
    if (state.createController || state.unpackController || state.zipController) return;
    if (!state.containerFile) return notify('Choose a container file first.', true);
    const file = state.containerFile;
    const button = $('#unpackButton');
    const restore = setBusy(button, 'Checking bundle');
    clearUnpackedResult();
    const controller = new AbortController();
    state.unpackController = controller;
    updateOperationButtons();
    updateProgress('unpackProgress', { percent: 0, label: 'Reading container locally…' });
    try {
      const bytes = new Uint8Array(await file.arrayBuffer());
      const result = await window.PolyglotEngine.unpackPolyglot(bytes, {
        signal: controller.signal,
        onProgress: (progress) => updateProgress('unpackProgress', progress),
      });
      if (controller.signal.aborted) throw Object.assign(new Error('Operation cancelled.'), { name: 'AbortError' });
      renderUnpacked(result);
      notify('Payload checksums verified. Choose files, then build a ZIP only if you need one.');
    } catch (error) {
      if (error.name === 'AbortError') {
        notify('Restore cancelled.');
      } else {
        const target = $('#unpackResult');
        target.hidden = false;
        target.innerHTML = `<div class="result-empty">${escapeHtml(error.message || 'Could not unpack this file.')}</div>`;
        notify(error.message || 'Could not unpack this file.', true);
      }
    } finally {
      state.unpackController = null;
      hideProgress('unpackProgress');
      restore();
      updateOperationButtons();
    }
  }

  function cancelOperation(kind) {
    if (kind === 'create') state.createController?.abort();
    else if (kind === 'unpack') (state.unpackController || state.zipController)?.abort();
  }

  function init() {
    if (!window.PolyglotEngine) {
      notify('The local bundle engine did not load. Keep engine.js beside index.html.', true);
      $('#createButton').disabled = true;
      $('#unpackButton').disabled = true;
      return;
    }
    $$('.mode-tab').forEach((tab) => tab.addEventListener('click', () => switchTab(tab.dataset.tab)));
    bindDropZone('payloadDrop', addPayloads);
    bindDropZone('hostDrop', (files) => setHostFile(files[0] || null));
    bindDropZone('containerDrop', (files) => setContainerFile(files[0] || null));

    $('#payloadFiles').addEventListener('change', (event) => {
      addPayloads([...event.target.files]);
      event.target.value = '';
    });
    $('#folderFiles').addEventListener('change', (event) => {
      addPayloads([...event.target.files]);
      event.target.value = '';
    });
    $('#payloadSearch').addEventListener('input', (event) => {
      state.payloadSearch = event.target.value;
      drawPayloads();
    });
    $('#payloadSort').addEventListener('change', (event) => {
      state.payloadSort = event.target.value;
      drawPayloads();
    });
    $('#payloadList').addEventListener('click', (event) => {
      const button = event.target.closest('[data-remove]');
      if (button) removePayload(Number(button.dataset.remove));
    });
    $('#clearPayloads').addEventListener('click', clearPayloads);
    $('#hostFile').addEventListener('change', (event) => {
      setHostFile(event.target.files[0] || null);
      event.target.value = '';
    });
    $('#containerFile').addEventListener('change', (event) => {
      setContainerFile(event.target.files[0] || null);
      event.target.value = '';
    });
    $('#clearHost').addEventListener('click', () => setHostFile(null));
    $('#clearContainer').addEventListener('click', () => setContainerFile(null));
    $('#includeHost').addEventListener('change', (event) => {
      if (event.target.checked && state.hostFile && state.payloads.length + 1 > MAX_FILES) {
        event.target.checked = false;
        notify(`The browser edition supports up to ${MAX_FILES.toLocaleString()} files per bundle.`, true);
      } else if (event.target.checked && state.hostFile && totalSourceBytes(state.payloads, state.hostFile, true) > LIMIT) {
        event.target.checked = false;
        notify('Including a copy of this host would exceed the browser size limit.', true);
      }
      clearCreateResult();
      updatePayloadBudget();
      updateOperationButtons();
    });
    $('#createButton').addEventListener('click', buildContainer);
    $('#createResult').addEventListener('click', (event) => {
      const copyButton = event.target.closest('[data-copy-value]');
      if (copyButton) copyText(copyButton.dataset.copyValue, 'Bundle SHA-256');
      if (event.target.closest('[data-create-report]') && state.createResult) {
        downloadVerificationReport(state.createResult.files, state.createResult.metadata);
      }
    });
    $('#unpackButton').addEventListener('click', unpackContainer);
    $('#unpackResult').addEventListener('change', (event) => {
      if (event.target.id === 'selectAllFiles') {
        if (event.target.checked) state.selectedUnpacked = new Set(state.unpackedFiles.map((_, index) => index));
        else state.selectedUnpacked.clear();
        renderUnpackedRows();
        updateUnpackedSelection();
      } else if (event.target.id === 'unpackedSort') {
        state.unpackedSort = event.target.value;
        renderUnpackedRows();
        updateUnpackedSelection();
      } else if (event.target.matches('[data-file-select]')) {
        const index = Number(event.target.dataset.fileSelect);
        if (event.target.checked) state.selectedUnpacked.add(index);
        else state.selectedUnpacked.delete(index);
        updateUnpackedSelection();
      }
    });
    $('#unpackResult').addEventListener('input', (event) => {
      if (event.target.id === 'unpackedSearch') filterUnpackedFiles();
    });
    $('#unpackResult').addEventListener('click', (event) => {
      const saveButton = event.target.closest('[data-save]');
      const hashButton = event.target.closest('[data-copy-hash]');
      const bundleCopyButton = event.target.closest('[data-copy-value]');
      if (saveButton) downloadFile(state.unpackedFiles[Number(saveButton.dataset.save)]);
      if (bundleCopyButton) copyText(bundleCopyButton.dataset.copyValue, 'ZIP integrity SHA-256');
      if (hashButton) {
        const file = state.unpackedFiles[Number(hashButton.dataset.copyHash)];
        if (file) copyText(file.sha256, 'File SHA-256');
      }
      if (event.target.closest('#selectNoneFiles')) {
        state.selectedUnpacked.clear();
        renderUnpackedRows();
        updateUnpackedSelection();
      }
      if (event.target.closest('#invertFiles')) {
        state.selectedUnpacked = new Set(state.unpackedFiles.map((_, index) => index).filter((index) => !state.selectedUnpacked.has(index)));
        renderUnpackedRows();
        updateUnpackedSelection();
      }
      if (event.target.closest('#downloadReport')) {
        downloadVerificationReport(state.unpackedFiles, {
          source: state.unpackSource,
          bundleSha256: state.unpackBundleSha256,
          name: state.containerFile?.name,
          filename: reportFilename(state.containerFile?.name),
        });
      }
      if (event.target.closest('#downloadSelectedZip')) downloadSelectedZip();
    });
    $$('[data-cancel-operation]').forEach((button) => button.addEventListener('click', () => cancelOperation(button.dataset.cancelOperation)));
    updatePayloadBudget();
    updateOperationButtons();

    if (!window.crypto?.subtle) {
      notify('This browser lacks Web Crypto. Try opening this folder in a current browser.', true);
    }
  }

  document.addEventListener('DOMContentLoaded', init);
})();
