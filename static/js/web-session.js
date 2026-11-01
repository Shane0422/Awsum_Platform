(() => {
  'use strict';
  const nativeFetch = window.fetch.bind(window);
  let redirecting = false;
  window.fetch = async (...args) => {
    const response = await nativeFetch(...args);
    if (response.status === 401 && !redirecting) {
      const target = response.headers.get('X-Awsum-Login');
      if (target) {
        const url = new URL(target, location.origin);
        const loginEntry = ['/', '/login', '/platform-admin'].includes(url.pathname) ||
          /^\/login\/(agency|merchant|store)\/[A-Za-z0-9_-]{1,32}$/.test(url.pathname);
        if (url.origin === location.origin && loginEntry) {
          redirecting = true;
          location.replace(url.href);
        }
      }
    }
    return response;
  };
})();
