(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.AwsumOptionGroup = api;
})(typeof globalThis === 'object' ? globalThis : this, function () {
  'use strict';

  // Common Option Group: every choice visible at once, one selected (native radios, so arrow keys work).
  // Styles: /static/css/awsum-option-group.css. The group value is read with getValue(), never from a radio name.
  const bound = new WeakSet();
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  // inherits: '' means "inherit" (not "empty"), so the Clear button is labelled Reset and stays available when required.
  function html({ id, name, options, value, clearable = false, required = false, inherits = false, ariaLabel = '' }) {
    const current = value == null ? '' : String(value);
    const items = (options || []).map(({ value: v, label }) => {
      const checked = String(v) === current ? ' checked' : '';
      return `<label class="awsum-option"><input class="awsum-option-input" type="radio" name="${esc(name)}" value="${esc(v)}"${checked}>`
        + `<span class="awsum-option-body"><span class="awsum-option-dot" aria-hidden="true"></span>${esc(label)}</span></label>`;
    }).join('');
    const classes = ['awsum-option-group', current ? 'has-value' : '', required ? 'is-required' : ''].filter(Boolean).join(' ');
    return `<div class="${classes}" id="${esc(id)}" role="radiogroup" data-awsum-option-group data-name="${esc(name)}"`
      + `${required ? ' aria-required="true"' : ''}${inherits ? ' data-option-inherits' : ''}${ariaLabel ? ` aria-label="${esc(ariaLabel)}"` : ''}>${items}`
      + `${clearable ? `<button type="button" class="awsum-option-clear" data-option-clear>${inherits ? 'Reset' : 'Clear'}</button>` : ''}</div>`;
  }

  const inputs = (group) => [...group.querySelectorAll('input.awsum-option-input')];
  const sync = (group) => group.classList.toggle('has-value', inputs(group).some((input) => input.checked));

  function getValue(group) {
    const checked = inputs(group).find((input) => input.checked);
    return checked ? checked.value : '';
  }

  function setValue(group, value) {
    const wanted = value == null ? '' : String(value);
    inputs(group).forEach((input) => { input.checked = input.value === wanted; });
    sync(group);
  }

  // Updates one option's visible label (e.g. an "inherit" choice that shows the inherited value).
  function setOptionLabel(group, value, label) {
    const input = inputs(group).find((item) => item.value === String(value ?? ''));
    if (!input) return;
    const body = input.nextElementSibling;
    if (body.lastChild && body.lastChild.nodeType === 3) body.lastChild.textContent = label;
    else body.append(label);
  }

  // Marks the value that applies while nothing is selected (inherited or system default); title explains its source.
  function setInherited(group, value, title = '') {
    const wanted = value == null ? '' : String(value);
    inputs(group).forEach((input) => {
      const label = input.closest('.awsum-option');
      const on = wanted !== '' && input.value === wanted;
      label.classList.toggle('is-inherited', on);
      if (on && title) label.title = title; else label.removeAttribute('title');
    });
  }

  function setRequired(group, required) {
    group.classList.toggle('is-required', !!required);
    if (required) group.setAttribute('aria-required', 'true');
    else group.removeAttribute('aria-required');
  }

  function bind(group) {
    if (bound.has(group)) return group;
    bound.add(group);
    group.addEventListener('change', () => sync(group));
    group.addEventListener('click', (event) => {
      if (!event.target.closest('[data-option-clear]')) return;
      setValue(group, '');
      group.dispatchEvent(new Event('change', { bubbles: true }));
    });
    sync(group);
    return group;
  }

  function bindAll(container = document) {
    container.querySelectorAll('[data-awsum-option-group]').forEach(bind);
  }

  return Object.freeze({ html, bind, bindAll, getValue, setValue, setOptionLabel, setInherited, setRequired });
});
