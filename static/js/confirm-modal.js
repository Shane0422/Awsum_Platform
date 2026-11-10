/* AWSUM Common Confirm Modal (window.AwsumConfirm).
 *
 * Same design and behavior as the existing master_management confirm (appConfirmModal), as a shared
 * component any page can load: static/css/awsum-confirm-modal.css + this file + Bootstrap Modal.
 * The markup is created on first use, so pages need no template include.
 *
 *   const result = await AwsumConfirm.show({
 *     title: 'Apply Inventory Type', message: 'Optional text.', variant: 'info' | 'warning' | 'danger' | 'success',
 *     details: [['Category III', 'Drinks'], ['Inventory Type', 'Stock -> Recipe']],   // optional label/value rows
 *     choices: [{ value: 'category', label: 'Category III only', description: '...' }, ...], // optional radios
 *     value: 'category',                       // initially selected choice (default: the first one)
 *     okText: 'Save', cancelText: 'Cancel',
 *   });
 *   // Cancel / close / Escape -> { confirmed: false }; OK -> { confirmed: true, choice: <selected value or null> }
 *
 * All text is set with textContent (never HTML).
 */
(function () {
  'use strict';

  const VARIANTS = ['info', 'warning', 'danger', 'success'];
  let root = null;
  let pending = null;

  function build() {
    if (root) return root;
    root = document.createElement('div');
    root.className = 'modal fade awsum-confirm-modal';
    root.id = 'awsumConfirmModal';
    root.tabIndex = -1;
    root.setAttribute('aria-labelledby', 'awsumConfirmTitle');
    root.setAttribute('aria-hidden', 'true');
    root.innerHTML = `
      <div class="modal-dialog modal-dialog-centered">
        <div class="modal-content">
          <div class="modal-header">
            <div class="awsum-confirm-head">
              <span class="awsum-confirm-icon" aria-hidden="true">!</span>
              <h5 class="modal-title" id="awsumConfirmTitle">Confirm</h5>
            </div>
            <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
          </div>
          <div class="modal-body">
            <p class="awsum-confirm-message" id="awsumConfirmMessage"></p>
            <dl class="awsum-confirm-details"></dl>
            <fieldset class="awsum-confirm-choices"><legend class="visually-hidden">Choose an option</legend></fieldset>
          </div>
          <div class="modal-footer">
            <button type="button" class="btn btn-sm awsum-confirm-cancel" data-bs-dismiss="modal">Cancel</button>
            <button type="button" class="btn btn-sm awsum-confirm-ok">OK</button>
          </div>
        </div>
      </div>`;
    document.body.appendChild(root);
    root.querySelector('.awsum-confirm-ok').addEventListener('click', () => {
      if (!pending) return;
      const checked = root.querySelector('.awsum-confirm-choices input:checked');
      finish({ confirmed: true, choice: checked ? checked.value : null });
      // Bootstrap ignores hide() while the open animation runs; close as soon as it has finished.
      const modal = window.bootstrap.Modal.getOrCreateInstance(root);
      if (root.dataset.state === 'shown') modal.hide();
      else root.addEventListener('shown.bs.modal', () => modal.hide(), { once: true });
    });
    // data-state: opening -> shown -> hidden (also lets tests and callers wait for a settled modal).
    root.addEventListener('show.bs.modal', () => { root.dataset.state = 'opening'; });
    root.addEventListener('shown.bs.modal', () => { root.dataset.state = 'shown'; });
    root.addEventListener('hidden.bs.modal', () => { root.dataset.state = 'hidden'; finish({ confirmed: false }); });
    return root;
  }

  function finish(result) {
    if (!pending) return;
    const resolve = pending;
    pending = null;
    resolve(result);
  }

  function fill(options) {
    const el = build();
    const variant = VARIANTS.includes(options.variant) ? options.variant : 'info';
    el.dataset.variant = variant;
    el.querySelector('.awsum-confirm-icon').textContent = variant === 'success' ? '✓' : '!';
    el.querySelector('.modal-title').textContent = options.title || 'Confirm';
    const message = el.querySelector('.awsum-confirm-message');
    message.textContent = options.message || '';
    message.hidden = !options.message;

    const details = el.querySelector('.awsum-confirm-details');
    details.replaceChildren();
    (options.details || []).forEach(([label, value]) => {
      const dt = document.createElement('dt');
      const dd = document.createElement('dd');
      dt.textContent = label;
      dd.textContent = value == null ? '' : String(value);
      details.append(dt, dd);
    });
    details.hidden = !details.children.length;

    const choices = el.querySelector('.awsum-confirm-choices');
    choices.querySelectorAll('label').forEach((label) => label.remove());
    const list = options.choices || [];
    const selected = list.some((c) => String(c.value) === String(options.value)) ? String(options.value)
      : (list[0] ? String(list[0].value) : null);
    list.forEach((choice, index) => {
      const label = document.createElement('label');
      label.className = 'awsum-confirm-choice';
      const input = document.createElement('input');
      input.type = 'radio';
      input.name = 'awsumConfirmChoice';
      input.className = 'form-check-input';
      input.value = String(choice.value);
      input.id = `awsumConfirmChoice${index}`;
      input.checked = input.value === selected;
      const text = document.createElement('span');
      const strong = document.createElement('strong');
      strong.textContent = choice.label;
      text.appendChild(strong);
      if (choice.description) {
        const small = document.createElement('small');
        small.textContent = choice.description;
        text.appendChild(small);
      }
      label.append(input, text);
      choices.appendChild(label);
    });
    choices.hidden = !list.length;

    el.querySelector('.awsum-confirm-ok').textContent = options.okText || 'OK';
    el.querySelector('.awsum-confirm-cancel').textContent = options.cancelText || 'Cancel';
    return selected;
  }

  function show(options = {}) {
    if (!window.bootstrap || !window.bootstrap.Modal) {
      // Same fallback as the existing confirm: the browser dialog (choices keep their default).
      const ok = window.confirm([options.title, options.message].filter(Boolean).join('\n\n'));
      const first = (options.choices || [])[0];
      return Promise.resolve(ok ? { confirmed: true, choice: options.value ?? (first ? String(first.value) : null) } : { confirmed: false });
    }
    finish({ confirmed: false }); // a newer request replaces an open one
    const el = build();
    const modal = window.bootstrap.Modal.getOrCreateInstance(el);
    // Wait until a closing/open modal is fully hidden, so its "hidden" event cannot cancel this request.
    const visible = el.classList.contains('show') || el.style.display === 'block'; // display stays while hiding
    const ready = visible
      ? new Promise((resolve) => { el.addEventListener('hidden.bs.modal', resolve, { once: true }); modal.hide(); })
      : Promise.resolve();
    return ready.then(() => new Promise((resolve) => {
      fill(options);
      pending = resolve;
      modal.show();
    }));
  }

  window.AwsumConfirm = { show };
})();
