(function (root, factory) {
  'use strict';
  if (typeof module === 'object' && module.exports) module.exports = factory(require('./money.js'));
  else root.AwsumMoneyKeypad = factory(root.AwsumMoneyInput);
})(typeof globalThis === 'object' ? globalThis : this, function (money) {
  'use strict';

  // Common Money Keypad: an on-screen cents-based keypad for any AwsumMoneyInput field.
  // Opt in by making the field's "$" prefix a button:
  //   <div class="input-group"><button type="button" class="input-group-text" data-money-keypad>$</button><input data-awsum-money ...></div>
  // (or data-money-keypad="<input id>" when the button is elsewhere). Clicks are delegated, so dynamic forms need no binding.
  // Formatting, cents entry and validation all come from AwsumMoneyInput; this file only adds the keypad UI.
  // Styles: /static/css/awsum-money-keypad.css.
  const GAP = 4;
  const MARGIN = 8;
  const KEYS = [
    ['7', '7'], ['8', '8'], ['9', '9'], ['back', '⌫', 'Backspace'],
    ['4', '4'], ['5', '5'], ['6', '6'], ['clear', 'C', 'Clear'],
    ['1', '1'], ['2', '2'], ['3', '3'], ['ok', 'OK'],
    ['0', '0'], ['00', '00'],
  ];

  let panel = null;
  let state = null; // { input, trigger, anchor, cents, fresh }

  // Below the anchor when it fits, else above, else as close as possible; always inside the viewport.
  function place(anchor, size, viewport) {
    const maxLeft = Math.max(MARGIN, viewport.width - size.width - MARGIN);
    const maxTop = Math.max(MARGIN, viewport.height - size.height - MARGIN);
    const left = Math.min(Math.max(anchor.left, MARGIN), maxLeft);
    let top = anchor.bottom + GAP;
    if (top + size.height > viewport.height - MARGIN) {
      const above = anchor.top - GAP - size.height;
      top = above >= MARGIN ? above : Math.min(Math.max(top, MARGIN), maxTop);
    }
    return { left: Math.round(left), top: Math.round(top) };
  }

  function findInput(trigger) {
    const target = trigger.getAttribute('data-money-keypad');
    if (target) return document.getElementById(target);
    const group = trigger.closest('.input-group');
    return group ? group.querySelector('input[data-awsum-money]') : null;
  }

  function build() {
    panel = document.createElement('div');
    panel.className = 'awsum-money-keypad';
    panel.hidden = true;
    panel.tabIndex = -1;
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-label', 'Amount keypad');
    panel.innerHTML = '<div class="awsum-money-keypad-display"><span class="awsum-money-keypad-currency">$</span>'
      + '<output class="awsum-money-keypad-value" aria-live="polite"></output></div>'
      + '<div class="awsum-money-keypad-error" role="alert"></div>'
      + '<div class="awsum-money-keypad-keys">'
      + KEYS.map(([key, text, label]) => `<button type="button" class="awsum-money-key awsum-money-key-${key}" data-key="${key}"`
        + `${label ? ` aria-label="${label}"` : ''}>${text}</button>`).join('')
      + '</div>';
    panel.addEventListener('click', (event) => {
      const button = event.target.closest('[data-key]');
      if (button) press(button.dataset.key);
    });
    panel.addEventListener('keydown', onKeydown);
    document.body.appendChild(panel);
  }

  function render() {
    const value = panel.querySelector('.awsum-money-keypad-value');
    value.textContent = state.cents == null ? '0.00' : money.format(money.fromMinorUnits(state.cents));
    value.classList.toggle('is-blank', state.cents == null);
    showError('');
  }

  function showError(message) {
    panel.querySelector('.awsum-money-keypad-error').textContent = message;
    panel.classList.toggle('has-error', !!message);
  }

  function position() {
    if (!state) return;
    const rect = state.anchor.getBoundingClientRect();
    const { left, top } = place(rect, { width: panel.offsetWidth, height: panel.offsetHeight },
      { width: document.documentElement.clientWidth, height: window.innerHeight });
    panel.style.left = `${left}px`;
    panel.style.top = `${top}px`;
  }

  function press(key) {
    if (!state) return;
    if (key === 'ok') return commit();
    if (key === 'clear') state.cents = null;
    else if (key === 'back') state.cents = money.popDigit(state.cents);
    else {
      // The loaded amount is shown first; the first digit starts a new amount (POS behavior).
      let cents = state.fresh ? null : state.cents;
      for (const char of key) cents = money.pushDigit(cents, char);
      state.cents = cents;
    }
    state.fresh = false;
    render();
  }

  function commit() {
    const value = state.cents == null ? '' : money.fromMinorUnits(state.cents);
    // Validate with the field's own rules (required/min/max) before touching the field.
    const probe = state.input.cloneNode(false);
    probe.value = value;
    if (!money.validate(probe)) {
      showError(probe.validationMessage);
      return;
    }
    const { input } = state;
    money.setValue(input, value);
    close(true);
    // Same events as typing, so live calculations, dirty state and error clearing keep working.
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
  }

  function onKeydown(event) {
    const key = event.key;
    let action = null;
    if (/^[0-9]$/.test(key)) action = key;
    else if (key === 'Backspace') action = 'back';
    else if (key === 'Delete' || key === 'c' || key === 'C') action = 'clear';
    else if (key === 'Enter') action = 'ok';
    else if (key === 'Escape') { event.preventDefault(); close(true); return; }
    if (!action) return;
    event.preventDefault();
    press(action);
  }

  function onOutside(event) {
    if (!state || panel.contains(event.target) || state.trigger.contains(event.target)) return;
    close(false);
  }

  function open(input, trigger) {
    if (!input || input.readOnly || input.disabled) return false;
    if (!panel) build();
    if (state) close(false);
    money.bind(input);
    trigger = trigger || input;
    state = {
      input, trigger, anchor: trigger.closest('.input-group') || input,
      cents: money.minorUnitsOf(input.value), fresh: true,
    };
    trigger.setAttribute('aria-expanded', 'true');
    panel.hidden = false;
    render();
    position();
    document.addEventListener('pointerdown', onOutside, true);
    window.addEventListener('resize', position);
    window.addEventListener('scroll', position, true);
    panel.focus();
    return true;
  }

  // Closing without OK leaves the field unchanged.
  function close(restoreFocus) {
    if (!state) return;
    const { trigger } = state;
    trigger.setAttribute('aria-expanded', 'false');
    panel.hidden = true;
    state = null;
    document.removeEventListener('pointerdown', onOutside, true);
    window.removeEventListener('resize', position);
    window.removeEventListener('scroll', position, true);
    if (restoreFocus && trigger.focus) trigger.focus();
  }

  if (typeof document !== 'undefined') {
    document.addEventListener('click', (event) => {
      const trigger = event.target.closest && event.target.closest('[data-money-keypad]');
      if (!trigger) return;
      event.preventDefault();
      const input = findInput(trigger);
      if (state && state.input === input) close(true);
      else open(input, trigger);
    });
  }

  return Object.freeze({
    open, close, place,
    isOpen: () => !!state,
    get element() { return panel; },
  });
});
