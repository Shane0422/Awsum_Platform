(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.AwsumOptionGroup = api;
})(typeof globalThis === 'object' ? globalThis : this, function () {
  'use strict';

  // Common Option Group: every choice visible at once, one selected (native radios, so arrow keys work).
  // multiple: true renders native checkboxes instead; any number can be selected and the value is an array.
  // Styles: /static/css/awsum-option-group.css. The group value is read with getValue(), never from an input name.
  const bound = new WeakSet();
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  // inherits: '' means "inherit" (not "empty"), so the Clear button is labelled Reset and stays available when required.
  //   While inheriting, the value in effect (setInherited) is shown CHECKED but the group stays in the "inherited"
  //   state: getValue() returns '' so saving keeps NULL. Only a user click makes it an Override; Reset inherits again.
  function html({ id, name, options, value, clearable = false, required = false, inherits = false, ariaLabel = '', multiple = false }) {
    const selected = new Set(multiple ? toList(value) : [value == null ? '' : String(value)]);
    const current = multiple ? (selected.size ? 'x' : '') : [...selected][0];
    const items = (options || []).map(({ value: v, label }) => {
      const checked = selected.has(String(v)) ? ' checked' : '';
      return `<label class="awsum-option"><input class="awsum-option-input" type="${multiple ? 'checkbox' : 'radio'}" name="${esc(name)}" value="${esc(v)}"${checked}>`
        + `<span class="awsum-option-body"><span class="awsum-option-dot" aria-hidden="true"></span>${esc(label)}</span></label>`;
    }).join('');
    const classes = ['awsum-option-group', multiple ? 'is-multiple' : '', current ? 'has-value' : '', required ? 'is-required' : ''].filter(Boolean).join(' ');
    return `<div class="${classes}" id="${esc(id)}" role="${multiple ? 'group' : 'radiogroup'}" data-awsum-option-group data-name="${esc(name)}"${multiple ? ' data-option-multiple' : ''}`
      + `${required ? ' aria-required="true"' : ''}${inherits ? ` data-option-inherits data-option-state="${current && !multiple ? 'override' : 'inherited'}"` : ''}${ariaLabel ? ` aria-label="${esc(ariaLabel)}"` : ''}>${items}`
      + `${clearable ? `<button type="button" class="awsum-option-clear" data-option-clear>${inherits ? 'Reset' : 'Clear'}</button>` : ''}`
      + `${inherits && !multiple ? '<span class="awsum-option-source" aria-live="polite"></span>' : ''}</div>`;
  }

  const toList = (value) => (Array.isArray(value) ? value : (value == null || value === '' ? [] : [value])).map(String);
  const isMultiple = (group) => group.hasAttribute('data-option-multiple');
  const inputs = (group) => [...group.querySelectorAll('input.awsum-option-input')];
  const inheritsMode = (group) => group.hasAttribute('data-option-inherits') && !isMultiple(group);
  const inheriting = (group) => inheritsMode(group) && group.dataset.optionState !== 'override';
  // has-value = a real (override / chosen) value. In inherit mode the checked inherited value is not one.
  function sync(group) {
    const checked = inputs(group).some((input) => input.checked);
    const inherited = inheriting(group);
    group.classList.toggle('has-value', checked && !inherited);
    group.classList.toggle('is-inheriting', checked && inherited);
    const source = group.querySelector('.awsum-option-source');
    if (source) source.textContent = !checked ? '' : (inherited ? (group.dataset.inheritedTitle || 'Inherited') : 'Override');
  }
  function checkOnly(group, wanted) {
    inputs(group).forEach((input) => { input.checked = wanted !== '' && input.value === wanted; });
  }

  // Radio mode: the selected value or ''. Multiple mode: an array of the selected values ([] when none).
  function getValue(group) {
    if (isMultiple(group)) return inputs(group).filter((input) => input.checked).map((input) => input.value);
    if (inheriting(group)) return ''; // the checked radio only shows the inherited value
    const checked = inputs(group).find((input) => input.checked);
    return checked ? checked.value : '';
  }

  function setValue(group, value) {
    if (isMultiple(group)) {
      const wanted = new Set(toList(value));
      inputs(group).forEach((input) => { input.checked = wanted.has(input.value); });
      sync(group);
      return;
    }
    const wanted = value == null ? '' : String(value);
    if (inheritsMode(group)) {
      // '' = inherit: show the inherited value checked. Anything else is a saved Override.
      group.dataset.optionState = wanted === '' ? 'inherited' : 'override';
      checkOnly(group, wanted === '' ? (group.dataset.inheritedValue || '') : wanted);
      sync(group);
      return;
    }
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
    if (!inheritsMode(group)) return;
    group.dataset.inheritedValue = wanted;
    group.dataset.inheritedTitle = title;
    if (inheriting(group)) checkOnly(group, wanted); // follow the new inherited value (e.g. Category III changed)
    sync(group);
  }

  function setRequired(group, required) {
    group.classList.toggle('is-required', !!required);
    if (required) group.setAttribute('aria-required', 'true');
    else group.removeAttribute('aria-required');
  }

  function bind(group) {
    if (bound.has(group)) return group;
    bound.add(group);
    // A user pick (mouse, keyboard or label click) is an Override, even when it equals the inherited value.
    group.addEventListener('click', (event) => {
      if (!inheritsMode(group) || !event.target.matches('input.awsum-option-input')) return;
      group.dataset.optionState = 'override';
      sync(group); // no native change fires when the clicked radio was already checked (the inherited one)
    });
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
