/* Store split view: right-hand "Devices & Licenses" panel and the draggable splitter. */
(function () {
  'use strict';

  const API = '/platform/store';
  const WIDTH_KEY = 'awsum.storeSplit.leftPct.v2';
  const state = { storeId: null, data: null, loading: false, token: 0, inited: false, modal: null, selected: new Set() };

  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  const $ = (id) => document.getElementById(id);
  const panel = () => $('storeDevicesPanel');

  async function api(path, options) {
    const res = await fetch(path, Object.assign({ credentials: 'same-origin', headers: { 'Content-Type': 'application/json' } }, options || {}));
    let body = null;
    try { body = await res.json(); } catch (_) { /* non-JSON error body */ }
    if (!res.ok) {
      const detail = body && body.detail;
      const error = new Error(typeof detail === 'string' ? detail : (res.status === 401 || res.status === 403 ? 'Permission denied.' : 'Request failed (' + res.status + ').'));
      error.status = res.status;
      throw error;
    }
    return body;
  }

  function fmtDate(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString();
  }

  /* ---------- splitter ---------- */
  function initSplitter() {
    const splitter = $('storeSplitter');
    const view = $('masterRoleView');
    if (!splitter || !view) return;
    const clamp = (pct) => Math.min(70, Math.max(35, pct));
    const apply = (pct) => {
      view.style.setProperty('--store-left', pct + '%');
      splitter.setAttribute('aria-valuenow', String(Math.round(pct)));
    };
    const saved = parseFloat(localStorage.getItem(WIDTH_KEY));
    apply(Number.isFinite(saved) ? clamp(saved) : 40);

    let dragging = false;
    const move = (clientX) => {
      const rect = view.getBoundingClientRect();
      apply(clamp(((clientX - rect.left) / rect.width) * 100));
    };
    splitter.addEventListener('pointerdown', (e) => {
      dragging = true;
      splitter.classList.add('dragging');
      splitter.setPointerCapture(e.pointerId);
      e.preventDefault();
    });
    splitter.addEventListener('pointermove', (e) => { if (dragging) move(e.clientX); });
    const stop = () => {
      if (!dragging) return;
      dragging = false;
      splitter.classList.remove('dragging');
      localStorage.setItem(WIDTH_KEY, String(parseFloat(view.style.getPropertyValue('--store-left')) || 40));
    };
    splitter.addEventListener('pointerup', stop);
    splitter.addEventListener('pointercancel', stop);
    splitter.addEventListener('keydown', (e) => {
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      const cur = parseFloat(view.style.getPropertyValue('--store-left')) || 40;
      const next = clamp(cur + (e.key === 'ArrowRight' ? 2 : -2));
      apply(next);
      localStorage.setItem(WIDTH_KEY, String(next));
      e.preventDefault();
    });
    splitter.addEventListener('dblclick', () => { apply(40); localStorage.setItem(WIDTH_KEY, '40'); });
  }

  /* ---------- rendering ---------- */
  function renderEmpty() {
    panel().innerHTML = '<div class="sd-empty">Select a store</div>';
  }

  function renderLoading(item) {
    panel().innerHTML = headerHtml(item ? {
      merchant_name: item.merchant_name || item.merchant_name, store_name: item.store_name, agency_name: item.installed_by_agency_name,
    } : {}) + '<div class="text-muted py-4">Loading...</div>';
  }

  function headerHtml(s) {
    const agency = s.agency_name ? esc(s.agency_name) + (s.agency_code ? ' (' + esc(s.agency_code) + ')' : '') : '—';
    return '<div class="sd-title">' + esc(s.store_name || '—') + '</div>' +
      '<div class="sd-sub">Merchant: ' + esc(s.merchant_name || '—') + ' &nbsp;·&nbsp; Agency: ' + agency + '</div>' +
      '<div id="sdActions" class="d-flex flex-wrap gap-2 mt-3"></div>';
  }

  function badge(code, label) {
    return '<span class="sd-badge ' + esc(code) + '">' + esc(label) + '</span>';
  }

  function render() {
    const d = state.data;
    if (!d) return;
    const s = d.store;
    const validIds = new Set(d.licenses.map(l => Number(l.license_id)));
    state.selected = new Set(Array.from(state.selected).filter(id => validIds.has(id)));

    const rows = d.licenses.map(l => {
      const btn = '<button type="button" class="btn btn-sm btn-outline-secondary py-0 px-2 me-1" data-sd-copy="' + esc(l.license_key) + '" title="Copy license key">Copy</button>' +
        '<button type="button" class="btn btn-sm btn-outline-primary py-0 px-2" data-sd-detail="' + l.license_id + '">Details</button>';
      return '<tr><td><input type="checkbox" class="form-check-input" data-sd-license-select="' + Number(l.license_id) + '" aria-label="Select ' + esc(l.name) + '"' + (state.selected.has(Number(l.license_id)) ? ' checked' : '') + '></td>' +
        '<td class="fw-semibold">' + esc(l.name) + '</td><td>' + esc(l.type) + '</td>' +
        '<td><span class="sd-key" title="' + esc(l.license_key) + '">' + esc(l.license_key) + '</span></td><td class="sd-os" title="' + esc(l.os || '') + '">' + esc(l.os || '—') + '</td>' +
        '<td>' + badge(l.status, l.status_label) + '</td><td class="text-end">' + btn + '</td></tr>';
    }).join('') || '<tr><td colspan="7" class="text-center text-muted py-4">No licenses issued yet.</td></tr>';

    panel().innerHTML = headerHtml(s) +
      '<h6 class="mb-2" style="font-size:14px;font-weight:600">Devices &amp; Licenses</h6>' +
      '<div class="d-flex align-items-center justify-content-between gap-2 mb-2"><span id="sdSelectedCount" class="small text-muted"></span>' +
      '<button type="button" class="btn btn-sm btn-outline-primary" id="sdSendEmail" disabled>Send Email</button></div>' +
      '<div class="sd-table-wrap"><table class="table table-hover align-middle sd-table mb-0"><thead><tr>' +
      '<th><input type="checkbox" class="form-check-input" id="sdSelectAll" aria-label="Select all licenses"></th>' +
      '<th>Name</th><th>Type</th><th>License Key</th><th>OS</th><th>Status</th><th></th></tr></thead><tbody>' + rows + '</tbody></table></div>';

    const selectAll = $('sdSelectAll');
    selectAll.checked = d.licenses.length > 0 && state.selected.size === d.licenses.length;
    selectAll.indeterminate = state.selected.size > 0 && state.selected.size < d.licenses.length;
    $('sdSelectedCount').textContent = state.selected.size + ' selected';
    $('sdSendEmail').disabled = state.selected.size === 0;
    $('sdSendEmail').addEventListener('click', openEmailModal);

    $('sdActions').innerHTML =
      '<button type="button" class="btn btn-sm btn-outline-secondary role-action-btn" id="sdBtnQty">Edit Contract Quantities</button>' +
      '<button type="button" class="btn btn-sm btn-primary role-action-btn" id="sdBtnIssue">Issue Licenses</button>';
    $('sdBtnQty').addEventListener('click', openQtyModal);
    $('sdBtnIssue').addEventListener('click', openIssueModal);
  }

  /* ---------- loading ---------- */
  async function load(storeId, item) {
    const token = ++state.token;
    state.loading = true;
    renderLoading(item);
    try {
      const data = await api(API + '/' + storeId + '/devices-licenses');
      if (token !== state.token) return; // a newer selection superseded this one
      state.data = data;
      render();
    } catch (err) {
      if (token !== state.token) return;
      state.data = null;
      panel().innerHTML = '<div class="alert alert-danger mt-2">' + esc(err.message) + '</div>';
    } finally {
      if (token === state.token) state.loading = false;
    }
  }

  function show(item) {
    const id = item && (item.id ?? item.store_id ?? item.i_store_id);
    if (!id) return;
    if (String(id) === String(state.storeId) && state.data) return; // same store: keep current data
    if (state.modal) state.modal.hide();
    state.storeId = id;
    state.data = null;
    state.selected.clear();
    load(id, item);
  }

  function clear() {
    state.token++;
    if (state.modal) state.modal.hide();
    state.storeId = null;
    state.data = null;
    state.selected.clear();
    if (panel()) renderEmpty();
  }

  /* ---------- modal helper ---------- */
  function openModal(title, bodyHtml, footerHtml, onReady) {
    let el = $('sdModal');
    if (!el) {
      el = document.createElement('div');
      el.id = 'sdModal';
      el.className = 'modal fade';
      el.tabIndex = -1;
      el.innerHTML = '<div class="modal-dialog modal-dialog-centered modal-lg"><div class="modal-content">' +
        '<div class="modal-header"><h6 class="modal-title" id="sdModalTitle"></h6><button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button></div>' +
        '<div class="modal-body" id="sdModalBody"></div><div class="modal-footer" id="sdModalFooter"></div></div></div>';
      document.body.appendChild(el);
      state.modal = new bootstrap.Modal(el);
    }
    $('sdModalTitle').textContent = title;
    $('sdModalBody').innerHTML = bodyHtml;
    $('sdModalFooter').innerHTML = footerHtml;
    state.modal.show();
    if (onReady) onReady($('sdModalBody'), $('sdModalFooter'));
  }
  const closeModal = () => state.modal && state.modal.hide();
  const showError = (msg) => { const e = $('sdModalError'); if (e) { e.textContent = msg || ''; e.classList.toggle('d-none', !msg); } };
  const errBox = '<div id="sdModalError" class="alert alert-danger py-2 d-none"></div>';

  function applyWorkspace(ws) {
    state.data = ws;
    render();
  }

  function newRequestId() {
    return (window.crypto && crypto.randomUUID)
      ? crypto.randomUUID()
      : (Date.now().toString(36) + Math.random().toString(36).slice(2, 12));
  }

  const EMAIL_CSS = '' +
    '#sdEmailModal .modal-dialog{max-width:1000px;width:calc(100% - 1rem)}' +
    '#sdEmailModal .modal-content{max-height:90vh;max-height:90dvh;background:#fff;border:0;border-radius:16px;box-shadow:0 20px 50px rgba(16,42,67,.22);font-size:14px;color:#102A43;overflow:hidden}' +
    '#sdEmailModal .sd-em-head{flex:0 0 auto;display:flex;align-items:center;gap:12px;padding:18px 24px;border-bottom:1px solid #E2E8F0}' +
    '#sdEmailModal .sd-em-ico{flex:0 0 auto;display:inline-flex;width:36px;height:36px;align-items:center;justify-content:center;border-radius:10px;background:#E7F0FE;color:#1263EB}' +
    '#sdEmailModal .sd-em-title{margin:0;font-size:18px;font-weight:700;color:#102A43}' +
    '#sdEmailModal .sd-em-sub{margin:0;font-size:13px;color:#64748B}' +
    '#sdEmailModal .sd-em-body{flex:1 1 auto;min-height:0;overflow-y:auto;padding:20px 24px;background:#fff}' +
    '#sdEmailModal .sd-em-foot{flex:0 0 auto;display:flex;justify-content:flex-end;gap:10px;padding:14px 24px;border-top:1px solid #E2E8F0;background:#fff}' +
    '#sdEmailModal .sd-em-info{display:flex;flex-wrap:wrap;align-items:center;gap:8px 20px;padding:12px 16px;margin-bottom:18px;background:#F4F7FB;border:1px solid #E2E8F0;border-radius:10px}' +
    '#sdEmailModal .sd-em-info span.k{color:#64748B;margin-right:4px}' +
    '#sdEmailModal .sd-em-badge{margin-left:auto;padding:3px 10px;border-radius:999px;background:#1263EB;color:#fff;font-size:12px;font-weight:600}' +
    '#sdEmailModal .sd-em-grid{display:grid;grid-template-columns:1fr;gap:20px}' +
    '@media (min-width:992px){#sdEmailModal .sd-em-grid{grid-template-columns:minmax(0,5fr) minmax(0,6fr)}}' +
    '#sdEmailModal .form-label{font-size:13px;font-weight:600;color:#102A43;margin-bottom:6px}' +
    '#sdEmailModal .form-control{font-size:14px;min-height:42px;border-color:#E2E8F0;border-radius:8px}' +
    '#sdEmailModal textarea.form-control{min-height:140px;line-height:1.5}' +
    '#sdEmailModal .form-control:focus{border-color:#1263EB;box-shadow:0 0 0 .2rem rgba(18,99,235,.15)}' +
    '#sdEmailModal .form-text{font-size:12px}' +
    '#sdEmailModal .btn{font-size:14px;min-height:40px;border-radius:8px;padding:0 18px}' +
    '#sdEmailModal .btn-primary{background:#1263EB;border-color:#1263EB}' +
    '#sdEmailModal .sd-em-pv{border:1px solid #E2E8F0;border-radius:10px;overflow:hidden;background:#F4F7FB}' +
    '#sdEmailModal .sd-em-pvmeta{padding:8px 12px;font-size:12px;color:#64748B;background:#fff;border-bottom:1px solid #E2E8F0;overflow-wrap:anywhere}' +
    '#sdEmailModal .sd-em-pvmeta b{color:#102A43}' +
    '#sdEmailModal iframe{display:block;width:100%;height:460px;border:0;background:#F4F7FB}' +
    '#sdEmailModal .sd-em-ok{text-align:center;padding:36px 12px}' +
    '#sdEmailModal .sd-em-ok .chk{display:inline-flex;width:52px;height:52px;align-items:center;justify-content:center;border-radius:50%;background:#E6F6EC;color:#16A34A;margin-bottom:14px}';

  const MAIL_ICON = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/></svg>';
  const CHECK_ICON = '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m5 12 5 5 9-10"/></svg>';

  function ensureEmailStyles() {
    if ($('sdEmailStyles')) return;
    const style = document.createElement('style');
    style.id = 'sdEmailStyles';
    style.textContent = EMAIL_CSS;
    document.head.appendChild(style);
  }

  function openEmailModal() {
    const d = state.data;
    if (!d || !state.selected.size) return;
    const s = d.store;
    const selected = d.licenses
      .filter(l => state.selected.has(Number(l.license_id)))
      .map(l => Object.assign({}, l, { name: l.device_name || l.name }));
    const subject = '[AwsumSolution] License Information - ' + (s.store_name || 'Store');
    const customMessage = 'Please keep this license information for the onsite activation of the assigned device.';
    const agencyEmailNote = s.agency_email
      ? 'Prefilled from the Store’s Agency. Changes apply to this email only.'
      : 'No Agency email is registered. Enter a recipient address.';

    openCompanyEmailModal({
      title: 'Send License Email',
      subtitle: 'Review the recipient and message, then send the selected licenses.',
      recipient: s.agency_email || '', recipientNote: agencyEmailNote, subject, message: customMessage,
      infoHtml: '<span><span class="k">Merchant</span><strong>' + esc(s.merchant_name || '—') + '</strong></span>' +
        '<span><span class="k">Store</span><strong>' + esc(s.store_name || '—') + '</strong></span>' +
        '<span><span class="k">Agency</span><strong>' + esc(s.agency_name || '—') + '</strong></span>' +
        '<span class="sd-em-badge">' + selected.length + ' license' + (selected.length === 1 ? '' : 's') + ' selected</span>',
      note: 'Full license keys are included exactly as issued. A mail-server acceptance confirms submission only, not recipient delivery.',
      preview: (payload) => api(API + '/' + state.storeId + '/licenses/email/preview', {
        method: 'POST', body: JSON.stringify({ license_ids: selected.map(l => Number(l.license_id)), message: payload.message }),
      }),
      send: (payload) => api(API + '/' + state.storeId + '/licenses/email', {
        method: 'POST', body: JSON.stringify({ ...payload, license_ids: selected.map(l => Number(l.license_id)) }),
      }),
      onAccepted: () => { state.selected.clear(); render(); },
    });
  }

  function openCompanyEmailModal(options) {
    ensureEmailStyles();
    const old = $('sdEmailModal');
    if (old) old.remove();
    const el = document.createElement('div');
    el.id = 'sdEmailModal';
    el.className = 'modal fade';
    el.tabIndex = -1;
    el.setAttribute('aria-labelledby', 'sdEmailTitle');
    el.innerHTML =
      '<div class="modal-dialog modal-dialog-centered"><div class="modal-content">' +
      '<div class="sd-em-head"><span class="sd-em-ico">' + MAIL_ICON + '</span>' +
      '<div class="flex-grow-1"><h2 class="sd-em-title" id="sdEmailTitle">' + esc(options.title) + '</h2>' +
      '<p class="sd-em-sub">' + esc(options.subtitle) + '</p></div>' +
      '<button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button></div>' +
      '<div class="sd-em-body" id="sdEmailBody">' +
      '<div id="sdModalError" class="alert alert-danger py-2 d-none" role="alert"></div>' +
      '<div class="sd-em-info">' + (options.infoHtml || '') + '</div>' +
      '<div class="sd-em-grid"><div>' +
      '<div class="mb-3"><label for="sdEmailTo" class="form-label">To</label>' +
      '<input id="sdEmailTo" type="email" class="form-control" maxlength="254" required value="' + esc(options.recipient || '') + '">' +
      '<div class="form-text">' + esc(options.recipientNote) + '</div></div>' +
      '<div class="mb-3"><label for="sdEmailSubject" class="form-label">Subject</label>' +
      '<input id="sdEmailSubject" type="text" class="form-control" maxlength="255" required value="' + esc(options.subject) + '"></div>' +
      '<div class="mb-2"><label for="sdEmailMessage" class="form-label">Message</label>' +
      '<textarea id="sdEmailMessage" class="form-control" rows="6" maxlength="10000" required>' + esc(options.message) + '</textarea></div>' +
      '<p class="form-text mb-0">' + esc(options.note) + '</p>' +
      '</div><div><div class="form-label">Email preview</div><div class="sd-em-pv">' +
      '<div class="sd-em-pvmeta"><div><b>To:</b> <span id="sdPvTo"></span></div><div><b>Subject:</b> <span id="sdPvSubject"></span></div></div>' +
      '<iframe id="sdEmailPreview" title="Email preview" sandbox="" referrerpolicy="no-referrer"></iframe></div></div></div>' +
      '</div>' +
      '<div class="sd-em-foot" id="sdEmailFooter">' +
      '<button type="button" class="btn btn-outline-secondary" data-bs-dismiss="modal">Cancel</button>' +
      '<button type="button" class="btn btn-primary" id="sdEmailSend">Send Email</button></div>' +
      '</div></div>';
    document.body.appendChild(el);
    const modal = new bootstrap.Modal(el);
    el.addEventListener('hidden.bs.modal', () => { modal.dispose(); el.remove(); });
    modal.show();

    const to = $('sdEmailTo');
    const subjectEl = $('sdEmailSubject');
    const messageEl = $('sdEmailMessage');
    const send = $('sdEmailSend');
    const frame = $('sdEmailPreview');
    subjectEl.readOnly = Boolean(options.readOnlyContent);
    messageEl.readOnly = Boolean(options.readOnlyContent);
    const showErr = (msg) => { const e = $('sdModalError'); e.textContent = msg || ''; e.classList.toggle('d-none', !msg); };
    let requestId = newRequestId();
    let submitting = false;
    let previewSeq = 0;
    let previewTimer = null;
    let previewReady = false;
    send.disabled = true;
    const payload = () => ({
      request_id: requestId, recipient: to.value, subject: subjectEl.value, message: messageEl.value,
    });

    const syncMeta = () => {
      $('sdPvTo').textContent = to.value || '—';
      $('sdPvSubject').textContent = subjectEl.value || '—';
    };
    const loadPreview = async () => {
      const seq = ++previewSeq;
      previewReady = false;
      send.disabled = true;
      try {
        const result = await options.preview(payload());
        if (seq === previewSeq) {
          frame.srcdoc = result.html;
          previewReady = true;
          send.disabled = submitting;
          showErr('');
        }
      } catch (err) {
        if (seq === previewSeq) {
          showErr(err.message);
          frame.srcdoc = '<body style="font-family:Arial;padding:16px;color:#b91c1c">Preview unavailable: ' + esc(err.message) + '</body>';
        }
      }
    };
    const schedulePreview = () => {
      previewReady = false; send.disabled = true; ++previewSeq;
      clearTimeout(previewTimer); previewTimer = setTimeout(loadPreview, 300);
    };
    to.addEventListener('input', () => { syncMeta(); if (options.readOnlyContent) schedulePreview(); });
    subjectEl.addEventListener('input', syncMeta);
    messageEl.addEventListener('input', schedulePreview);
    syncMeta();
    loadPreview();

    send.addEventListener('click', async () => {
      if (submitting || !previewReady) return;
      if (!to.reportValidity() || !subjectEl.reportValidity() || !messageEl.reportValidity()) return;
      submitting = true;
      send.disabled = true;
      send.innerHTML = '<span class="spinner-border spinner-border-sm me-2" aria-hidden="true"></span>Sending...';
      showErr('');
      try {
        const result = await options.send(payload());
        if (options.onAccepted) options.onAccepted(result);
        $('sdEmailBody').innerHTML = '<div class="sd-em-ok"><div class="chk">' + CHECK_ICON + '</div>' +
          '<h3 class="h5" style="color:#102A43">메일 서버에 접수되었습니다</h3>' +
          '<p class="mb-1">Recipient: <strong>' + esc(result.recipient || to.value) + '</strong></p>' +
          '<p class="text-muted small mb-0">The mail server accepted the message for sending. This does not confirm that the recipient received it.' +
          (result.message_id ? '<br>Message ID: ' + esc(result.message_id) : '') + '</p></div>';
        $('sdEmailFooter').innerHTML = '<button type="button" class="btn btn-primary" data-bs-dismiss="modal">Close</button>';
      } catch (err) {
        showErr(err.message);
        $('sdEmailBody').scrollTop = 0;
        if (err.status === 502 || err.status === 503) requestId = newRequestId();
        submitting = false;
        send.disabled = false;
        send.textContent = 'Send Email';
      }
    });
    el.addEventListener('hidden.bs.modal', () => { clearTimeout(previewTimer); ++previewSeq; });
  }

  window.AwsumEmailPreview = { open: openCompanyEmailModal };

  /* ---------- edit quantities ---------- */
  function openQtyModal() {
    const d = state.data;
    if (!d) return;
    const body = errBox +
      '<div class="row g-3">' +
      '<div class="col-6"><label class="form-label">POS contracted quantity</label><input id="sdQtyPos" type="number" min="0" max="999" class="form-control" value="' + d.summary.POS.contracted + '"></div>' +
      '<div class="col-6"><label class="form-label">KIOSK contracted quantity</label><input id="sdQtyKiosk" type="number" min="0" max="999" class="form-control" value="' + d.summary.KIOSK.contracted + '"></div>' +
      '<div class="col-12"><div class="form-check form-switch"><input id="sdQtyOrdering" class="form-check-input" type="checkbox"' + (d.summary.CUSTOMER_ORDERING.enabled ? ' checked' : '') + '>' +
      '<label class="form-check-label" for="sdQtyOrdering">Customer Ordering service</label></div></div></div>' +
      '<p class="small text-muted mt-3 mb-0">Saving only changes the contracted quantities. It never issues, revokes or deletes licenses. Pricing and billing are managed in Contract / Billing.</p>';
    openModal('Edit Contract Quantities', body,
      '<button type="button" class="btn btn-outline-secondary" data-bs-dismiss="modal">Cancel</button><button type="button" class="btn btn-primary" id="sdQtySave">Save</button>',
      () => {
        $('sdQtySave').addEventListener('click', async (ev) => {
          const btn = ev.currentTarget;
          const pos = $('sdQtyPos').value, kiosk = $('sdQtyKiosk').value;
          if (!/^\d{1,3}$/.test(pos) || !/^\d{1,3}$/.test(kiosk)) { showError('Quantities must be whole numbers from 0 to 999.'); return; }
          btn.disabled = true;
          try {
            const ws = await api(API + '/' + state.storeId + '/entitlement', {
              method: 'PUT',
              body: JSON.stringify({ pos_qty: Number(pos), kiosk_qty: Number(kiosk), customer_ordering: $('sdQtyOrdering').checked }),
            });
            applyWorkspace(ws);
            closeModal();
          } catch (err) {
            showError(err.message);
            btn.disabled = false;
          }
        });
      });
  }

  /* ---------- issue licenses ---------- */
  function openIssueModal() {
    const d = state.data;
    if (!d) return;
    const body = errBox +
      '<div class="row g-3">' +
      '<div class="col-6"><label class="form-label">License type</label><select id="sdIssueType" class="form-select">' +
      '<option value="POS">POS</option><option value="KIOSK">KIOSK</option><option value="CUSTOMER_ORDERING">Customer Ordering (service)</option></select></div>' +
      '<div class="col-6"><label class="form-label">Quantity</label><input id="sdIssueQty" type="number" min="1" value="1" class="form-control"></div></div>' +
      '<div id="sdIssuePreview" class="alert alert-info mt-3 mb-0"></div>';
    openModal('Issue Licenses', body,
      '<button type="button" class="btn btn-outline-secondary" data-bs-dismiss="modal">Cancel</button><button type="button" class="btn btn-primary" id="sdIssueGo">Issue</button>',
      () => {
        const requestId = (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : (Date.now().toString(36) + Math.random().toString(36).slice(2, 12));
        const typeEl = $('sdIssueType'), qtyEl = $('sdIssueQty'), go = $('sdIssueGo');
        let submitting = false;
        const remainingOf = () => {
          const t = typeEl.value;
          if (t === 'CUSTOMER_ORDERING') return d.summary.CUSTOMER_ORDERING.enabled ? d.summary.CUSTOMER_ORDERING.remaining : 0;
          return d.summary[t].remaining;
        };
        const refresh = () => {
          const qty = Number(qtyEl.value);
          const remaining = remainingOf();
          const valid = Number.isInteger(qty) && qty >= 1 && qty <= remaining;
          const label = typeEl.value === 'CUSTOMER_ORDERING' ? 'Customer Ordering service' : typeEl.value;
          $('sdIssuePreview').className = 'alert mt-3 mb-0 ' + (valid ? 'alert-info' : 'alert-warning');
          $('sdIssuePreview').textContent = valid
            ? 'This will create ' + qty + ' new ' + label + ' license(s). Remaining issuable: ' + remaining + '.'
            : 'Issuable now: ' + remaining + '. Enter a quantity between 1 and ' + remaining + ' (contracted minus already issued).';
          go.disabled = !valid || submitting;
        };
        typeEl.addEventListener('change', refresh);
        qtyEl.addEventListener('input', refresh);
        refresh();
        go.addEventListener('click', async () => {
          if (submitting) return;
          submitting = true;
          go.disabled = true;
          showError('');
          try {
            const res = await api(API + '/' + state.storeId + '/licenses/issue', {
              method: 'POST',
              body: JSON.stringify({ license_type: typeEl.value, quantity: Number(qtyEl.value), request_id: requestId }),
            });
            applyWorkspace(res.workspace);
            closeModal();
          } catch (err) {
            showError(err.message);
            submitting = false; // allow retry; the same request_id keeps a retry idempotent
            refresh();
          }
        });
      });
  }

  /* ---------- details / unlink ---------- */
  async function openDetail(licenseId) {
    openModal('License Details', '<div class="text-muted">Loading...</div>', '<button type="button" class="btn btn-outline-secondary" data-bs-dismiss="modal">Close</button>');
    let info;
    try {
      info = await api(API + '/' + state.storeId + '/licenses/' + licenseId);
    } catch (err) {
      $('sdModalBody').innerHTML = '<div class="alert alert-danger">' + esc(err.message) + '</div>';
      return;
    }
    const l = info.license, dev = info.device;
    const row = (k, v) => '<dt class="col-5 text-muted fw-normal">' + k + '</dt><dd class="col-7">' + esc(v ?? '—') + '</dd>';
    const deviceBlock = dev
      ? '<h6 class="mt-3">Activated Device</h6><dl class="row mb-0">' + row('Device ID', dev.device_code) + row('Name', dev.name) + row('OS', dev.os) + row('OS version', dev.os_version) +
        row('Model', dev.model) + row('App version', dev.app_version) + row('Fingerprint', dev.fingerprint_tail ? '…' + dev.fingerprint_tail : null) +
        row('Last seen', fmtDate(dev.last_seen)) + row('Activated at', fmtDate(dev.first_activated_at)) + '</dl>'
      : '<div class="alert alert-secondary mt-3 mb-0">Not activated. No device is linked to this license yet; device details appear after field activation.</div>';
    const history = info.history.length
      ? '<h6 class="mt-3">Link History</h6><div class="table-responsive"><table class="table table-sm sd-table mb-0"><thead><tr><th>Device</th><th>Linked</th><th>Unlinked</th><th>Reason</th></tr></thead><tbody>' +
        info.history.map(h => '<tr><td>' + esc(h.device_code || h.device_id) + '</td><td>' + fmtDate(h.linked_at) + '</td><td>' + (h.unlinked_at ? fmtDate(h.unlinked_at) + ' by ' + esc(h.unlinked_by || '—') : 'Active') + '</td><td>' + esc(h.unlink_reason || '—') + '</td></tr>').join('') +
        '</tbody></table></div>'
      : '';
    $('sdModalTitle').textContent = l.name + ' · ' + l.type;
    $('sdModalBody').innerHTML = errBox + '<dl class="row mb-0">' + row('License key', l.license_key) + row('Status', l.status_label) + row('Issued', fmtDate(l.issued_at)) + '</dl>' + deviceBlock + history;
    if (dev) {
      $('sdModalFooter').innerHTML = '<button type="button" class="btn btn-outline-danger me-auto" id="sdUnlink">Unlink Device…</button><button type="button" class="btn btn-outline-secondary" data-bs-dismiss="modal">Close</button>';
      $('sdUnlink').addEventListener('click', () => confirmUnlink(l, dev));
    }
  }

  function confirmUnlink(lic, dev) {
    $('sdModalTitle').textContent = 'Unlink Device';
    $('sdModalBody').innerHTML = errBox +
      '<p>You are about to unlink this device from <strong>' + esc(lic.name) + '</strong>:</p>' +
      '<dl class="row"><dt class="col-4 text-muted fw-normal">Device ID</dt><dd class="col-8">' + esc(dev.device_code) + '</dd>' +
      '<dt class="col-4 text-muted fw-normal">Name</dt><dd class="col-8">' + esc(dev.name || '—') + '</dd>' +
      '<dt class="col-4 text-muted fw-normal">OS / Model</dt><dd class="col-8">' + esc([dev.os, dev.os_version, dev.model].filter(Boolean).join(' · ') || '—') + '</dd></dl>' +
      '<label class="form-label">Reason (optional)</label><input id="sdUnlinkReason" class="form-control" maxlength="255" placeholder="e.g. Hardware replaced">' +
      '<p class="small text-muted mt-3 mb-0">The device will be refused the next time it authenticates online. A device that is currently offline keeps working until it next connects (or until any offline grace period ends). The device record and link history are preserved, and the license can then be activated on a new device.</p>';
    $('sdModalFooter').innerHTML = '<button type="button" class="btn btn-outline-secondary" data-bs-dismiss="modal">Cancel</button><button type="button" class="btn btn-danger" id="sdUnlinkGo">Unlink</button>';
    $('sdUnlinkGo').addEventListener('click', async (ev) => {
      const btn = ev.currentTarget;
      btn.disabled = true;
      try {
        const res = await api(API + '/' + state.storeId + '/licenses/' + lic.license_id + '/unlink', {
          method: 'POST',
          body: JSON.stringify({ device_id: dev.device_id, reason: $('sdUnlinkReason').value }),
        });
        applyWorkspace(res.workspace);
        closeModal();
      } catch (err) {
        showError(err.message);
        btn.disabled = false;
      }
    });
  }

  /* ---------- events ---------- */
  function bindPanelEvents() {
    panel().addEventListener('change', (e) => {
      const target = e.target;
      if (target.matches('#sdSelectAll')) {
        if (target.checked && state.data) {
          state.data.licenses.forEach(l => state.selected.add(Number(l.license_id)));
        } else {
          state.selected.clear();
        }
        render();
        return;
      }
      const checkbox = target.closest('[data-sd-license-select]');
      if (!checkbox) return;
      const licenseId = Number(checkbox.getAttribute('data-sd-license-select'));
      if (checkbox.checked) state.selected.add(licenseId);
      else state.selected.delete(licenseId);
      render();
    });
    panel().addEventListener('click', async (e) => {
      const copy = e.target.closest('[data-sd-copy]');
      if (copy) {
        const key = copy.getAttribute('data-sd-copy');
        try {
          if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(key);
          else {
            const ta = document.createElement('textarea');
            ta.value = key; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove();
          }
          const old = copy.textContent;
          copy.textContent = 'Copied';
          setTimeout(() => { copy.textContent = old; }, 1200);
        } catch (_) {
          window.prompt('Copy license key:', key);
        }
        return;
      }
      const detail = e.target.closest('[data-sd-detail]');
      if (detail) openDetail(detail.getAttribute('data-sd-detail'));
    });
  }

  function init() {
    if (state.inited || !panel()) return;
    state.inited = true;
    initSplitter();
    bindPanelEvents();
    renderEmpty();
  }

  window.AwsumStoreDevices = { init, show, clear };
})();
