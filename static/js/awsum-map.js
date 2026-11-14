/*
 * AWSUM Common Map (Employee address now; Merchant / Store / Agency forms reuse it the same way).
 * One implementation of "Show Map" for an address form: each screen passes its field ids and the map config URL of
 * its scope (GET {apiBase}/map-config -> { enabled, embed_key }: backend.services.maps through the scope's router).
 * Two actions: Show / Hide Map (the map inside AWSUM) and Open in Google Maps (a new browser tab, Google Maps URLs,
 * no key needed). Privacy: nothing is sent to Google until the user clicks one of them; Address Line 2 is never part
 * of a map query; Hide / Reset remove the map frame. The map box is 380px high and as wide as the form. It opens only
 * once the config says a key is set: without an Embed key, or when the config cannot be read (HTTP error / network),
 * Show Map stays as it is and a hint (different for each case) points to Open in Google Maps. Saving and the rest of
 * the form never depend on the map.
 */
(function (global) {
  'use strict';

  const EMBED_URL = 'https://www.google.com/maps/embed/v1/place';
  const SEARCH_URL = 'https://www.google.com/maps/search/';
  const INCOMPLETE_MESSAGE = 'Enter Address Line 1 and a ZIP Code (or City and State) to show the map.';
  const NO_KEY_MESSAGE = 'Map preview is not set up. Use Open in Google Maps.';
  const LOADING_MESSAGE = 'Loading map...';
  const MAP_HEIGHT = 380; // px, every address form; the width follows the form content
  const configs = {}; // config URL -> Promise of { state, embedKey, status }; a read answer is kept for the page

  // The hint when the map cannot be shown: no key (a set-up choice) versus a config read that failed.
  function configMessage(config) {
    if (config.state === 'off') return NO_KEY_MESSAGE;
    const reason = config.status ? `HTTP ${config.status}` : 'network error';
    return `Map settings could not be loaded (${reason}). Try again or use Open in Google Maps.`;
  }

  const clean = (value) => String(value == null ? '' : value).replace(/\s+/g, ' ').trim();

  // parts: { line1, city, state, zip, country }. Address Line 2 (unit / apartment) is never part of a map query.
  function formatAddress(parts) {
    const p = parts || {};
    const region = [clean(p.state), clean(p.zip)].filter(Boolean).join(' ');
    return [clean(p.line1), clean(p.city), region, clean(p.country)].filter(Boolean).join(', ');
  }

  // Enough to find the place: Address Line 1 plus a ZIP Code, or plus City and State.
  function isComplete(parts) {
    const p = parts || {};
    return !!clean(p.line1) && (!!clean(p.zip) || (!!clean(p.city) && !!clean(p.state)));
  }

  // state: 'ready' (key set), 'off' (no key) or 'error' (HTTP error with its status, or network / bad answer: status 0).
  // Only an answer is kept; an error is read again on the next Show Map.
  function loadConfig(url) {
    if (!url) return Promise.resolve({ state: 'off', embedKey: '', status: 0 });
    if (!configs[url]) {
      configs[url] = fetch(url, { credentials: 'same-origin' })
        .then((res) => {
          if (!res.ok) return { state: 'error', embedKey: '', status: res.status };
          return res.json().then((data) => {
            const key = (data && data.enabled && data.embed_key) || '';
            return { state: key ? 'ready' : 'off', embedKey: key, status: res.status };
          });
        })
        .catch(() => ({ state: 'error', embedKey: '', status: 0 }))
        .then((config) => {
          if (config.state === 'error') delete configs[url];
          return config;
        });
    }
    return configs[url];
  }

  // container: element or id; options: { fields: { line1, city, state, zip, country } (ids), configUrl }.
  // Field ids, not elements, so a re-rendered form is always read fresh. Any edit of those fields resets the map.
  function create(container, options) {
    const root = typeof container === 'string' ? document.getElementById(container) : container;
    const fields = (options && options.fields) || {};
    root.innerHTML = `<div class="d-flex flex-wrap align-items-center gap-2">
        <button type="button" class="btn btn-sm btn-outline-primary" data-map-toggle>Show Map</button>
        <button type="button" class="btn btn-sm btn-outline-secondary" data-map-external>Open in Google Maps</button>
        <span class="small text-muted" data-map-message role="status"></span></div>
      <div class="mt-2 d-none" data-map-panel><div class="w-100 border rounded overflow-hidden" data-map-frame style="height:${MAP_HEIGHT}px"></div></div>`;
    const part = (name) => root.querySelector(`[data-map-${name}]`);
    const button = part('toggle');
    let address = null;   // set by setAddress(); otherwise the current field values are read
    let visible = false;
    let token = 0;        // a Hide / Reset during the config read cancels that Show

    const message = (text) => { part('message').textContent = text || ''; };
    const readFields = () => {
      const value = (key) => { const el = fields[key] && document.getElementById(fields[key]); return el ? el.value : ''; };
      return { line1: value('line1'), city: value('city'), state: value('state'), zip: value('zip'), country: value('country') };
    };

    function hide() {
      token += 1;
      part('frame').innerHTML = '';   // removes the iframe: no further map request
      part('panel').classList.add('d-none');
      button.textContent = 'Show Map';
      visible = false;
    }

    async function show(next) {
      const parts = next || address || readFields();
      if (!isComplete(parts)) {
        hide();
        message(INCOMPLETE_MESSAGE);
        return false;
      }
      const query = formatAddress(parts);
      const mine = ++token;
      if (!visible) message(LOADING_MESSAGE); // a redraw of an open map keeps it open while the config is read
      const config = await loadConfig(options && options.configUrl);
      if (mine !== token) return false;
      const frame = part('frame');
      frame.innerHTML = '';
      if (config.state !== 'ready') {
        hide();                          // no empty map box: Show Map stays, only the hint
        message(configMessage(config));
        return false;
      }
      message('');
      visible = true;
      button.textContent = 'Hide Map';
      part('panel').classList.remove('d-none');
      const iframe = document.createElement('iframe');
      iframe.className = 'd-block w-100 h-100 border-0';
      iframe.title = 'Map';
      iframe.loading = 'lazy';
      iframe.allowFullscreen = true;
      iframe.src = `${EMBED_URL}?key=${encodeURIComponent(config.embedKey)}&q=${encodeURIComponent(query)}`;
      frame.appendChild(iframe);
      return true;
    }

    // Open in Google Maps: the current address in a new tab (official Maps URL, no key, no opener / referrer).
    function openExternal(next) {
      const parts = next || address || readFields();
      if (!isComplete(parts)) {
        message(INCOMPLETE_MESSAGE);
        return false;
      }
      if (!visible) message('');
      global.open(`${SEARCH_URL}?api=1&query=${encodeURIComponent(formatAddress(parts))}`, '_blank', 'noopener,noreferrer');
      return true;
    }

    function reset() {
      address = null;
      hide();
      message('');
    }

    function setAddress(next) {
      address = next || null;
      if (visible) show();
    }

    button.addEventListener('click', () => (visible ? hide() : show()));
    part('external').addEventListener('click', () => openExternal());
    Object.values(fields).forEach((id) => {
      const el = id && document.getElementById(id);
      if (!el) return;
      // Typing and the common ZIP autofill (which fires 'input' on City / State / Country) both reset the map.
      ['input', 'change'].forEach((type) => el.addEventListener(type, () => { if (visible || part('message').textContent) reset(); }));
    });

    return { show, hide, reset, setAddress, openExternal, isVisible: () => visible };
  }

  global.AwsumMap = { create, formatAddress, isComplete };
})(window);
