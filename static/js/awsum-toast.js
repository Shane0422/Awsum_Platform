/* AWSUM Common Toast Notification (window.AwsumToast).
 *
 * One notification component for every screen (Platform Admin, Agency / Merchant / Store Portals,
 * all Master Groups, and later POS / KIOSK). No framework dependency: load
 * static/css/awsum-toast.css + this file. The markup is created on first use.
 *
 *   AwsumToast.success('MD00002 saved.');          // small overlay, top right, closes after 1 second
 *   AwsumToast.info('Not saved. No changes were made.');
 *   AwsumToast.warning('Item is in use ...');      // stays until closed (x button or Escape)
 *   AwsumToast.error('Unable to save: ...');       // stays until closed; announced assertively
 *   AwsumToast.done('updated', 'Category III');    // standard text: "Category III updated."
 *   AwsumToast.flash('User created.');             // show after the next page load (reload flows)
 *   AwsumToast.clear();                            // remove every toast
 *
 * Standard messages (AwsumToast.message(action, label)):
 *   created -> "<label> created."   updated -> "<label> updated."   saved -> "<label> saved."
 *   deleted -> "<label> deleted."   activated / deactivated -> "<label> activated." / "... deactivated."
 *   status  -> "<label> status changed."
 *
 * Rules: success / info close by themselves (1 s, paused while hovered or focused); warning / error never
 * close by themselves. The same message is never stacked twice (its timer restarts) and a new success
 * replaces the previous success. Toasts are fixed overlays: they never move the layout, scroll, focus or
 * selected tab. All text is set with textContent (never HTML).
 */
