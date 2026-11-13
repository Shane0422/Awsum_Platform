// AWSUM Common Date (static/js/date.js): format / parse / validate / mask. Run: node --test tools/test_date.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const date = require('../static/js/date.js');

test('display format is MM-DD-YYYY; API stays ISO', () => {
  assert.equal(date.FORMAT, 'MM-DD-YYYY');
  assert.equal(date.format('1961-04-22'), '04-22-1961');
  assert.equal(date.format('2026-11-13T21:06:29'), '11-13-2026');
  for (const empty of ['', null, undefined, 'not a date']) assert.equal(date.format(empty), '');
  assert.equal(date.formatDateTime('2026-11-13T21:06:29.26'), '11-13-2026 09:06 PM');
  assert.equal(date.formatDateTime('2026-11-13 00:05:00'), '11-13-2026 12:05 AM');
  assert.equal(date.formatDateTime(null, '—'), '—');
});

test('parse accepts MM-DD-YYYY, M-D-YYYY, MMDDYYYY, ISO and the earlier MM/DD/YYYY', () => {
  for (const text of ['04-22-1961', '4-22-1961', '04221961', '1961-04-22', '04/22/1961', ' 4/22/1961 ']) {
    assert.equal(date.parse(text), '1961-04-22', text);
  }
  assert.equal(date.parse(''), '');
  assert.equal(date.parse(null), '');
  for (const bad of ['02-30-2024', '13-01-2020', '00-10-2020', '1961/04/22', '22-04-1961', 'abc', '04-22-61']) {
    assert.equal(date.parse(bad), null, bad);
  }
  assert.equal(date.parse('02-29-2024'), '2024-02-29');
  assert.equal(date.parse('02-29-2023'), null);
});

test('validation: required, invalid, min / max (today) and custom messages', () => {
  const birthday = { label: 'Birthday', max: 'today', maxMessage: 'Birthday cannot be in the future.' };
  assert.equal(date.validate('', birthday), '');
  assert.equal(date.validate('', { label: 'Start Date', required: true }), 'Start Date is required.');
  assert.equal(date.validate('13-45-1990', birthday), 'Enter Birthday as MM-DD-YYYY.');
  assert.equal(date.validate(null, { label: 'Hire Date' }), 'Enter Hire Date as MM-DD-YYYY.');
  assert.equal(date.validate('2999-01-01', birthday), 'Birthday cannot be in the future.');
  assert.equal(date.validate(date.today(), birthday), '');
  assert.equal(date.validate('01-01-2020', { label: 'Start Date', min: '2020-02-01' }), 'Start Date must be on or after 02-01-2020.');
  assert.equal(date.validate('03-01-2020', { label: 'End Date', max: '2020-02-01' }), 'End Date must be on or before 02-01-2020.');
  assert.equal(date.isValidIso('2024-02-29'), true);
  assert.equal(date.isValidIso('2024-02-30'), false);
  assert.match(date.today(), /^\d{4}-\d{2}-\d{2}$/);
});

test('typing mask inserts dashes and pads a single-digit month / day', () => {
  for (const [typed, shown] of [['0', '0'], ['04', '04'], ['042', '04-2'], ['0422', '04-22'], ['04221', '04-22-1'],
    ['04221961', '04-22-1961'], ['042219611', '04-22-1961'], ['4-', '04-'], ['04-2-', '04-02-'], ['4/22/1961', '04-22-1961'],
    ['04-22-1961', '04-22-1961'], ['ab04c22', '04-22'], ['1961-04-22', '1961-04-22']]) {
    assert.equal(date.mask(typed), shown, typed);
  }
});
