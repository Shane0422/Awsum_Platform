/* AWSUM Common Lookup Modal (window.AwsumLookup).
 *
 * A searchable, server-paged selection list in a Bootstrap modal (same pm-modal look as the other AWSUM modals).
 * Like the Common Confirm, the markup is created on first use, so pages only load this file and
 * static/css/awsum-lookup.css. One modal serves every lookup; the caller describes the data:
 *
 *   const result = await AwsumLookup.open({
 *     title: 'Select Sales Floor Location', subtitle: 'Optional text.',
 *     fixed: [['Area', 'Sales Floor']],                       // read-only conditions shown above the search
 *     searchPlaceholder: 'Search code or name',
 *     filters: [{ key: 'store_id', label: 'Store', value: '', options: [['', 'All Stores'], ['5', 'Main']] }],
 *     columns: [{ label: 'Code', value: (item) => item.code, className: 'awsum-lookup-mono', wide: false }],
 *     fetch: (params) => api('GET', `/x?${new URLSearchParams(params)}`), // params: q, page, size + filter keys
 *                                                             // -> { items, total, page, pages }
 *     key: (item) => item.code,                               // identity (current value / selection)
 *     selectable: (item) => item.active,                      // false: listed but cannot be chosen
 *     disabledLabel: 'Inactive', current: 'S01-B01-C01', emptyText: 'Nothing found.',
 *     summary: (data) => `${data.total} record(s)`,
 *   });
 *   // Select / double-click / Enter -> { action: 'select', item }; Cancel / close / Escape -> { action: 'cancel' }
 *
 * Keyboard: type to search; Arrow Down moves from the search box into the list; Arrow Up / Down / Home / End move
 * between rows; Enter (or Space) selects the focused row. All text is set with textContent (never HTML).
 */
