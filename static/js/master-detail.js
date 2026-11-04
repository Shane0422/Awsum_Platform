(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.AwsumMasterDetail = api;
})(typeof globalThis === 'object' ? globalThis : this, function () {
  'use strict';

  // Common Master-Detail helpers (left Tree + right detail form), used by Master Group and Merchant management.
  // Tree: groups list items under their parents (e.g. Category I > II > III, Agency) with expand / collapse group rows. Grouping is client-side over the rows already loaded, so the page's
  // search, filter, sort and paging stay unchanged. Styles: /static/css/awsum-master-detail.css.
  //
  // levels: [{ key(item) -> string|null, name(item) -> string, order(item) -> number }], top level first.
  //   A null key stops the path there (the item sits under its last known parent).
  // collapsed: Set of group keys the user closed. rowHtml(item, depth) renders an item row.
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  function pathKeys(item, levels) {
    const keys = [];
    let path = '';
    for (const level of levels) {
      const key = level.key(item);
      if (!key) break;
      path += (path ? '/' : '') + key;
      keys.push(path);
    }
    return keys;
  }

  function html({ items, levels, collapsed, colspan, rowHtml }) {
    const root = { children: new Map(), items: [] };
    items.forEach((item, index) => {
      let node = root;
      pathKeys(item, levels).forEach((path, i) => {
        if (!node.children.has(path)) {
          node.children.set(path, {
            key: path, order: levels[i].order ? levels[i].order(item) : 0, first: index,
            name: levels[i].name(item), children: new Map(), items: [], count: 0,
          });
        }
        node = node.children.get(path);
        node.count += 1;
      });
      node.items.push(item);
    });
    const out = [];
    const walk = (node, depth) => {
      [...node.children.values()].sort((a, b) => a.order - b.order || a.first - b.first).forEach((group) => {
        const open = !collapsed.has(group.key);
        out.push(`<tr class="mg-tree-group mg-tree-l${depth + 1}" data-tree-toggle="${esc(group.key)}" tabindex="0" aria-expanded="${open}" style="--mg-depth:${depth}">
          <td colspan="${colspan}"><span class="mg-tree-sign" aria-hidden="true">${open ? '&minus;' : '+'}</span>${esc(group.name)}<span class="mg-tree-count">${group.count}</span></td></tr>`);
        if (open) walk(group, depth + 1);
      });
      node.items.forEach((item) => out.push(rowHtml(item, depth)));
    };
    walk(root, 0);
    return out.join('');
  }

  // Search results and the selected item always have their parent path open.
  function expand(collapsed, items, levels) {
    items.forEach((item) => pathKeys(item, levels).forEach((key) => collapsed.delete(key)));
  }

  function toggle(collapsed, key) {
    if (collapsed.has(key)) collapsed.delete(key);
    else collapsed.add(key);
  }

  // The list panel fills the viewport below the workspace top; only its table scrolls (--mg-md-offset).
  function fitWorkspace(workspace) {
    if (!workspace) return;
    const fit = () => workspace.style.setProperty('--mg-md-offset', `${Math.round(workspace.getBoundingClientRect().top + window.scrollY)}px`);
    fit();
    window.addEventListener('resize', fit);
  }

  return Object.freeze({ treeHtml: html, treePathKeys: pathKeys, expandTree: expand, toggleTree: toggle, fitWorkspace });
});
