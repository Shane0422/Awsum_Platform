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
  };

  const COLUMNS = {
    tax: [
      ['code', 'Code', (r) => `<span class="mg-code">${esc(r.code)}</span>`, true],
      ['name', 'Tax Name', (r) => esc(r.name), true],
      ['rate', 'Rate (%)', (r) => esc(r.rate), true],
      ['active', 'Status', (r) => badge(r.active), true],
    ],
    category1: [
      ['code', 'Code', (r) => `<span class="mg-code">${esc(r.code)}</span>`, true],
      ['name', 'Name', (r) => esc(r.name), true],
      ['display_order', 'Order', (r) => esc(r.display_order), true],
      ['active', 'Status', (r) => badge(r.active), true],
    ],
    category2: [
      ['code', 'Code', (r) => `<span class="mg-code">${esc(r.code)}</span>`, true],
      ['', 'Category I', (r) => esc(r.category1_name), false],
      ['name', 'Name', (r) => esc(r.name), true],
      ['display_order', 'Order', (r) => esc(r.display_order), true],
      ['active', 'Status', (r) => badge(r.active), true],
    ],
    category3: [
      ['code', 'Code', (r) => `<span class="mg-code">${esc(r.code)}</span>`, true],
      ['', 'Category I', (r) => esc(r.category1_name), false],
      ['', 'Category II', (r) => esc(r.category2_name), false],
      ['name', 'Name', (r) => esc(r.name), true],
      ['display_order', 'Order', (r) => esc(r.display_order), true],
      ['active', 'Status', (r) => badge(r.active), true],
    ],
    md: [
      ['', 'Image', (r) => `<span class="mg-thumbnail"><img src="/images/platform/defaults/no-image.svg" alt="">${r.image_url ? `<img src="${esc(r.image_url)}" alt="" loading="lazy">` : ''}</span>`, false],
      ['code', 'Item Code', (r) => `<span class="mg-code">${esc(r.code)}</span>`, true],
      ['name', 'Item Name', (r) => esc(r.name), true],
      ['item_kind', 'Kind', (r) => esc(label(r.item_kind)), true],
      ['', 'Category', (r) => esc([r.category1_name, r.category2_name, r.category3_name].filter(Boolean).join(' > ')), false],
      ['retail_price', 'Retail', (r) => money(r.retail_price), true],
      ['p1_price', 'P1', (r) => levelCell(r, 1), true],
      ['p2_price', 'P2', (r) => levelCell(r, 2), true],
      ['p3_price', 'P3', (r) => levelCell(r, 3), true],
      ['', 'Cost', (r) => money(r.cost), false, 'cost'],
      ['billing_cycle', 'Billing', (r) => esc(label(r.billing_cycle)), true],
      ['', 'License', (r) => (r.license_required ? esc(label(r.license_type)) + ' x' + esc(r.licenses_per_unit) : '-'), false],
      ['', 'Serial', (r) => yes(r.serial_required), false],
      ['active', 'Status', (r) => badge(r.active), true],
    ],
  };

  const state = { q: '', active: '', sort: '', direction: 'asc', page: 1, size: 20, items: [], options: null, editing: null };
  let modal = null;
  let saving = false;
  let allowClose = false;
  let imageUploader = null;

  function showMessage(text, kind) {
    const box = $('mgMessage');
    box.className = `alert alert-${kind || 'success'}`;
    box.textContent = text;
  }
  function clearMessage() { $('mgMessage').className = 'alert d-none'; }

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
  const cols = () => COLUMNS[entity].filter((c) => c[4] !== 'cost' || (state.options && state.options.can_view_cost));
  function renderHead() {
    $('mgHead').innerHTML = '<tr>' + cols().map(([key, text, , sortable]) => {
      const sorted = sortable && state.sort === key;
      const cls = sortable ? `sortable${sorted ? ' sorted' : ''}${sorted && state.direction === 'desc' ? ' desc' : ''}` : '';
      return `<th class="${cls}" ${sortable ? `data-sort="${key}" tabindex="0"` : ''}>${text}</th>`;
    }).join('') + '<th class="text-end">Actions</th></tr>';
  }

  function renderRows(data) {
    const columns = cols();
    $('mgBody').innerHTML = data.items.length
      ? data.items.map((r) => `<tr>${columns.map((c) => `<td>${c[2](r)}</td>`).join('')}
          <td class="text-end text-nowrap">
            <button type="button" class="btn btn-sm btn-outline-primary" data-act="edit" data-id="${r.id}">Edit</button>
            <button type="button" class="btn btn-sm btn-outline-secondary" data-act="toggle" data-id="${r.id}">${r.active ? 'Deactivate' : 'Activate'}</button>
            <button type="button" class="btn btn-sm btn-outline-danger" data-act="delete" data-id="${r.id}">Delete</button>
          </td></tr>`).join('')
      : `<tr><td colspan="${columns.length + 1}" class="text-center text-muted py-4">No records found.</td></tr>`;
    $('mgSummary').textContent = `${data.total} record(s)`;
    $('mgPageLabel').textContent = `${data.page} / ${data.pages}`;
    $('mgPrev').disabled = data.page <= 1;
    $('mgNext').disabled = data.page >= data.pages;
  }

  async function load() {
    const params = new URLSearchParams({
      q: state.q, active: state.active, sort: state.sort, direction: state.direction, page: state.page, size: state.size,
    });
    try {
      const data = await api('GET', `${BASE}/${entity}?${params}`);
      state.items = data.items;
      state.page = data.page;
      state.sort = data.sort;
      state.direction = data.direction;
      renderHead();
      renderRows(data);
    } catch (err) {
      showMessage(err.status === 401 ? 'Your session has expired. Please sign in again.' : err.message, 'danger');
    }
  }

  async function loadOptions() {
    state.options = await api('GET', `${BASE}/options`);
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
    billing_cycle: 'clock', item_kind: 'box', unit: 'box', retail_price: 'dollar', cost: 'dollar',
    license_required: 'key', license_type: 'key', licenses_per_unit: 'hash', serial_required: 'hash'
  };
  const labelIcon = (name) => FIELD_ICON[name]
    ? `<svg class="pm-lbl-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[FIELD_ICON[name]]}</svg>` : '';

  function field(name, text, control, opts) {
    const col = (opts && opts.col) || 'col-md-6';
    return `<div class="${col}" data-field-wrap="${name}">
      <label class="form-label" for="mgf_${name}">${labelIcon(name)}${text}${opts && opts.required ? ' <span class="pm-req" aria-hidden="true">*</span>' : ''}</label>
      ${control}
      <div class="invalid-feedback" data-error-for="${name}"></div>
      ${opts && opts.help ? `<div class="form-text">${opts.help}</div>` : ''}
    </div>`;
  }
  const input = (name, type, extra) => `<input class="form-control${type === 'number' || /inputmode="decimal"/.test(extra || '') ? ' pm-num' : ''}" id="mgf_${name}" name="${name}" type="${type}" ${extra || ''}>`;
  const select = (name, extra) => `<select class="form-select" id="mgf_${name}" name="${name}" ${extra || ''}></select>`;
  const textarea = (name) => `<textarea class="form-control" id="mgf_${name}" name="${name}" rows="2"></textarea>`;
  const activeField = () => field('active', 'Status', select('active'), { col: 'col-md-6' });

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
      case 'category2':
        return field('category1_id', 'Category I', select('category1_id'), { required: true })
          + field('name', 'Name', input('name', 'text', 'maxlength="100"'), { required: true })
          + field('display_order', 'Display Order', input('display_order', 'number', 'min="0" step="1"'))
          + activeField() + notes;
      case 'category3':
        return field('category1_id', 'Category I', select('category1_id'), { required: true })
          + field('category2_id', 'Category II', select('category2_id'), { required: true })
          + field('name', 'Name', input('name', 'text', 'maxlength="100"'), { required: true })
          + field('display_order', 'Display Order', input('display_order', 'number', 'min="0" step="1"'))
          + activeField() + notes;
      default:
        return mdFormHtml();
    }
  }

  // MD Master form: Classification / Item Information / Pricing / Options. Field names are unchanged.
  function mdFormHtml() {
    const section = (heading, body, hint) => `<section class="col-12 pm-section">
      <div class="pm-section-head"><h3>${heading}</h3>${hint ? `<span>${hint}</span>` : ''}</div>
      <div class="row g-3">${body}</div></section>`;
    const classification = field('category1_id', 'Category I', select('category1_id'), { required: true, col: 'col-md-4' })
      + field('category2_id', 'Category II', select('category2_id'), { col: 'col-md-4' })
      + field('category3_id', 'Category III', select('category3_id'), { col: 'col-md-4' })
      + field('default_tax_id', 'Tax', select('default_tax_id'), { col: 'col-md-6' })
      + field('billing_cycle', 'Billing Cycle', select('billing_cycle'), { required: true, col: 'col-md-6' });
    const info = field('name', 'Item Name', input('name', 'text', 'maxlength="200"'), { required: true, col: 'col-md-8' })
      + field('item_kind', 'Item Kind', select('item_kind'), { required: true, col: 'col-md-4' })
      + field('description', 'Description', textarea('description'), { col: 'col-12' });
    const options = field('license_required', 'License Required', select('license_required'), { col: 'col-md-4' })
      + field('license_type', 'License Type', select('license_type'), { required: true, col: 'col-md-4' })
      + field('licenses_per_unit', 'Licenses per Unit', input('licenses_per_unit', 'number', 'min="1" step="1"'), { required: true, col: 'col-md-4' })
      + field('serial_required', 'Serial Number Required', select('serial_required'), { col: 'col-md-6' })
      + activeField()
      + field('notes', 'Notes', textarea('notes'), { col: 'col-12' });
    return `<div class="col-12 mg-md-top">
          <div class="mg-md-fields">${section('Classification', classification)}${section('Item Information', info)}</div>
          <div id="mgImageUploader" class="mg-image-panel"></div>
        </div>`
      + section('Pricing', pricingHtml(), 'USD &middot; all prices are before tax')
      + section('Options', options);
  }
  const f = (name) => $('mgf_' + name);
  const setOptions = (name, opts, selected, blank) => {
    const el = f(name);
    if (!el) return;
    el.innerHTML = (blank === undefined ? '' : `<option value="">${esc(blank)}</option>`)
      + opts.map(([v, t]) => `<option value="${esc(v)}">${esc(t)}</option>`).join('');
    el.value = selected == null ? '' : String(selected);
  };

  // Active parents, plus the currently saved parent so existing records stay editable.
  const cat1Opts = (keep) => state.options.category1.filter((c) => c.active || c.id === keep).map((c) => [c.id, `${c.name} (${c.code})`]);
  const cat2Opts = (c1, keep) => state.options.category2.filter((c) => c.category1_id === Number(c1) && (c.active || c.id === keep)).map((c) => [c.id, `${c.name} (${c.code})`]);
  const cat3Opts = (c2, keep) => state.options.category3.filter((c) => c.category2_id === Number(c2) && (c.active || c.id === keep)).map((c) => [c.id, `${c.name} (${c.code})`]);

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
  }
  function refreshLicenseFields() {
    if (entity !== 'md') return;
    const on = f('license_required').value === 'true';
    ['license_type', 'licenses_per_unit'].forEach((n) => {
      f(n).disabled = !on;
      f(n).closest('[data-field-wrap]').classList.toggle('d-none', !on);
    });
  }

  function pricingHtml() {
    const moneyInput = (name) => `<div class="input-group"><span class="input-group-text">$</span>${input(name, 'text', `data-awsum-money data-money-min="0" ${name === 'retail_price' ? 'required' : ''} inputmode="decimal" autocomplete="off"`)}</div>`;
    const canCost = !!(state.options && state.options.can_view_cost);
    const colW = canCost ? 'col-md-4' : 'col-md-6';
    let html = field('unit', 'Unit', input('unit', 'text', 'maxlength="30"'), { required: true, col: colW })
      + field('retail_price', 'Retail Price', moneyInput('retail_price'), { required: true, col: colW })
      + (canCost ? field('cost', 'Cost', moneyInput('cost'), { col: colW, help: 'Purchase cost. Visible to authorized users only.' }) : '');
    html += '<div class="col-12"><div class="mg-price-table" role="table" aria-label="Agency price levels">'
      + '<div class="mg-price-row mg-price-head" role="row"><div>Price Level</div><div>Pricing Mode</div><div>Discount %</div><div>Price</div></div>';
    [1, 2, 3].forEach((n) => {
      html += `<div class="mg-price-row" role="row" data-level="${n}">
        <div class="mg-level" data-label="Price Level">P${n}</div>
        <div data-label="Pricing Mode" data-field-wrap="p${n}_mode">${select(`p${n}_mode`, `aria-label="P${n} pricing mode"`)}<div class="invalid-feedback" data-error-for="p${n}_mode"></div></div>
        <div data-label="Discount %" data-field-wrap="p${n}_discount_pct"><div class="input-group">${input(`p${n}_discount_pct`, 'text', `inputmode="decimal" aria-label="P${n} discount percent" autocomplete="off"`)}<span class="input-group-text">%</span></div><div class="invalid-feedback" data-error-for="p${n}_discount_pct"></div></div>
        <div data-label="Price" data-field-wrap="p${n}_price"><div class="input-group"><span class="input-group-text">$</span>${input(`p${n}_price`, 'text', `data-awsum-money data-money-min="0" inputmode="decimal" aria-label="P${n} price" autocomplete="off"`)}</div><div class="invalid-feedback" data-error-for="p${n}_price"></div></div>
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
    if (f('display_order')) f('display_order').value = val('display_order', 100);
    if (f('notes')) f('notes').value = val('notes', '');
    setOptions('active', [['true', 'Active'], ['false', 'Inactive']], editing ? String(row.active) : 'true');

    if (f('category1_id')) {
      setOptions('category1_id', cat1Opts(row && row.category1_id), row && row.category1_id, 'Select Category I');
      refreshCategory2(row && row.category2_id);
      if (entity === 'md') refreshCategory3(row && row.category3_id);
    }
    if (entity === 'md') {
      f('description').value = val('description', '');
      f('unit').value = val('unit', 'EA');
      f('retail_price').value = val('retail_price', '0.00');
      if (f('cost')) f('cost').value = val('cost', '');
      [1, 2, 3].forEach((n) => {
        setOptions(`p${n}_mode`, [['', 'Not set'], ['FIXED', 'Fixed Amount'], ['DISCOUNT', 'Discount %']], editing ? (row[`p${n}_mode`] || '') : '');
        f(`p${n}_discount_pct`).value = val(`p${n}_discount_pct`, '');
        f(`p${n}_price`).value = val(`p${n}_price`, '');
      });
      refreshPricing();
      f('licenses_per_unit').value = val('licenses_per_unit', 1);
      setOptions('item_kind', o.item_kinds.map((k) => [k, label(k)]), row && row.item_kind, 'Select kind');
      setOptions('billing_cycle', o.billing_cycles.map((k) => [k, label(k)]), val('billing_cycle', 'ONE_TIME'));
      setOptions('license_type', o.license_types.map((k) => [k, label(k)]), row && row.license_type, 'Select type');
      setOptions('license_required', [['false', 'No'], ['true', 'Yes']], String(editing ? row.license_required : false));
      setOptions('serial_required', [['false', 'No'], ['true', 'Yes']], String(editing ? row.serial_required : false));
      setOptions(
        'default_tax_id',
        o.taxes.filter((t) => t.active || t.id === (row && row.default_tax_id)).map((t) => [t.id, `${t.name} (${t.rate}%)`]),
        row && row.default_tax_id,
        'None',
      );
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

  const saveLabel = () => (state.editing ? 'Save Changes' : 'Create');

  function discardImage() {
    if (imageUploader) imageUploader.destroy();
    imageUploader = null;
  }

  function openForm(row) {
    if (saving) return;
    discardImage();
    allowClose = false;
    state.editing = row || null;
    $('mgModalTitle').textContent = row ? `Edit ${title}` : `Add ${title}`;
    $('mgModalSub').textContent = row ? `Update this ${title} record. The code stays the same.` : `Create a new ${title} record. The code is generated automatically.`;
    $('mgSave').textContent = saveLabel();
    $('mgFields').innerHTML = formHtml();
    if (row) {
      const codeTarget = entity === 'md' ? $('mgFields').querySelector('.mg-md-fields') : $('mgFields');
      codeTarget.insertAdjacentHTML('afterbegin',
        `<div class="col-12 mg-record-code"><span class="text-muted small">Code</span> <span class="mg-code fw-semibold">${esc(row.code)}</span> <span class="text-muted small">(auto-generated, not editable)</span></div>`);
    }
    fillForm(row);
    AwsumMoneyInput.refreshAll($('mgFields'));
    clearErrors();
    bindFormEvents();
    if (entity === 'md') {
      imageUploader = AwsumImages.createUploader($('mgImageUploader'), {
        idPrefix: 'mgImage', label: 'Item Image', previewSize: 120, fit: 'contain',
      });
      imageUploader.reset(row && row.image_url, !!(row && row.image_path));
    }
    modal.show();
  }

  function bindFormEvents() {
    const c1 = f('category1_id');
    const c2 = f('category2_id');
    if (c1 && c2) {
      c1.addEventListener('change', () => refreshCategory2(null)); // parent change resets children
    }
    if (c2 && f('category3_id')) c2.addEventListener('change', () => refreshCategory3(null));
    if (entity === 'md') {
      f('license_required').addEventListener('change', refreshLicenseFields);
      f('retail_price').addEventListener('input', refreshPricing);
      [1, 2, 3].forEach((n) => {
        f(`p${n}_mode`).addEventListener('change', refreshPricing);
        f(`p${n}_discount_pct`).addEventListener('input', refreshPricing);
      });
    }
  }

  function collect() {
    const payload = {};
    $('mgFields').querySelectorAll('[name]').forEach((el) => {
      if (el.disabled) return;
      payload[el.name] = el.hasAttribute('data-awsum-money') ? AwsumMoneyInput.getValue(el) : el.value;
    });
    ['active', 'license_required', 'serial_required'].forEach((k) => {
      if (k in payload) payload[k] = payload[k] === 'true';
    });
    return payload;
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
    try {
      let body = collect();
      const editing = state.editing;
      let path = `${BASE}/${entity}${editing ? '/' + editing.id : ''}`;
      if (entity === 'md') {
        body = imageUploader.formData(body);
        path += '/with-image';
      }
      const data = editing
        ? await api('PUT', path, body)
        : await api('POST', path, body);
      allowClose = true;
      modal.hide();
      if (entity.startsWith('category') || entity === 'tax') await loadOptions();
      showMessage(data.item.image_warning || `${data.item.code} saved.`, data.item.image_warning ? 'warning' : 'success');
      await load();
    } catch (err) {
      const box = $('mgFormError');
      box.textContent = err.status === 401 ? 'Your session has expired. Please sign in again.' : err.message;
      box.classList.remove('d-none');
      showFieldErrors(err.errors);
    } finally {
      saving = false;
      btn.disabled = false;
      btn.textContent = saveLabel();
      if (imageUploader) imageUploader.setDisabled(false);
    }
  }

  // ---- row actions ----
  async function rowAction(act, id) {
    const row = state.items.find((r) => r.id === id);
    if (!row) return;
    clearMessage();
    if (act === 'edit') { openForm(row); return; }
    try {
      if (act === 'toggle') {
        const body = { ...rowToPayload(row), active: !row.active };
        await api('PUT', `${BASE}/${entity}/${id}`, body);
        showMessage(`${row.code} ${row.active ? 'deactivated' : 'activated'}.`, 'success');
      } else if (act === 'delete') {
        if (row.in_use) {
          showMessage(`${row.code} is in use and cannot be deleted. Deactivate it instead.`, 'warning');
          return;
        }
        if (!window.confirm(`Delete ${row.code}? This cannot be undone.`)) return;
        const result = await api('DELETE', `${BASE}/${entity}/${id}`);
        showMessage(result.message === 'Deleted.' ? `${row.code} deleted.` : result.message, result.message === 'Deleted.' ? 'success' : 'warning');
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
      category1_id: r.category1_id, category2_id: r.category2_id, category3_id: r.category3_id,
      description: r.description, item_kind: r.item_kind, unit: r.unit, retail_price: r.retail_price,
      cost: r.cost,
      p1_mode: r.p1_mode, p1_discount_pct: r.p1_discount_pct, p1_price: r.p1_price,
      p2_mode: r.p2_mode, p2_discount_pct: r.p2_discount_pct, p2_price: r.p2_price,
      p3_mode: r.p3_mode, p3_discount_pct: r.p3_discount_pct, p3_price: r.p3_price,
      default_tax_id: r.default_tax_id, billing_cycle: r.billing_cycle, license_required: r.license_required,
      license_type: r.license_type, licenses_per_unit: r.licenses_per_unit, serial_required: r.serial_required,
    };
  }

  // ---- init ----
  function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

  // Exports every row matching the current search / status / sort, not just the visible page.
  async function exportList(format) {
    clearMessage();
    const params = new URLSearchParams({ format, q: state.q, active: state.active, sort: state.sort, direction: state.direction });
    const url = `${BASE}/${entity}/export?${params}`;
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
    modal = new bootstrap.Modal($('mgModal'));
    $('mgModal').addEventListener('hide.bs.modal', (event) => {
      if (saving && !allowClose) event.preventDefault();
    });
    $('mgModal').addEventListener('hidden.bs.modal', discardImage);
    $('mgBody').addEventListener('error', (event) => {
      if (event.target.tagName === 'IMG') event.target.hidden = true;
    }, true);
    renderHead();
    $('mgAddBtn').addEventListener('click', () => { clearMessage(); openForm(null); });
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
    $('mgBody').addEventListener('click', (e) => {
      const btn = e.target.closest('button[data-act]');
      if (btn) rowAction(btn.dataset.act, Number(btn.dataset.id));
    });
    try { await loadOptions(); } catch (err) { showMessage(err.message, 'danger'); }
    await load();
  }

  document.addEventListener('DOMContentLoaded', init);
})();
