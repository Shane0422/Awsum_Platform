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
