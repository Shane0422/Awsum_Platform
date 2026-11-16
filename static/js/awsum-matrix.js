/* AWSUM Common Matrix (window.AwsumMatrix): a row x column grid of money cells with inherited / override values,
 * e.g. MD Details x Stores (Store prices). It only renders and collects changes: loading data, saving through an API
 * and permissions stay with the caller (the server decides; read-only columns only follow it).
 *
 *   const matrix = AwsumMatrix.create(container, {
 *     id: 'storePrices',                                            // id prefix (unique per page)
 *     fixedColumns: [{ key: 'detail', label: 'Detail', render: (row) => '<b>#1</b>' },   // trusted markup
 *                    { key: 'merchant', label: 'Merchant Price', render: (row) => '$5.00' }],
 *     rows: [{ id: 11, ... }],
 *     columns: [{ id: 21, label: 'B Store', sub: 'STR26B', active: true, readOnly: false,
 *                 header: { key: 'pos_visible', label: 'POS Visible', value: true, version: '0' } }],
 *     cell: (row, column) => ({ inherited: '5.00', override: null | '4.00', version: '0' }),
 *     labels: { inherited: 'Inherited', override: 'Store', revert: 'Use the inherited price' },
 *     filters: { search: true, status: true, overridesOnly: true },
 *     money: { min: '0', max: '9999999999.99', keypad: false },
 *     onChange: ({ dirty, count }) => {},
 *   });
 *   matrix.changes()   // { cells: [{ row, column, value: '4.00' | null, version }], headers: [{ column, key, value, version }] }
 *   matrix.isDirty(); matrix.isValid(); matrix.markConflicts(list); matrix.setData({ rows, columns, cell });
 *   matrix.confirmDiscard(); matrix.destroy();
 *
 * Cell rules: an empty input = inherited (the inherited value is always shown as the placeholder with an
 * "Inherited" label). Any value typed into an inherited cell is an explicit override, even when it equals the inherited
 * value; $0.00 is a valid override. Revert (or clearing the input) returns the cell to inherited: value null.
 * A cell is dirty only when it differs from what was loaded (an override edited back to its loaded value is clean).
 * Cell inputs have no name attribute, so a surrounding form's Save never collects them.
 * Money entry, formatting and validation come from AwsumMoneyInput (cents-based); the optional "$" button opens the
 * Common Money Keypad. Text values are escaped; fixedColumns render() markup is trusted (built by the caller).
 * Styles: /static/css/awsum-matrix.css.
 */
