/*
 * AWSUM Common Address (Agency / Merchant / Store / Employee forms).
 * One implementation of the US ZIP lookup and City / State / Country autofill; each screen passes its field ids and
 * the API base of its scope (GET {apiBase}/zip-lookup/{zip}: backend.utils.zip_lookup through the scope's router).
 * Screen-specific follow-ups (Store tax rate / time zone defaults) are callbacks, never copies of this logic.
 */
(function (global) {
  'use strict';

  const US_COUNTRY_NAME = 'United States';
  const ZIP_PATTERN = /^\d{5}(-\d{4})?$/;

  function isUsCountry(value) {
    return ['US', 'USA', 'UNITED STATES', 'UNITED STATES OF AMERICA'].includes(String(value || '').trim().toUpperCase());
  }

  async function lookupZip(apiBase, zip) {
    const rawZip = String(zip || '').trim();
    if (!ZIP_PATTERN.test(rawZip)) return { found: false, reason: 'invalid' };
    const res = await fetch(`${apiBase}/zip-lookup/${encodeURIComponent(rawZip)}`, { credentials: 'same-origin' });
    return res.ok ? res.json() : { found: false, reason: 'unavailable' };
  }

  function byId(id) {
    return id ? document.getElementById(id) : null;
  }

  // Look up the ZIP in options.zip and fill City / State / Country. options: { apiBase, zip, city, state, country,
  // onFilled(data), onLookupStart() }. Ids, not elements, so a re-rendered form is always read fresh.
  async function autoFillFromZip(options) {
    const zipInput = byId(options.zip);
    const cityInput = byId(options.city);
    const stateInput = byId(options.state);
    const countryInput = byId(options.country);
    if (!zipInput || !cityInput || !stateInput) return;

    const rawZip = String(zipInput.value || '').trim();
    let statusEl = zipInput.parentElement.querySelector('.zip-lookup-status');
    if (!statusEl) {
      statusEl = document.createElement('div');
      statusEl.className = 'zip-lookup-status small mt-1';
      statusEl.style.minHeight = '0';
      zipInput.insertAdjacentElement('afterend', statusEl);
    }
    const setStatus = (text, cls) => {
      statusEl.textContent = text;
      statusEl.className = `zip-lookup-status small mt-1 ${cls || ''}`;
    };
    setStatus('');
    // An unchanged ZIP is skipped only when City/State are already filled (legacy records may have a ZIP without City/State).
    const locationFilled = String(cityInput.value || '').trim() && String(stateInput.value || '').trim();
    if (!rawZip || rawZip === zipInput.dataset.lastLookupZip || (rawZip === zipInput.dataset.zipOnFocus && locationFilled)) return;

    if (!ZIP_PATTERN.test(rawZip)) {
      setStatus('Enter a US ZIP code (12345 or 12345-6789), or fill the address manually.', 'text-muted');
      return;
    }

    zipInput.dataset.lastLookupZip = rawZip;
    setStatus('Looking up...', 'text-muted');

    try {
      const data = await lookupZip(options.apiBase, rawZip);
      if (String(zipInput.value || '').trim() !== rawZip) return;
      if (!data.found) {
        zipInput.dataset.lastLookupZip = '';
        setStatus('ZIP code could not be found. Please enter the address manually.', 'text-warning');
        return;
      }
      cityInput.value = data.city || '';
      stateInput.value = String(data.state || '').toUpperCase();
      if (countryInput) countryInput.value = data.country || US_COUNTRY_NAME;
      if (typeof options.onFilled === 'function') options.onFilled(data);
      setStatus('');
      [cityInput, stateInput, countryInput].forEach((el) => {
        if (el) el.dispatchEvent(new Event('input', { bubbles: true }));
      });
      if (typeof options.afterFilled === 'function') options.afterFilled(data);
    } catch (err) {
      zipInput.dataset.lastLookupZip = '';
      setStatus('ZIP code could not be found. Please enter the address manually.', 'text-warning');
    }
  }

  // Bind the ZIP autofill once per input. options as autoFillFromZip, plus onAddressChange() for State / Country edits.
  function bindZipAutoFill(options) {
    const zipInput = byId(options.zip);
    if (!zipInput || zipInput.dataset.zipAutofillBound === '1') return;
    zipInput.dataset.zipAutofillBound = '1';
    const handleZipLookup = () => autoFillFromZip(options);
    zipInput.addEventListener('focus', () => {
      zipInput.dataset.zipOnFocus = String(zipInput.value || '').trim();
      zipInput.dataset.lastLookupZip = '';
    });
    zipInput.addEventListener('blur', handleZipLookup);
    zipInput.addEventListener('change', handleZipLookup);
    zipInput.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter') return;
      event.preventDefault();
      handleZipLookup();
    });
    if (typeof options.onAddressChange !== 'function') return;
    const stateInput = byId(options.state);
    const countryInput = byId(options.country);
    if (stateInput && stateInput.dataset.stateAutofillBound !== '1') {
      stateInput.dataset.stateAutofillBound = '1';
      stateInput.addEventListener('blur', options.onAddressChange);
      stateInput.addEventListener('change', options.onAddressChange);
    }
    if (countryInput && countryInput.dataset.countryAutofillBound !== '1') {
      countryInput.dataset.countryAutofillBound = '1';
      countryInput.addEventListener('blur', options.onAddressChange);
      countryInput.addEventListener('change', options.onAddressChange);
    }
  }

  global.AwsumAddress = { US_COUNTRY_NAME, isUsCountry, lookupZip, autoFillFromZip, bindZipAutoFill };
})(window);
