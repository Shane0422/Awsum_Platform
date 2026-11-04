const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const money = require('../static/js/money.js');

test('required presentation examples and optional/zero values', () => {
  for (const [value, expected] of [[2500, '2,500.00'], [2500.5, '2,500.50'],
    [1000000, '1,000,000.00'], [0, '0.00'], [null, ''], ['', ''],
    ['-2500.5', '-2,500.50'], ['-0', '0.00'], ['.5', '0.50']]) {
    assert.equal(money.format(value), expected);
  }
});

test('exact decimal formatting, half-up rounding and exponent-valued calculations', () => {
  assert.equal(money.format('9007199254740993.99'), '9,007,199,254,740,993.99');
  assert.equal(money.format('1.005'), '1.01');
  assert.equal(money.format('-1.005'), '-1.01');
  assert.equal(money.format('999.999'), '1,000.00');
  assert.equal(money.format(1e-7), '0.00');
  assert.equal(money.format(1e21), '1,000,000,000,000,000,000,000.00');
});

test('serialization removes grouping and retains decimal precision without Number conversion', () => {
  assert.equal(money.normalize('2,500.5'), '2500.50');
  assert.equal(money.normalize('0002500'), '2500.00');
  assert.equal(money.normalize('9007199254740993.99'), '9007199254740993.99');
  assert.equal(money.normalize('2500.'), '2500.00');
});

test('invalid input is never stripped into a different amount or silently rounded', () => {
  for (const value of ['1,23', '25,00', '2,500,', '1e3', '12abc', '$2.00', '1 000',
    '--1', 'NaN', 'Infinity', '.', '-', true, {}, NaN, Infinity, '2.501']) {
    assert.throws(() => money.normalize(value), TypeError, String(value));
  }
});

test('common minor-unit helpers preserve existing half-up discount calculation', () => {
  const cents = (money.toMinorUnits('2,500.50') * (10000n - 1250n) + 5000n) / 10000n;
  assert.equal(money.fromMinorUnits(cents), '2187.94');
  assert.equal(money.fromMinorUnits(-5n), '-0.05');
  assert.throws(() => money.toMinorUnits(''), TypeError);
});

class Input extends EventTarget {
  constructor(value = '') {
    super();
    this.value = value;
    this.dataset = {};
    this.attributes = new Map();
    this.classList = { add: () => {} };
    this.name = 'amount';
  }
  hasAttribute(name) { return this.attributes.has(name); }
  getAttribute(name) { return this.attributes.get(name); }
  setAttribute(name, value) { this.attributes.set(name, value); }
  removeAttribute(name) { this.attributes.delete(name); }
  setCustomValidity(message) { this.validationMessage = message; }
  reportValidity() { this.reported = true; }
}

test('bind/focus/edit/blur and comma-free API value', () => {
  const input = new Input('2500.5');
  money.bind(input);
  assert.equal(input.value, '2,500.50');
  assert.equal(input.type, 'text');
  assert.equal(input.inputMode, 'decimal');
  input.dispatchEvent(new Event('focus'));
  assert.equal(input.value, '2500.50');
  input.value = '1000000';
  input.dispatchEvent(new Event('input'));
  assert.equal(input.value, '1000000', 'typing must not move the caret');
  input.dispatchEvent(new Event('blur'));
  assert.equal(input.value, '1,000,000.00');
  assert.equal(money.getValue(input), '1000000.00');
});

test('invalid paste, precision and range errors are explicit and block serialization', () => {
  const input = new Input();
  input.setAttribute('min', '0');
  money.bind(input);
  for (const value of ['bad', '1,23', '2.555', '-0.01']) {
    input.value = value;
    input.dispatchEvent(new Event('input'));
    assert.ok(input.validationMessage, value);
    assert.throws(() => money.getValue(input), TypeError);
    assert.equal(input.reported, true);
    input.dispatchEvent(new Event('blur'));
    assert.equal(input.value, value, 'do not replace invalid text with zero');
  }
  input.value = '';
  assert.equal(money.getValue(input), '', 'unset is not zero');
  assert.equal(input.validationMessage, '');
});