(function (root, factory) {
  'use strict';
  const api = factory(root);
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.AwsumMatrix = api;
})(typeof globalThis === 'object' ? globalThis : this, function (root) {
  'use strict';

  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const LABELS = {
    inherited: 'Inherited', override: 'Override', changed: 'Changed', revert: 'Use the inherited value',
    readOnly: 'View only', search: 'Search', searchPlaceholder: 'Search name or code', status: 'Status',
    all: 'All', active: 'Active', inactive: 'Inactive', overridesOnly: 'Overrides only',
    empty: 'No columns match the filter.', conflict: 'Changed by another user. The latest value is shown.',
    discardTitle: 'Discard changes?', discardMessage: 'Your unsaved changes in this table will be lost.', discardOk: 'Discard',
  };

  function money() {
    const m = root.AwsumMoneyInput;
    if (!m) throw new Error('AwsumMatrix requires AwsumMoneyInput (static/js/money.js).');
    return m;
  }
  const norm = (value) => (value == null || value === '' ? null : money().normalize(String(value)));

  function create(container, options) {
    if (!container) throw new Error('AwsumMatrix: container is required.');
    const cfg = { filters: { search: true, status: true, overridesOnly: true }, money: { min: '0' }, ...options };
    const labels = { ...LABELS, ...(cfg.labels || {}) };
    const prefix = cfg.id || 'awsumMatrix';
    let rows = [];
    let columns = [];
    let cells = new Map(); // `${row}|${column}` -> { row, column, inherited, original, version, current, conflict }
    let heads = new Map(); // column id -> { key, label, original, value, version, conflict }
    const filter = { q: '', status: 'all', overridesOnly: false };
    const key = (r, c) => `${r}|${c}`;

    container.classList.add('awsum-matrix');
    container.innerHTML = toolbarHtml() + `<div class="awsum-matrix-scroll" tabindex="0"><table class="table table-sm align-middle mb-0 awsum-matrix-table"></table></div>
      <div class="awsum-matrix-empty text-muted small d-none" data-matrix-empty>${esc(labels.empty)}</div>`;
    const table = container.querySelector('.awsum-matrix-table');
    container.addEventListener('input', onInput);
    container.addEventListener('change', onChange);
    container.addEventListener('blur', onBlur, true);
    container.addEventListener('click', onClick);

    function toolbarHtml() {
      const f = cfg.filters || {};
      if (!f.search && !f.status && !f.overridesOnly) return '';
      return `<div class="awsum-matrix-toolbar">
        ${f.search ? `<input type="search" class="form-control form-control-sm awsum-matrix-search" data-matrix-filter="q" placeholder="${esc(labels.searchPlaceholder)}" aria-label="${esc(labels.search)}">` : ''}
        ${f.status ? `<select class="form-select form-select-sm awsum-matrix-status" data-matrix-filter="status" aria-label="${esc(labels.status)}">
          <option value="all">${esc(labels.all)}</option><option value="active">${esc(labels.active)}</option><option value="inactive">${esc(labels.inactive)}</option></select>` : ''}
        ${f.overridesOnly ? `<label class="form-check form-check-inline mb-0 awsum-matrix-overrides"><input class="form-check-input" type="checkbox" data-matrix-filter="overridesOnly"> ${esc(labels.overridesOnly)}</label>` : ''}
      </div>`;
    }

    function setData(data) {
      rows = (data.rows || cfg.rows || []).slice();
      columns = (data.columns || cfg.columns || []).slice();
      const cellOf = data.cell || cfg.cell;
      cells = new Map();
      heads = new Map();
      rows.forEach((row) => columns.forEach((col) => {
        const c = cellOf(row, col) || {};
        const original = norm(c.override);
        cells.set(key(row.id, col.id), { row: row.id, column: col.id, inherited: norm(c.inherited), original,
          version: c.version ?? '0', current: original, conflict: '' });
      }));
      columns.forEach((col) => {
        if (col.header) heads.set(col.id, { key: col.header.key, label: col.header.label, original: !!col.header.value,
          value: !!col.header.value, version: col.header.version ?? '0', conflict: '' });
      });
      render();
    }

    function render() {
      const fixed = cfg.fixedColumns || [];
      const head = `<thead><tr>${fixed.map((f, i) => `<th class="awsum-matrix-fixed" data-fixed="${i}" scope="col">${esc(f.label)}</th>`).join('')}
        ${columns.map((col) => `<th class="awsum-matrix-col${col.readOnly ? ' is-read-only' : ''}" data-col="${esc(col.id)}" scope="col">${headHtml(col)}</th>`).join('')}</tr></thead>`;
      const body = `<tbody>${rows.map((row) => `<tr data-row="${esc(row.id)}">
        ${fixed.map((f, i) => `<th class="awsum-matrix-fixed" data-fixed="${i}" scope="row">${f.render ? f.render(row) : esc(row[f.key])}</th>`).join('')}
        ${columns.map((col) => `<td class="awsum-matrix-cell" data-col="${esc(col.id)}" data-cell="${esc(key(row.id, col.id))}">${cellHtml(row, col)}</td>`).join('')}
      </tr>`).join('')}</tbody>`;
      table.innerHTML = head + body;
      table.querySelectorAll('input[data-matrix-money]').forEach((input) => money().bind(input));
      rows.forEach((row) => columns.forEach((col) => paint(key(row.id, col.id))));
      columns.forEach((col) => paintHead(col.id));
      applyFilter();
      layoutFixed();
      notify();
    }

    function headHtml(col) {
      const h = heads.get(col.id);
      const status = col.active === false ? `<span class="awsum-matrix-badge is-inactive">${esc(labels.inactive)}</span>` : '';
      const ro = col.readOnly ? `<span class="awsum-matrix-badge is-read-only" title="${esc(col.readOnlyText || labels.readOnly)}">${esc(labels.readOnly)}</span>` : '';
      const control = h ? `<label class="form-check form-switch mb-0 awsum-matrix-head-control">
          <input class="form-check-input" type="checkbox" role="switch" data-matrix-head="${esc(col.id)}"${h.value ? ' checked' : ''}${col.readOnly ? ' disabled' : ''}>
          <span class="form-check-label">${esc(h.label)}</span></label>` : '';
      return `<div class="awsum-matrix-col-title">${esc(col.label)}</div>
        ${col.sub ? `<div class="awsum-matrix-col-sub">${esc(col.sub)}</div>` : ''}<div class="awsum-matrix-col-badges">${status}${ro}</div>${control}`;
    }

    function cellHtml(row, col) {
      const id = `${prefix}_${row.id}_${col.id}`.replace(/[^A-Za-z0-9_-]/g, '_');
      const keypad = cfg.money && cfg.money.keypad
        ? `<button type="button" class="input-group-text" data-money-keypad="${esc(id)}" aria-label="Open amount keypad"${col.readOnly ? ' disabled' : ''}>$</button>` : '<span class="input-group-text">$</span>';
      const limits = `${cfg.money && cfg.money.min != null ? ` data-money-min="${esc(cfg.money.min)}"` : ''}${cfg.money && cfg.money.max != null ? ` data-money-max="${esc(cfg.money.max)}"` : ''}`;
      return `<div class="input-group input-group-sm awsum-matrix-input">${keypad}
          <input type="text" id="${esc(id)}" class="form-control" data-matrix-money data-awsum-money${limits}
            aria-label="${esc(col.label)} ${esc(row.label || row.id)}"${col.readOnly ? ' disabled' : ''}></div>
        <div class="awsum-matrix-state"><span data-matrix-source></span>
          <button type="button" class="awsum-matrix-revert" data-matrix-revert title="${esc(labels.revert)}" aria-label="${esc(labels.revert)}"${col.readOnly ? ' disabled' : ''}>&#8634;</button></div>`;
    }

    function paint(k) {
      const c = cells.get(k);
      const td = table.querySelector(`[data-cell="${CSS.escape(k)}"]`);
      if (!c || !td) return;
      const input = td.querySelector('input');
      if (document.activeElement !== input) {
        if (c.current == null) input.value = '';
        else money().setValue(input, c.current);
      }
      input.placeholder = c.inherited == null ? '' : money().format(c.inherited);
      const override = c.current != null;
      const dirty = isCellDirty(c);
      td.classList.toggle('is-override', override);
      td.classList.toggle('is-inherited', !override);
      td.classList.toggle('is-dirty', dirty);
      td.classList.toggle('is-conflict', !!c.conflict);
      td.title = c.conflict || '';
      td.querySelector('[data-matrix-source]').textContent = (override ? labels.override : labels.inherited) + (dirty ? ` · ${labels.changed}` : '');
      td.querySelector('[data-matrix-revert]').hidden = !override;
    }

    function paintHead(colId) {
      const h = heads.get(colId);
      const th = table.querySelector(`th[data-col="${CSS.escape(String(colId))}"]`);
      if (!h || !th) return;
      th.querySelector('[data-matrix-head]').checked = h.value;
      th.classList.toggle('is-dirty', h.value !== h.original);
      th.classList.toggle('is-conflict', !!h.conflict);
      th.title = h.conflict || '';
    }

    const isCellDirty = (c) => c.current !== c.original;

    function cellOfInput(input) {
      const td = input.closest('[data-cell]');
      return td ? cells.get(td.dataset.cell) : null;
    }

    function readInput(c, input) {
      if (!input.value.trim()) { c.current = null; return; } // empty = inherited
      try { c.current = money().normalize(input.value); } catch (e) { /* invalid: validate() marks it */ }
    }

    function onInput(event) {
      const input = event.target;
      if (input.matches('[data-matrix-filter]')) { readFilters(); return; }
      if (!input.matches('[data-matrix-money]')) return;
      const c = cellOfInput(input);
      if (!c) return;
      readInput(c, input);
      c.conflict = '';
      paintState(c);
      notify();
    }

    function paintState(c) { // repaint without rewriting the focused input's text
      const k = key(c.row, c.column);
      paint(k);
    }

    function onChange(event) {
      const t = event.target;
      if (t.matches('[data-matrix-filter]')) { readFilters(); return; }
      if (t.matches('[data-matrix-head]')) {
        const colId = columns.find((col) => String(col.id) === t.dataset.matrixHead).id;
        const h = heads.get(colId);
        h.value = t.checked;
        h.conflict = '';
        paintHead(colId);
        applyFilter();
        notify();
      } else if (t.matches('[data-matrix-money]')) {
        const c = cellOfInput(t);
        if (c) { readInput(c, t); paint(key(c.row, c.column)); notify(); }
      }
    }

    function onBlur(event) {
      const input = event.target;
      if (!input.matches || !input.matches('[data-matrix-money]')) return;
      const c = cellOfInput(input);
      if (!c) return;
      readInput(c, input);
      setTimeout(() => { paint(key(c.row, c.column)); applyFilter(); notify(); }, 0); // after AwsumMoneyInput's blur
    }

    function onClick(event) {
      const button = event.target.closest('[data-matrix-revert]');
      if (!button || button.disabled) return;
      const td = button.closest('[data-cell]');
      const c = cells.get(td.dataset.cell);
      c.current = null;
      c.conflict = '';
      const input = td.querySelector('input');
      input.value = '';
      money().validate(input);
      paint(td.dataset.cell);
      applyFilter();
      notify();
    }

    function readFilters() {
      const q = container.querySelector('[data-matrix-filter="q"]');
      const s = container.querySelector('[data-matrix-filter="status"]');
      const o = container.querySelector('[data-matrix-filter="overridesOnly"]');
      filter.q = q ? q.value.trim().toLowerCase() : '';
      filter.status = s ? s.value : 'all';
      filter.overridesOnly = !!(o && o.checked);
      applyFilter();
    }

    function columnHasOverride(col) {
      const h = heads.get(col.id);
      return rows.some((row) => cells.get(key(row.id, col.id)).current != null) || (h && h.value === false);
    }

    function visibleColumn(col) {
      if (filter.q && !`${col.label} ${col.sub || ''}`.toLowerCase().includes(filter.q)) return false;
      if (filter.status === 'active' && col.active === false) return false;
      if (filter.status === 'inactive' && col.active !== false) return false;
      if (filter.overridesOnly && !columnHasOverride(col)) return false;
      return true;
    }

    function applyFilter() {
      let shown = 0;
      columns.forEach((col) => {
        const visible = visibleColumn(col);
        shown += visible ? 1 : 0;
        table.querySelectorAll(`[data-col="${CSS.escape(String(col.id))}"]`).forEach((el) => { el.hidden = !visible; });
      });
      container.querySelector('[data-matrix-empty]').classList.toggle('d-none', shown > 0 || !columns.length);
    }

    function layoutFixed() { // sticky offsets of the fixed columns (left = width of the fixed columns before it)
      const first = table.querySelector('thead tr');
      if (!first) return;
      let left = 0;
      (cfg.fixedColumns || []).forEach((_, i) => {
        table.querySelectorAll(`[data-fixed="${i}"]`).forEach((el) => { el.style.left = `${left}px`; });
        const cell = first.querySelector(`[data-fixed="${i}"]`);
        left += cell ? cell.getBoundingClientRect().width : 0;
      });
      const last = (cfg.fixedColumns || []).length - 1;
      table.querySelectorAll(`[data-fixed="${last}"]`).forEach((el) => el.classList.add('is-last-fixed'));
    }

    function invalidCells() {
      return [...table.querySelectorAll('input[data-matrix-money]')].filter((input) => !money().validate(input));
    }

    function changes() {
      const invalid = invalidCells();
      if (invalid.length) {
        invalid[0].reportValidity();
        throw new TypeError(invalid[0].validationMessage);
      }
      const out = { cells: [], headers: [] };
      cells.forEach((c) => {
        if (isCellDirty(c)) out.cells.push({ row: c.row, column: c.column, value: c.current, version: c.version });
      });
      heads.forEach((h, column) => {
        if (h.value !== h.original) out.headers.push({ column, key: h.key, value: h.value, version: h.version });
      });
      return out;
    }

    function count() {
      let n = 0;
      cells.forEach((c) => { n += isCellDirty(c) ? 1 : 0; });
      heads.forEach((h) => { n += h.value !== h.original ? 1 : 0; });
      return n;
    }

    function notify() {
      const n = count();
      container.classList.toggle('is-dirty', n > 0);
      if (cfg.onChange) cfg.onChange({ dirty: n > 0, count: n });
    }

    // Server conflicts (e.g. a 409): each listed cell / header takes the server's latest state (it becomes the loaded
    // state, so the user's edit of that cell is dropped) and is highlighted; other unsaved edits are kept.
    function markConflicts(list) {
      (list || []).forEach((item) => {
        if (item.header) {
          const h = heads.get(item.column);
          if (!h) return;
          h.original = !!item.value;
          h.value = h.original;
          h.version = item.version ?? h.version;
          h.conflict = item.message || labels.conflict;
          paintHead(item.column);
          return;
        }
        const c = cells.get(key(item.row, item.column));
        if (!c) return;
        c.original = norm(item.override);
        c.current = c.original;
        if (item.inherited !== undefined) c.inherited = norm(item.inherited);
        c.version = item.version ?? c.version;
        c.conflict = item.message || labels.conflict;
        const input = table.querySelector(`[data-cell="${CSS.escape(key(c.row, c.column))}"] input`);
        if (input && document.activeElement === input) input.blur();
        paint(key(c.row, c.column));
      });
      applyFilter();
      notify();
    }

    async function confirmDiscard() {
      if (!count()) return true;
      if (root.AwsumConfirm) {
        const result = await root.AwsumConfirm.show({ title: labels.discardTitle, message: labels.discardMessage,
          okText: labels.discardOk, variant: 'warning' });
        return !!(result && result.confirmed);
      }
      return root.confirm ? root.confirm(labels.discardMessage) : false;
    }

    function destroy() {
      table.querySelectorAll('input[data-matrix-money]').forEach((input) => money().destroy && money().destroy(input));
      container.removeEventListener('input', onInput);
      container.removeEventListener('change', onChange);
      container.removeEventListener('blur', onBlur, true);
      container.removeEventListener('click', onClick);
      container.classList.remove('awsum-matrix', 'is-dirty');
      container.innerHTML = '';
    }

    setData({ rows: cfg.rows, columns: cfg.columns, cell: cfg.cell });
    return {
      element: container,
      setData, changes, markConflicts, confirmDiscard, destroy, layout: layoutFixed,
      isDirty: () => count() > 0,
      isValid: () => invalidCells().length === 0,
      dirtyCount: count,
    };
  }

  return Object.freeze({ create });
});
