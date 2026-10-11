(() => {
  'use strict';

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const state = { payloads: [], hostFile: null, containerFile: null, toastTimer: 0, createUrl: null, zipUrl: null };
  const LIMIT = window.PolyglotEngine?.MAX_BYTES || 128 * 1024 * 1024;

  const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[character]);

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
    $('#createButton').disabled = !state.payloads.length && !(state.hostFile && $('#includeHost').checked);
  }

  function drawPayloads() {
    const list = $('#payloadList');
    $('#payloadCount').textContent = `${state.payloads.length} FILE${state.payloads.length === 1 ? '' : 'S'}`;
    if (!state.payloads.length) {
      list.innerHTML = '<div class="empty-list">Your payloads will appear here.</div>';
      updateCreateButton();
      return;
    }
    list.innerHTML = state.payloads.map((file, index) => `<div class="file-row">
      <span class="file-chip-icon">${escapeHtml(extension(file.name))}</span>
      <span class="file-meta"><strong title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</strong><small>${prettyBytes(file.size)} · ${escapeHtml(file.type || 'unknown file type')}</small></span>
      <button class="remove-file" type="button" data-remove="${index}" aria-label="Remove ${escapeHtml(file.name)}">×</button>
    </div>`).join('');
    $$('[data-remove]', list).forEach((button) => button.addEventListener('click', () => {
      state.payloads.splice(Number(button.dataset.remove), 1);
      drawPayloads();
      $('#createResult').hidden = true;
    }));
    updateCreateButton();
  }

  function addPayloads(fileList) {
    const incoming = [...fileList];
    if (!incoming.length) return;
    const nextTotal = totalSourceBytes() + incoming.reduce((sum, file) => sum + file.size, 0);
    if (nextTotal > LIMIT) return notify(`Selected data exceeds the ${Math.round(LIMIT / 1024 / 1024)} MiB browser limit.`, true);
    for (const file of incoming) {
      const duplicate = state.payloads.some((old) => old.name === file.name && old.size === file.size && old.lastModified === file.lastModified);
      if (!duplicate) state.payloads.push(file);
    }
    $('#createResult').hidden = true;
    drawPayloads();
  }

  function setHostFile(file) {
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
    $('#createResult').hidden = true;
    updateCreateButton();
  }

  function setContainerFile(file) {
    if (file && file.size > LIMIT) return notify(`This file exceeds the ${Math.round(LIMIT / 1024 / 1024)} MiB browser limit.`, true);
    state.containerFile = file || null;
    $('#containerSelected').hidden = !file;
    $('#unpackButton').disabled = !file;
    $('#unpackResult').hidden = true;
    $('#containerPrompt').textContent = file ? 'Container selected' : 'Drop a PolyglotMaker file here';
    if (file) {
      $('#containerExt').textContent = extension(file.name);
      $('#containerName').textContent = file.name;
      $('#containerSize').textContent = `${prettyBytes(file.size)} · ready to scan locally`;
    }
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

  function downloadFile(file) {
    clickDownload(new Blob([file.data], { type: file.mime || 'application/octet-stream' }), file.name.split('/').pop() || 'payload.bin');
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
    if (!state.payloads.length && !(state.hostFile && $('#includeHost').checked)) {
      return notify('Add at least one payload file first.', true);
    }
    const button = $('#createButton');
    const restore = setBusy(button, 'Building locally');
    try {
      const result = await window.PolyglotEngine.createPolyglot({
        payloads: state.payloads,
        hostFile: state.hostFile,
        includeHost: $('#includeHost').checked,
      });
      if (state.createUrl) URL.revokeObjectURL(state.createUrl);
      const blob = new Blob([result.bytes], { type: 'application/octet-stream' });
      state.createUrl = URL.createObjectURL(blob);
      const output = $('#createResult');
      output.hidden = false;
      output.innerHTML = `<span class="success-check" aria-hidden="true">✓</span><span class="result-copy"><strong>Polyglot built in this browser</strong><small>${result.files.length} payload${result.files.length === 1 ? '' : 's'} · ${prettyBytes(result.totalBytes)} · ${escapeHtml(result.host.toUpperCase())} host</small></span><a class="download-link" href="${escapeHtml(state.createUrl)}" download="${escapeHtml(result.fileName)}">Download ${escapeHtml(result.fileName)} <span aria-hidden="true">↓</span></a>`;
      output.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'nearest' });
      notify('Container created. Your files stayed on this device.');
    } catch (error) {
      notify(error.message || 'Could not create this container.', true);
    } finally {
      restore();
      updateCreateButton();
    }
  }

  function unpackFilename(name) {
    const base = String(name || 'polyglot').replace(/\\/g, '/').split('/').pop();
    const dot = base.lastIndexOf('.');
    const stem = dot > 0 ? base.slice(0, dot) : base;
    return `${stem || 'polyglot'}-unpacked.zip`;
  }

  function renderUnpacked(result, zipUrl, zipName) {
    const target = $('#unpackResult');
    const total = result.files.reduce((sum, item) => sum + item.size, 0);
    target.hidden = false;
    target.innerHTML = `<div class="unpacked-result-head"><div class="unpack-success"><span class="success-check" aria-hidden="true">✓</span><span><strong>${result.files.length} file${result.files.length === 1 ? '' : 's'} verified</strong><small>${prettyBytes(total)} total · ${escapeHtml(result.source)}</small></span></div><a class="download-all" href="${escapeHtml(zipUrl)}" download="${escapeHtml(zipName)}">Download all as ZIP <span aria-hidden="true">↓</span></a></div>
      <div class="file-results">${result.files.map((file, index) => `<div class="unpack-file-row"><span class="file-chip-icon">${escapeHtml(extension(file.name))}</span><span><strong title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</strong><small>${prettyBytes(file.size)} · SHA-256 ${escapeHtml(file.sha256.slice(0, 12))}…</small></span><button class="save-one" type="button" data-save="${index}" aria-label="Save ${escapeHtml(file.name)}">Save ↓</button></div>`).join('')}</div>`;
    $$('[data-save]', target).forEach((button) => button.addEventListener('click', () => downloadFile(result.files[Number(button.dataset.save)])));
  }

  async function unpackContainer() {
    if (!state.containerFile) return notify('Choose a container file first.', true);
    const button = $('#unpackButton');
    const restore = setBusy(button, 'Checking bundle');
    try {
      const bytes = new Uint8Array(await state.containerFile.arrayBuffer());
      const result = await window.PolyglotEngine.unpackPolyglot(bytes);
      const zipBytes = window.PolyglotEngine.downloadZip(result.files);
      if (state.zipUrl) URL.revokeObjectURL(state.zipUrl);
      state.zipUrl = URL.createObjectURL(new Blob([zipBytes], { type: 'application/zip' }));
      renderUnpacked(result, state.zipUrl, unpackFilename(state.containerFile.name));
      notify('Payload checksums verified.');
    } catch (error) {
      const target = $('#unpackResult');
      target.hidden = false;
      target.innerHTML = `<div class="result-empty">${escapeHtml(error.message || 'Could not unpack this file.')}</div>`;
      notify(error.message || 'Could not unpack this file.', true);
    } finally {
      restore();
      button.disabled = !state.containerFile;
    }
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
      if (event.target.checked && state.hostFile && totalSourceBytes(state.payloads, state.hostFile, true) > LIMIT) {
        event.target.checked = false;
        notify('Including a copy of this host would exceed the browser size limit.', true);
      }
      updateCreateButton();
    });
    $('#createButton').addEventListener('click', buildContainer);
    $('#unpackButton').addEventListener('click', unpackContainer);
    updateCreateButton();

    if (!window.crypto?.subtle) {
      notify('This browser lacks Web Crypto. Try opening this folder in a current browser.', true);
    }
  }

  document.addEventListener('DOMContentLoaded', init);
})();
