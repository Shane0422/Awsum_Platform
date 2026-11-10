/* AWSUM Common Code Input (window.AwsumCodeInput) - upper-case code fields such as Location Codes.
 *
 * Same pattern as AwsumBarcodeInput: mark the input, then bind (screens call bindAll after rendering a form).
 *
 *   <input data-awsum-code ...>          AwsumCodeInput.bindAll(container)
 *
 * - Lower-case letters become upper-case immediately: typing, paste, drop and autofill (k01-s01 -> K01-S01).
 *   The caret / selection stays where it was. Digits, hyphens and every other character are kept as typed.
 * - IME composition is left alone until it finishes, then converted.
 * - Leaving the field (change) also removes leading / trailing spaces (and upper-cases a value set by a script).
 * The server applies the same rule again on save (backend/services/location_code.py).
 * Used by Category III / MD Master Sales Floor and Warehouse Location; later Receiving / Transfer / Issue.
 *
 * Decimal mode (minimal extension, same binding):  <input data-awsum-decimal data-decimal-scale="3" [data-decimal-signed]>
 * - Only digits and one decimal point stay (no commas); decimal places are cut to data-decimal-scale (0 = whole
 *   numbers); '-' is kept only with data-decimal-signed. Leaving the field drops a trailing point and adds a
 *   leading 0 (".5" -> "0.5"). Not for money: amounts use AwsumMoneyInput. The server re-validates every value.
 * Used by Common Maintenance NUMBER Values; later PK/Size.
 */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.AwsumCodeInput = api;
})(typeof globalThis === 'object' ? globalThis : this, function () {
  'use strict';

  const bindings = new WeakMap();

  function normalize(value) {
    return String(value == null ? '' : value).toUpperCase();
  }

  function normalizeDecimal(value, scale, signed) {
    const raw = String(value == null ? '' : value).replace(/[^0-9.-]/g, '');
    const negative = !!signed && raw.startsWith('-');
    const text = raw.replace(/-/g, '');
    const dot = text.indexOf('.');
    const places = Math.max(0, Math.floor(Number(scale) || 0));
    const whole = dot < 0 ? text : text.slice(0, dot);
    const fraction = dot < 0 ? '' : text.slice(dot + 1).replace(/\./g, '').slice(0, places);
    return (negative ? '-' : '') + whole + (dot >= 0 && places > 0 ? `.${fraction}` : '');
  }

  // Finished value (change): no trailing point, no lone '-', '.5' -> '0.5'.
  function finishDecimal(value, scale, signed) {
    let text = normalizeDecimal(String(value == null ? '' : value).trim(), scale, signed);
    if (text.endsWith('.')) text = text.slice(0, -1);
    if (text === '-') return '';
    return text.replace(/^(-?)\./, (_match, sign) => `${sign}0.`);
  }

  const isDecimal = (input) => input.hasAttribute('data-awsum-decimal');
  const decimalOf = (input, value, finish) => (finish ? finishDecimal : normalizeDecimal)(
    value, input.dataset.decimalScale, input.hasAttribute('data-decimal-signed'));

  function apply(input) {
    const next = isDecimal(input) ? decimalOf(input, input.value, false) : normalize(input.value);
    if (next === input.value) return;
    const start = input.selectionStart;
    const end = input.selectionEnd;
    const removed = input.value.length - next.length; // e.g. 'ß' -> 'SS' changes the length; decimals drop characters
    input.value = next;
    if (start != null && document.activeElement === input && (removed === 0 || isDecimal(input))) {
      const caret = Math.max(0, Math.min(next.length, start - Math.max(removed, 0)));
      try {
        if (removed === 0) input.setSelectionRange(start, end);
        else input.setSelectionRange(caret, caret);
      } catch (_) { /* type without selection support */ }
    }
  }

  function bind(input) {
    if (!input || bindings.has(input)) return input;
    const decimal = isDecimal(input);
    if (decimal) input.inputMode = 'decimal';
    else {
      input.dataset.awsumCode = '';
      input.autocapitalize = 'characters'; // mobile keyboards start in capitals
    }
    input.autocomplete = 'off';
    input.spellcheck = false;
    // Capture phase: the value is already upper-case when the screen's own input listeners run.
    const onInput = (event) => { if (!event.isComposing) apply(input); };
    const onComposition = () => apply(input);
    const onChange = () => {
      const next = decimal ? decimalOf(input, input.value, true) : normalize(input.value).trim();
      if (next !== input.value) input.value = next;
    };
    input.addEventListener('input', onInput, true);
    input.addEventListener('compositionend', onComposition);
    input.addEventListener('change', onChange, true);
    bindings.set(input, { onInput, onComposition, onChange });
    apply(input); // a value filled before binding
    return input;
  }

  function bindAll(container = document) {
    container.querySelectorAll('input[data-awsum-code], input[data-awsum-decimal]').forEach(bind);
  }

  function destroy(input) {
    const handlers = bindings.get(input);
    if (!handlers) return;
    input.removeEventListener('input', handlers.onInput, true);
    input.removeEventListener('compositionend', handlers.onComposition);
    input.removeEventListener('change', handlers.onChange, true);
    bindings.delete(input);
  }

  return Object.freeze({ bind, bindAll, destroy, normalize, normalizeDecimal, finishDecimal });
});