(function () {
  'use strict';

  const AUTO_CLOSE_MS = 1000;
  const MAX_VISIBLE = 3;
  const FLASH_KEY = 'awsum.toast.flash';
  const KINDS = { success: 'success', info: 'info', warning: 'warning', danger: 'danger', error: 'danger' };
  const ICONS = { success: '✓', info: 'i', warning: '!', danger: '!' };
  const PERSISTENT = new Set(['warning', 'danger']);
  const ACTIONS = {
    created: 'created', create: 'created', updated: 'updated', update: 'updated', saved: 'saved', save: 'saved',
    deleted: 'deleted', delete: 'deleted', activated: 'activated', activate: 'activated',
    deactivated: 'deactivated', deactivate: 'deactivated', status: 'status changed',
  };
  let region = null;

  function ensureRegion() {
    if (region && region.isConnected) return region;
    region = document.getElementById('awsumToastRegion');
    if (!region) {
      region = document.createElement('div');
      region.id = 'awsumToastRegion';
      region.className = 'awsum-toast-region';
      region.setAttribute('aria-live', 'polite');
      region.setAttribute('aria-relevant', 'additions');
      document.body.appendChild(region);
    }
    return region;
  }

  function toasts() { return region && region.isConnected ? [...region.querySelectorAll('.awsum-toast')] : []; }

  function remove(toast) {
    if (!toast || !toast.isConnected) return;
    clearTimeout(toast._awsumTimer);
    toast.remove();
  }

  function schedule(toast) {
    clearTimeout(toast._awsumTimer);
    if (!toast._awsumDuration) return;
    toast._awsumTimer = setTimeout(() => {
      // Paused while the pointer or keyboard focus is on it (WCAG 2.2.1); closes once it is left.
      if (toast.matches(':hover') || toast.contains(document.activeElement)) schedule(toast);
      else remove(toast);
    }, toast._awsumDuration);
  }

  function build(kind, message, title) {
    const toast = document.createElement('div');
    toast.className = `awsum-toast awsum-toast-${kind}`;
    toast.dataset.kind = kind;
    toast.dataset.message = message;
    // Errors and warnings interrupt the screen reader; success / info are announced politely.
    toast.setAttribute('role', PERSISTENT.has(kind) ? 'alert' : 'status');
    const icon = document.createElement('span');
    icon.className = 'awsum-toast-icon';
    icon.setAttribute('aria-hidden', 'true');
    icon.textContent = ICONS[kind];
    const body = document.createElement('div');
    body.className = 'awsum-toast-body';
    if (title) {
      const strong = document.createElement('strong');
      strong.className = 'awsum-toast-title';
      strong.textContent = title;
      body.appendChild(strong);
    }
    const text = document.createElement('div');
    text.className = 'awsum-toast-message';
    text.textContent = message;
    body.appendChild(text);
    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'awsum-toast-close';
    close.setAttribute('aria-label', 'Close notification');
    close.textContent = '×';
    close.addEventListener('click', () => remove(toast));
    toast.append(icon, body, close);
    toast.addEventListener('mouseleave', () => schedule(toast));
    toast.addEventListener('focusout', () => schedule(toast));
    return toast;
  }

  function show(message, options) {
    const opts = typeof options === 'string' ? { kind: options } : (options || {});
    const text = message == null ? '' : String(message).trim();
    if (!text) return null;
    if (!document.body) { // called from <head>: wait for the page
      document.addEventListener('DOMContentLoaded', () => show(text, opts), { once: true });
      return null;
    }
    const kind = KINDS[opts.kind] || 'success';
    const host = ensureRegion();
    // Same message already shown: refresh it instead of stacking a duplicate.
    let toast = toasts().find(t => t.dataset.kind === kind && t.dataset.message === text);
    if (!toast) {
      // A new success / info replaces the previous one (continuous saves never pile up).
      if (!PERSISTENT.has(kind)) toasts().filter(t => !PERSISTENT.has(t.dataset.kind)).forEach(remove);
      toast = build(kind, text, opts.title);
      host.appendChild(toast);
      const list = toasts();
      list.slice(0, Math.max(0, list.length - MAX_VISIBLE)).forEach(remove);
    }
    toast._awsumDuration = PERSISTENT.has(kind) && !opts.duration ? 0 : (opts.duration || AUTO_CLOSE_MS);
    schedule(toast);
    return toast;
  }

  function message(action, label) {
    const verb = ACTIONS[String(action || '').toLowerCase()] || String(action || 'saved');
    return `${label || 'Item'} ${verb}.`;
  }

  function flash(text, options) {
    try {
      sessionStorage.setItem(FLASH_KEY, JSON.stringify({ message: String(text || ''), options: options || {} }));
    } catch (_) { /* storage unavailable: nothing to show after reload */ }
  }

  function showFlash() {
    let item = null;
    try {
      item = JSON.parse(sessionStorage.getItem(FLASH_KEY) || 'null');
      sessionStorage.removeItem(FLASH_KEY);
    } catch (_) { item = null; }
    if (item && item.message) show(item.message, item.options);
  }

  // Keyboard: Escape closes the newest toast when focus is on a toast, or when no dialog is open
  // (an open Bootstrap modal keeps its own Escape behavior).
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || event.defaultPrevented) return;
    const list = toasts();
    if (!list.length) return;
    const focused = list.find(t => t.contains(document.activeElement));
    if (focused) { remove(focused); return; }
    if (document.querySelector('.modal.show')) return;
    remove(list[list.length - 1]);
  });

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', showFlash, { once: true });
  else showFlash();

  window.AwsumToast = {
    AUTO_CLOSE_MS,
    show,
    success: (text, options) => show(text, { ...(options || {}), kind: 'success' }),
    info: (text, options) => show(text, { ...(options || {}), kind: 'info' }),
    warning: (text, options) => show(text, { ...(options || {}), kind: 'warning' }),
    error: (text, options) => show(text, { ...(options || {}), kind: 'danger' }),
    message,
    done: (action, label, options) => show(message(action, label), { ...(options || {}), kind: 'success' }),
    flash,
    dismiss: remove,
    clear: () => toasts().forEach(remove),
  };
})();