test('large maximum comparison is exact, computed values and reuse are supported', () => {
  const input = new Input();
  input.dataset.moneyMax = '9007199254740993.98';
  money.bind(input);
  input.value = '9007199254740993.99';
  assert.equal(money.validate(input), false);
  money.setValue(input, '2500.505');
  assert.equal(input.value, '2,500.51');
  assert.equal(money.getValue(input), '2500.51');
  money.bind(input);
  money.destroy(input);
  input.value = '2500';
  input.dispatchEvent(new Event('blur'));
  assert.equal(input.value, '2500', 'destroy removes handlers');
});

test('required amount is distinct from optional blank and from zero', () => {
  const input = new Input();
  input.required = true;
  money.bind(input);
  assert.throws(() => money.getValue(input), TypeError);
  money.setValue(input, 0);
  assert.equal(money.getValue(input), '0.00');
});

const root = path.resolve(__dirname, '..');
const template = fs.readFileSync(path.join(root, 'templates/platform/master_management.html'), 'utf8');

test('all current configured monetary fields explicitly use money, never label guessing', () => {
  const declarations = [...template.matchAll(/\{ name: '([^']+)', label: '[^']*', type: '([^']+)'/g)];
  const monetary = new Set(['monthly_fee', 'agency_commission', 'platform_fee', 'setup_fee',
    'monthly_base_fee', 'monthly_device_fee', 'monthly_user_fee', 'monthly_total_fee',
    'tax_amount', 'total_monthly_fee', 'store_base_fee', 'pos_fee', 'kiosk_fee',
    'mobile_order_fee', 'extra_user_fee', 'extra_device_fee', 'subtotal', 'tax', 'total',
    'default_monthly_fee']);
  const fields = declarations.filter(([, name]) => monetary.has(name));
  assert.equal(fields.length, 22);
  fields.forEach(([, name, type]) => assert.equal(type, 'money', name));
  for (const name of ['transaction_fee_rate', 'tax_rate', 'sort_order', 'included_pos_count']) {
    const matches = declarations.filter(([, key]) => key === name);
    assert.ok(matches.length, name);
    matches.forEach(([, , type]) => assert.notEqual(type, 'money', name));
  }
  assert.ok(template.includes("field.type === 'money' ? AwsumMoneyInput.getValue(input)"));
});

test('active JS and template surfaces contain no independent two-place formatter', () => {
  for (const source of [template, fs.readFileSync(path.join(root, 'static/js/master_group.js'), 'utf8')]) {
    assert.doesNotMatch(source, /\.toFixed\(2\)|Intl\.NumberFormat/);
    assert.doesNotMatch(source, /const profileMoney|function formatMoney/);
  }
  for (const name of ['platform/platform_base', 'client/client_base', 'store/workspace_base', 'shared/dashboard_base']) {
    assert.match(fs.readFileSync(path.join(root, `templates/${name}.html`), 'utf8'), /js\/money\.js/);
  }
});

function type(input, inputType, data = null) {
  const event = new Event('beforeinput', { cancelable: true });
  event.inputType = inputType;
  event.data = data;
  input.dispatchEvent(event);
  return event;
}

