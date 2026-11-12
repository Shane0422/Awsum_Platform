(function () {
  'use strict';

  const root = document.getElementById('mgPage');
  if (!root) return;
  const entity = root.dataset.entity;
  const title = root.dataset.title;
  const BASE = root.dataset.apiBase;

  const $ = (id) => document.getElementById(id);
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const label = (v) => ({ ONE_TIME: 'One-time', MONTHLY: 'Monthly', ANNUAL: 'Annual', HARDWARE: 'Hardware', SOFTWARE: 'Software', SERVICE: 'Service', CUSTOMER_ORDERING: 'Customer Ordering' }[v] || v || '');
  const yes = (v) => (v ? 'Yes' : 'No');
  const badge = (on) => `<span class="badge ${on ? 'text-bg-success' : 'text-bg-secondary'}">${on ? 'Active' : 'Inactive'}</span>`;
  // An unset price is shown as "Not set", never as $0.00.
  // "$" prefix that opens the Common Money Keypad (static/js/money-keypad.js) for the money input next to it.
  const MONEY_KEYPAD_BUTTON = '<button type="button" class="input-group-text" data-money-keypad aria-label="Open amount keypad" aria-haspopup="dialog" aria-expanded="false">$</button>';
  const money = (v) => (v == null || v === '' ? '<span class="text-muted">Not set</span>' : '$' + esc(AwsumMoneyInput.format(v)));
  const levelCell = (r, n) => {
    const price = r[`p${n}_price`];
    if (price == null) return money(null);
    return r[`p${n}_mode`] === 'DISCOUNT' ? `${money(price)} <small class="text-muted">(${esc(r[`p${n}_discount_pct`])}% off)</small>` : money(price);
  };

  // Same rule as the server: retail x (1 - pct/100), cents, rounded half up. BigInt avoids float error.
  function toCents(text) {
    const m = /^\s*(\d+)(?:\.(\d{1,2}))?\s*$/.exec(String(text ?? ''));
    return m ? BigInt(m[1]) * 100n + BigInt((m[2] || '').padEnd(2, '0') || '0') : null;
  }
  function discountPrice(retailText, pctText) {
    const retail = retailText ? AwsumMoneyInput.toMinorUnits(retailText) : null;
    const pct = toCents(pctText); // hundredths of a percent
    if (retail === null || pct === null || pct > 10000n) return null;
    const cents = (retail * (10000n - pct) + 5000n) / 10000n;
    return AwsumMoneyInput.fromMinorUnits(cents);
  }

  const SUBTITLES = {
    tax: 'Tax rates that items can use as their default tax.',
    category1: 'Top-level product classification.',
    category2: 'Second-level classification under Category I.',
    category3: 'Third-level classification under Category II.',
    md: 'Sellable and billable items with their license and serial management rules.',
    item_kind: 'Super Admin System Master. Codes (IKC00001) are issued automatically; System Codes are fixed program logic keys.',
    location: 'Sales Floor and Warehouse Location Codes of the selected Store.',
    common_code: 'Common codes of this scope by group (Platform and each Merchant keep their own). Select a group to manage its codes.',
  };

  const taxLabel = (tax) => tax ? `${tax.name} (${Number(tax.rate)}%)` : '';
  // Optional Color swatch (server-validated #RRGGBB); no swatch when blank.
  const swatch = (hex) => (/^#[0-9A-Fa-f]{6}$/.test(hex || '') ? `<span class="mg-color-swatch" style="background:${hex}" aria-hidden="true"></span>` : '');

  // Master-Detail (left Tree + right form) entities and the parent Category levels grouped in their tree
  // (Tax, Category I and Item Kind have no parent, so their tree is a flat list). An entity not listed here
  // would use the full-width list with the modal form.
  const TREE_LEVELS = { tax: [], category1: [], category2: [1], category3: [1, 2], md: [1, 2, 3], item_kind: [], color: [], location: [], common_code: [] };
  const split = Object.prototype.hasOwnProperty.call(TREE_LEVELS, entity);
  const nameColumn = (text) => [['name', text, (r) => esc(r.name), true]];
  const TREE_NAME_COLUMN = nameColumn('Category / Name');

  const COLUMNS = {
    // Master-Detail lists show only the Name; parent Categories are group rows (see treeHtml).
    tax: nameColumn('Tax Name'),
    category1: TREE_NAME_COLUMN,
    category2: TREE_NAME_COLUMN,
    category3: TREE_NAME_COLUMN,
    md: TREE_NAME_COLUMN,
    item_kind: nameColumn('Display Name'),
    location: [['code', 'Location', (r) => `<span class="mg-code fw-semibold">${esc(r.code)}</span> ${esc(r.name)}`, true]],
    // Common Maintenance: the Value column exists only for a group with a Value Type (label = the group's label).
    common_code: [
      ['code', 'Code', (r) => `<span class="mg-code fw-semibold">${esc(r.code)}</span>`, true],
      ['name', 'Name', (r) => esc(r.name), true],
      ['value', 'Value', (r) => commonValueCell(r), false, 'value'],
      ['active', 'Status', (r) => badge(r.active), true],
    ],
  };

  const state = { q: '', active: '', sort: '', direction: 'asc', page: 1, size: 20, items: [], options: null, editing: null, details: [], editingDetailId: null, collapsed: new Set(), lastData: null, storeId: null, group: null };
  // Store-scoped master (Location Master): every request names the selected Store; the server re-checks it.
  const storeScoped = entity === 'location';
  // Group-scoped master (Common Maintenance): every request names the selected Common Group (?group=UNIT).
  const groupScoped = entity === 'common_code';
  const scoped = (path) => {
    const params = [];
    if (storeScoped && state.storeId) params.push(`store_id=${encodeURIComponent(state.storeId)}`);
    if (groupScoped && state.group) params.push(`group=${encodeURIComponent(state.group)}`);
    return params.length ? `${path}${path.includes('?') ? '&' : '?'}${params.join('&')}` : path;
  };
  const currentGroup = () => ((state.options && state.options.common_groups) || []).find((g) => g.code === state.group) || null;
  // Value cell by Value Type: COLOR swatch + HEX, CURRENCY via the Common money formatter, others as stored.
  function commonValueCell(r) {
    if (r.value == null || r.value === '') return '';
    if (r.value_type === 'COLOR') return `${swatch(r.value)}<span class="mg-code">${esc(r.value)}</span>`;
    if (r.value_type === 'CURRENCY') return money(r.value);
    return esc(r.value);
  }
  let modal = null;
  let saving = false;
  let allowClose = false;
  let imageUploader = null;
  let detailLoadToken = 0;
  let pageActions = null; // Common Page Header actions (split screens): Export / Add / Deactivate / Delete / Save
  let formOpen = false;   // a create or edit form is shown (vs. nothing selected)

  // Results use the Common Toast (success/info close after 1 s; warning/danger stay until closed).
  function showMessage(text, kind) { AwsumToast.show(text, { kind: kind || 'success' }); }
  function clearMessage() { AwsumToast.clear(); }

  async function api(method, path, body) {
    const res = await fetch(path, {
      method,
      credentials: 'same-origin',
      headers: body && !(body instanceof FormData) ? { 'Content-Type': 'application/json' } : {},
      body: body instanceof FormData ? body : (body ? JSON.stringify(body) : undefined),
    });
    let data = {};
    try { data = await res.json(); } catch (_) { /* non-JSON */ }
    if (!res.ok) {
      const err = new Error(typeof data.detail === 'string' ? data.detail : 'Request failed.');
      err.status = res.status;
      err.errors = data.errors || {};
      throw err;
    }
    return data;
  }

  // ---- list ----
  const cols = () => COLUMNS[entity]
    .filter((c) => c[4] !== 'cost' || (state.options && state.options.can_view_cost))
    .filter((c) => c[4] !== 'value' || !!(currentGroup() && currentGroup().value_type))
    .map((c) => (c[4] === 'value' ? [c[0], esc(currentGroup().value_label || 'Value'), c[2], c[3], c[4]] : c));
  function renderHead() {
    if (twoAreaList) {
      $('mgHead').innerHTML = `<tr>${LOCATION_AREA_COLUMNS.map(([, text]) => `<th>${text}</th>`).join('')}</tr>`;
      return;
    }
    $('mgHead').innerHTML = '<tr>' + cols().map(([key, text, , sortable]) => {
      const sorted = sortable && state.sort === key;
      const cls = sortable ? `sortable${sorted ? ' sorted' : ''}${sorted && state.direction === 'desc' ? ' desc' : ''}` : '';
      return `<th class="${cls}" ${sortable ? `data-sort="${key}" tabindex="0"` : ''}>${text}</th>`;
    }).join('') + (split ? '' : '<th class="text-end">Actions</th>') + '</tr>';
  }

  function rowHtml(r, columns, depth) {
    return `<tr${split ? ` class="mg-md-row${state.editing && state.editing.id === r.id ? ' selected' : ''}" data-row-select="${r.id}" tabindex="0" aria-label="Edit ${esc(r.name)}" style="--mg-depth:${depth || 0}"` : ''}>${columns.map((c) => `<td>${c[2](r)}</td>`).join('')}
          ${split ? '' : `<td class="text-end text-nowrap">
            <button type="button" class="btn btn-sm btn-outline-primary" data-act="edit" data-id="${r.id}">Edit</button>
            <button type="button" class="btn btn-sm btn-outline-secondary" data-act="toggle" data-id="${r.id}">${r.active ? 'Deactivate' : 'Activate'}</button>
            ${r.system ? '' : `<button type="button" class="btn btn-sm btn-outline-danger" data-act="delete" data-id="${r.id}">Delete</button>`}
          </td>`}</tr>`;
  }

  // ---- Location Master list: two independent columns (Sales Floor | Warehouse Location Codes) ----
  // Each column is its own list (same Search / Status / page / page size, Location Code ascending); a row pairs
  // nothing, a shorter column leaves empty cells. A code cell is the selectable item (same data-row-select).
  const twoAreaList = entity === 'location';
  const LOCATION_AREA_COLUMNS = [['SALES_FLOOR', 'Sales Floor'], ['WAREHOUSE', 'Warehouse']];
  function locationCell(r) {
    if (!r) return '<td class="mg-loc-empty"></td>';
    const selected = state.editing && state.editing.id === r.id ? ' selected' : '';
    return `<td class="mg-loc-cell${selected}${r.active ? '' : ' inactive'}" data-row-select="${r.id}" tabindex="0" aria-label="Edit ${esc(r.code)}" title="${esc(r.name || '')}">`
      + `<span class="mg-code">${esc(r.code)}</span>${r.active ? '' : ' <span class="mg-loc-inactive">Inactive</span>'}`
      + `<span class="mg-loc-name">${esc(r.name || '')}</span></td>`;
  }
  function locationColumnsHtml(columns) {
    const [left, right] = LOCATION_AREA_COLUMNS.map(([area]) => columns[area] || []);
    return Array.from({ length: Math.max(left.length, right.length) },
      (_, i) => `<tr class="mg-loc-row">${locationCell(left[i])}${locationCell(right[i])}</tr>`).join('');
  }
  async function loadLocationColumns(params) {
    const pages = await Promise.all(LOCATION_AREA_COLUMNS.map(([area]) => {
      const query = new URLSearchParams(params);
      query.set('location_area', area);
      query.set('sort', 'code');
      query.set('direction', 'asc');
      return api('GET', scoped(`${BASE}/${entity}?${query}`));
    }));
    const pageCount = Math.max(...pages.map((p) => p.pages));
    if (state.page > pageCount) { // e.g. the last item of the last page was deleted: show the last page
      state.page = pageCount;
      return loadLocationColumns(new URLSearchParams({ ...Object.fromEntries(new URLSearchParams(params)), page: state.page }));
    }
    const columns = {};
    // A column with fewer pages has nothing on a later page (the server would repeat its last page).
    LOCATION_AREA_COLUMNS.forEach(([area], i) => { columns[area] = pages[i].page === state.page ? pages[i].items : []; });
    return {
      items: [...columns.SALES_FLOOR, ...columns.WAREHOUSE], columns,
      total: pages.reduce((sum, p) => sum + p.total, 0),
      page: state.page, pages: pageCount, sort: 'code', direction: 'asc',
    };
  }

  // ---- Master-Detail list: the current page's items grouped by their parent Categories (expand / collapse) ----
  // Grouping is client-side over the same paged API result, so Search / Status / Sort / Paging stay unchanged.
  const catKey = (level, r) => {
    const id = r[`category${level}_id`];
    const name = r[`category${level}_name`];
    return id != null ? `c${level}:${id}` : (name ? `c${level}:${name}` : null);
  };
  // Tree levels for this entity, rendered by the Common AwsumMasterDetail tree (static/js/master-detail.js).
  const treeLevels = (TREE_LEVELS[entity] || []).map((level) => ({
      key: (r) => catKey(level, r),
      name: (r) => r[`category${level}_name`] || `Category ${'I'.repeat(level)} not found`,
      order: (r) => catOrder(level, r),
  }));
  // Category display order comes from the existing options lookup; unknown categories keep page order.
  function catOrder(level, r) {
    const list = (state.options && state.options[`category${level}`]) || [];
    const index = list.findIndex((c) => c.id === r[`category${level}_id`]);
    return index < 0 ? Number.MAX_SAFE_INTEGER : index;
  }
  const treeHtml = (items, columns) => AwsumMasterDetail.treeHtml({
    items, levels: treeLevels, collapsed: state.collapsed, colspan: columns.length,
    rowHtml: (r, depth) => rowHtml(r, columns, depth),
  });
  const expandTreePaths = (items) => AwsumMasterDetail.expandTree(state.collapsed, items, treeLevels);
  function toggleTreeGroup(key) {
    AwsumMasterDetail.toggleTree(state.collapsed, key);
    if (state.lastData) renderRows(state.lastData);
    const row = [...$('mgBody').querySelectorAll('tr[data-tree-toggle]')].find((tr) => tr.dataset.treeToggle === key);
    if (row) row.focus();
  }

  function renderRows(data) {
    const columns = cols();
    state.lastData = data;
    $('mgBody').innerHTML = data.items.length
      ? (twoAreaList ? locationColumnsHtml(data.columns || {}) : split ? treeHtml(data.items, columns) : data.items.map((r) => rowHtml(r, columns)).join(''))
      : `<tr><td colspan="${twoAreaList ? 2 : columns.length + (split ? 0 : 1)}" class="text-center text-muted py-4">No records found.</td></tr>`;
    $('mgSummary').textContent = `${data.total} record(s)`;
    // No location at all in the selected Store (not just no search hits): show the registration guide.
    storeHasNoLocations = storeScoped && !!state.storeId && data.total === 0 && !state.q.trim() && !state.active;
    syncLocationGuide();
    $('mgPageLabel').textContent = `${data.page} / ${data.pages}`;
    $('mgPrev').disabled = data.page <= 1;
    $('mgNext').disabled = data.page >= data.pages;
  }

  // Location Master: the first visit of a Store creates its default Locations once (server decides: only when the
  // Store has no Location and was never initialized). Asked once per Store per page; failures are left to the list.
  const initializedStores = new Set();
  async function ensureDefaultLocations() {
    if (!twoAreaList || !state.storeId || initializedStores.has(state.storeId) || storeReadOnly()) return;
    initializedStores.add(state.storeId);
    try {
      const result = await api('POST', scoped(`${BASE}/location/initialize`));
      if (result.created) showMessage(`${result.created} default Locations were created for this Store.`, 'success');
    } catch (_) { /* access problems are reported by the list request */ }
  }

  async function load() {
    const params = new URLSearchParams({
      q: state.q, active: state.active, sort: state.sort, direction: state.direction, page: state.page, size: state.size,
    });
    if ((storeScoped && !state.storeId) || (groupScoped && !state.group)) {
      state.items = [];
      renderHead();
      renderRows({ items: [], total: 0, page: 1, pages: 1 });
      return;
    }
    try {
      if (twoAreaList) await ensureDefaultLocations();
      const data = twoAreaList ? await loadLocationColumns(params) : await api('GET', scoped(`${BASE}/${entity}?${params}`));
      state.items = data.items;
      state.page = data.page;
      state.sort = data.sort;
      state.direction = data.direction;
      if (split) {
        if (state.q.trim()) expandTreePaths(data.items);
        if (state.editing) expandTreePaths([state.editing]);
      }
      renderHead();
      renderRows(data);
    } catch (err) {
      showMessage(err.status === 401 ? 'Your session has expired. Please sign in again.' : err.message, 'danger');
    }
  }

  async function loadOptions() {
    state.options = await api('GET', `${BASE}/options`);
  }

  // Store Options > Item Features (from /options). Platform / Merchant get every feature ON.
  // OFF only hides the field and leaves it out of the save payload; the saved value is kept.
  // 'location' = Business Options > Use Location Management (not an Item Feature): Platform / Merchant always on.
  // 'platform_md' = MD fields used only by Platform MD (Price Level P1-P3, Billing Cycle, License): off for a
  // Merchant / Store scope (options.md_platform_fields). MD Recommended Location: off there too for now
  // (options.md_recommended_location; the Merchant shared Location Master comes later). The server ignores both.
  const featureOn = (feature) => {
    const o = state.options;
    if (feature === 'platform_md') return !(o && o.md_platform_fields === false);
    if (feature === 'location') {
      return !(o && o.location_management === false) && !(entity === 'md' && o && o.md_recommended_location === false);
    }
    return !(o && o.item_features && o.item_features[feature] === false);
  };
  // Store login: inherited Merchant masters it views only (options.view_only_entities; the server refuses writes).
  const viewOnly = () => ((state.options && state.options.view_only_entities) || []).includes(entity);
  function applyItemFeatures(root) {
    root.querySelectorAll('[data-item-feature]').forEach((el) => {
      const off = !featureOn(el.dataset.itemFeature);
      el.classList.toggle('d-none', off);
      el.toggleAttribute('data-item-feature-off', off);
    });
  }

  // ---- form ----
  const ICONS = {
    tag: '<circle cx="7.5" cy="7.5" r="1.5"/><path d="M3 3h8l9 9-8 8-9-9z"/>',
    layers: '<path d="m12 3 9 5-9 5-9-5z"/><path d="m3 13 9 5 9-5"/>',
    percent: '<path d="M19 5 5 19"/><circle cx="7" cy="7" r="2"/><circle cx="17" cy="17" r="2"/>',
    dollar: '<path d="M12 3v18"/><path d="M16 7.5c0-1.7-1.8-3-4-3s-4 1.3-4 3 1.8 2.7 4 3 4 1.3 4 3-1.8 3-4 3-4-1.3-4-3"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    box: '<path d="M21 8 12 3 3 8v8l9 5 9-5z"/><path d="m3 8 9 5 9-5M12 13v8"/>',
    text: '<path d="M4 6h16M4 12h16M4 18h10"/>',
    key: '<circle cx="8" cy="15" r="4"/><path d="m11 12 9-9M16 7l3 3"/>',
    hash: '<path d="M5 9h14M5 15h14M10 4 8 20M16 4l-2 16"/>',
    check: '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
    list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
    shield: '<path d="M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6z"/>'
  };
  const FIELD_ICON = {
    name: 'tag', rate: 'percent', display_order: 'list', active: 'check', notes: 'text', description: 'text',
    category1_id: 'layers', category2_id: 'layers', category3_id: 'layers', default_tax_id: 'percent',
    billing_cycle: 'clock', item_kind: 'box', unit: 'box', inventory_type: 'layers', keep: 'shield', output_devices: 'hash', retail_price: 'dollar', cost: 'dollar',
    license_required: 'key', license_type: 'key', licenses_per_unit: 'hash', serial_required: 'hash',
    code: 'tag', location_area: 'layers', zone: 'hash', aisle: 'hash', bay: 'hash', level: 'hash'
  };
  const labelIcon = (name) => FIELD_ICON[name]
    ? `<svg class="pm-lbl-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[FIELD_ICON[name]]}</svg>` : '';

  function field(name, text, control, opts) {
    const col = (opts && opts.col) || 'col-md-6';
    return `<div class="${col}" data-field-wrap="${name}"${opts && opts.feature ? ` data-item-feature="${opts.feature}"` : ''}>
      ${opts && opts.labelAction ? '<div class="mg-label-row">' : ''}<label class="form-label" for="mgf_${name}">${labelIcon(name)}${text}${opts && opts.required ? ' <span class="pm-req" aria-hidden="true">*</span>' : ''}</label>${opts && opts.labelAction ? `${opts.labelAction}</div>` : ''}
      ${control}
      <div class="invalid-feedback" data-error-for="${name}"></div>
      ${opts && opts.help ? `<div class="form-text">${opts.help}</div>` : ''}
    </div>`;
  }
  const input = (name, type, extra) => `<input class="form-control${type === 'number' || /inputmode="decimal"/.test(extra || '') ? ' pm-num' : ''}" id="mgf_${name}" name="${name}" type="${type}" ${extra || ''}>`;
  const select = (name, extra) => `<select class="form-select" id="mgf_${name}" name="${name}" ${extra || ''}></select>`;
  const textarea = (name) => `<textarea class="form-control" id="mgf_${name}" name="${name}" rows="2"></textarea>`;
  const activeField = () => field('active', 'Status', select('active'), { col: 'col-md-6' });
  // Location Code placeholder by Area (an example only; the format is never enforced).
  // ---- Location Master: one range form for new locations (Zone + two From/To ranges -> every combination) ----
  // Sales Floor S01-B01-C01 (Bay, Shelf); Warehouse W01-R01-C01 (Rack, Level). From = To creates one code.
  // The server builds the codes, reports existing ones (skipped) and saves the new ones in one transaction.
  const LOCATION_RANGE_PARTS = { SALES_FLOOR: [['bay', 'Bay'], ['shelf', 'Shelf']], WAREHOUSE: [['rack', 'Rack'], ['level', 'Level']] };
  const LOCATION_ZONE_DEFAULTS = { SALES_FLOOR: 'S01', WAREHOUSE: 'W01' };
  const rangeMode = () => entity === 'location' && !state.editing && !!$('mgRangePreview');
  const rangeArea = () => (f('location_area') && AwsumOptionGroup.getValue(f('location_area'))) || 'SALES_FLOOR';
  function locationRangeHtml(o, example) {
    const number = (name) => input(name, 'number', 'min="1" max="99" step="1" inputmode="numeric"');
    const parts = Object.entries(LOCATION_RANGE_PARTS).map(([area, pairs]) => `<div class="col-12" data-range-area="${area}"><div class="row g-3">${
      pairs.map(([key, label]) => field(`${key}_from`, `${label} From`, number(`${key}_from`), { required: true, col: 'col-6 col-md-3' })
        + field(`${key}_to`, `${label} To`, number(`${key}_to`), { required: true, col: 'col-6 col-md-3' })).join('')}</div></div>`).join('');
    return field('location_area', 'Area', AwsumOptionGroup.html({
      id: 'mgf_location_area', name: 'location_area', ariaLabel: 'Area', required: true,
      options: (o.location_areas || []).map((a) => ({ value: a.code, label: a.name })),
    }), { required: true })
      + field('zone', 'Zone', input('zone', 'text', 'maxlength="10" data-awsum-code'),
        { required: true, labelAction: example, help: 'e.g. S01 (Sales Floor) or W01 (Warehouse).' })
      + parts
      + '<div class="col-12"><div class="mg-range-preview" id="mgRangePreview" aria-live="polite">'
      + '<div class="mg-range-summary" id="mgRangeSummary"></div><div class="mg-range-codes" id="mgRangeCodes"></div></div></div>'
      + field('name', 'Location Name', input('name', 'text', 'maxlength="100"'), { help: 'Optional. Empty = each Location Code.' })
      + field('display_order', 'Display Order', input('display_order', 'number', 'min="0" step="1"'))
      + activeField() + field('notes', 'Notes', textarea('notes'), { col: 'col-12' });
  }
  // Area change: show its two ranges (the other Area's inputs are disabled, so they are never sent) and its Zone default.
  function syncLocationRange() {
    if (!rangeMode()) return;
    const area = rangeArea();
    $('mgFields').querySelectorAll('[data-range-area]').forEach((group) => {
      const on = group.dataset.rangeArea === area;
      group.classList.toggle('d-none', !on);
      group.querySelectorAll('input').forEach((el) => { el.disabled = !on; });
    });
    const zone = f('zone');
    if (!zone.value.trim() || Object.values(LOCATION_ZONE_DEFAULTS).includes(zone.value.trim().toUpperCase())) {
      zone.value = LOCATION_ZONE_DEFAULTS[area];
    }
    loadRangePreview();
  }
  function renderRangePreview(data, message) {
    if (!$('mgRangePreview')) return;
    $('mgRangeSummary').innerHTML = data
      ? `<span>Total <b>${data.total}</b></span> <span>Existing <b>${data.duplicates}</b> <small>(skipped)</small></span> <span class="mg-range-new">New <b>${data.new}</b></span>`
      : `<span class="text-muted">${esc(message || 'Enter the Zone and ranges to preview the codes.')}</span>`;
    $('mgRangeCodes').innerHTML = data ? data.codes.map((c) => `<span class="mg-range-code${c.exists ? ' exists' : ''}"${c.exists ? ' title="Already exists in this Store (skipped)"' : ''}>${esc(c.code)}</span>`).join('') : '';
  }
  let rangeToken = 0;
  async function loadRangePreview() {
    if (!rangeMode()) return null;
    const token = ++rangeToken;
    try {
      const data = await api('POST', scoped(`${BASE}/location/range/preview`), collect());
      if (token === rangeToken) renderRangePreview(data);
      return data;
    } catch (err) {
      if (token === rangeToken) renderRangePreview(null, Object.values(err.errors || {})[0] || err.message);
      return null;
    }
  }
  const scheduleRangePreview = debounce(loadRangePreview, 250);
  // Save (create mode): fresh preview -> Common Confirm with the counts -> one transaction on the server.
  async function saveLocationRange(body) {
    const preview = await api('POST', scoped(`${BASE}/location/range/preview`), body); // field errors -> the form
    renderRangePreview(preview);
    if (!preview.new) {
      showMessage(`All ${preview.total} Location Code(s) already exist in this Store. Nothing was saved.`, 'warning');
      return;
    }
    const areaName = ((state.options && state.options.location_areas) || []).find((a) => a.code === preview.location_area);
    const result = await AwsumConfirm.show({
      title: 'Create Locations',
      message: `Create ${preview.new} new Location Code(s)?${preview.duplicates ? ` ${preview.duplicates} existing code(s) will be skipped.` : ''}`,
      variant: 'info',
      details: [['Area', areaName ? areaName.name : preview.location_area], ['Codes', `${preview.codes[0].code}${preview.total > 1 ? ` \u2026 ${preview.codes[preview.total - 1].code}` : ''}`],
        ['Total', String(preview.total)], ['Existing (skipped)', String(preview.duplicates)], ['New', String(preview.new)]],
      okText: 'Save',
    });
    if (!result.confirmed) { showMessage('Not saved. No changes were made.', 'info'); return; }
    const data = await api('POST', scoped(`${BASE}/location/range`), body);
    resetMdForm();
    showMessage(`${data.created} location(s) created.${data.duplicates ? ` ${data.duplicates} existing code(s) skipped.` : ''}`, 'success');
    await load();
  }
  // Location Master guide: shown on the right while the selected Store has no locations (and no form is open);
  // the same image opens enlarged from "View Example" (Bootstrap modal).
  let storeHasNoLocations = false;
  function syncLocationGuide() {
    if (entity !== 'location' || !$('mgLocationGuide')) return;
    $('mgLocationGuide').classList.toggle('d-none', !(storeHasNoLocations && !formOpen));
  }
  // Location Guide (page header) and View Example (form) open the same Bootstrap modal, fitted to the screen.
  function setGuideZoom(zoomed) {
    $('mgExampleModal').querySelector('.mg-example-scroll').classList.toggle('zoomed', zoomed);
    $('mgExampleZoom').textContent = zoomed ? 'Fit to Screen' : 'Zoom In';
    $('mgExampleZoom').setAttribute('aria-pressed', String(zoomed));
    $('mgExampleScrollHint').classList.toggle('d-none', !zoomed);
  }
  function showLocationExample() {
    if (!$('mgExampleModal')) return;
    setGuideZoom(false);
    bootstrap.Modal.getOrCreateInstance($('mgExampleModal')).show();
  }
  // Common recommended positions (Location Management): free format; Common Location Code rule
  // (AwsumCodeInput upper-cases while typing / pasting; the server trims and upper-cases again on save).
  // Category III = default for all Stores; MD Master = Override (empty = inherit, Reset clears it).
  const LOCATION_FIELDS = [['sales_location', 'Sales Floor Location'], ['warehouse_location', 'Warehouse Location']];
  // Common Location Selector (Category III and MD Master): a read-only code chosen with Search (AwsumLookup) and
  // removed with Clear. A saved code is shown as it is, even when no Store has it any more.
  const LOCATION_FIELD_AREAS = { sales_location: 'SALES_FLOOR', warehouse_location: 'WAREHOUSE' };
  // opts.clear: a Clear button (Category III: Not set). MD Master has none; its Reset link returns to Category III.
  const SEARCH_ICON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    + 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>';
  const locationInput = (key, text, opts) => `<div class="input-group mg-loc-picker">${input(key, 'text', 'maxlength="50" readonly')}`
    + `<button type="button" class="btn btn-outline-secondary mg-loc-search" data-loc-search="${key}" title="Search ${text}" aria-label="Search ${text}">${SEARCH_ICON}</button>`
    + (opts && opts.clear ? `<button type="button" class="btn btn-outline-secondary" data-loc-clear="${key}" title="Clear ${text}" aria-label="Clear ${text}">Clear</button>` : '')
    + '</div>';
  function syncLocationPickers() {
    LOCATION_FIELDS.forEach(([key]) => {
      const button = $('mgFields').querySelector(`[data-loc-clear="${key}"]`);
      if (f(key) && button) button.disabled = !f(key).value.trim();
    });
  }
  function setLocationValue(key, value) {
    f(key).value = value;
    f(key).dispatchEvent(new Event('input', { bubbles: true })); // MD: the existing inherit / Override display
    syncLocationPickers();
  }
  async function pickLocation(key, text) {
    if (!window.AwsumLookup || !f(key)) return;
    const o = state.options || {};
    const area = LOCATION_FIELD_AREAS[key];
    const areaName = ((o.location_areas || []).find((a) => a.code === area) || {}).name || area;
    const stores = o.stores || [];
    const filters = o.store_fixed ? [] : [{
      key: 'store_id', label: 'Store', value: '',
      options: [['', `All Stores (${stores.length})`], ...stores.map((store) => [String(store.id), storeLabel(store)])],
    }];
    filters.push({ key: 'active', label: 'Status', value: 'active', sendEmpty: true,
      options: [['active', 'Active'], ['', 'All status'], ['inactive', 'Inactive']] });
    const result = await AwsumLookup.open({
      title: `Select ${text}`,
      subtitle: 'Location Codes of the Stores you can access. The code is saved as text for every Store.',
      fixed: [['Area', areaName]],
      searchPlaceholder: 'Search code or name',
      filters,
      columns: [
        { label: 'Location Code', value: (item) => item.code, className: 'awsum-lookup-mono' },
        { label: 'Name', value: (item) => item.name || '', wide: true },
        { label: 'Stores', value: (item) => item.stores, className: 'text-end', title: 'Stores where this code is registered' },
        { label: 'Active Stores', value: (item) => item.active_stores, className: 'text-end', title: 'Stores where it is active' },
      ],
      fetch: (params) => api('GET', `${BASE}/location/codes?${new URLSearchParams({ ...params, location_area: area })}`),
      key: (item) => item.code,
      selectable: (item) => item.active_stores > 0, // inactive in every Store: listed, never chosen
      disabledLabel: 'Inactive',
      current: f(key).value.trim(),
      emptyText: 'No Location Codes found. Location Master creates each Store\'s default codes on its first visit.',
      summary: (data) => `${data.total} code(s) · ${data.store_total} Store(s) in scope`,
    });
    if (result.action === 'select') setLocationValue(key, result.item.code);
    const back = $('mgFields').querySelector(`[data-loc-search="${key}"]`);
    if (back) back.focus();
  }

  function formHtml() {
    const notes = field('notes', 'Notes', textarea('notes'), { col: 'col-12' });
    switch (entity) {
      case 'tax':
        return field('name', 'Tax Name', input('name', 'text', 'maxlength="100"'), { required: true })
          + field('rate', 'Tax Rate (%)', input('rate', 'text', 'inputmode="decimal"'), { required: true, help: '0 is allowed. Up to 4 decimal places.' })
          + activeField() + notes;
      case 'category1':
        return field('name', 'Name', input('name', 'text', 'maxlength="100"'), { required: true })
          + field('display_order', 'Display Order', input('display_order', 'number', 'min="0" step="1"'))
          + activeField() + notes;
      case 'item_kind':
        // System Code: entered once on create (the IKC number is issued separately) and never changed.
        return (state.editing ? '' : field('system_code', 'System Code', input('system_code', 'text', 'maxlength="30" data-awsum-code'), { required: true, help: 'Program logic key (A-Z, 0-9, _). Cannot be changed later. Not selectable on MD Master until its program logic is defined.' }))
          + field('name', 'Display Name', input('name', 'text', 'maxlength="100"'), { required: true })
          + field('display_order', 'Display Order', input('display_order', 'number', 'min="0" step="1"'))
          + activeField() + notes;
      case 'location': {
        const o = state.options || {};
        // Fixed after create (Category III / MD Master keep Location Codes as text): Edit shows it read-only.
        const example = '<button type="button" class="btn btn-link btn-sm p-0 mg-example-link" data-location-example>View Example</button>';
        if (!state.editing) return locationRangeHtml(o, example); // new locations: one range form
        const code = field('code', 'Location Code', `<div class="form-control-plaintext mg-code fw-semibold py-1" id="mgf_code_view">${esc(state.editing.code)}</div>`,
          { labelAction: example, help: 'Cannot be changed after it is saved.' });
        return code
          + field('name', 'Location Name', input('name', 'text', 'maxlength="100"'), { help: 'Optional. Empty = the Location Code.' })
          + field('location_area', 'Area', AwsumOptionGroup.html({
            id: 'mgf_location_area', name: 'location_area', ariaLabel: 'Area', required: true,
            options: (o.location_areas || []).map((a) => ({ value: a.code, label: a.name })),
          }), { required: true })
          + field('display_order', 'Display Order', input('display_order', 'number', 'min="0" step="1"'))
          + activeField() + notes;
      }
      case 'common_code': {
        const g = currentGroup() || {};
        // MANUAL codes are typed once on create (upper case) and never change; AUTO codes are issued by the server.
        const code = !state.editing && g.code_mode !== 'AUTO'
          ? field('code', 'Code', input('code', 'text', `maxlength="${g.code_max_length || 30}" data-awsum-code`), { required: true, help: 'A-Z, 0-9, _ and -. Saved in upper case. Cannot be changed later.' })
          : '';
        return code
          + field('name', 'Name', input('name', 'text', 'maxlength="100"'), { required: true, help: 'Unique in this group (case-insensitive).' })
          + field('description', 'Description', input('description', 'text', 'maxlength="200"'))
          + commonValueField(g)
          + field('display_order', 'Display Order', input('display_order', 'number', 'min="0" step="1"'))
          + activeField() + notes;
      }
      case 'category2':
        return field('category1_id', 'Category I', select('category1_id'), { required: true })
          + field('name', 'Name', input('name', 'text', 'maxlength="100"'), { required: true })
          + field('display_order', 'Display Order', input('display_order', 'number', 'min="0" step="1"'))
          + activeField() + notes;
      case 'category3':
        return field('category1_id', 'Category I', select('category1_id'), { required: true })
          + field('category2_id', 'Category II', select('category2_id'), { required: true })
          + field('name', 'Name', input('name', 'text', 'maxlength="100"'), { required: true })
          + [1, 2, 3].map((n) => field(`tax${n}_id`, `Tax ${n}`, select(`tax${n}_id`))).join('')
          + inventoryFieldsHtml(false, ['col-12', 'col-12'])
          + LOCATION_FIELDS.map(([key, text]) => field(key, text, locationInput(key, text, { clear: true }),
            { col: 'col-md-6', feature: 'location', help: 'Default for MD Masters in all Stores. Clear = Not set. Stock is not affected.' })).join('')
          + field('display_order', 'Display Order', input('display_order', 'number', 'min="0" step="1"'))
          + activeField() + notes;
      default:
        return mdFormHtml();
    }
  }

  // Common Maintenance Value input, chosen by the group's Value Type (one common structure, no per-group screens):
  // TEXT = text, NUMBER = Common decimal input (AwsumCodeInput decimal mode), CURRENCY = AwsumMoneyInput,
  // COLOR = HEX with swatch preview, NULL = no Value field. The server validates the same group rules.
  function commonValueField(g) {
    const label = esc(g.value_label || 'Value');
    const opts = { required: !!g.value_required };
    const range = [g.value_min != null ? `min ${g.value_min}` : '', g.value_max != null ? `max ${g.value_max}` : ''].filter(Boolean).join(', ');
    switch (g.value_type) {
      case 'TEXT':
        return field('value', label, input('value', 'text', `maxlength="${g.text_max_length || 200}"`), opts);
      case 'NUMBER':
        return field('value', label, input('value', 'text', `inputmode="decimal" autocomplete="off" data-awsum-decimal data-decimal-scale="${g.value_scale || 0}"${Number(g.value_min) < 0 ? ' data-decimal-signed' : ''}`),
          { ...opts, help: `Up to ${g.value_scale || 0} decimal place(s)${range ? ` (${range})` : ''}.` });
      case 'CURRENCY':
        return field('value', label, `<div class="input-group">${MONEY_KEYPAD_BUTTON}${input('value', 'text', `data-awsum-money data-money-min="${g.value_min != null ? esc(g.value_min) : '0'}"${g.value_max != null ? ` data-money-max="${esc(g.value_max)}"` : ''} inputmode="decimal" autocomplete="off"`)}</div>`, opts);
      case 'COLOR':
        return field('value', label, `<div class="input-group"><span class="input-group-text" id="mgValueSwatch"></span>${input('value', 'text', 'maxlength="7" placeholder="#RRGGBB" autocomplete="off" spellcheck="false"')}</div>`,
          { ...opts, help: opts.required ? 'e.g. #1F2A44.' : 'Optional, e.g. #1F2A44.' });
      default:
        return '';
    }
  }
  function refreshValueSwatch() {
    if ($('mgValueSwatch') && f('value')) $('mgValueSwatch').innerHTML = swatch(f('value').value.trim()) || '&nbsp;';
  }

  // Inventory Type / Keep (Common Option Group): value = Inventory Type number / Keep code, label = server name.
  // '' is stored as NULL: MD inherits Category III, Category III falls back to the system default (None / Dry).
  // The Common Reset/Clear button returns to that state; the value then in effect is marked by setInherited.
  function inventoryFieldsHtml(inherit, cols) {
    const o = state.options || {};
    return field('inventory_type', 'Inventory Type', AwsumOptionGroup.html({
      id: 'mgf_inventory_type', name: 'inventory_type', ariaLabel: 'Inventory Type', clearable: true, inherits: inherit,
      options: (o.inventory_types || []).map((t) => ({ value: t.type_id, label: t.name })),
    }), { col: cols[0] })
      + field('keep', 'Keep <span class="pm-req d-none" id="mgKeepReq" aria-hidden="true">*</span>', AwsumOptionGroup.html({
        id: 'mgf_keep', name: 'keep', ariaLabel: 'Keep', clearable: true, inherits: inherit,
        options: (o.keep_types || []).map((k) => ({ value: k.code, label: k.name })),
      }), { col: cols[1] });
  }

  // MD Master form: Classification / Item Information / Pricing / Options. Field names are unchanged.
  function mdFormHtml() {
    // attrs: optional extra attributes on the Common Section (e.g. id, data-item-feature).
    const section = (heading, body, hint, attrs) => `<section class="col-12 pm-section"${attrs ? ` ${attrs}` : ''}>
      ${heading ? `<div class="pm-section-head"><h3>${heading}</h3>${hint ? `<span>${hint}</span>` : ''}</div>` : ''}
      <div class="row g-3">${body}</div></section>`;
    const classification = field('category1_id', 'Category I', select('category1_id'), { required: true, col: 'col-md-4' })
      + field('category2_id', 'Category II', select('category2_id'), { col: 'col-md-4' })
      + field('category3_id', 'Category III', select('category3_id'), { col: 'col-md-4' })
      + input('default_tax_id', 'hidden')
      + field('category_tax_display', 'Tax', '<div id="mgf_category_tax_display" class="form-control-plaintext" aria-live="polite"></div>', { col: 'col-md-6 mg-inline-field' });
    // Barcode / PK/Size / Size have no name: they are saved to the Default Detail, not the MD payload.
    const detailInput = (key, extra) => `<input class="form-control" id="mgf_default_${key}" type="text" maxlength="100" autocomplete="off" ${extra || ''}>`;
    const code = field('record_code', 'Code', '<input class="form-control mg-record-code-input" id="mgf_record_code" type="text" readonly tabindex="-1" placeholder="Auto">', { col: 'col-md-3' })
      + field('active', 'Status', select('active'), { col: 'col-md-3 ms-auto' })
      + field('item_kind', 'Item Kind', select('item_kind'), { required: true, col: 'col-md-3' });
    // Color is the Default Detail's Color (no name: saved to the Default Detail, never to the MD Master).
    const info = field('name', 'Item Name', input('name', 'text', 'maxlength="200"'), { required: true, col: 'col-md-5' })
      + field('second_name', 'Second Name', input('second_name', 'text', 'maxlength="200"'), { col: 'col-md' })
      + field('default_color', 'Color', '<select class="form-select" id="mgf_default_color"></select>', { col: 'col-md-3', feature: 'color' })
      + '<div class="w-100" aria-hidden="true"></div>'
      + field('default_barcode', 'Barcode', detailInput('barcode', 'data-awsum-barcode'), { col: 'col-6 col-md-3' })
      + basePriceHtml(detailInput);
    // Description sits in the GENERAL tab after MD Detail and Options (same field; value/save unchanged).
    const description = field('description', 'Description', textarea('description'), { col: 'col-12' });
    const license = field('billing_cycle', 'Billing Cycle', select('billing_cycle'), { required: true, col: 'col-md-4', feature: 'platform_md' })
      + field('license_required', 'License Required', select('license_required'), { col: 'col-md-4', feature: 'platform_md' })
      + field('license_type', 'License Type', select('license_type'), { required: true, col: 'col-md-4' })
      + field('licenses_per_unit', 'Licenses per Unit', input('licenses_per_unit', 'number', 'min="1" step="1"'), { required: true, col: 'col-md-4' })
      + field('serial_required', 'Serial Number Required', select('serial_required'), { col: 'col-md-6' });
    // Notes is not shown on the MD form; the hidden input keeps the stored value so Save does not clear it.
    // Output Device #1-#8 (Common Option Group, multiple): none selected = not output. Value is a list of numbers.
    // Location (Location Management): its own Common Section right under Keep. Empty = inherit Category III.
    // Reset = the Common underlined text link of Inventory Type / Keep (.awsum-option-clear), next to the source caption.
    const location = LOCATION_FIELDS.map(([key, text]) => field(key, text, locationInput(key, text), { // Reset, no Clear
      col: 'col-12 col-md-6',
      help: `<span id="mgLocSource_${key}"></span><button type="button" class="awsum-option-clear" data-loc-reset="${key}" aria-label="Reset ${text}">Reset</button>`,
    })).join('');
    const options = inventoryFieldsHtml(true, ['col-12', 'col-12 col-md-5']);
    const outputs = field('output_devices', 'Output Device', AwsumOptionGroup.html({
      id: 'mgf_output_devices', name: 'output_devices', ariaLabel: 'Output Device', multiple: true, clearable: true,
      options: [1, 2, 3, 4, 5, 6, 7, 8].map((n) => ({ value: n, label: `#${n}` })),
    }), { col: 'col-12', feature: 'output_device' })
      + input('notes', 'hidden');
    const details = `<section class="col-12 pm-section" id="mgDetailSection">
          <div class="pm-section-head d-flex justify-content-between align-items-center">
            <div><h3>MD Detail</h3><span>Barcode, packaging, cost and price by detail.</span></div>
            <button type="button" class="btn btn-sm btn-outline-primary" id="mgDetailAdd" disabled>Add Detail</button>
          </div>
          <div class="alert alert-danger py-2 d-none" id="mgDetailMessage" role="alert"></div>
          <div class="table-responsive">
            <table class="table table-sm table-hover align-middle mb-2">
              <thead><tr><th>Default</th><th>Barcode</th><th data-item-feature="color">Color</th><th>Unit</th><th>PK/Size</th><th>Size</th>${detailCostOn() ? '<th>Cost</th>' : ''}<th>Price</th><th data-item-feature="modifier">Modifier</th><th class="text-end">Actions</th></tr></thead>
              <tbody id="mgDetailBody"><tr><td colspan="9" class="text-center text-muted">Save the MD Master before adding details.</td></tr></tbody>
            </table>
          </div>
          <div class="border rounded p-3 d-none" id="mgDetailEditor">
            <div class="row g-2">
              ${[['barcode', 'Barcode', 'text'], ['color', 'Color', 'select'], ['unit', 'Unit', 'select'], ['pkSize', 'PK/Size', 'text'], ['size', 'Size', 'text'], ['cost', 'Cost', 'money'], ['price', 'Price', 'money'], ['modifier', 'Modifier', 'text']].map(([key, text, type]) => type === 'select'
                ? `<div class="col-6 col-md-3"${key === 'color' ? ' data-item-feature="color"' : ''}><label class="form-label" for="mgDetail${key[0].toUpperCase()}${key.slice(1)}">${text}</label><select id="mgDetail${key[0].toUpperCase()}${key.slice(1)}" class="form-select"${key === 'unit' ? ' required' : ''}></select></div>` :
                `<div class="${key === 'modifier' ? 'col-12 col-md-6' : 'col-6 col-md-3'}"${key === 'modifier' ? ' data-item-feature="modifier"' : ''}><label class="form-label" for="mgDetail${key[0].toUpperCase()}${key.slice(1)}">${text}</label>${type === 'money' ? `<div class="input-group">${MONEY_KEYPAD_BUTTON}` : ''}<input id="mgDetail${key[0].toUpperCase()}${key.slice(1)}" class="form-control" type="${type === 'money' ? 'text' : type}" ${type === 'money' ? 'data-awsum-money data-money-min="0" inputmode="decimal" autocomplete="off"' : ''} ${['unit', 'price'].includes(key) ? 'required' : ''} ${key === 'barcode' ? 'maxlength="100" data-awsum-barcode' : ''}>${type === 'money' ? '</div>' : ''}</div>`
              ).join('')}
              <div class="col-6 col-md-3 d-flex align-items-end"><label class="form-check mb-2"><input class="form-check-input" id="mgDetailDefault" type="checkbox"> Default Detail</label></div>
            </div>
            <div class="d-flex justify-content-end gap-2 mt-3">
              <button type="button" class="btn btn-sm btn-outline-secondary" id="mgDetailCancel">Cancel</button>
              <button type="button" class="btn btn-sm btn-primary" id="mgDetailSave">Save Detail</button>
            </div>
          </div>
        </section>`;
    return `<div class="col-12 mg-md-top">
          <div class="mg-md-fields">${section('', code)}${section('', classification)}${section('', info)}</div>
          <div id="mgImageUploader" class="mg-image-panel"></div>
        </div>
        <div class="col-12 mg-md-pageframe">
          <ul class="nav nav-tabs" id="mgMdTabs" role="tablist">
            <li class="nav-item" role="presentation">
              <button class="nav-link active" id="mgMdTabGeneral" data-bs-toggle="tab" data-bs-target="#mgMdPaneGeneral" type="button" role="tab" aria-controls="mgMdPaneGeneral" aria-selected="true">GENERAL</button>
            </li>
            <li class="nav-item" role="presentation" data-item-feature="platform_md">
              <button class="nav-link" id="mgMdTabOption1" data-bs-toggle="tab" data-bs-target="#mgMdPaneOption1" type="button" role="tab" aria-controls="mgMdPaneOption1" aria-selected="false">PRICE LEVEL</button>
            </li>
            <li class="nav-item" role="presentation">
              <button class="nav-link" id="mgMdTabOption2" data-bs-toggle="tab" data-bs-target="#mgMdPaneOption2" type="button" role="tab" aria-controls="mgMdPaneOption2" aria-selected="false">${featureOn('platform_md') ? 'LICENSE' : 'SERIAL'}</button>
            </li>
          </ul>
          <div class="tab-content">
            <div class="tab-pane fade show active" id="mgMdPaneGeneral" role="tabpanel" aria-labelledby="mgMdTabGeneral" tabindex="0">
              <div class="row g-3">${details}${section('Options', options)}${section('Location', location, 'Recommended position for all Stores. Empty = Category III default. Stock is not affected.', 'id="mgLocSection" data-item-feature="location"')}${section('', outputs)}${section('', description)}</div>
            </div>
            <div class="tab-pane fade" id="mgMdPaneOption1" role="tabpanel" aria-labelledby="mgMdTabOption1" tabindex="0" data-item-feature="platform_md">
              <div class="row g-3">${section('Price Level', priceLevelsHtml(), 'USD &middot; all prices are before tax')}</div>
            </div>
            <div class="tab-pane fade" id="mgMdPaneOption2" role="tabpanel" aria-labelledby="mgMdTabOption2" tabindex="0">
              <div class="row g-3">${section(featureOn('platform_md') ? 'License' : 'Serial Number', license)}</div>
            </div>
          </div>
        </div>`;
  }
  const f = (name) => $('mgf_' + name);
  const setOptions = (name, opts, selected, blank) => fillSelect(f(name), opts, selected, blank);
  function fillSelect(el, opts, selected, blank) {
    if (!el) return;
    el.innerHTML = (blank === undefined ? '' : `<option value="">${esc(blank)}</option>`)
      + opts.map(([v, t]) => `<option value="${esc(v)}">${esc(t)}</option>`).join('');
    el.value = selected == null ? '' : String(selected);
  }
  // MD Color = COLOR codes from Common Maintenance (MD Detail color_code_id): active ones, plus the saved one so
  // an inactive Color stays selected (and is kept) on edit.
  const colorById = (id) => ((state.options && state.options.color_codes) || []).find((c) => c.id === Number(id));
  const colorOpts = (keep) => ((state.options && state.options.color_codes) || [])
    .filter((c) => c.active || (keep != null && c.id === Number(keep))).map((c) => [c.id, c.active ? c.name : `${c.name} (Inactive)`]);
  const fillColorSelect = (el, selected) => fillSelect(el, colorOpts(selected), selected, 'None');
  // Common UNIT selector, shared by MD Master and MD Detail (UNIT codes from Common Maintenance, "EA · Each"):
  // active codes, plus the saved value when it is inactive or not in the UNIT list, so it is shown and kept.
  // No UNIT list (Common tables not migrated): only the saved / default value, as before.
  const DEFAULT_UNIT = 'EA';
  const unitOpts = (keep) => {
    const units = (state.options && state.options.units) || [];
    const opts = units.filter((u) => u.active || u.code === keep)
      .map((u) => [u.code, `${u.code} \u00b7 ${u.name}${u.active ? '' : ' (Inactive)'}`]);
    if (keep && !units.some((u) => u.code === keep)) opts.push([keep, units.length ? `${keep} (not in Unit list)` : keep]);
    return opts;
  };
  const fillUnitSelect = (el, selected) => {
    const value = selected == null || selected === '' ? DEFAULT_UNIT : String(selected);
    fillSelect(el, unitOpts(value), value);
  };
  const colorCell = (id) => { const c = colorById(id); return c ? swatch(c.swatch_hex) + esc(c.name) : ''; };

  // Active parents, plus the currently saved parent so existing records stay editable.
  const categoryLabel = (category) => ['category3', 'md'].includes(entity) ? category.name : `${category.name} (${category.code})`;
  const cat1Opts = (keep) => state.options.category1.filter((c) => c.active || c.id === keep).map((c) => [c.id, categoryLabel(c)]);
  const cat2Opts = (c1, keep) => state.options.category2.filter((c) => c.category1_id === Number(c1) && (c.active || c.id === keep)).map((c) => [c.id, categoryLabel(c)]);
  const cat3Opts = (c2, keep) => state.options.category3.filter((c) => c.category2_id === Number(c2) && (c.active || c.id === keep)).map((c) => [c.id, categoryLabel(c)]);

  function refreshCategory2(selected) {
    if (!f('category2_id')) return;
    const c1 = f('category1_id').value;
    if (entity === 'category3') {
      setOptions('category2_id', c1 ? cat2Opts(c1, selected) : [], selected, c1 ? 'Select Category II' : 'Select Category I first');
    } else {
      setOptions('category2_id', c1 ? cat2Opts(c1, selected) : [], selected, c1 ? 'None' : 'Select Category I first');
    }
    f('category2_id').disabled = !c1;
    if (entity === 'md') refreshCategory3(null);
  }
  function refreshCategory3(selected) {
    if (!f('category3_id')) return;
    const c2 = f('category2_id').value;
    setOptions('category3_id', c2 ? cat3Opts(c2, selected) : [], selected, c2 ? 'None' : 'Select Category II first');
    f('category3_id').disabled = !c2;
    refreshCategoryTaxDisplay();
  }
  function refreshCategoryTaxDisplay() {
    const display = f('category_tax_display');
    if (!display) return;
    const category = state.options.category3.find((c) => c.id === Number(f('category3_id').value));
    display.textContent = [1, 2, 3].map((n) => {
      const tax = state.options.taxes.find((t) => t.id === category?.[`tax${n}_id`]);
      return taxLabel(tax);
    }).filter(Boolean).join(' \u00b7 ') || 'None';
    refreshInheritLabels();
  }
  const inventoryTypeOf = (id) => ((state.options && state.options.inventory_types) || []).find((t) => t.type_id === Number(id));
  const selectedCategory3 = () => (entity === 'md' && f('category3_id') && state.options
    ? state.options.category3.find((c) => c.id === Number(f('category3_id').value)) : null);
  // Without an override, mark the value in effect: MD -> Category III, else system default (None / Dry).
  // MD Location: the Category III default shows as the placeholder; a typed value is the Override.
  function refreshLocationInherit() {
    if (entity !== 'md' || !f('sales_location')) return;
    const c3 = selectedCategory3();
    LOCATION_FIELDS.forEach(([key]) => {
      const inherited = c3 && c3[key] ? c3[key] : '';
      const own = f(key).value.trim();
      f(key).placeholder = inherited ? `${inherited} (Category III)` : 'Not set';
      $(`mgLocSource_${key}`).textContent = own ? 'Override' : (inherited ? 'Inherited from Category III' : 'Not set');
      $('mgFields').querySelector(`[data-loc-reset="${key}"]`).classList.toggle('d-none', !own);
    });
  }
  function refreshInheritLabels() {
    refreshLocationInherit();
    if (!f('inventory_type') || !state.options) return;
    const o = state.options, c3 = selectedCategory3();
    const mark = (group, fromCategory, fallback) => AwsumOptionGroup.setInherited(group,
      fromCategory != null && fromCategory !== '' ? fromCategory : fallback,
      fromCategory != null && fromCategory !== '' ? 'Inherited from Category III' : 'System default');
    mark(f('inventory_type'), c3 && c3.inventory_type, o.default_inventory_type);
    mark(f('keep'), c3 && c3.keep, o.default_keep);
    refreshKeepRequired();
  }
  // Keep requirement comes from the Common Inventory behavior (keep_required) of the effective
  // Inventory Type (MD override, else Category III, else default), never from a type number.
  function refreshKeepRequired() {
    const group = f('keep');
    if (!group) return;
    const c3 = selectedCategory3();
    const typeId = AwsumOptionGroup.getValue(f('inventory_type')) || (c3 && c3.inventory_type) || state.options.default_inventory_type;
    const behavior = inventoryTypeOf(typeId);
    const required = !!(behavior && behavior.keep_required);
    AwsumOptionGroup.setRequired(group, required);
    $('mgKeepReq').classList.toggle('d-none', !required);
  }
  function refreshLicenseFields() {
    if (entity !== 'md') return;
    const on = f('license_required').value === 'true' && featureOn('platform_md'); // Merchant MD: never shown
    ['license_type', 'licenses_per_unit'].forEach((n) => {
      f(n).disabled = !on;
      f(n).closest('[data-field-wrap]').classList.toggle('d-none', !on);
    });
  }

  // Unit / Price stay MD fields (the backend mirrors them to the Default Detail); Cost is edited only in Detail.
  function basePriceHtml(detailInput) {
    const canCost = !!(state.options && state.options.can_view_cost);
    return field('unit', 'Unit', select('unit'), { required: true, col: 'col-6 col-md-2' })
      + field('default_pk_size', 'PK/Size', detailInput('pk_size'), { col: 'col-6 col-md-2' })
      + field('default_size', 'Size', detailInput('size'), { col: 'col-6 col-md-2' })
      + field('retail_price', 'Price', `<div class="input-group">${MONEY_KEYPAD_BUTTON}${input('retail_price', 'text', 'data-awsum-money data-money-min="0" required inputmode="decimal" autocomplete="off"')}</div>`, { required: true, col: 'col-6 col-md-3' })
      + (canCost ? input('cost', 'hidden') : '');
  }
  function priceLevelsHtml() {
    let html = '<div class="col-12"><div class="mg-price-table" role="table" aria-label="Agency price levels">'
      + '<div class="mg-price-row mg-price-head" role="row"><div>Price Level</div><div>Pricing Mode</div><div>Discount %</div><div>Price</div></div>';
    [1, 2, 3].forEach((n) => {
      html += `<div class="mg-price-row" role="row" data-level="${n}">
        <div class="mg-level" data-label="Price Level">P${n}</div>
        <div data-label="Pricing Mode" data-field-wrap="p${n}_mode">${select(`p${n}_mode`, `aria-label="P${n} pricing mode"`)}<div class="invalid-feedback" data-error-for="p${n}_mode"></div></div>
        <div data-label="Discount %" data-field-wrap="p${n}_discount_pct"><div class="input-group">${input(`p${n}_discount_pct`, 'text', `inputmode="decimal" aria-label="P${n} discount percent" autocomplete="off"`)}<span class="input-group-text">%</span></div><div class="invalid-feedback" data-error-for="p${n}_discount_pct"></div></div>
        <div data-label="Price" data-field-wrap="p${n}_price"><div class="input-group">${MONEY_KEYPAD_BUTTON}${input(`p${n}_price`, 'text', `data-awsum-money data-money-min="0" inputmode="decimal" aria-label="P${n} price" autocomplete="off"`)}</div><div class="invalid-feedback" data-error-for="p${n}_price"></div></div>
      </div>`;
    });
    html += '</div><div class="form-text">Discount mode: Price = Retail Price &times; (1 &minus; Discount % &divide; 100), rounded to cents. Not set is different from $0.00.</div></div>';
    return html;
  }
  // Keeps each level's inputs consistent with its mode; DISCOUNT prices are derived and read-only.
  function refreshPricing() {
    if (entity !== 'md') return;
    [1, 2, 3].forEach((n) => {
      const mode = f(`p${n}_mode`).value;
      const pct = f(`p${n}_discount_pct`);
      const price = f(`p${n}_price`);
      pct.disabled = mode !== 'DISCOUNT';
      price.disabled = mode === '';
      price.readOnly = mode === 'DISCOUNT';
      if (mode === '') { pct.value = ''; price.value = ''; }
      if (mode === 'FIXED') pct.value = '';
      if (mode === 'DISCOUNT') {
        const retail = AwsumMoneyInput.validate(f('retail_price')) ? AwsumMoneyInput.normalize(f('retail_price').value) : '';
        AwsumMoneyInput.setValue(price, discountPrice(retail, pct.value) ?? '');
      }
      AwsumMoneyInput.validate(price);
    });
  }

  function fillForm(row) {
    const o = state.options;
    const editing = !!row;
    const val = (k, d) => (editing && row[k] != null ? row[k] : d);

    if (f('name')) f('name').value = val('name', '');
    if (f('rate')) f('rate').value = val('rate', '');
    if (groupScoped) {
      if (f('description')) f('description').value = val('description', '');
      if (f('value')) {
        if (f('value').hasAttribute('data-awsum-money')) AwsumMoneyInput.setValue(f('value'), val('value', ''));
        else f('value').value = val('value', '');
      }
      refreshValueSwatch();
    }
    LOCATION_FIELDS.forEach(([key]) => { if (f(key)) f(key).value = val(key, ''); });
    syncLocationPickers();
    if (entity === 'location') {
      if (f('code')) f('code').value = val('code', ''); // create only; Edit shows the saved code read-only
      AwsumOptionGroup.setValue(f('location_area'), val('location_area', 'SALES_FLOOR'));
      if (!row && f('zone')) { // range form defaults: one code (S01-B01-C01) until the ranges are widened
        f('zone').value = LOCATION_ZONE_DEFAULTS.SALES_FLOOR;
        Object.values(LOCATION_RANGE_PARTS).flat().forEach(([key]) => { f(`${key}_from`).value = '1'; f(`${key}_to`).value = '1'; });
      }
    }
    if (f('default_color')) fillColorSelect(f('default_color'), null);
    if (f('display_order')) f('display_order').value = val('display_order', 100);
    if (f('notes')) f('notes').value = val('notes', '');
    setOptions('active', [['true', 'Active'], ['false', 'Inactive']], editing ? String(row.active) : 'true');
    // The default Item Kind always stays active (the server enforces it too).
    if (editing && row.active_locked && row.active) f('active').disabled = true;

    if (f('category1_id')) {
      setOptions('category1_id', cat1Opts(row && row.category1_id), row && row.category1_id, 'Select Category I');
      refreshCategory2(row && row.category2_id);
      if (entity === 'md') refreshCategory3(row && row.category3_id);
    }
    if (entity === 'category3') {
      [1, 2, 3].forEach((n) => {
        const key = `tax${n}_id`;
        setOptions(key,
          o.taxes.filter((t) => t.active || t.id === (row && row[key]))
            .map((t) => [t.id, taxLabel(t)]),
          row && row[key], 'None');
      });
    }
    if (f('inventory_type')) {
      // NULL -> '' (nothing selected): MD = inherit Category III, Category III = system default.
      AwsumOptionGroup.setValue(f('inventory_type'), row && row.inventory_type != null ? row.inventory_type : '');
      AwsumOptionGroup.setValue(f('keep'), (row && row.keep) || '');
      refreshInheritLabels();
      refreshKeepRequired();
    }
    if (entity === 'md') {
      f('description').value = val('description', '');
      f('second_name').value = val('second_name', '');
      AwsumOptionGroup.setValue(f('output_devices'), (row && row.output_devices) || []);
      fillUnitSelect(f('unit'), val('unit', DEFAULT_UNIT));
      AwsumMoneyInput.setValue(f('retail_price'), val('retail_price', '0.00'));
      if (f('cost')) f('cost').value = val('cost', '');
      [1, 2, 3].forEach((n) => {
        setOptions(`p${n}_mode`, [['', 'Not set'], ['FIXED', 'Fixed Amount'], ['DISCOUNT', 'Discount %']], editing ? (row[`p${n}_mode`] || '') : '');
        f(`p${n}_discount_pct`).value = val(`p${n}_discount_pct`, '');
        AwsumMoneyInput.setValue(f(`p${n}_price`), val(`p${n}_price`, ''));
      });
      refreshPricing();
      f('licenses_per_unit').value = val('licenses_per_unit', 1);
      // Active Item Kind System Master rows; a saved inactive code stays selectable on edit.
      const kinds = (o.item_kinds || []).map((k) => (typeof k === 'string' ? [k, label(k)] : [k.code, k.name]));
      if (editing && row.item_kind && !kinds.some(([k]) => k === row.item_kind)) kinds.push([row.item_kind, label(row.item_kind)]);
      setOptions('item_kind', kinds, editing ? row.item_kind : (o.default_item_kind || (kinds[0] || [])[0]));
      setOptions('billing_cycle', o.billing_cycles.map((k) => [k, label(k)]), val('billing_cycle', 'ONE_TIME'));
      setOptions('license_type', o.license_types.map((k) => [k, label(k)]), row && row.license_type, 'Select type');
      setOptions('license_required', [['false', 'No'], ['true', 'Yes']], String(editing ? row.license_required : false));
      setOptions('serial_required', [['false', 'No'], ['true', 'Yes']], String(editing ? row.serial_required : false));
      // Preserve the legacy stored value; displayed taxes come only from Category III.
      f('default_tax_id').value = val('default_tax_id', '');
      refreshLicenseFields();
    }
  }

  function clearErrors() {
    $('mgFormError').classList.add('d-none');
    $('mgFields').querySelectorAll('.is-invalid').forEach((el) => el.classList.remove('is-invalid'));
    $('mgFields').querySelectorAll('[data-error-for]').forEach((el) => { el.textContent = ''; });
  }
  function showFieldErrors(errors) {
    let first = null;
    Object.entries(errors || {}).forEach(([name, msg]) => {
      const el = f(name);
      const slot = $('mgFields').querySelector(`[data-error-for="${name}"]`);
      if (el) { el.classList.add('is-invalid'); first = first || el; }
      if (slot) slot.textContent = msg;
    });
    if (first) first.focus();
  }

  // Location Master: a new record is saved with 'Save' (other screens keep 'Create').
  const saveLabel = () => (state.editing ? 'Save Changes' : entity === 'location' ? 'Save' : 'Create');

  function discardImage() {
    if (imageUploader) imageUploader.destroy();
    imageUploader = null;
  }

  // The Master-Detail form header reuses the list row actions for the selected record.
  function syncMdActions() {
    if (!split) return;
    const row = state.editing;
    $('mgMdToggle').classList.toggle('d-none', !row || !!row.read_only || (!!row.active_locked && row.active));
    // Common Maintenance: Delete stays visible for a selected code; a protected (EA) or in-use code shows it disabled
    // with the reason (the server refuses both with 409 anyway). Other screens keep hiding it for System rows.
    const commonCode = entity === 'common_code';
    $('mgMdDelete').classList.toggle('d-none', !row || !!row.read_only || (!commonCode && !!row.system));
    if (commonCode) {
      const reason = deleteBlockedReason(row);
      $('mgMdDelete').disabled = !!reason;
      $('mgMdDelete').title = reason;
      $('mgMdDelete').setAttribute('aria-label', reason ? `Delete (not available: ${reason})` : 'Delete');
    }
    if (row) $('mgMdToggle').textContent = row.active ? 'Deactivate' : 'Activate';
    // Page Header buttons follow the form state: nothing selected / new record / existing record.
    // A read-only Common Group (COLOR) has no record actions: the page stays in 'none'.
    if (pageActions) pageActions.setMode(row && !row.read_only ? 'edit' : (formOpen && !row ? 'create' : 'none'));
  }
  const article = (text) => (/^(md|[aeiou])/i.test(text) ? 'an' : 'a');
  function deleteBlockedReason(row) {
    if (!row) return '';
    if (row.protected) return `${row.code} is a protected default code and cannot be deleted.`;
    if (row.in_use) return `${row.code} is in use and cannot be deleted. Deactivate it instead.`;
    return '';
  }
  function resetMdForm() {
    discardImage();
    state.editing = null;
    state.details = [];
    formOpen = false;
    $('mgFields').innerHTML = '';
    $('mgModalTitle').textContent = `Select ${article(title)} ${title}`;
    $('mgModalSub').textContent = `Select a record from the list or add a new ${title}.`;
    $('mgSave').textContent = saveLabel();
    $('mgSave').disabled = true;
    syncMdActions();
    syncLocationGuide();
  }

  function openForm(row) {
    if (saving) return;
    discardImage();
    allowClose = false;
    state.editing = row || null;
    formOpen = true;
    syncMdActions();
    syncLocationGuide();
    if (split) {
      $('mgBody').querySelectorAll('[data-row-select]').forEach((el) => {
        el.classList.toggle('selected', !!row && Number(el.dataset.rowSelect) === row.id);
      });
    }
    $('mgModalTitle').textContent = row ? `Edit ${title}` : `Add ${title}`;
    $('mgModalSub').textContent = row ? `Update this ${title} record. The code stays the same.`
      : entity === 'location' ? 'Choose the Area, Zone and ranges. Existing codes are skipped.' : `Create a new ${title} record. The code is generated automatically.`;
    $('mgSave').textContent = saveLabel();
    $('mgSave').disabled = false;
    $('mgFields').innerHTML = formHtml();
    if (entity === 'md') {
      f('record_code').value = row ? row.code : '';
    } else if (row && entity !== 'location') { // a Location Code is an editable field, not a generated code
      const codeNote = groupScoped ? (currentGroup() && currentGroup().code_mode === 'AUTO' ? 'auto-generated, not editable' : 'cannot be changed')
        : (row.system && !row.system_code ? 'System Code: logic key, not editable' : 'auto-generated, not editable');
      // Item Kind: the program logic key is shown next to the Code when it differs (required System Codes).
      const systemCode = row.system_code && row.system_code !== row.code
        ? ` <span class="text-muted small ms-2">System Code</span> <span class="mg-code fw-semibold">${esc(row.system_code)}</span> <span class="text-muted small">(${row.logic_defined === false ? 'no program logic yet: not selectable on MD Master' : 'program logic key, protected'})</span>` : '';
      $('mgFields').insertAdjacentHTML('afterbegin',
        `<div class="col-12 mg-record-code"><span class="text-muted small">Code</span> <span class="mg-code fw-semibold">${esc(row.code)}</span> <span class="text-muted small">(${codeNote})</span>${systemCode}</div>`);
    }
    fillForm(row);
    AwsumMoneyInput.refreshAll($('mgFields'));
    if (window.AwsumBarcodeInput) AwsumBarcodeInput.bindAll($('mgFields'));
    if (window.AwsumCodeInput) AwsumCodeInput.bindAll($('mgFields')); // Location Codes: upper case while typing
    if (window.AwsumOptionGroup) AwsumOptionGroup.bindAll($('mgFields'));
    applyItemFeatures($('mgFields'));
    if (row && row.read_only) { // read-only Common Group (COLOR): view only; the server refuses writes too
      $('mgFields').querySelectorAll('input, select, textarea, button').forEach((el) => { el.disabled = true; });
      $('mgSave').disabled = true;
      const message = row.read_only_message || (currentGroup() && currentGroup().read_only_message) || 'This group is read-only.';
      $('mgFields').insertAdjacentHTML('afterbegin', `<div class="col-12"><div class="alert alert-info py-1 px-2 mb-0 small" id="mgReadOnlyNote">${esc(message)}</div></div>`);
    }
    clearErrors();
    bindFormEvents();
    if (entity === 'md') {
      imageUploader = AwsumImages.createUploader($('mgImageUploader'), {
        idPrefix: 'mgImage', label: 'Item Image', previewSize: 120, fit: 'contain',
      });
      imageUploader.reset(row && row.image_url, !!(row && row.image_path));
      if (row && row.read_only) $('mgImageUploader').querySelectorAll('input, button').forEach((el) => { el.disabled = true; });
      loadMdDetails(row);
    }
    if (modal) modal.show();
  }

  function detailMessage(text, kind = 'danger') {
    const box = $('mgDetailMessage');
    box.textContent = text;
    box.className = `alert alert-${kind} py-2`;
  }

  function clearDetailMessage() {
    const box = $('mgDetailMessage');
    box.textContent = '';
    box.className = 'alert alert-danger py-2 d-none';
  }

  function renderMdDetails() {
    const body = $('mgDetailBody');
    if (!state.details.length) {
      body.innerHTML = `<tr><td colspan="${7 + (detailCostOn() ? 1 : 0) + (featureOn('color') ? 1 : 0) + (featureOn('modifier') ? 1 : 0)}" class="text-center text-muted">${state.editing ? 'No details found.' : 'Save the MD Master before adding details.'}</td></tr>`;
      return;
    }
    const readOnly = !!(state.editing && state.editing.read_only);
    body.innerHTML = state.details.map((detail) => `<tr>
      <td>${detail.is_default ? '<span class="badge text-bg-primary">Default</span>' : ''}</td>
      <td>${esc(detail.barcode || '')}</td>${featureOn('color') ? `<td>${colorCell(detail.color_code_id)}</td>` : ''}<td>${esc(detail.unit || '')}</td><td>${esc(detail.pk_size || '')}</td>
      <td>${esc(detail.size || '')}</td>${detailCostOn() ? `<td>${money(detail.cost)}</td>` : ''}<td>${money(detail.price)}</td>${featureOn('modifier') ? `<td>${esc(detail.modifier || '')}</td>` : ''}
      <td class="text-end text-nowrap">${readOnly ? '' : `
        <button type="button" class="btn btn-sm btn-outline-primary" data-detail-act="edit" data-id="${detail.id}">Edit</button>
        <button type="button" class="btn btn-sm btn-outline-danger" data-detail-act="delete" data-id="${detail.id}" ${detail.is_default ? 'disabled title="The Default Detail cannot be deleted."' : ''}>Delete</button>`}
      </td></tr>`).join('');
  }
  // MD Detail Cost column: hidden for a Store login (options.detail_cost; the server never sends it there).
  function detailCostOn() { return !(state.options && state.options.detail_cost === false); }

  async function loadMdDetails(row) {
    const token = ++detailLoadToken;
    state.details = [];
    state.mdLocations = null; // links of the previously opened MD must not show
    $('mgDetailAdd').disabled = !row || !!row.read_only;
    $('mgDetailEditor').classList.add('d-none');
    clearDetailMessage();
    renderMdDetails();
    if (!row) return;
    try {
      const data = await api('GET', `${BASE}/md/${row.id}/details`);
      if (token !== detailLoadToken || !state.editing || state.editing.id !== row.id) return;
      state.details = data.items;
      const defaultDetail = state.details.find((detail) => detail.is_default);
      if (defaultDetail) {
        state.editing = {
          ...state.editing,
          unit: defaultDetail.unit,
          retail_price: defaultDetail.price,
          cost: defaultDetail.cost,
        };
        fillUnitSelect(f('unit'), defaultDetail.unit);
        AwsumMoneyInput.setValue(f('retail_price'), defaultDetail.price ?? '');
        if (f('cost')) f('cost').value = defaultDetail.cost ?? '';
        f('default_barcode').value = defaultDetail.barcode || '';
        f('default_pk_size').value = defaultDetail.pk_size || '';
        f('default_size').value = defaultDetail.size || '';
        fillColorSelect(f('default_color'), defaultDetail.color_code_id);
        refreshPricing();
      }
      renderMdDetails();
    } catch (err) {
      if (token === detailLoadToken) detailMessage(err.status === 401 ? 'Your session has expired. Please sign in again.' : err.message);
    }
  }

  function showMdDetailEditor(detail) {
    clearDetailMessage();
    state.editingDetailId = detail ? detail.id : null;
    const values = detail || { barcode: '', unit: DEFAULT_UNIT, pk_size: '', size: '', cost: '', price: '', modifier: '', color_code_id: null, is_default: false };
    fillColorSelect($('mgDetailColor'), values.color_code_id);
    fillUnitSelect($('mgDetailUnit'), values.unit); // same Common UNIT selector as MD Master
    [['Barcode', values.barcode], ['PkSize', values.pk_size], ['Size', values.size],
      ['Cost', values.cost], ['Price', values.price], ['Modifier', values.modifier]].forEach(([name, value]) => {
      const el = $(`mgDetail${name}`);
      if (el.hasAttribute('data-awsum-money')) AwsumMoneyInput.setValue(el, value == null ? '' : value);
      else el.value = value == null ? '' : value;
    });
    $('mgDetailDefault').checked = !!values.is_default;
    $('mgDetailDefault').disabled = !!(detail && detail.is_default);
    $('mgDetailSave').textContent = detail ? 'Save Changes' : 'Add Detail';
    $('mgDetailEditor').classList.remove('d-none');
    // Add Detail starts at Barcode (first field, scanner-ready); Edit Detail keeps focusing Unit.
    $(detail ? 'mgDetailUnit' : 'mgDetailBarcode').focus();
  }

  async function saveMdDetail() {
    if (!state.editing || saving) return;
    const button = $('mgDetailSave');
    const payload = {
      barcode: $('mgDetailBarcode').value.trim(),
      unit: $('mgDetailUnit').value.trim(),
      pk_size: $('mgDetailPkSize').value.trim(),
      size: $('mgDetailSize').value.trim(),
      cost: null,
      price: null,
      modifier: $('mgDetailModifier').value.trim(),
      color_code_id: $('mgDetailColor').value ? Number($('mgDetailColor').value) : null,
      is_default: $('mgDetailDefault').checked,
    };
    if (!featureOn('modifier')) delete payload.modifier; // OFF: the server keeps the saved Modifier
    if (!featureOn('color')) delete payload.color_code_id; // OFF: the server keeps the saved Color
    clearDetailMessage();
    try {
      payload.cost = AwsumMoneyInput.getValue($('mgDetailCost')) || null;
      payload.price = AwsumMoneyInput.getValue($('mgDetailPrice')) || null;
    } catch (err) {
      detailMessage(err.message);
      return;
    }
    button.disabled = true;
    try {
      const detailPath = `${BASE}/md/${state.editing.id}/details`;
      const detailAction = state.editingDetailId ? 'updated' : 'created';
      if (state.editingDetailId) {
        await api('PUT', `${detailPath}/${state.editingDetailId}`, payload);
      } else {
        await api('POST', detailPath, payload);
      }
      $('mgDetailEditor').classList.add('d-none');
      await loadMdDetails(state.editing);
      showMessage(AwsumToast.message(detailAction, 'MD Detail'), 'success');
    } catch (err) {
      detailMessage(err.status === 401 ? 'Your session has expired. Please sign in again.' : err.message);
    } finally {
      button.disabled = false;
    }
  }

  // Saves Barcode / PK/Size / Size / Color to the Default Detail through the existing Detail API.
  // color_code_id is sent only when it is in values (Use Color ON), so OFF keeps the saved Color.
  async function syncDefaultDetail(mdId, values) {
    const data = await api('GET', `${BASE}/md/${mdId}/details`);
    const detail = data.items.find((item) => item.is_default);
    if (!detail || Object.keys(values).every((key) => String(detail[key] ?? '') === String(values[key] ?? ''))) return;
    await api('PUT', `${BASE}/md/${mdId}/details/${detail.id}`, {
      barcode: detail.barcode || '', unit: detail.unit, pk_size: detail.pk_size || '', size: detail.size || '',
      cost: detail.cost, price: detail.price, modifier: detail.modifier || '', is_default: true, ...values,
    });
  }

  async function deleteMdDetail(id) {
    const detail = state.details.find((item) => item.id === id);
    if (!detail || detail.is_default) return;
    if (!window.confirm('Delete this MD Detail? This cannot be undone.')) return;
    clearDetailMessage();
    try {
      await api('DELETE', `${BASE}/md/${state.editing.id}/details/${id}`);
      await loadMdDetails(state.editing);
      showMessage(AwsumToast.message('deleted', 'MD Detail'), 'success');
    } catch (err) {
      detailMessage(err.status === 401 ? 'Your session has expired. Please sign in again.' : err.message);
    }
  }

  function bindFormEvents() {
    const c1 = f('category1_id');
    const c2 = f('category2_id');
    if (c1 && c2) {
      c1.addEventListener('change', () => refreshCategory2(null)); // parent change resets children
    }
    if (c2 && f('category3_id')) c2.addEventListener('change', () => refreshCategory3(null));
    if (f('inventory_type')) f('inventory_type').addEventListener('change', refreshKeepRequired);
    if ($('mgValueSwatch') && f('value')) f('value').addEventListener('input', refreshValueSwatch);
    LOCATION_FIELDS.forEach(([key, text]) => { // Common Location Selector (Category III / MD Master)
      if (!f(key)) return;
      $('mgFields').querySelector(`[data-loc-search="${key}"]`).addEventListener('click', () => pickLocation(key, text));
      const clear = $('mgFields').querySelector(`[data-loc-clear="${key}"]`);
      if (clear) {
        clear.addEventListener('click', () => { // Category III only: Not set
          setLocationValue(key, '');
          $('mgFields').querySelector(`[data-loc-search="${key}"]`).focus();
        });
      }
      f(key).addEventListener('keydown', (event) => { // the read-only field opens the selector from the keyboard
        if (event.key === 'Enter' || event.key === 'ArrowDown') { event.preventDefault(); pickLocation(key, text); }
      });
    });
    if (rangeMode()) { // range form: Area switches its ranges; any change refreshes the preview
      f('location_area').addEventListener('change', syncLocationRange);
      $('mgFields').querySelectorAll('#mgf_zone, [data-range-area] input').forEach((el) => el.addEventListener('input', scheduleRangePreview));
      syncLocationRange();
    }
    if (entity === 'location') {
      $('mgFields').querySelectorAll('[data-location-example]').forEach((btn) => btn.addEventListener('click', showLocationExample));
    }
    if (entity === 'md') {
      f('category3_id').addEventListener('change', refreshCategoryTaxDisplay);
      f('license_required').addEventListener('change', refreshLicenseFields);
      f('retail_price').addEventListener('input', refreshPricing);
      [1, 2, 3].forEach((n) => {
        f(`p${n}_mode`).addEventListener('change', refreshPricing);
        f(`p${n}_discount_pct`).addEventListener('input', refreshPricing);
      });
      $('mgDetailAdd').addEventListener('click', () => showMdDetailEditor(null));
      $('mgDetailCancel').addEventListener('click', () => $('mgDetailEditor').classList.add('d-none'));
      $('mgDetailSave').addEventListener('click', saveMdDetail);
      LOCATION_FIELDS.forEach(([key]) => {
        f(key).addEventListener('input', refreshLocationInherit);
        $('mgFields').querySelector(`[data-loc-reset="${key}"]`).addEventListener('click', () => {
          f(key).value = ''; // Reset: back to the Category III default (saved as NULL with Save Changes)
          refreshLocationInherit();
          syncLocationPickers();
          $('mgFields').querySelector(`[data-loc-search="${key}"]`).focus();
        });
      });
      $('mgDetailBody').addEventListener('click', (event) => {
        const button = event.target.closest('button[data-detail-act]');
        if (!button) return;
        const detail = state.details.find((item) => item.id === Number(button.dataset.id));
        if (!detail) return;
        if (button.dataset.detailAct === 'edit') showMdDetailEditor(detail);
        if (button.dataset.detailAct === 'delete') deleteMdDetail(detail.id);
      });
    }
  }

  function collect() {
    const payload = {};
    $('mgFields').querySelectorAll('[name]').forEach((el) => {
      if (el.disabled || el.type === 'radio' || el.type === 'checkbox') return; // option groups are read below
      if (el.closest('[data-item-feature-off]')) return; // OFF feature: keep the saved value
      payload[el.name] = el.hasAttribute('data-awsum-money') ? AwsumMoneyInput.getValue(el) : el.value;
    });
    $('mgFields').querySelectorAll('[data-awsum-option-group]').forEach((group) => {
      if (group.closest('[data-item-feature-off]')) return; // OFF feature: keep the saved value
      payload[group.dataset.name] = AwsumOptionGroup.getValue(group);
    });
    ['active', 'license_required', 'serial_required'].forEach((k) => {
      if (k in payload) payload[k] = payload[k] === 'true';
    });
    return payload;
  }

  // Category III Inventory Type change with MD items: ask the scope (Common Confirm Modal).
  // Returns the payload flag to add, {} for a plain save, or null when the user cancels (nothing is saved).
  const inventoryTypeLabel = (id) => {
    const o = state.options || {};
    if (id == null || id === '') {
      const fallback = inventoryTypeOf(o.default_inventory_type);
      return `Not set (System default: ${fallback ? fallback.name : 'None'})`;
    }
    const type = inventoryTypeOf(id);
    return type ? type.name : String(id);
  };
  async function confirmCategory3InventoryScope(editing, body) {
    if (entity !== 'category3' || !editing) return {};
    const before = editing.inventory_type == null ? '' : String(editing.inventory_type);
    const after = body.inventory_type == null ? '' : String(body.inventory_type);
    if (before === after) return {};
    const impact = await api('GET', `${BASE}/category3/${editing.id}/inventory-impact`);
    if (!impact.can_apply_to_md || !impact.md_total) return {};
    const result = await AwsumConfirm.show({
      title: 'Inventory Type Change',
      message: 'Choose where to apply the new Inventory Type.',
      variant: 'warning',
      details: [
        ['Category III', impact.name],
        ['Inventory Type', `${inventoryTypeLabel(before)} \u2192 ${inventoryTypeLabel(after)}`],
        ['MD Master items', String(impact.md_total)],
        ['Item overrides', String(impact.override_total)],
      ],
      choices: [
        { value: 'category', label: 'Change Category III only', description: 'Item overrides are kept. Items that inherit follow the new value.' },
        { value: 'all', label: 'Apply to all MD Master items', description: `Clears ${impact.override_total} item override(s) so every item in this Category III inherits the new value.` },
      ],
      value: 'category',
      okText: 'Save Changes',
    });
    if (!result.confirmed) return null;
    return result.choice === 'all' ? { apply_inventory_to_md: true } : {};
  }

  async function save(event) {
    event.preventDefault();
    if (saving) return;
    saving = true;
    const btn = $('mgSave');
    btn.disabled = true;
    btn.textContent = 'Saving...';
    if (imageUploader) imageUploader.setDisabled(true);
    clearErrors();
    let reopen = null; // Common Maintenance: show the saved record (its Code is fixed) after a create
    try {
      let body = collect();
      const editing = state.editing;
      if (rangeMode()) { await saveLocationRange(body); return; } // Location Master: new codes from the range form
      const scope = await confirmCategory3InventoryScope(editing, body);
      if (scope === null) { showMessage('Not saved. No changes were made.', 'info'); return; } // Cancel
      if (editing && editing.active && body.active === false && !(await confirmInUseDeactivate(editing))) {
        showMessage('Not saved. No changes were made.', 'info');
        return;
      }
      Object.assign(body, scope);
      const detailValues = entity === 'md'
        ? Object.fromEntries(['barcode', 'pk_size', 'size'].map((key) => [key, f(`default_${key}`).value.trim()]))
        : null;
      if (detailValues && featureOn('color')) detailValues.color_code_id = f('default_color').value ? Number(f('default_color').value) : null;
      // The Barcode belongs to the Default Detail (saved after the MD), so a duplicate is caught before
      // anything is saved. The Detail API re-checks it on the server and the DB index is the final guard.
      if (entity === 'md' && detailValues.barcode) {
        const params = new URLSearchParams({ barcode: detailValues.barcode });
        if (editing) params.set('md_id', editing.id);
        const check = await api('GET', `${BASE}/md/barcode-check?${params}`);
        if (!check.available) {
          const err = new Error(check.message);
          err.errors = { default_barcode: check.message };
          throw err;
        }
      }
      let path = scoped(`${BASE}/${entity}${editing ? '/' + editing.id : ''}`);
      if (entity === 'md') {
        body = imageUploader.formData(body);
        path += '/with-image';
      }
      const data = editing
        ? await api('PUT', path, body)
        : await api('POST', path, body);
      if (modal) {
        allowClose = true;
        modal.hide();
      } else if (split) {
        state.editing = data.item;
        $('mgModalTitle').textContent = `Edit ${title}`;
        $('mgModalSub').textContent = `Update this ${title} record. The code stays the same.`;
        $('mgSave').textContent = saveLabel();
        syncMdActions();
        // Common Maintenance / Location: reopen the created record (its code is now fixed; Location Name may be defaulted).
        if ((groupScoped || entity === 'location') && !editing) reopen = data.item;
      }
      if (entity.startsWith('category') || entity === 'tax') await loadOptions();
      const resetNote = data.item.md_inventory_reset != null ? ` ${data.item.md_inventory_reset} MD item override(s) now inherit this Category III.` : '';
      showMessage(data.item.image_warning || `${AwsumToast.message(editing ? 'updated' : 'created', data.item.code)}${resetNote}`, data.item.image_warning ? 'warning' : 'success');
      if (entity === 'md') {
        try {
          await syncDefaultDetail(data.item.id, detailValues);
        } catch (err) {
          showMessage(`${data.item.code} saved, but Default Detail was not updated: ${err.message}`, 'warning');
        }
        await loadMdDetails(data.item);
      }
      await load();
    } catch (err) {
      const box = $('mgFormError');
      box.textContent = err.status === 401 ? 'Your session has expired. Please sign in again.' : err.message;
      box.classList.remove('d-none');
      showFieldErrors(err.errors);
    } finally {
      saving = false;
      btn.disabled = split && !formOpen; // closed after a Location range save (resetMdForm): nothing to save
      btn.textContent = saveLabel();
      if (imageUploader) imageUploader.setDisabled(false);
    }
    if (reopen) openForm(state.items.find((r) => r.id === reopen.id) || reopen);
  }

  // Common Maintenance: deactivating a code that MD still uses keeps the saved MD values; warn first (Common Confirm).
  async function confirmInUseDeactivate(row) {
    if (entity === 'location') { // Common Confirm for every Location deactivation
      const result = await AwsumConfirm.show({
        title: row.in_use ? 'Deactivate Location in Use' : 'Deactivate Location',
        message: `Deactivate ${row.code}? It is kept and can be activated again${row.in_use ? '; its links are kept' : ''}.`,
        variant: 'warning',
        details: [['Location', row.code], ['Name', row.name || ''], ['Area', row.location_area_name || '']],
        okText: 'Deactivate',
      });
      return !!result.confirmed;
    }
    if (!groupScoped || !row.in_use) return true;
    const result = await AwsumConfirm.show({
      title: 'Deactivate Code in Use',
      message: `${row.code} is used by MD Master or MD Detail. Saved items keep it, but it will not be offered for new selections.`,
      variant: 'warning',
      details: [['Group', state.group || ''], ['Code', row.code], ['Name', row.name || '']],
      okText: 'Deactivate',
    });
    return !!result.confirmed;
  }

  // ---- row actions ----
  async function rowAction(act, id) {
    const row = state.items.find((r) => r.id === id) || (state.editing && state.editing.id === id ? state.editing : null);
    if (!row) return;
    clearMessage();
    if (act === 'edit') { openForm(row); return; }
    try {
      if (act === 'toggle') {
        if (row.active && !(await confirmInUseDeactivate(row))) return;
        const body = { ...rowToPayload(row), active: !row.active };
        await api('PUT', scoped(`${BASE}/${entity}/${id}`), body);
        showMessage(AwsumToast.message(row.active ? 'deactivated' : 'activated', row.code), 'success');
        if (split && state.editing && state.editing.id === id) {
          state.editing = { ...state.editing, active: !row.active };
          if (f('active')) f('active').value = String(state.editing.active);
          syncMdActions();
        }
      } else if (act === 'delete') {
        if (entity === 'common_code' && deleteBlockedReason(row)) {
          showMessage(deleteBlockedReason(row), 'warning');
          return;
        }
        if (row.system) {
          showMessage(`${row.code} is a System Code and cannot be deleted. Deactivate it instead.`, 'warning');
          return;
        }
        if (row.in_use) {
          showMessage(`${row.code} is in use and cannot be deleted. Deactivate it instead.`, 'warning');
          return;
        }
        if (entity === 'location' || entity === 'common_code') { // Common Confirm (other screens keep the browser confirm for now)
          const result = await AwsumConfirm.show({
            title: entity === 'location' ? 'Delete Location' : 'Delete Code',
            message: `Delete ${row.code}? This cannot be undone.`, variant: 'danger',
            details: entity === 'location'
              ? [['Location', row.code], ['Name', row.name || ''], ['Area', row.location_area_name || '']]
              : [['Group', state.group || ''], ['Code', row.code], ['Name', row.name || '']],
            okText: 'Delete',
          });
          if (!result.confirmed) return;
        } else if (!window.confirm(`Delete ${row.code}? This cannot be undone.`)) return;
        const result = await api('DELETE', scoped(`${BASE}/${entity}/${id}`));
        showMessage(result.message === 'Deleted.' ? AwsumToast.message('deleted', row.code) : result.message, result.message === 'Deleted.' ? 'success' : 'warning');
        if (split && result.message === 'Deleted.' && state.editing && state.editing.id === id) resetMdForm();
      }
      await loadOptions();
      await load();
    } catch (err) {
      showMessage(err.message, 'danger');
    }
  }

  function rowToPayload(r) {
    return {
      name: r.name, rate: r.rate, display_order: r.display_order, notes: r.notes, active: r.active,
      value: r.value,
      code: r.code, location_area: r.location_area,
      sales_location: r.sales_location, warehouse_location: r.warehouse_location,
      category1_id: r.category1_id, category2_id: r.category2_id, category3_id: r.category3_id,
      description: r.description, second_name: r.second_name, item_kind: r.item_kind, unit: r.unit, retail_price: r.retail_price,
      cost: r.cost,
      p1_mode: r.p1_mode, p1_discount_pct: r.p1_discount_pct, p1_price: r.p1_price,
      p2_mode: r.p2_mode, p2_discount_pct: r.p2_discount_pct, p2_price: r.p2_price,
      p3_mode: r.p3_mode, p3_discount_pct: r.p3_discount_pct, p3_price: r.p3_price,
      default_tax_id: r.default_tax_id, billing_cycle: r.billing_cycle,
      inventory_type: r.inventory_type, keep: r.keep, license_required: r.license_required,
      license_type: r.license_type, licenses_per_unit: r.licenses_per_unit, serial_required: r.serial_required,
    };
  }

  // ---- init ----
  function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

  // Exports every row matching the current search / status / sort, not just the visible page.
  async function exportList(format) {
    clearMessage();
    const params = new URLSearchParams({ format, q: state.q, active: state.active, sort: state.sort, direction: state.direction });
    const url = scoped(`${BASE}/${entity}/export?${params}`);
    if (format === 'pdf') {
      if (!window.open(url, '_blank')) showMessage('Please allow pop-ups to export PDF.', 'warning');
      return;
    }
    const btn = $('mgExportBtn');
    btn.disabled = true;
    try {
      const res = await fetch(url, { credentials: 'same-origin', cache: 'no-store' });
      if (!res.ok) {
        let detail = 'Unable to export. Refresh or sign in again.';
        try { const d = await res.json(); if (typeof d.detail === 'string') detail = d.detail; } catch (_) { /* non-JSON */ }
        throw new Error(detail);
      }
      const match = /filename="?([^";]+)"?/.exec(res.headers.get('Content-Disposition') || '');
      const blobUrl = URL.createObjectURL(await res.blob());
      const a = document.createElement('a');
      a.href = blobUrl;
      a.download = match ? match[1] : `master-export.${format === 'excel' ? 'xls' : 'csv'}`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(blobUrl);
    } catch (err) {
      showMessage(err.message, 'danger');
    } finally {
      btn.disabled = false;
    }
  }

  async function init() {
    $('mgSubtitle').textContent = SUBTITLES[entity];
    if (!split) {
      modal = new bootstrap.Modal($('mgModal'));
      $('mgModal').addEventListener('hide.bs.modal', (event) => {
        if (saving && !allowClose) event.preventDefault();
      });
      $('mgModal').addEventListener('hidden.bs.modal', discardImage);
    } else {
      $('mgSave').disabled = true;
      document.querySelectorAll('[data-md-act]').forEach((btn) => {
        btn.addEventListener('click', () => { if (state.editing) rowAction(btn.dataset.mdAct, state.editing.id); });
      });
      // The existing buttons (same nodes and listeners) are arranged by the Common Page Actions.
      // Location Master: Add is hidden while a new record is open, and Cancel closes the form (resetMdForm).
      const locationActions = entity === 'location';
      pageActions = AwsumPageActions.mount($('mgPageActions'), {
        mode: 'none',
        actions: [
          { id: 'mgExportBtn', menu: true, label: 'Export' },
          ...(locationActions ? [{ id: 'mgGuideBtn', pin: true }] : []), // Location Guide: every mode (icon only on phones)
          { id: 'mgAddBtn', pin: (mode) => mode === 'none', ...(locationActions ? { modes: ['none', 'edit'] } : {}) },
          { id: 'mgMdToggle', modes: ['edit'] },
          { id: 'mgMdDelete', modes: ['edit'], danger: true },
          ...(locationActions ? [{ id: 'mgCancelBtn', modes: ['create', 'edit'], pin: true }] : []),
          { id: 'mgSave', modes: ['create', 'edit'], pin: true },
        ],
      });
      if ($('mgCancelBtn')) {
        $('mgCancelBtn').addEventListener('click', () => {
          if (saving) return;
          clearErrors();
          resetMdForm();
          $('mgBody').querySelectorAll('[data-row-select].selected').forEach((el) => el.classList.remove('selected'));
        });
      }
      AwsumMasterDetail.fitWorkspace(document.querySelector('.mg-md-workspace'));
    }
    $('mgBody').addEventListener('error', (event) => {
      if (event.target.tagName === 'IMG') event.target.hidden = true;
    }, true);
    renderHead();
    $('mgAddBtn').addEventListener('click', () => { clearMessage(); openForm(null); });
    if ($('mgGuideBtn')) {
      $('mgGuideBtn').addEventListener('click', showLocationExample);
      $('mgExampleZoom').addEventListener('click', () => setGuideZoom(!$('mgExampleModal').querySelector('.mg-example-scroll').classList.contains('zoomed')));
    }
    if ($('mgGuideAdd')) $('mgGuideAdd').addEventListener('click', () => $('mgAddBtn').click()); // same Add action
    document.querySelectorAll('[data-export-format]').forEach((item) => {
      item.addEventListener('click', () => exportList(item.dataset.exportFormat));
    });
    $('mgForm').addEventListener('submit', save);
    $('mgSearch').addEventListener('input', debounce((e) => { state.q = e.target.value; state.page = 1; load(); }, 250));
    $('mgActiveFilter').addEventListener('change', (e) => { state.active = e.target.value; state.page = 1; load(); });
    $('mgPageSize').addEventListener('change', (e) => { state.size = Number(e.target.value); state.page = 1; load(); });
    $('mgPrev').addEventListener('click', () => { state.page -= 1; load(); });
    $('mgNext').addEventListener('click', () => { state.page += 1; load(); });
    const sortHandler = (e) => {
      const th = e.target.closest('th[data-sort]');
      if (!th) return;
      if (e.type === 'keydown' && e.key !== 'Enter' && e.key !== ' ') return;
      const key = th.dataset.sort;
      state.direction = state.sort === key && state.direction === 'asc' ? 'desc' : 'asc';
      state.sort = key;
      load();
    };
    $('mgHead').addEventListener('click', sortHandler);
    $('mgHead').addEventListener('keydown', sortHandler);
    const selectMdRow = (e) => {
      const btn = e.target.closest('button[data-act]');
      if (btn) {
        rowAction(btn.dataset.act, Number(btn.dataset.id));
        return;
      }
      if (!split || (e.type === 'keydown' && !['Enter', ' '].includes(e.key))) return;
      const group = e.target.closest('tr[data-tree-toggle]');
      if (group) {
        if (e.type === 'keydown') {
          if (e.target !== group) return;
          e.preventDefault();
        }
        toggleTreeGroup(group.dataset.treeToggle);
        return;
      }
      const tr = e.target.closest('[data-row-select]'); // a row (code cell on Location Master)
      if (!tr || (e.type === 'keydown' && e.target !== tr)) return;
      if (e.type === 'keydown') e.preventDefault();
      const row = state.items.find((item) => item.id === Number(tr.dataset.rowSelect));
      if (!row) return;
      openForm(row);
      if (entity === 'md') refreshMdInheritance(row.id);
    };
    // MD values in effect depend on Category III (inheritance) and on bulk changes made elsewhere: after opening,
    // reload Category III values and the saved row, then re-mark inheritance. A saved Inventory Type / Keep that
    // changed elsewhere is taken only while the user has not touched that group.
    let mdOpenToken = 0;
    async function refreshMdInheritance(id) {
      const token = ++mdOpenToken;
      try {
        await loadOptions();
        await load();
      } catch (err) { return; } // keep the loaded data; load() already reports errors
      if (token !== mdOpenToken || !state.editing || state.editing.id !== id || !f('inventory_type')) return;
      const fresh = state.items.find((item) => item.id === id);
      if (fresh) {
        const text = (v) => (v == null ? '' : String(v));
        ['inventory_type', 'keep'].forEach((key) => {
          const group = f(key);
          if (text(fresh[key]) !== text(state.editing[key]) && AwsumOptionGroup.getValue(group) === text(state.editing[key])) {
            AwsumOptionGroup.setValue(group, fresh[key] == null ? '' : fresh[key]);
          }
        });
        state.editing = { ...state.editing, inventory_type: fresh.inventory_type, keep: fresh.keep };
      }
      refreshInheritLabels();
    }
    $('mgBody').addEventListener('click', selectMdRow);
    $('mgBody').addEventListener('keydown', selectMdRow);
    try { await loadOptions(); } catch (err) { showMessage(err.message, 'danger'); }
    if (viewOnly() && !groupScoped) { // Common Maintenance keeps its group rule (setupGroupSelect)
      $('mgAddBtn').disabled = true;
      $('mgAddBtn').title = 'Managed by your Merchant. Store logins can view it only.';
    }
    if (storeScoped) setupStoreSelect();
    if (groupScoped) setupGroupSelect();
    await load();
  }

  // ---- Common Maintenance group selector (same pattern as the Store selector) ----
  function setupGroupSelect() {
    const groups = (state.options && state.options.common_groups) || [];
    const select = $('mgGroup');
    select.innerHTML = groups.length
      ? groups.map((g) => `<option value="${esc(g.code)}">${esc(g.code)} \u00b7 ${esc(g.name)}${g.read_only ? ' (view only)' : ''}</option>`).join('')
      : '<option value="">No Common Group available</option>';
    // ?group=COLOR (e.g. a link from another screen) pre-selects that group; the server checks it on every request.
    const requested = String(new URLSearchParams(window.location.search).get('group') || '').toUpperCase();
    const first = groups.find((g) => g.code === requested) || groups[0];
    state.group = first ? first.code : null;
    select.value = state.group || '';
    select.addEventListener('change', () => {
      state.group = select.value || null;
      state.page = 1;
      state.sort = '';
      resetMdForm();
      clearMessage();
      syncGroupActions();
      load();
    });
    syncGroupActions();
  }
  function syncGroupActions() {
    const g = currentGroup();
    $('mgAddBtn').disabled = !g || !!g.read_only;
    $('mgGroupNote').textContent = g && g.read_only ? (g.read_only_message || 'This group is read-only.') : (g && g.description) || '';
    $('mgGroupNote').classList.toggle('d-none', !$('mgGroupNote').textContent);
    try { // keep ?group= in the address so a reload or a shared link opens the same group
      const url = new URL(window.location.href);
      if (state.group) url.searchParams.set('group', state.group); else url.searchParams.delete('group');
      window.history.replaceState(null, '', url);
    } catch (_) { /* history not available */ }
  }

  // Store selector (Platform: active Stores + inactive ones with Location data; Merchant: its Stores).
  // A Store login is fixed to its own Store, so the selector is hidden.
  function storeLabel(store) {
    return [store.code, store.name].filter(Boolean).join(' \u00b7 ')
      + (store.merchant_name && !(state.options && state.options.store_fixed) ? ` (${store.merchant_name})` : '')
      + (store.active ? '' : ' [Inactive]');
  }
  function setupStoreSelect() {
    const o = state.options || {};
    const stores = o.stores || [];
    const select = $('mgStore');
    if (o.store_fixed) {
      state.storeId = o.store_fixed;
      $('mgStoreWrap').classList.add('d-none');
    } else {
      select.innerHTML = stores.length
        ? stores.map((store) => `<option value="${store.id}">${esc(storeLabel(store))}</option>`).join('')
        : '<option value="">No Store available</option>';
      // ?store_id= (e.g. "Add Location" from MD Master) pre-selects that Store when it is offered;
      // the server still checks the Store on every request.
      const requested = Number(new URLSearchParams(window.location.search).get('store_id'));
      const first = stores.find((store) => store.id === requested) || stores.find((store) => store.active) || stores[0];
      state.storeId = first ? first.id : null;
      select.value = state.storeId == null ? '' : String(state.storeId);
      select.addEventListener('change', () => {
        state.storeId = select.value ? Number(select.value) : null;
        state.page = 1;
        if (split) resetMdForm();
        clearMessage();
        syncStoreActions();
        load();
      });
    }
    syncStoreActions();
  }
  // Inactive Store of a Merchant login (server flag read_only): view only. Platform Admin manages every Store.
  const currentStore = () => ((state.options && state.options.stores) || []).find((store) => store.id === state.storeId);
  const storeReadOnly = () => !!(currentStore() && currentStore().read_only);
  function syncStoreActions() {
    const store = currentStore();
    $('mgAddBtn').disabled = !state.storeId || storeReadOnly();
    if ($('mgGuideAdd')) $('mgGuideAdd').disabled = storeReadOnly(); // empty-Store guide: same Add action
    const note = $('mgStoreNote');
    if (note) {
      note.innerHTML = store && store.read_only
        ? `<span class="mg-store-badge">Inactive</span><span>${esc(store.read_only_message || 'This Store is Inactive: view only.')}</span>` : '';
      note.classList.toggle('d-none', !(store && store.read_only));
    }
    if (!state.storeId) showMessage('Select a Store to manage its Locations.', 'info');
  }

  document.addEventListener('DOMContentLoaded', init);
})();
