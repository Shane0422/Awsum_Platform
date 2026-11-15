/* AWSUM Common Collapsible Section (window.AwsumCollapsible): an accordion-style section any screen can reuse.
 *
 *   container.innerHTML = AwsumCollapsible.controlsHtml() +                 // optional Expand All / Collapse All
 *     AwsumCollapsible.html({ id: 'licQty', title: 'Contract Quantities', summary: 'POS 2/3', expanded: true,
 *                             actionsHtml: '<button ...>Edit</button>', bodyHtml: '<table>...</table>' });
 *   AwsumCollapsible.bind(container);   // once per container (event delegation; re-rendered sections keep working)
 *
 * - The heading is a real <button> (Enter / Space work natively) with aria-expanded / aria-controls, a left chevron,
 *   the title and a right summary. actionsHtml sits beside the heading button (never inside it), so it stays usable
 *   and visible while the section is collapsed.
 * - The body uses Bootstrap Collapse when it is loaded (animation, .collapse / .show); otherwise the hidden attribute.
 * - Expanded / collapsed state is kept per section id for the page, so a re-render (same id) keeps what the user chose;
 *   `expanded` is only the initial state. expandAll(root) / collapseAll(root) act on the sections inside root.
 * All text values are escaped; actionsHtml / bodyHtml are trusted markup built by the caller.
 * Styles: /static/css/awsum-collapsible.css.
 */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.AwsumCollapsible = api;
})(typeof globalThis === 'object' ? globalThis : this, function () {
  'use strict';

  const state = new Map(); // section id -> expanded
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const bodyId = (id) => `awsumCollapsible-${id}`;

  function isExpanded(id, initial = true) {
    return state.has(id) ? state.get(id) : !!initial;
  }

  function html({ id, title, summary = '', expanded = true, actionsHtml = '', bodyHtml = '' }) {
    const open = isExpanded(id, expanded);
    return `<section class="awsum-collapsible${open ? ' is-open' : ''}" data-awsum-collapsible="${esc(id)}">
      <div class="awsum-collapsible-head">
        <button type="button" class="awsum-collapsible-toggle" aria-expanded="${open}" aria-controls="${esc(bodyId(id))}">
          <span class="awsum-collapsible-chevron" aria-hidden="true"></span>
          <span class="awsum-collapsible-title">${esc(title)}</span>
          <span class="awsum-collapsible-summary">${esc(summary)}</span>
        </button>
        ${actionsHtml ? `<div class="awsum-collapsible-actions">${actionsHtml}</div>` : ''}
      </div>
      <div class="awsum-collapsible-body collapse${open ? ' show' : ''}" id="${esc(bodyId(id))}"${open ? '' : ' hidden'}>${bodyHtml}</div>
    </section>`;
  }

  function controlsHtml() {
    return '<div class="awsum-collapsible-controls">' +
      '<button type="button" class="btn btn-sm btn-link px-1" data-awsum-collapsible-all="expand">Expand All</button>' +
      '<button type="button" class="btn btn-sm btn-link px-1" data-awsum-collapsible-all="collapse">Collapse All</button></div>';
  }

  function setExpanded(section, open) {
    if (!section) return;
    const id = section.dataset.awsumCollapsible;
    const toggle = section.querySelector(':scope > .awsum-collapsible-head > .awsum-collapsible-toggle');
    const body = section.querySelector(':scope > .awsum-collapsible-body');
    state.set(id, open);
    section.classList.toggle('is-open', open);
    if (toggle) toggle.setAttribute('aria-expanded', String(open));
    if (!body) return;
    const Collapse = typeof window !== 'undefined' && window.bootstrap && window.bootstrap.Collapse;
    if (open) body.hidden = false;
    if (Collapse) {
      // Bootstrap ignores show / hide while a transition runs (fast clicks, Expand All then a click): when a
      // transition ends, the section is brought to the state last requested.
      if (!body.dataset.awsumCollapsibleSync) {
        body.dataset.awsumCollapsibleSync = '1';
        body.addEventListener('shown.bs.collapse', () => { if (!state.get(id)) setExpanded(section, false); });
        body.addEventListener('hidden.bs.collapse', () => {
          if (state.get(id)) setExpanded(section, true);
          else body.hidden = true;
        });
      }
      const instance = Collapse.getOrCreateInstance(body, { toggle: false });
      if (open) instance.show();
      else instance.hide();
    } else {
      body.classList.toggle('show', open);
      body.hidden = !open;
    }
  }

  function sections(root) {
    return root ? Array.from(root.querySelectorAll('[data-awsum-collapsible]')) : [];
  }

  function expandAll(root) { sections(root).forEach((section) => setExpanded(section, true)); }
  function collapseAll(root) { sections(root).forEach((section) => setExpanded(section, false)); }

  function bind(container) {
    if (!container || container.dataset.awsumCollapsibleBound) return;
    container.dataset.awsumCollapsibleBound = '1';
    container.addEventListener('click', (event) => {
      const all = event.target.closest('[data-awsum-collapsible-all]');
      if (all && container.contains(all)) {
        if (all.dataset.awsumCollapsibleAll === 'expand') expandAll(container);
        else collapseAll(container);
        return;
      }
      const toggle = event.target.closest('.awsum-collapsible-toggle');
      if (!toggle || !container.contains(toggle)) return;
      const section = toggle.closest('[data-awsum-collapsible]');
      setExpanded(section, toggle.getAttribute('aria-expanded') !== 'true');
    });
  }

  return Object.freeze({ html, controlsHtml, bind, expandAll, collapseAll, isExpanded, setExpanded });
});
