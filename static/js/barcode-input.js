(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.AwsumBarcodeInput = api;
})(typeof globalThis === 'object' ? globalThis : this, function () {
  'use strict';

  // Scanners type the code then send Enter; Enter completes the barcode instead of submitting the form.
  const bindings = new WeakMap();

  function complete(input) {
    input.value = input.value.trim();
    input.dispatchEvent(new CustomEvent('awsum:barcode', { bubbles: true, detail: { value: input.value } }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
  }

  function bind(input) {
    if (bindings.has(input)) return input;
    input.dataset.awsumBarcode = '';
    input.autocomplete = 'off';
    input.spellcheck = false;
    const onKeyDown = (event) => {
      if (event.key !== 'Enter' || event.isComposing) return;
      event.preventDefault();
      event.stopPropagation();
      complete(input);
      input.select();
    };
    input.addEventListener('keydown', onKeyDown);
    bindings.set(input, { onKeyDown });
    return input;
  }

  function bindAll(container = document) {
    container.querySelectorAll('input[data-awsum-barcode]').forEach(bind);
  }

  function destroy(input) {
    const handlers = bindings.get(input);
    if (!handlers) return;
    input.removeEventListener('keydown', handlers.onKeyDown);
    bindings.delete(input);
  }

  return Object.freeze({ bind, bindAll, destroy });
});
