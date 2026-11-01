(() => {
  'use strict';
  const form = document.getElementById('loginForm');
  const data = document.getElementById('loginBrandingData');
  if (!form || !data) return;
  const initial = JSON.parse(data.textContent);
  const code = document.getElementById('loginAccessCode');
  const logo = document.getElementById('loginBrandLogo');
  const name = document.getElementById('loginBrandName');
  const status = document.getElementById('loginBrandStatus');
  let generation = 0;
  let timer;
  function apply(brand) {
    name.textContent = brand.organization_name;
    logo.alt = brand.organization_name;
    logo.classList.toggle('d-none', !brand.logo_url);
    name.classList.toggle('d-none', !!brand.logo_url);
    if (brand.logo_url) logo.src = brand.logo_url;
    else logo.removeAttribute('src');
    document.getElementById('loginBrandCaption').textContent = brand.description;
    document.getElementById('loginOrganization').textContent = brand.organization_name;
  }
  logo.addEventListener('error', () => {
    logo.classList.add('d-none');
    name.classList.remove('d-none');
  });
  apply(initial);
  // PLATFORM and explicit organization entry paths are immutable, regardless
  // of cookies, prior sessions, or remembered Access Codes.
  if (code.readOnly) return;
  async function resolve() {
    const current = ++generation;
    apply(initial);
    status.textContent = '';
    const value = code.value.trim();
    if (!value) return;
    try {
      const response = await fetch('/auth/branding?' + new URLSearchParams({access_code: value}), {cache: 'no-store'});
      const brand = await response.json();
      if (current !== generation) return;
      if (!response.ok) throw new Error(brand.detail || 'Unable to resolve organization branding.');
      apply(brand);
    } catch (error) {
      if (current === generation) status.textContent = error.message;
    }
  }
  code.addEventListener('input', () => {
    generation++;
    apply(initial);
    clearTimeout(timer);
    timer = setTimeout(resolve, 300);
  });
  code.addEventListener('change', resolve);
  document.getElementById('loginModal').addEventListener('show.bs.modal', resolve);
  if (code.value.trim()) resolve();
})();
