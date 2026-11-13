/* AWSUM Common Date (AwsumDateInput): the one date format, parser, validator, input and picker for every screen.

Standard (Nova, 2026-11-13): screens show and accept MM-DD-YYYY (04-22-1961); DB / API keep ISO YYYY-MM-DD.
- format(iso) -> 'MM-DD-YYYY'; formatDateTime(stored) -> 'MM-DD-YYYY hh:mm AM' (the stored text, no time-zone shift).
- parse(text) -> ISO, '' (empty) or null (not a real date). Accepts MM-DD-YYYY, M-D-YYYY, MMDDYYYY, ISO and the
  earlier MM/DD/YYYY (compatibility while other screens still show slashes).
- validate(iso, rules) -> '' or the error message (required / min / max, with optional custom messages).
- bind(input, rules) turns a text input into the date input: MM-DD-YYYY mask, calendar button + picker (min / max
  respected), blur check with the error shown in the field's .invalid-feedback, getValue() -> ISO for the API.
  bindAll(root) binds every input[data-awsum-date] (rules from data-date-min / -max ('today' allowed) / -required /
  -label / -max-message / -min-message).
The picker is the master_management.html picker made common (its screens move here in a later step); no screen may
build its own date picker. Load with /static/css/awsum-date.css.
*/
(function (root, factory) {
  'use strict';
  const api = factory(root);
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.AwsumDateInput = api;
})(typeof globalThis === 'object' ? globalThis : this, function (root) {
  'use strict';

  const FORMAT = 'MM-DD-YYYY';
  const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October',
    'November', 'December'];
  const YEAR_WINDOW = 100;
  const pad = (n) => String(n).padStart(2, '0');

  function isValidIso(value) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value == null ? '' : value).trim());
    if (!m) return false;
    const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
    const date = new Date(Date.UTC(y, mo - 1, d));
    return y >= 1 && date.getUTCFullYear() === y && date.getUTCMonth() === mo - 1 && date.getUTCDate() === d;
  }

  function toIso(y, mo, d) {
    const iso = `${String(y).padStart(4, '0')}-${pad(mo)}-${pad(d)}`;
    return isValidIso(iso) ? iso : null;
  }

  /** ISO (or an ISO date-time) -> 'MM-DD-YYYY'; '' when empty or not a date. */
  function format(value) {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value == null ? '' : value).trim());
    return m ? `${m[2]}-${m[3]}-${m[1]}` : '';
  }

  /** A stored timestamp 'YYYY-MM-DD[T ]HH:MM...' -> 'MM-DD-YYYY hh:mm AM/PM' exactly as stored; empty -> fallback. */
  function formatDateTime(value, empty) {
    const m = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/.exec(String(value == null ? '' : value));
    if (!m) return empty === undefined ? '' : empty;
    const hour = Number(m[4]);
    return `${m[2]}-${m[3]}-${m[1]} ${pad(hour % 12 || 12)}:${m[5]} ${hour < 12 ? 'AM' : 'PM'}`;
  }

  /** Screen text -> ISO; '' for empty; null when it is not a real date in an accepted form. */
  function parse(value) {
    const text = String(value == null ? '' : value).trim();
    if (!text) return '';
    let m = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(text);
    if (m) return toIso(m[1], m[2], m[3]);
    m = /^(\d{1,2})[-/](\d{1,2})[-/](\d{4})$/.exec(text);
    if (m) return toIso(m[3], m[1], m[2]);
    m = /^(\d{2})(\d{2})(\d{4})$/.exec(text);
    if (m) return toIso(m[3], m[1], m[2]);
    return null;
  }

  /** Today in the viewer's local calendar, as ISO. */
  function today() {
    const d = new Date();
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  }

  const resolve = (bound) => (bound === 'today' ? today() : bound || '');

  /**
   * '' when valid, else the message. rules: {label, required, min, max ('today' allowed), minMessage, maxMessage}.
   * value may be ISO, screen text or null (= not a date).
   */
  function validate(value, rules) {
    const r = rules || {};
    const label = r.label || 'Date';
    const iso = value === null ? null : (isValidIso(value) ? value : parse(value));
    if (iso === '') return r.required ? `${label} is required.` : '';
    if (iso === null) return `Enter ${label} as ${FORMAT}.`;
    const min = resolve(r.min);
    const max = resolve(r.max);
    if (min && iso < min) return r.minMessage || `${label} must be on or after ${format(min)}.`;
    if (max && iso > max) return r.maxMessage || `${label} must be on or before ${format(max)}.`;
    return '';
  }

  /** Typing mask: digits only with dashes after MM and DD; a typed '-' or '/' after one digit pads it (4- -> 04-). */
  function mask(text) {
    const raw = String(text || '');
    if (/^\d{4}-/.test(raw)) return raw.slice(0, 10); // pasted ISO: kept, normalized on blur
    const parts = raw.split(/[-/]/);
    const digits = parts.map((part, i) => {
      const d = part.replace(/\D/g, '');
      return i < 2 && i < parts.length - 1 && d.length === 1 ? `0${d}` : d;
    }).join('').slice(0, 8);
    return withDashes(digits, /[-/]$/.test(raw));
  }
  function withDashes(digits, trailing) {
    let out = digits.slice(0, 2);
    if (digits.length > 2 || (trailing && digits.length === 2)) out += '-' + digits.slice(2, 4);
    if (digits.length > 4 || (trailing && digits.length === 4)) out += '-' + digits.slice(4, 8);
    return out;
  }

  // ---------- DOM: input binding ----------
  const bound = typeof WeakMap === 'function' ? new WeakMap() : null;
  const doc = root && root.document;

  function rulesFromData(input) {
    const d = input.dataset || {};
    return {
      label: d.dateLabel || (input.labels && input.labels[0] ? input.labels[0].textContent.replace('*', '').trim() : ''),
      required: d.dateRequired === 'true' || input.required, min: d.dateMin || '', max: d.dateMax || '',
      minMessage: d.dateMinMessage || '', maxMessage: d.dateMaxMessage || '',
    };
  }

  function feedbackOf(input) {
    const group = input.closest('.awsum-date-group') || input;
    let next = group.nextElementSibling;
    while (next && !next.classList.contains('invalid-feedback')) next = next.nextElementSibling;
    return next;
  }

  function showError(input, message) {
    input.classList.toggle('is-invalid', !!message);
    input.setAttribute('aria-invalid', message ? 'true' : 'false');
    const box = feedbackOf(input);
    if (box) box.textContent = message || '';
  }

  function bind(input, rules) {
    if (!input || !bound || bound.has(input)) return input;
    const state = { rules: Object.assign(rulesFromData(input), rules || {}) };
    bound.set(input, state);
    input.type = 'text';
    input.placeholder = FORMAT;
    input.inputMode = 'numeric';
    input.autocomplete = 'off';
    input.maxLength = 10;
    input.dataset.awsumDate = '';
    input.classList.add('awsum-date-input');
    if (input.value) setValue(input, parse(input.value) || input.value);
    if (!input.closest('.awsum-date-group')) {
      const group = doc.createElement('div');
      group.className = 'input-group has-validation awsum-date-group';
      input.parentNode.insertBefore(group, input);
      group.appendChild(input);
      const button = doc.createElement('button');
      button.type = 'button';
      button.className = 'btn btn-outline-secondary awsum-date-trigger';
      button.setAttribute('aria-label', `Open calendar${state.rules.label ? ` for ${state.rules.label}` : ''}`);
      button.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>';
      group.appendChild(button);
      button.addEventListener('click', (event) => {
        event.preventDefault();
        if (!input.disabled && !input.readOnly) openPicker(input);
      });
    }
    input.addEventListener('input', () => {
      const masked = mask(input.value);
      if (masked !== input.value) input.value = masked;
      if (input.classList.contains('is-invalid')) showError(input, '');
    });
    input.addEventListener('blur', () => { check(input); });
    input.addEventListener('keydown', (event) => {
      if (event.key === 'ArrowDown' && event.altKey) { event.preventDefault(); openPicker(input); }
    });
    return input;
  }

  function bindAll(container) {
    if (!container) return;
    container.querySelectorAll('input[data-awsum-date]').forEach((input) => bind(input));
  }

  /** Replace / extend the rules of a bound input (e.g. a min that depends on another field). */
  function setRules(input, rules) {
    const state = bound && bound.get(input);
    if (state) Object.assign(state.rules, rules || {});
  }

  /** Shows ISO (or '') as MM-DD-YYYY and clears the field error. */
  function setValue(input, iso) {
    if (!input) return;
    input.value = isValidIso(iso) ? format(iso) : (iso || '');
    showError(input, '');
  }

  /** The value for the API: ISO when valid, '' when empty, otherwise the typed text (the server refuses it). */
  function getValue(input) {
    if (!input) return '';
    const iso = parse(input.value);
    return iso === null ? String(input.value).trim() : iso;
  }

  /** Normalizes the text to MM-DD-YYYY when it is a date, shows / clears the error; true when valid. */
  function check(input) {
    const state = bound && bound.get(input);
    const iso = parse(input.value);
    if (iso) input.value = format(iso);
    const message = validate(iso, state ? state.rules : {});
    showError(input, message);
    return !message;
  }

  // ---------- DOM: the common picker (one popover per page) ----------
  let picker = null;
  let active = null;

  function ensurePicker() {
    if (picker) return picker;
    picker = doc.createElement('div');
    picker.className = 'awsum-date-picker is-hidden';
    picker.setAttribute('role', 'dialog');
    picker.setAttribute('aria-label', 'Choose a date');
    doc.body.appendChild(picker);
    picker.addEventListener('mousedown', (event) => event.preventDefault()); // keep focus (no blur check meanwhile)
    picker.addEventListener('click', (event) => {
      const button = event.target.closest('[data-date-action]');
      if (!button || !active || button.disabled) return;
      const action = button.dataset.dateAction;
      if (action === 'prev' || action === 'next') {
        active.month += action === 'prev' ? -1 : 1;
        if (active.month < 0) { active.month = 11; active.year -= 1; }
        if (active.month > 11) { active.month = 0; active.year += 1; }
        render();
      } else if (action === 'today') {
        choose(today());
      } else if (action === 'clear') {
        choose('');
      } else if (action === 'day') {
        choose(button.dataset.iso);
      }
    });
    picker.addEventListener('change', (event) => {
      const control = event.target.closest('[data-date-action]');
      if (!control || !active) return;
      const n = Number(control.value);
      if (control.dataset.dateAction === 'month' && n >= 0 && n <= 11) active.month = n;
      if (control.dataset.dateAction === 'year' && Number.isInteger(n)) active.year = n;
      render();
    });
    doc.addEventListener('mousedown', (event) => {
      if (!active || picker.contains(event.target)) return;
      const group = active.input.closest('.awsum-date-group');
      if (group && group.contains(event.target)) return;
      closePicker();
    });
    doc.addEventListener('keydown', (event) => { if (event.key === 'Escape' && active) closePicker(); });
    root.addEventListener('resize', () => { if (active) position(active.input); });
    root.addEventListener('scroll', () => { if (active) position(active.input); }, true);
    return picker;
  }

  function choose(iso) {
    const input = active.input;
    setValue(input, iso);
    closePicker();
    check(input);
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    input.focus();
  }

  function position(input) {
    const rect = (input.closest('.awsum-date-group') || input).getBoundingClientRect();
    const width = 286;
    const margin = 8;
    const left = Math.max(margin, Math.min(rect.left, root.innerWidth - width - margin));
    const below = rect.bottom + 6;
    const top = below + 340 > root.innerHeight && rect.top > 346 ? rect.top - 346 : below;
    picker.style.left = `${left}px`;
    picker.style.top = `${top}px`;
  }

  function openPicker(input) {
    ensurePicker();
    const state = bound.get(input);
    const selected = parse(input.value) || '';
    const rules = state ? state.rules : {};
    const base = selected || (resolve(rules.max) && today() > resolve(rules.max) ? resolve(rules.max) : today());
    active = { input, selected, rules, year: Number(base.slice(0, 4)), month: Number(base.slice(5, 7)) - 1 };
    render();
    position(input);
    picker.classList.remove('is-hidden');
  }

  function closePicker() {
    if (picker) picker.classList.add('is-hidden');
    active = null;
  }

  function render() {
    const { year, month, selected, rules } = active;
    const min = resolve(rules.min);
    const max = resolve(rules.max);
    const now = today();
    const first = new Date(Date.UTC(year, month, 1)).getUTCDay();
    const cells = [];
    for (let i = 0; i < 42; i += 1) {
      const date = new Date(Date.UTC(year, month, i - first + 1));
      const iso = `${String(date.getUTCFullYear()).padStart(4, '0')}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}`;
      const off = (min && iso < min) || (max && iso > max);
      const cls = ['awsum-date-day', date.getUTCMonth() !== month ? 'is-outside' : '', iso === now ? 'is-today' : '',
        iso === selected ? 'is-selected' : ''].filter(Boolean).join(' ');
      cells.push(`<button type="button" class="${cls}" data-date-action="day" data-iso="${iso}"${off ? ' disabled' : ''} aria-label="${format(iso)}">${date.getUTCDate()}</button>`);
    }
    const topYear = Math.max(year + 10, max ? Number(max.slice(0, 4)) : Number(now.slice(0, 4)) + 10);
    const years = [];
    for (let y = topYear; y >= Math.min(year, topYear) - YEAR_WINDOW; y -= 1) years.push(y);
    const prevOff = min && `${year}-${pad(month + 1)}-01` <= min;
    const nextOff = max && (month === 11 ? `${year + 1}-01-01` : `${year}-${pad(month + 2)}-01`) > max;
    picker.innerHTML = `
      <div class="awsum-date-head">
        <button type="button" class="awsum-date-nav" data-date-action="prev" aria-label="Previous month"${prevOff ? ' disabled' : ''}>&#8249;</button>
        <select class="awsum-date-select month" data-date-action="month" aria-label="Month">${MONTHS.map((name, i) => `<option value="${i}"${i === month ? ' selected' : ''}>${name}</option>`).join('')}</select>
        <select class="awsum-date-select year" data-date-action="year" aria-label="Year">${years.map((y) => `<option value="${y}"${y === year ? ' selected' : ''}>${y}</option>`).join('')}</select>
        <button type="button" class="awsum-date-nav" data-date-action="next" aria-label="Next month"${nextOff ? ' disabled' : ''}>&#8250;</button>
      </div>
      <div class="awsum-date-week">${['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'].map((d) => `<span>${d}</span>`).join('')}</div>
      <div class="awsum-date-grid">${cells.join('')}</div>
      <div class="awsum-date-foot">
        <button type="button" class="btn btn-sm btn-outline-secondary" data-date-action="clear">Clear</button>
        <button type="button" class="btn btn-sm btn-primary" data-date-action="today"${(min && now < min) || (max && now > max) ? ' disabled' : ''}>Today</button>
      </div>`;
  }

  return {
    FORMAT, format, formatDateTime, parse, isValidIso, validate, today, mask,
    bind, bindAll, setRules, setValue, getValue, check, open: openPicker, close: closePicker,
  };
});
