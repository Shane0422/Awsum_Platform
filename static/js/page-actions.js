/* AWSUM Common Page Actions (window.AwsumPageActions).
 *
 * Manages the action area of the Common Page Header (page_header(..., actions_id=...)). It adopts the
 * screen's EXISTING buttons (same DOM nodes, ids and event listeners - nothing is re-created or bound
 * twice), so confirm dialogs, validation and save / delete logic stay exactly as they are.
 *
 *   const actions = AwsumPageActions.mount(document.getElementById('mgPageActions'), {
 *     mode: 'none',                                   // 'none' (nothing selected) | 'create' | 'edit'
 *     actions: [                                      // display order
 *       { id: 'mgExportBtn', menu: true, label: 'Export' },          // a Bootstrap .btn-group dropdown
 *       { id: 'mgAddBtn', pin: (mode) => mode === 'none' },
 *       { id: 'mgMdToggle', modes: ['edit'] },
 *       { id: 'mgMdDelete', modes: ['edit'], danger: true, permission: 'delete' },
 *       { id: 'mgSave', modes: ['create', 'edit'], pin: true },     // Save / Create: always visible
 *     ],
 *     permissions: { delete: true },                  // optional; an action whose permission is false is hidden
 *   });
 *   actions.setMode('edit'); actions.setPermissions({ delete: false }); actions.setEnabled('mgSave', false);
 *
 * - modes: in which states the action is shown (default: all). The screen's own show / hide (d-none)
 *   and disabled states are respected and followed.
 * - Small screens (< 576px): unpinned actions move into a "More ..." dropdown; pinned ones stay visible.
 *   A menu action (e.g. Export) contributes its own items under a heading.
 */
(function () {
  'use strict';

  const NARROW = '(max-width: 575.98px)';
  const MODES = ['none', 'create', 'edit'];

  function mount(container, options) {
    if (!container) return null;
    const opts = options || {};
    let mode = MODES.includes(opts.mode) ? opts.mode : 'none';
    let permissions = { ...(opts.permissions || {}) };
    const media = window.matchMedia ? window.matchMedia(NARROW) : { matches: false, addEventListener() {} };
    let narrow = false;

    // ---- the existing buttons, in the configured order ----
    const actions = (opts.actions || []).map((spec) => {
      const control = document.getElementById(spec.id);
      if (!control) return null;
      const element = spec.menu ? control.closest('.btn-group') || control : control;
      container.appendChild(element); // move (keeps the node and its listeners), never copy
      element.dataset.pageAction = spec.id;
      return { spec, control, element, items: spec.menu ? [...element.querySelectorAll('.dropdown-menu > li')] : [] };
    }).filter(Boolean);

    // ---- More ... (small screens) ----
    const more = document.createElement('div');
    more.className = 'btn-group awsum-page-more';
    more.innerHTML = '<button type="button" class="btn btn-outline-secondary dropdown-toggle" data-bs-toggle="dropdown" '
      + 'aria-expanded="false" aria-label="More actions">More &#8943;</button><ul class="dropdown-menu dropdown-menu-end"></ul>';
    const moreMenu = more.querySelector('.dropdown-menu');
    container.prepend(more);

    const allowed = (a) => (a.spec.modes || MODES).includes(mode) && !(a.spec.permission && permissions[a.spec.permission] === false);
    const pinned = (a) => (typeof a.spec.pin === 'function' ? a.spec.pin(mode) : !!a.spec.pin);
    const hiddenByScreen = (el) => el.classList.contains('d-none') || el.hidden;

    function toMenu(a) {
      if (a.inMenu) return;
      a.inMenu = true;
      if (a.spec.menu) {
        const head = document.createElement('li');
        head.innerHTML = '<h6 class="dropdown-header"></h6>';
        head.firstChild.textContent = a.spec.label || a.control.textContent.trim();
        a.menuNodes = [head, ...a.items];
        a.menuNodes.forEach((node) => moreMenu.appendChild(node));
        a.element.classList.add('awsum-page-action-moved');
      } else {
        const li = document.createElement('li');
        a.savedClass = a.control.className;
        a.control.className = `dropdown-item${a.spec.danger ? ' text-danger' : ''}`;
        if (/\bd-none\b/.test(a.savedClass)) a.control.classList.add('d-none');
        li.appendChild(a.control);
        moreMenu.appendChild(li);
        a.menuNodes = [li];
      }
    }

    function fromMenu(a) {
      if (!a.inMenu) return;
      a.inMenu = false;
      if (a.spec.menu) {
        const list = a.element.querySelector('.dropdown-menu');
        a.items.forEach((li) => list.appendChild(li));
        a.menuNodes[0].remove();
        a.element.classList.remove('awsum-page-action-moved');
      } else {
        const hidden = a.control.classList.contains('d-none'); // keep the screen's current state
        a.control.className = a.savedClass;
        a.control.classList.toggle('d-none', hidden);
        a.menuNodes[0].remove();
        a.element = a.control;
      }
      a.menuNodes = [];
    }

    function layout() {
      narrow = media.matches;
      // Back to the header first (keeps the configured order), then move the unpinned ones.
      actions.forEach(fromMenu);
      actions.forEach((a) => container.appendChild(a.element));
      if (narrow) actions.filter((a) => !pinned(a)).forEach(toMenu);
      refresh();
    }

    function refresh() {
      let visibleInMenu = 0;
      actions.forEach((a) => {
        const on = allowed(a);
        a.element.classList.toggle('awsum-page-action-off', !on);
        if (a.inMenu) {
          const show = on && (a.spec.menu ? !hiddenByScreen(a.element) : !hiddenByScreen(a.control));
          a.menuNodes.forEach((node) => { node.hidden = !show; });
          if (show) visibleInMenu += 1;
        }
      });
      more.classList.toggle('d-none', !visibleInMenu);
    }

    // Follow the screen's own d-none / hidden / disabled changes (e.g. Delete hidden for a System Code).
    const observer = new MutationObserver(refresh);
    actions.forEach((a) => {
      observer.observe(a.control, { attributes: true, attributeFilter: ['class', 'hidden', 'disabled'] });
      if (a.element !== a.control) observer.observe(a.element, { attributes: true, attributeFilter: ['class', 'hidden'] });
    });
    if (media.addEventListener) media.addEventListener('change', layout);
    else if (media.addListener) media.addListener(layout);
    container.classList.add('awsum-page-actions');
    layout();

    return {
      get mode() { return mode; },
      setMode(next) {
        if (!MODES.includes(next) || next === mode) return;
        mode = next;
        layout(); // pins may depend on the mode
      },
      setPermissions(next) { permissions = { ...permissions, ...(next || {}) }; refresh(); },
      setEnabled(id, enabled) {
        const a = actions.find((item) => item.spec.id === id);
        if (a) a.control.disabled = !enabled;
      },
      refresh,
      layout,
    };
  }

  window.AwsumPageActions = { mount, NARROW };
})();