test('cents-based entry fills digits from the right', () => {
  const input = new Input();
  input.setAttribute('min', '0');
  money.bind(input);
  let inputs = 0;
  input.addEventListener('input', () => { inputs += 1; });
  input.dispatchEvent(new Event('focus'));
  const event = type(input, 'insertText', '1');
  assert.equal(event.defaultPrevented, true);
  assert.equal(input.value, '0.01');
  for (const digit of '2345') type(input, 'insertText', digit);
  assert.equal(input.value, '123.45');
  assert.equal(inputs, 5, 'each keystroke still fires input for live calculations');
  input.dispatchEvent(new Event('blur'));
  assert.equal(input.value, '123.45');
  assert.equal(money.getValue(input), '123.45');
  for (const digit of '678') type(input, 'insertText', digit);
  input.dispatchEvent(new Event('blur'));
  assert.equal(input.value, '123,456.78');
  for (const [digits, expected] of [['1', '0.01'], ['123', '1.23'], ['12345', '123.45']]) {
    input.value = '';
    for (const digit of digits) type(input, 'insertText', digit);
    input.dispatchEvent(new Event('blur'));
    assert.equal(input.value, expected, digits);
  }
});

test('cents-based backspace, non-digits, paste, sign and read-only', () => {
  const input = new Input();
  input.setAttribute('min', '0');
  money.bind(input);
  input.dispatchEvent(new Event('focus'));
  for (const digit of '12345') type(input, 'insertText', digit);
  type(input, 'deleteContentBackward');
  assert.equal(input.value, '12.34');
  type(input, 'insertText', 'a');
  type(input, 'insertText', '.');
  type(input, 'insertText', '-');
  assert.equal(input.value, '12.34', 'letters, point and disallowed minus are ignored');
  for (let i = 0; i < 4; i += 1) type(input, 'deleteContentBackward');
  assert.equal(input.value, '', 'deleting every digit leaves blank, not zero');
  type(input, 'insertText', '0');
  assert.equal(input.value, '0.00', 'zero can still be entered');
  input.value = '';
  type(input, 'insertFromPaste', '2,500.50');
  assert.equal(input.value, '2500.50', 'pasted amount keeps decimal meaning');
  input.value = '';
  type(input, 'insertFromPaste', '12345');
  assert.equal(input.value, '123.45', 'pasted digits are cents');
  input.value = '';
  type(input, 'insertFromPaste', '1,23');
  assert.equal(input.value, '1,23');
  assert.ok(input.validationMessage, 'invalid paste is explicit, not stripped');

  const signed = new Input();
  money.bind(signed);
  signed.dispatchEvent(new Event('focus'));
  for (const char of '25-0') type(signed, 'insertText', char);
  assert.equal(signed.value, '-2.50', 'minus allowed without a minimum');

  const readonly = new Input('5');
  readonly.readOnly = true;
  money.bind(readonly);
  const event = type(readonly, 'insertText', '1');
  assert.equal(event.defaultPrevented, false);
  assert.equal(readonly.value, '5.00');
});

test('Common Money Keypad placement stays inside the viewport', () => {
  const keypad = require('../static/js/money-keypad.js');
  const size = { width: 232, height: 260 };
  const viewport = { width: 1000, height: 760 };
  assert.deepEqual(keypad.place({ left: 100, top: 100, bottom: 130 }, size, viewport), { left: 100, top: 134 }, 'below the field');
  assert.deepEqual(keypad.place({ left: 900, top: 700, bottom: 730 }, size, viewport), { left: 760, top: 436 }, 'flips above and clamps right edge');
  assert.deepEqual(keypad.place({ left: -50, top: 10, bottom: 600 }, size, viewport), { left: 8, top: 492 }, 'clamps left/bottom when neither side fits');
  assert.deepEqual(keypad.place({ left: 0, top: 0, bottom: 20 }, size, { width: 200, height: 200 }), { left: 8, top: 8 }, 'tiny viewport');
});

test('shared cents digit helpers', () => {
  assert.equal(money.pushDigit(null, '1'), 1n);
  assert.equal(money.pushDigit(123n, '4'), 1234n);
  assert.equal(money.pushDigit(-12n, '3'), -123n);
  assert.equal(money.popDigit(123n), 12n);
  assert.equal(money.popDigit(5n), null);
  assert.equal(money.minorUnitsOf('95,000.00'), 9500000n);
  assert.equal(money.minorUnitsOf('bad'), null);
});