(function () {
  'use strict';

  const PAGE_SIZE = 20;
  let root = null;
  let current = null; // { options, resolve, page, items, highlighted, token, done }

  const $ = (id) => document.getElementById(id);

  function build() {
    if (root) return root;
    root = document.createElement('div');
    root.className = 'modal fade pm-modal awsum-lookup-modal';
    root.id = 'awsumLookupModal';
    root.tabIndex = -1;
    root.setAttribute('aria-labelledby', 'awsumLookupTitle');
    root.setAttribute('aria-hidden', 'true');
    root.innerHTML = `
      <div class="modal-dialog modal-lg modal-dialog-centered modal-dialog-scrollable">
        <div class="modal-content">
          <div class="modal-header">
            <div class="pm-head-text">
              <h2 class="modal-title" id="awsumLookupTitle"></h2>
              <span class="pm-head-sub" id="awsumLookupSub"></span>
            </div>
            <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
          </div>
          <div class="modal-body">
            <div class="awsum-lookup-fixed" id="awsumLookupFixed"></div>
            <div class="awsum-lookup-filters">
              <div class="awsum-lookup-search">
                <label class="form-label" for="awsumLookupSearch">Search</label>
                <input type="search" class="form-control" id="awsumLookupSearch" autocomplete="off">
              </div>
              <div class="awsum-lookup-filter-slots" id="awsumLookupFilters"></div>
            </div>
            <div class="awsum-lookup-table">
              <table class="table table-hover mb-0">
                <thead><tr id="awsumLookupHead"></tr></thead>
                <tbody id="awsumLookupBody"></tbody>
              </table>
            </div>
            <div class="awsum-lookup-pager">
              <span class="text-muted small" id="awsumLookupSummary" role="status"></span>
              <div class="btn-group" role="group" aria-label="Pages">
                <button type="button" class="btn btn-outline-secondary btn-sm" id="awsumLookupPrev">Previous</button>
                <span class="btn btn-outline-secondary btn-sm disabled" id="awsumLookupPage"></span>
                <button type="button" class="btn btn-outline-secondary btn-sm" id="awsumLookupNext">Next</button>
              </div>
            </div>
          </div>
          <div class="modal-footer">
            <button type="button" class="btn btn-outline-secondary" data-bs-dismiss="modal">Cancel</button>
            <button type="button" class="btn btn-primary" id="awsumLookupSelect" disabled>Select</button>
          </div>
        </div>
      </div>`;
    document.body.appendChild(root);

    let timer = null;
    $('awsumLookupSearch').addEventListener('input', () => {
      clearTimeout(timer);
      timer = setTimeout(() => load(1), 250);
    });
    $('awsumLookupSearch').addEventListener('keydown', (event) => {
      if (event.key === 'Enter') { event.preventDefault(); clearTimeout(timer); load(1); }
      if (event.key === 'ArrowDown') { event.preventDefault(); focusRow(0); }
    });
    $('awsumLookupFilters').addEventListener('change', () => load(1));
    $('awsumLookupPrev').addEventListener('click', () => load(current.page - 1));
    $('awsumLookupNext').addEventListener('click', () => load(current.page + 1));
    $('awsumLookupSelect').addEventListener('click', () => choose(current && current.highlighted));
    const body = $('awsumLookupBody');
    body.addEventListener('click', (event) => {
      const row = event.target.closest('tr[data-index]');
      if (row) highlight(Number(row.dataset.index));
    });
    body.addEventListener('dblclick', (event) => {
      const row = event.target.closest('tr[data-index]');
      if (row) choose(Number(row.dataset.index));
    });
    body.addEventListener('keydown', (event) => {
      const row = event.target.closest('tr[data-index]');
      if (!row) return;
      const index = Number(row.dataset.index);
      const moves = { ArrowDown: index + 1, ArrowUp: index - 1, Home: 0, End: current.items.length - 1 };
      if (event.key in moves) {
        event.preventDefault();
        if (event.key === 'ArrowUp' && index === 0) { $('awsumLookupSearch').focus(); return; }
        focusRow(moves[event.key]);
      } else if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        choose(index);
      }
    });
    root.addEventListener('shown.bs.modal', () => $('awsumLookupSearch').focus());
    root.addEventListener('hidden.bs.modal', () => finish({ action: 'cancel' }));
    return root;
  }

  function finish(result) {
    if (!current || current.done) return;
    current.done = true;
    current.resolve(result);
  }

  const selectable = (item) => !current.options.selectable || current.options.selectable(item);

  function rowsOf() { return [...$('awsumLookupBody').querySelectorAll('tr[data-index]')]; }

  function highlight(index) {
    if (!current || !current.items[index]) return;
    current.highlighted = selectable(current.items[index]) ? index : null;
    rowsOf().forEach((row) => {
      const on = Number(row.dataset.index) === current.highlighted;
      row.classList.toggle('selected', on);
      row.setAttribute('aria-selected', String(on));
    });
    $('awsumLookupSelect').disabled = current.highlighted == null;
  }

  function focusRow(index) {
    const rows = rowsOf();
    if (!rows.length) return;
    const row = rows[Math.min(Math.max(index, 0), rows.length - 1)];
    row.focus();
    highlight(Number(row.dataset.index));
  }

  function choose(index) {
    const item = index == null ? null : current.items[index];
    if (!item || !selectable(item)) return;
    finish({ action: 'select', item });
    bootstrap.Modal.getOrCreateInstance(root).hide();
  }

  function message(text) {
    const cols = current.options.columns.length;
    $('awsumLookupBody').innerHTML = `<tr><td colspan="${cols}" class="text-center text-muted py-4"></td></tr>`;
    $('awsumLookupBody').querySelector('td').textContent = text;
  }

  async function load(page) {
    const opts = current.options;
    const token = ++current.token;
    const params = { q: $('awsumLookupSearch').value.trim(), page: Math.max(page || 1, 1), size: opts.pageSize || PAGE_SIZE };
    $('awsumLookupFilters').querySelectorAll('select[data-key]').forEach((select) => {
      if (select.value !== '' || select.dataset.keepEmpty) params[select.dataset.key] = select.value;
    });
    current.highlighted = null;
    $('awsumLookupSelect').disabled = true;
    message('Loading...');
    let data;
    try {
      data = await opts.fetch(params);
    } catch (err) {
      if (token === current.token) {
        message(err && err.message ? err.message : 'Could not load the list.');
        $('awsumLookupSummary').textContent = '';
      }
      return;
    }
    if (token !== current.token) return; // a newer search or page replaced this one
    current.items = data.items || [];
    current.page = data.page || 1;
    $('awsumLookupPage').textContent = `${current.page} / ${data.pages || 1}`;
    $('awsumLookupPrev').disabled = current.page <= 1;
    $('awsumLookupNext').disabled = current.page >= (data.pages || 1);
    $('awsumLookupSummary').textContent = opts.summary ? opts.summary(data) : `${data.total || 0} record(s)`;
    if (!current.items.length) { message(opts.emptyText || 'No records found.'); return; }
    const body = $('awsumLookupBody');
    body.textContent = '';
    current.items.forEach((item, index) => {
      const row = document.createElement('tr');
      const ok = selectable(item);
      const isCurrent = opts.current && opts.key && opts.key(item) === opts.current;
      row.dataset.index = String(index);
      row.tabIndex = 0;
      row.setAttribute('aria-selected', 'false');
      if (!ok) { row.classList.add('disabled'); row.setAttribute('aria-disabled', 'true'); }
      if (isCurrent) row.classList.add('current');
      opts.columns.forEach((col, c) => {
        const cell = document.createElement('td');
        if (col.className) cell.className = col.className;
        if (col.wide) cell.classList.add('awsum-lookup-wide');
        cell.textContent = col.value(item) == null ? '' : String(col.value(item));
        if (c === 0 && !ok) {
          const badge = document.createElement('span');
          badge.className = 'awsum-lookup-badge';
          badge.textContent = opts.disabledLabel || 'Not available';
          cell.appendChild(badge);
        }
        if (c === 0 && isCurrent) {
          const badge = document.createElement('span');
          badge.className = 'awsum-lookup-badge current';
          badge.textContent = 'Current';
          cell.appendChild(badge);
        }
        row.appendChild(cell);
      });
      body.appendChild(row);
    });
    const at = current.items.findIndex((item) => opts.key && opts.key(item) === opts.current);
    if (at >= 0) highlight(at);
  }

  function open(options) {
    build();
    if (current && !current.done) finish({ action: 'cancel' });
    return new Promise((resolve) => {
      current = { options: options || {}, resolve, page: 1, items: [], highlighted: null, token: 0, done: false };
      const opts = current.options;
      $('awsumLookupTitle').textContent = opts.title || 'Select';
      $('awsumLookupSub').textContent = opts.subtitle || '';
      const fixed = $('awsumLookupFixed');
      fixed.textContent = '';
      (opts.fixed || []).forEach(([label, value]) => {
        const item = document.createElement('span');
        const strong = document.createElement('b');
        item.textContent = `${label}: `;
        strong.textContent = value;
        item.appendChild(strong);
        fixed.appendChild(item);
      });
      fixed.classList.toggle('d-none', !(opts.fixed || []).length);
      $('awsumLookupSearch').value = '';
      $('awsumLookupSearch').placeholder = opts.searchPlaceholder || 'Search';
      const filters = $('awsumLookupFilters');
      filters.textContent = '';
      (opts.filters || []).forEach((filter, i) => {
        const wrap = document.createElement('div');
        const label = document.createElement('label');
        const select = document.createElement('select');
        label.className = 'form-label';
        label.htmlFor = `awsumLookupFilter${i}`;
        label.textContent = filter.label;
        select.className = 'form-select';
        select.id = `awsumLookupFilter${i}`;
        select.dataset.key = filter.key;
        if (filter.sendEmpty) select.dataset.keepEmpty = '1';
        filter.options.forEach(([value, text]) => select.add(new Option(text, value)));
        select.value = filter.value == null ? '' : String(filter.value);
        wrap.append(label, select);
        filters.appendChild(wrap);
      });
      const head = $('awsumLookupHead');
      head.textContent = '';
      (opts.columns || []).forEach((col) => {
        const th = document.createElement('th');
        th.scope = 'col';
        th.textContent = col.label;
        if (col.className) th.className = col.className.replace('awsum-lookup-mono', '').trim();
        if (col.wide) th.classList.add('awsum-lookup-wide');
        if (col.title) th.title = col.title;
        head.appendChild(th);
      });
      bootstrap.Modal.getOrCreateInstance(root).show();
      load(1);
    });
  }

  window.AwsumLookup = { open };
})();
