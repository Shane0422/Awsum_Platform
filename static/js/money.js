(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.AwsumMoneyInput = api;
})(typeof globalThis === 'object' ? globalThis : this, function () {
  'use strict';

  const bindings = new WeakMap();
  const message = 'Enter a monetary amount with at most 2 decimal places (for example, 2,500.50).';

  function decimal(value) {
    if (value == null || value === '') return '';
    if (typeof value !== 'string' && typeof value !== 'number') throw new TypeError(message);
    if (typeof value === 'number' && !Number.isFinite(value)) throw new TypeError(message);
    let text = String(value).trim();
    if (typeof value === 'number' && /e/i.test(text)) {
      const [coefficient, exponent] = text.split('e');
      const negative = coefficient.startsWith('-');
      const parts = coefficient.replace(/^-/, '').split('.');
      const digits = parts.join('');
      const point = parts[0].length + Number(exponent);
      text = (negative ? '-' : '') + (point <= 0 ? '0.' + '0'.repeat(-point) + digits
        : point >= digits.length ? digits + '0'.repeat(point - digits.length)
          : digits.slice(0, point) + '.' + digits.slice(point));
    }
    if (!text) return '';
    if (!/^-?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d*)?$/.test(text)
        && !/^-?\.\d+$/.test(text)) throw new TypeError(message);
    return text.replace(/,/g, '');
  }

  function fixed(value, round) {
    const text = decimal(value);
    if (!text) return '';
    const negative = text.startsWith('-');
    const [whole, fraction = ''] = text.replace(/^-/, '').split('.');
    if (!round && fraction.length > 2) throw new TypeError(message);
    let cents = BigInt(whole || '0') * 100n + BigInt(fraction.slice(0, 2).padEnd(2, '0'));
    if (round && fraction.length > 2 && fraction[2] >= '5') cents += 1n;
    return `${negative && cents !== 0n ? '-' : ''}${cents / 100n}.${String(cents % 100n).padStart(2, '0')}`;
  }

  function normalize(value) { return fixed(value, false); }

  function toMinorUnits(value) {
    const text = normalize(value);
    if (!text) throw new TypeError(message);
    return BigInt(text.replace('.', ''));
  }

  function fromMinorUnits(cents) {
    if (typeof cents !== 'bigint') throw new TypeError('Minor units must be a BigInt.');
    const absolute = cents < 0n ? -cents : cents;
    return `${cents < 0n ? '-' : ''}${absolute / 100n}.${String(absolute % 100n).padStart(2, '0')}`;
  }

  function format(value) {
    const text = fixed(value, true);
    if (!text) return '';
    const [whole, fraction] = text.split('.');
    return `${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}.${fraction}`;
  }

  function validate(input) {
    let error = '';
    try {
      const value = normalize(input.value);
      if (input.required && value === '') error = 'Enter a monetary amount.';
      if (value && input.dataset.moneyMin !== undefined
          && toMinorUnits(value) < toMinorUnits(input.dataset.moneyMin)) {
        error = `Amount must be at least ${format(input.dataset.moneyMin)}.`;
      }
      if (value && input.dataset.moneyMax !== undefined
          && toMinorUnits(value) > toMinorUnits(input.dataset.moneyMax)) {
        error = `Amount must be at most ${format(input.dataset.moneyMax)}.`;
      }
    } catch (errorValue) {
      if (!(errorValue instanceof TypeError)) throw errorValue;
      error = errorValue.message;
    }
    input.setCustomValidity(error);
    if (error) input.setAttribute('aria-invalid', 'true');
    else input.removeAttribute('aria-invalid');
    return !error;
  }

  function refresh(input) {
    if (validate(input)) input.value = format(input.value);
  }

  function bind(input) {
    if (bindings.has(input)) return input;
    input.type = 'text';
    input.inputMode = 'decimal';
    input.autocomplete = 'off';
    input.dataset.awsumMoney = '';
    input.classList.add('text-end');
    ['min', 'max'].forEach(key => {
      if (input.hasAttribute(key)) input.dataset[`money${key[0].toUpperCase()}${key.slice(1)}`] = input.getAttribute(key);
      input.removeAttribute(key);
    });
    input.removeAttribute('step');
    const onInput = () => validate(input);
    const onFocus = () => {
      if (validate(input)) input.value = decimal(input.value);
    };
    const onBlur = () => refresh(input);
    input.addEventListener('input', onInput);
    input.addEventListener('focus', onFocus);
    input.addEventListener('blur', onBlur);
    bindings.set(input, { onInput, onFocus, onBlur });
    refresh(input);
    return input;
  }

  function bindAll(container = document) {
    container.querySelectorAll('input[data-awsum-money]').forEach(bind);
  }

  function refreshAll(container = document) {
    container.querySelectorAll('input[data-awsum-money]').forEach(input => { bind(input); refresh(input); });
  }

  function setValue(input, value) {
    bind(input);
    // Programmatic values may be calculated beyond cents; user-entered precision is never silently rounded.
    input.value = format(value);
    validate(input);
  }

  function getValue(input) {
    if (!validate(input)) {
      input.reportValidity();
      throw new TypeError(`${input.name || input.id || 'Amount'}: ${input.validationMessage}`);
    }
    return normalize(input.value);
  }

  function destroy(input) {
    const handlers = bindings.get(input);
    if (!handlers) return;
    input.removeEventListener('input', handlers.onInput);
    input.removeEventListener('focus', handlers.onFocus);
    input.removeEventListener('blur', handlers.onBlur);
    bindings.delete(input);
  }

  return Object.freeze({ format, normalize, toMinorUnits, fromMinorUnits, bind, bindAll, refreshAll, setValue, getValue, validate, destroy });
});
