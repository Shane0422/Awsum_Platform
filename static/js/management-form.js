(function () {
  'use strict';
  document.querySelectorAll('form[data-awsum-form]').forEach(form => {
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (form.dataset.saving === 'true' || !form.reportValidity()) return;
      const errorBox = form.querySelector('[data-form-error]');
      const button = form.querySelector('[type="submit"]');
      form.dataset.saving = 'true';
      button.disabled = true;
      errorBox.classList.add('d-none');
      try {
        const response = await fetch(form.action, {
          method: form.dataset.method || 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(Object.fromEntries(new FormData(form))),
        });
        const result = await response.json();
        if (!response.ok) {
          const detail = Array.isArray(result.detail)
            ? result.detail.map(item => `${item.loc.slice(1).join('.')}: ${item.msg}`).join('\n')
            : result.detail;
          throw new Error(detail || 'Unable to save changes.');
        }
        // The page reloads to show the saved data; the Common Toast shows the result after the reload.
        const method = (form.dataset.method || 'POST').toUpperCase();
        if (window.AwsumToast) {
          AwsumToast.flash(form.dataset.successMessage || (method === 'DELETE' ? AwsumToast.message('deleted', 'Record')
            : method === 'POST' ? AwsumToast.message('created', 'Record') : AwsumToast.message('saved', 'Changes')));
        }
        window.location.reload();
      } catch (error) {
        errorBox.textContent = error.message || 'Unable to save changes.';
        errorBox.classList.remove('d-none');
      } finally {
        form.dataset.saving = 'false';
        button.disabled = false;
      }
    });
    const modal = form.closest('.modal');
    if (modal) modal.addEventListener('hide.bs.modal', event => {
      if (form.dataset.saving === 'true') event.preventDefault();
    });
  });
})();
