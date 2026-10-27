// ImportKit UI. CSV content comes from third parties, so every value is rendered
// through textContent / DOM nodes; no HTML string is ever built from data.
'use strict';

(() => {
  const MAX_UPLOAD_BYTES = 800_000;
  const SAMPLE_CSV = [
    'name,email,company',
    'Avery Stone,avery@example.com,Northstar Labs',
    'Jordan Lee,jordan@example.com,Fieldnote',
    'Sam Rivera,not-an-email,Studio North',
    'Taylor Morgan,avery@example.com,Duplicate row',
  ].join('\n');

  const $ = (selector, root = document) => root.querySelector(selector);

  /** Create an element; string/number children become text nodes. */
  function h(tag, props = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
      if (value === undefined || value === null || value === false) continue;
      if (key === 'class') node.className = value;
      else if (key === 'dataset') Object.assign(node.dataset, value);
      else if (key === 'onclick') node.addEventListener('click', value);
      else if (key in node && typeof value !== 'string') node[key] = value;
      else node.setAttribute(key, value === true ? '' : String(value));
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  class ApiError extends Error {
    constructor(message, status) {
      super(message);
      this.status = status;
    }
  }

  async function api(url, { method = 'GET', json, form } = {}) {
    const init = { method, headers: { Accept: 'application/json' } };
    if (json !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(json);
    } else if (form) {
      init.body = form;
    }
    let response;
    try {
      response = await fetch(url, init);
    } catch {
      throw new ApiError('Network error. Check your connection and try again.', 0);
    }
    let data = null;
    if ((response.headers.get('content-type') || '').includes('application/json')) {
      try { data = await response.json(); } catch { data = null; }
    }
    if (!response.ok) {
      throw new ApiError((data && data.error) || `Request failed (${response.status}).`, response.status);
    }
    return data;
  }

  // ---------- toast ----------
  let toastTimer;
  function toast(message) {
    const node = $('#toast');
    node.textContent = message;
    node.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => node.classList.remove('show'), 3200);
  }

  /** Run an async action once at a time per button (prevents double submits). */
  async function busy(button, label, action) {
    if (!button || button.dataset.busy === '1') return undefined;
    const original = button.textContent;
    button.dataset.busy = '1';
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    button.classList.add('is-busy');
    if (label) button.textContent = label;
    try {
      return await action();
    } finally {
      button.dataset.busy = '';
      button.removeAttribute('aria-busy');
      button.classList.remove('is-busy');
      if (button.isConnected) {
        button.textContent = original;
        button.disabled = false;
      }
    }
  }

  function formatDate(value) {
    if (!value) return '';
    const iso = value.includes('T') ? value : `${value.replace(' ', 'T')}Z`;
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return '';
    return date.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  }

  const plural = (n, one, many) => `${n.toLocaleString('en-US')} ${n === 1 ? one : many}`;

  // ---------- state ----------
  let dashboard = null;
  let current = null;

  function setBusy(node, value) {
    if (value) node.setAttribute('aria-busy', 'true');
    else node.removeAttribute('aria-busy');
  }

  function errorState(tag, message, retry) {
    return h(tag, { class: 'state error' },
      message, tag === 'td' ? null : h('br'),
      retry ? h('button', { type: 'button', class: 'secondary', onclick: retry }, 'Try again') : null);
  }

  function renderStats() {
    $('#contact-count').textContent = dashboard.contacts.toLocaleString('en-US');
    $('#import-count').textContent = dashboard.imports.toLocaleString('en-US');
    $('#pending-count').textContent = dashboard.pending.toLocaleString('en-US');
    $('#rejected-count').textContent = dashboard.rejected.toLocaleString('en-US');
    $('#nav-count').textContent = dashboard.contacts.toLocaleString('en-US');
    $('#contact-total').textContent = plural(dashboard.contacts, 'record', 'records');
    setBusy($('#stats'), false);
  }

  function renderContacts(contacts) {
    const body = $('#contact-list');
    body.replaceChildren(
      ...(contacts.length
        ? contacts.map((c) => h('tr', {}, h('td', {}, c.name), h('td', {}, c.email),
          h('td', {}, c.company || h('span', { class: 'cell-empty', 'aria-label': 'none' }, '—'))))
        : [h('tr', {}, h('td', { colspan: 3, class: 'state' }, 'No contacts imported yet.'))]),
    );
    setBusy(body, false);
  }

  function renderHistory() {
    const list = $('#history-list');
    if (!dashboard.latest.length) {
      list.replaceChildren(h('li', { class: 'state' }, 'No imports yet.'));
    } else {
      list.replaceChildren(...dashboard.latest.map((item) => h('li', {},
        h('button', {
          type: 'button',
          class: 'history-row',
          dataset: { import: String(item.id) },
          'aria-current': current && current.id === item.id ? 'true' : null,
        },
        h('span', { class: 'history-icon', 'aria-hidden': 'true' }, '↥'),
        h('span', { class: 'history-main' },
          h('span', { class: 'history-name' }, item.filename),
          h('span', { class: 'history-date' },
            `${formatDate(item.createdAt)} · ${item.validRows} ready · ${item.invalidRows} issues`)),
        h('span', { class: `history-status ${item.status}` }, item.status)))));
    }
    setBusy(list, false);
  }

  async function load() {
    try {
      const [dash, contacts] = await Promise.all([api('/api/dashboard'), api('/api/contacts')]);
      dashboard = dash;
      renderStats();
      renderContacts(contacts);
      renderHistory();
      return true;
    } catch (error) {
      const retry = () => start();
      $('#history-list').replaceChildren(errorState('li', error.message, retry));
      $('#contact-list').replaceChildren(h('tr', {}, errorState('td', error.message)));
      setBusy($('#history-list'), false);
      setBusy($('#contact-list'), false);
      setBusy($('#stats'), false);
      return false;
    }
  }

  // ---------- preview panel ----------
  function setPreviewHead(title, subtitle) {
    $('#preview-title').textContent = title;
    $('#preview-subtitle').textContent = subtitle;
  }

  function renderEmpty() {
    current = null;
    setPreviewHead('Start with a safe preview', 'Your upload is checked before any record is saved.');
    $('#preview-actions').replaceChildren();
    const body = $('#preview-body');
    body.replaceChildren(h('div', { class: 'empty-preview' },
      h('div', { class: 'empty-icon', 'aria-hidden': 'true' }, '▤'),
      h('strong', {}, 'Nothing has been imported yet'),
      h('p', {}, 'Try a sample file or upload your own CSV to see validation in action.'),
      h('div', { class: 'empty-actions' },
        h('button', { type: 'button', class: 'primary', onclick: () => openImport() }, 'Upload a CSV'),
        h('button', { type: 'button', class: 'secondary', id: 'try-sample', onclick: (e) => trySample(e.currentTarget) }, 'Try sample data'))));
    setBusy(body, false);
  }

  function renderPreviewError(message, retry) {
    $('#preview-actions').replaceChildren();
    $('#preview-body').replaceChildren(errorState('p', message, retry));
    setBusy($('#preview-body'), false);
  }

  function cell(value) {
    return h('td', {}, value || h('span', { class: 'cell-empty', 'aria-label': 'empty' }, '—'));
  }

  function renderImport(item) {
    current = item;
    setPreviewHead(item.filename,
      `${plural(item.totalRows, 'row', 'rows')} · ${item.validRows} valid · ${item.invalidRows} need attention · ${item.status}`);

    const actions = [];
    if (item.status === 'preview') {
      actions.push(h('button', {
        type: 'button', class: 'primary', id: 'commit', disabled: item.validRows === 0,
        onclick: (e) => commit(item, e.currentTarget),
      }, `Commit ${plural(item.validRows, 'valid row', 'valid rows')} →`));
    } else if (item.status === 'committed') {
      actions.push(h('button', {
        type: 'button', class: 'danger', id: 'revert', onclick: (e) => revert(item, e.currentTarget),
      }, 'Undo this import'));
    }
    if (item.invalidRows) {
      actions.push(h('a', {
        class: 'error-export', href: `/api/imports/${encodeURIComponent(item.id)}/errors.csv`, download: '',
      }, 'Download error report ↓'));
    }
    $('#preview-actions').replaceChildren(...actions);

    const rows = item.rows || [];
    const body = $('#preview-body');
    if (!rows.length) {
      body.replaceChildren(h('p', { class: 'state' }, 'No row details available.'));
    } else {
      const head = h('thead', {}, h('tr', {}, ...['Row', 'Status', 'Name', 'Email', 'Company', 'Validation']
        .map((label) => h('th', { scope: 'col' }, label))));
      const tbody = h('tbody', {}, ...rows.map((row) => h('tr', { class: row.status === 'invalid' ? 'invalid' : 'valid' },
        h('td', {}, row.rowNumber),
        h('td', {}, h('span', { class: `row-status ${row.status === 'invalid' ? 'invalid' : 'valid'}` }, row.status)),
        cell(row.data.name), cell(row.data.email), cell(row.data.company),
        h('td', {}, row.errors.length
          ? h('ul', { class: 'row-errors' }, ...row.errors.map((e) => h('li', {}, `${e.field}: ${e.message}`)))
          : h('span', { class: 'cell-empty', 'aria-label': 'no issues' }, '—')))));
      const caption = h('caption', { class: 'visually-hidden' }, `Validation results for ${item.filename}`);
      body.replaceChildren(h('div', { class: 'data-wrap', tabindex: '0', role: 'region', 'aria-label': 'Row validation results' },
        h('table', { class: 'data-table' }, caption, head, tbody)));
    }
    setBusy(body, false);
    if (dashboard) renderHistory();
  }

  async function showImport(id) {
    setBusy($('#preview-body'), true);
    try {
      renderImport(await api(`/api/imports/${encodeURIComponent(id)}`));
    } catch (error) {
      renderPreviewError(error.message, () => showImport(id));
    }
  }

  async function commit(item, button) {
    await busy(button, 'Committing…', async () => {
      try {
        const result = await api(`/api/imports/${encodeURIComponent(item.id)}/commit`, { method: 'POST', json: {} });
        await Promise.all([showImport(item.id), load()]);
        toast(result.replayed
          ? 'This import was already committed; nothing was duplicated.'
          : `${plural(result.created, 'contact', 'contacts')} committed.`);
      } catch (error) {
        toast(error.message);
      }
    });
  }

  function confirmRevert() {
    const dialog = $('#confirm-dialog');
    return new Promise((resolve) => {
      dialog.returnValue = 'cancel';
      dialog.addEventListener('close', () => resolve(dialog.returnValue === 'confirm'), { once: true });
      dialog.showModal();
    });
  }

  async function revert(item, button) {
    if (!(await confirmRevert())) return;
    await busy(button, 'Undoing…', async () => {
      try {
        const result = await api(`/api/imports/${encodeURIComponent(item.id)}/revert`, { method: 'POST', json: {} });
        await Promise.all([showImport(item.id), load()]);
        toast(result.replayed ? 'This import was already undone.'
          : `Import reverted; ${plural(result.removed, 'contact was', 'contacts were')} removed.`);
      } catch (error) {
        toast(error.message);
      }
    });
  }

  async function trySample(button) {
    await busy(button, 'Validating…', async () => {
      try {
        const result = await api('/api/imports/preview', {
          method: 'POST', json: { filename: 'sample-contacts.csv', csv: SAMPLE_CSV },
        });
        await load();
        renderImport(result);
        toast(`${result.validRows} rows are ready; ${result.invalidRows} need attention.`);
      } catch (error) {
        toast(error.message);
      }
    });
  }

  // ---------- import dialog ----------
  const dialog = $('#import-dialog');
  const form = $('#import-form');
  const fileInput = $('#csv-file');
  const textArea = $('#csv-text');
  const mappingSet = $('#mapping');
  const detectStatus = $('#detect-status');
  const submitButton = $('#submit-import');
  const formError = $('#form-error');
  const fields = { name: form.elements.nameColumn, email: form.elements.emailColumn, company: form.elements.companyColumn };
  let selectedFile = null;
  let inspectSeq = 0;
  let inspectTimer;

  function setDetect(message, isError = false) {
    detectStatus.textContent = message;
    detectStatus.classList.toggle('error', isError);
  }

  function resetMapping(message) {
    for (const select of Object.values(fields)) select.replaceChildren();
    mappingSet.disabled = true;
    submitButton.disabled = true;
    setDetect(message);
  }

  function fillSelect(select, headers, chosen, placeholder) {
    select.replaceChildren(h('option', { value: '' }, placeholder),
      ...headers.filter((col) => col.key).map((col) => h('option', { value: col.key, selected: col.key === chosen }, col.label)));
    select.value = chosen || '';
  }

  function delimiterName(value) {
    return { ',': 'comma', ';': 'semicolon', '\t': 'tab', '|': 'pipe' }[value] || value;
  }

  async function inspect() {
    const seq = ++inspectSeq;
    formError.textContent = '';
    const text = textArea.value;
    if (!selectedFile && !text.trim()) {
      resetMapping('Add a file or paste rows to detect columns.');
      return;
    }
    setDetect('Detecting columns…');
    try {
      let result;
      if (selectedFile) {
        const data = new FormData();
        data.append('file', selectedFile);
        result = await api('/api/imports/inspect', { method: 'POST', form: data });
      } else {
        result = await api('/api/imports/inspect', { method: 'POST', json: { csv: text } });
      }
      if (seq !== inspectSeq) return;
      const suggestion = result.suggestedMapping || {};
      fillSelect(fields.name, result.headers, suggestion.name, 'Select a column');
      fillSelect(fields.email, result.headers, suggestion.email, 'Select a column');
      fillSelect(fields.company, result.headers, suggestion.company, '— Not mapped —');
      mappingSet.disabled = false;
      submitButton.disabled = false;
      setDetect(`Detected ${plural(result.totalRows, 'row', 'rows')} and ${plural(result.headers.length, 'column', 'columns')} (${delimiterName(result.delimiter)} separated).`);
    } catch (error) {
      if (seq !== inspectSeq) return;
      resetMapping('');
      setDetect(error.message, true);
    }
  }

  function chooseFile(file) {
    formError.textContent = '';
    if (!file) return;
    if (file.size > MAX_UPLOAD_BYTES) {
      selectedFile = null;
      fileInput.value = '';
      $('#file-title').textContent = 'Choose a CSV file';
      resetMapping('');
      setDetect(`“${file.name}” is larger than 800 KB. Split it into smaller files.`, true);
      return;
    }
    selectedFile = file;
    textArea.value = '';
    $('#file-title').textContent = `${file.name} · ${Math.max(1, Math.round(file.size / 1000))} KB`;
    form.elements.filename.value = file.name.slice(0, 100);
    inspect();
  }

  function openImport() {
    form.reset();
    selectedFile = null;
    inspectSeq += 1;
    formError.textContent = '';
    $('#file-title').textContent = 'Choose a CSV file';
    resetMapping('Add a file or paste rows to detect columns.');
    if (!dialog.open) dialog.showModal();
  }

  fileInput.addEventListener('change', () => chooseFile(fileInput.files[0]));
  textArea.addEventListener('input', () => {
    if (selectedFile) {
      selectedFile = null;
      fileInput.value = '';
      $('#file-title').textContent = 'Choose a CSV file';
    }
    clearTimeout(inspectTimer);
    inspectTimer = setTimeout(inspect, 350);
  });
  $('#use-sample').addEventListener('click', () => {
    selectedFile = null;
    fileInput.value = '';
    $('#file-title').textContent = 'Choose a CSV file';
    textArea.value = SAMPLE_CSV;
    form.elements.filename.value = 'sample-contacts.csv';
    inspect();
  });

  const dropzone = $('.dropzone', form);
  dropzone.addEventListener('dragover', (event) => { event.preventDefault(); dropzone.classList.add('dragging'); });
  dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragging'));
  dropzone.addEventListener('drop', (event) => {
    event.preventDefault();
    dropzone.classList.remove('dragging');
    chooseFile(event.dataTransfer && event.dataTransfer.files[0]);
  });

  dialog.addEventListener('click', (event) => {
    if (event.target === dialog || event.target.closest('[data-close]')) dialog.close();
  });

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    formError.textContent = '';
    const mapping = { name: fields.name.value, email: fields.email.value, company: fields.company.value };
    if (!mapping.name || !mapping.email) {
      formError.textContent = 'Choose the name and email columns.';
      (mapping.name ? fields.email : fields.name).focus();
      return;
    }
    if (mapping.name === mapping.email) {
      formError.textContent = 'Name and email must come from different columns.';
      fields.email.focus();
      return;
    }
    const filename = form.elements.filename.value.trim() || 'contacts.csv';
    await busy(submitButton, 'Validating…', async () => {
      try {
        let result;
        if (selectedFile) {
          const data = new FormData();
          data.append('file', selectedFile);
          data.append('filename', filename);
          data.append('nameColumn', mapping.name);
          data.append('emailColumn', mapping.email);
          data.append('companyColumn', mapping.company);
          result = await api('/api/imports/preview', { method: 'POST', form: data });
        } else {
          result = await api('/api/imports/preview', {
            method: 'POST', json: { filename, csv: textArea.value, mapping },
          });
        }
        dialog.close();
        await load();
        renderImport(result);
        toast(`${result.validRows} rows are ready; ${result.invalidRows} need attention.`);
      } catch (error) {
        formError.textContent = error.message;
      }
    });
  });

  // ---------- page wiring ----------
  $('#new-import').addEventListener('click', () => openImport());
  $('#history-list').addEventListener('click', (event) => {
    const row = event.target.closest('[data-import]');
    if (row) showImport(Number(row.dataset.import));
  });
  for (const link of document.querySelectorAll('.nav')) {
    link.addEventListener('click', () => {
      for (const other of document.querySelectorAll('.nav')) {
        other.classList.toggle('active', other === link);
        if (other === link) other.setAttribute('aria-current', 'page');
        else other.removeAttribute('aria-current');
      }
    });
  }

  // ---------- theme ----------
  const themeButton = $('#theme-toggle');
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)');
  const effectiveTheme = () => document.documentElement.dataset.theme || (prefersDark.matches ? 'dark' : 'light');
  function syncThemeButton() {
    const dark = effectiveTheme() === 'dark';
    themeButton.setAttribute('aria-pressed', String(dark));
    themeButton.title = dark ? 'Switch to light mode' : 'Switch to dark mode';
  }
  themeButton.addEventListener('click', () => {
    const next = effectiveTheme() === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem('importkit-theme', next); } catch { /* private mode */ }
    syncThemeButton();
  });
  prefersDark.addEventListener('change', syncThemeButton);
  syncThemeButton();

  async function start() {
    setBusy($('#stats'), true);
    if (!(await load())) {
      renderPreviewError('The workspace could not be loaded.', () => start());
      return;
    }
    if (current) await showImport(current.id);
    else if (dashboard.latest.length) await showImport(dashboard.latest[0].id);
    else renderEmpty();
  }

  start();
})();
