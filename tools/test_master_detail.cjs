'use strict';
// Common Master-Detail tree helper (static/js/master-detail.js).
const test = require('node:test');
const assert = require('node:assert/strict');
const md = require('../static/js/master-detail.js');

const levels = [
  { key: (r) => (r.c1 ? `c1:${r.c1}` : null), name: (r) => r.c1, order: (r) => ({ Food: 1, Tools: 2 })[r.c1] || 9 },
  { key: (r) => (r.c2 ? `c2:${r.c2}` : null), name: (r) => r.c2 },
];
const items = [
  { id: 1, c1: 'Tools', c2: 'Hand', name: 'Hammer' },
  { id: 2, c1: 'Food', c2: 'Drinks', name: 'Soda' },
  { id: 3, c1: 'Food', c2: 'Drinks', name: 'Water' },
  { id: 4, c1: 'Food', c2: null, name: 'Loose' },
];
const row = (r, depth) => `<tr data-row-select="${r.id}" style="--mg-depth:${depth}"><td>${r.name}</td></tr>`;
const order = (html) => [...html.matchAll(/data-tree-toggle="([^"]+)"|data-row-select="(\d+)"/g)].map((m) => m[1] || `#${m[2]}`);

test('groups by levels in display order with counts and depth', () => {
  const html = md.treeHtml({ items, levels, collapsed: new Set(), colspan: 1, rowHtml: row });
  assert.deepEqual(order(html), ['c1:Food', 'c1:Food/c2:Drinks', '#2', '#3', '#4', 'c1:Tools', 'c1:Tools/c2:Hand', '#1']);
  assert.match(html, /mg-tree-l1[^>]*>\s*<td colspan="1">.*Food<span class="mg-tree-count">3</);
  assert.match(html, /data-row-select="2" style="--mg-depth:2"/);
  assert.match(html, /data-row-select="4" style="--mg-depth:1"/, 'a missing level stops the path');
});

test('collapse, expand and flat lists', () => {
  const collapsed = new Set();
  md.toggleTree(collapsed, 'c1:Food');
  const html = md.treeHtml({ items, levels, collapsed, colspan: 1, rowHtml: row });
  assert.deepEqual(order(html), ['c1:Food', 'c1:Tools', 'c1:Tools/c2:Hand', '#1']);
  assert.match(html, /data-tree-toggle="c1:Food"[^>]*aria-expanded="false"/);
  md.expandTree(collapsed, [items[1]], levels);
  assert.equal(collapsed.size, 0);
  assert.deepEqual(order(md.treeHtml({ items, levels: [], collapsed, colspan: 1, rowHtml: row })), ['#1', '#2', '#3', '#4']);
  assert.match(md.treeHtml({ items: [{ id: 9, c1: '<b>' }], levels, collapsed, colspan: 1, rowHtml: row }), /&lt;b&gt;/);
});
