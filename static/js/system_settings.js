(function () {
  'use strict';

  const API = '/platform/api/system-settings';
  const sections = ['company', 'storage', 'email', 'general'];
  let settings = null;
  let activeSection = 'company';
  let snapshots = {};
  let environmentManaged = false;
  let emailEnvironmentManaged = false;
  let existingStorageData = false;
  let storageOriginalPath = '';
  let pendingLogoFile = null;
  let logoPreviewUrl = null;
  let smtpPortIsDefault = true;
  let suggestedSmtpUsername = '';
  let updatingSmtpPort = false;
  let testEmailInitialized = false;
  let testEmailSending = false;
  let logoInput = null;

  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));

  async function api(path, options) {
    const requestOptions = Object.assign({
      credentials: 'same-origin',
    }, options || {});
    requestOptions.headers = Object.assign(
      requestOptions.body instanceof FormData ? {} : { 'Content-Type': 'application/json' },
      requestOptions.headers || {},
    );
    const response = await fetch(path, requestOptions);
    let body = null;
    try { body = await response.json(); } catch (_) { /* Error response may not be JSON. */ }
    if (!response.ok) {
      const error = new Error(body && body.detail ? body.detail : 'Request failed (' + response.status + ').');
      error.status = response.status;
      error.result = body && body.result;
      error.testedAt = body && body.tested_at;
      throw error;
    }
    return body;
  }

  function valuesFor(section) {
    const form = document.querySelector('[data-settings-section="' + section + '"]');
    const result = {};
    form.querySelectorAll('input, textarea, select').forEach((control) => {
      if (!control.id || control.type === 'file' || control.id === 'clear_smtp_password' ||
          control.id === 'confirm_existing_data_path_change') return;
      result[control.id] = control.type === 'checkbox' ? control.checked : control.value;
    });
    if (section === 'email') {
      if (emailEnvironmentManaged) return { test_recipient_email: $('test_recipient_email').value };
      result.smtp_password = $('smtp_password').value;
      result.clear_smtp_password = $('clear_smtp_password').checked;
      result.smtp_port_is_default = smtpPortIsDefault;
    }
    if (section === 'storage') {
      result.confirm_existing_data_path_change = $('confirm_existing_data_path_change').checked;
    }
    return result;
  }

  function isDirty(section) {
    return (section === 'company' && pendingLogoFile !== null) ||
      JSON.stringify(valuesFor(section)) !== JSON.stringify(snapshots[section] || {});
  }

  function restoreValues(section, values) {
    const form = document.querySelector('[data-settings-section="' + section + '"]');
    Object.keys(values).forEach((id) => {
      const control = form.querySelector('#' + id);
      if (!control) return;
      if (control.type === 'checkbox') control.checked = values[id];
      else control.value = values[id];
    });
    const result = document.querySelector('[data-save-result="' + section + '"]');
    if (result) result.textContent = isDirty(section) ? 'Unsaved changes' : 'Saved.';
  }

  function setMessage(message, kind) {
    const el = $('settingsGlobalMessage');
    el.textContent = message;
    el.className = 'alert alert-' + (kind || 'info') + (message ? '' : ' d-none');
  }

  function markSaved(section, resultText) {
    snapshots[section] = valuesFor(section);
    const result = document.querySelector('[data-save-result="' + section + '"]');
    if (result) {
      result.textContent = resultText || 'Saved.';
      result.classList.remove('text-danger');
    }
  }

  function fillCompany(value) {
    ['company_name', 'display_name', 'website', 'contact_email', 'phone', 'address'].forEach((key) => {
      $(key).value = value[key] || '';
    });
    if (!pendingLogoFile) {
      if (value.logo_url) $('companyLogoPreview').src = value.logo_url;
      else $('companyLogoPreview').removeAttribute('src');
      $('companyLogoStatus').textContent = 'Saved logo';
    }
  }

  function fillStorage(value) {
    environmentManaged = value.environment_override;
    existingStorageData = value.has_existing_data;
    storageOriginalPath = value.effective_path;
    const field = $('data_root_path');
    field.value = value.configured_path || value.effective_path || '';
    field.readOnly = environmentManaged;
    $('effectiveStoragePath').textContent = value.effective_path || '—';
    $('storageSource').textContent = value.source === 'environment' ? 'Environment (AWSUM_STORAGE_ROOT)' :
      (value.source === 'system_settings' ? 'System Settings' : 'Project default');
    $('storageStatus').textContent = value.ok ? 'Exists · readable · writable' :
      (value.exists ? 'Exists, but not fully accessible' : 'Does not exist');
    $('storageChangeWarning').textContent = 'Saving a different root path does not move or delete owner data, images (media), or image/path recovery maps. Check whether data already exists at ' +
      value.effective_path + ' and separately copy/migrate files before relying on the new location.';
    $('storageChangeWarning').classList.toggle('d-none', !value.has_existing_data || environmentManaged);
    $('storageMigrationConfirmWrap').classList.toggle('d-none', !value.has_existing_data || environmentManaged);
    $('confirm_existing_data_path_change').checked = false;
  }

  function fillEmail(value) {
    emailEnvironmentManaged = value.read_only;
    smtpPortIsDefault = Boolean(value.smtp_port_default);
    suggestedSmtpUsername = value.from_email || '';
    ['sender_name', 'from_email', 'reply_to_email', 'smtp_host', 'smtp_port', 'smtp_username',
      'encryption', 'test_recipient_email'].forEach((key) => {
      $(key).value = value[key] == null ? '' : value[key];
    });
    $('clear_smtp_password').checked = false;
    $('smtpPasswordSavedStatus').textContent = value.password_configured ?
      '비밀번호 설정됨. 변경하려면 새 비밀번호를 입력하세요.' :
      '비밀번호가 설정되어 있지 않습니다.';
    $('toggleSmtpPassword').disabled = value.read_only;
    $('authentication').checked = Boolean(value.authentication);
    document.querySelectorAll('.smtp-setting').forEach((input) => {
      input.disabled = value.read_only;
      input.readOnly = value.read_only && input.tagName === 'INPUT';
    });
    $('clear_smtp_password').disabled = value.read_only || !value.password_configured;
    const missing = (value.missing || []).join(' / ') || value.configuration_error || 'Review SMTP settings.';
    const sourceNote = value.read_only ?
      'Source: environment (AWSUM_SMTP_*). Values saved here are ignored while these variables are set; only Test Recipient Email can be changed.' :
      'Source: System Settings.';
    $('smtpCredentialStatus').textContent = value.password_configured ? 'Configured' : 'Not configured';
    $('encryptionKeyStatus').textContent = settings.encryption_key_configured ? 'Configured' : 'Not configured';
    $('smtpDeliveryStatus').textContent = (value.ready ? 'Email delivery: Ready' : 'Email delivery: Not ready — ' + missing) +
      ' · ' + sourceNote;
    $('smtpDeliveryStatus').className = 'alert ' + (value.ready ? 'alert-success' : 'alert-warning');
    if (value.test_result && value.tested_at) {
      const success = value.test_status === 'send_accepted' || value.test_status === 'connection_success';
      setEmailTestResult(value.test_result + ' Tested at ' + value.tested_at + '.', success);
    }
  }

  function fillGeneral(value, timezones) {
    const select = $('default_timezone');
    select.innerHTML = timezones.map((zone) =>
      '<option value="' + esc(zone) + '">' + esc(zone) + '</option>'
    ).join('');
    select.value = value.default_timezone || 'UTC';
    $('date_format').value = value.date_format || 'YYYY-MM-DD';
    $('time_format').value = value.time_format || '12h';
  }

  function updateSettings(response) {
    settings = response;
    fillCompany(response.company);
    fillStorage(response.storage);
    fillEmail(response.email);
    fillGeneral(response.general, response.timezones || ['UTC']);
    sections.forEach((section) => {
      snapshots[section] = valuesFor(section);
      const result = document.querySelector('[data-save-result="' + section + '"]');
      if (result && !result.textContent) result.textContent = 'Saved settings loaded.';
    });
  }

  async function loadSettings() {
    try {
      const response = await api(API);
      updateSettings(response);
      $('settingsLoading').classList.add('d-none');
      $('settingsContent').classList.remove('d-none');
    } catch (error) {
      $('settingsLoading').innerHTML = '<div class="alert alert-danger">' + esc(error.message) + '</div>';
    }
  }

  function setWebsiteError(message) {
    $('websiteError').textContent = message || '';
    $('website').classList.toggle('is-invalid', Boolean(message));
  }

  function normalizeWebsiteInput() {
    const input = $('website');
    const value = input.value.trim();
    if (value && !/^https?:\/\//i.test(value) && !value.includes('://')) input.value = 'https://' + value;
    else input.value = value;
  }

  async function saveSection(section) {
    const result = document.querySelector('[data-save-result="' + section + '"]');
    if (section === 'company') {
      normalizeWebsiteInput();
      setWebsiteError('');
    }
    const submitButton = document.querySelector('[data-settings-section="' + section + '"] [type="submit"]');
    if (submitButton) submitButton.disabled = true;
    result.textContent = 'Saving…';
    result.classList.remove('text-danger');
    const otherUnsaved = {};
    sections.filter((candidate) => candidate !== section && isDirty(candidate)).forEach((candidate) => {
      otherUnsaved[candidate] = valuesFor(candidate);
    });
    const payload = Object.assign({ revision: settings.revision }, valuesFor(section));
    try {
      if (section === 'company' && logoInput) logoInput.pending();
      let response;
      if (section === 'company') {
        const formData = new FormData();
        formData.append('settings', JSON.stringify(payload));
        if (pendingLogoFile) formData.append('file', pendingLogoFile, pendingLogoFile.name);
        response = await api(API + '/company/save', { method: 'POST', body: formData });
        pendingLogoFile = null;
        if (logoInput) logoInput.reset();
        if (logoPreviewUrl) URL.revokeObjectURL(logoPreviewUrl);
        logoPreviewUrl = null;
      } else {
        response = await api(API + '/' + section, {
          method: 'PUT',
          body: JSON.stringify(payload),
        });
      }
      if (section === 'email') {
        $('smtp_password').value = '';
        $('smtp_password').type = 'password';
        $('toggleSmtpPassword').setAttribute('aria-pressed', 'false');
        $('toggleSmtpPassword').setAttribute('aria-label', 'Show entered SMTP password');
        $('smtpPasswordEyeIcon').hidden = false;
        $('smtpPasswordEyeSlashIcon').hidden = true;
      }
      updateSettings(response);
      Object.keys(otherUnsaved).forEach((candidate) => restoreValues(candidate, otherUnsaved[candidate]));
      markSaved(section, 'Saved.');
      setMessage(response.image_warning || section[0].toUpperCase() + section.slice(1) + ' settings saved.',
        response.image_warning ? 'warning' : 'success');
    } catch (error) {
      result.textContent = 'Save failed — ' + error.message;
      result.classList.add('text-danger');
      if (section === 'company' && /^website /.test(error.message)) {
        setWebsiteError('Enter a valid website, e.g. awsumsolution.com or https://awsumsolution.com');
      }
      if (section === 'company' && pendingLogoFile) {
        if (settings.company.logo_url) $('companyLogoPreview').src = settings.company.logo_url;
        else $('companyLogoPreview').removeAttribute('src');
        $('companyLogoStatus').textContent =
          'Upload failed; the saved logo is unchanged. Selected file retained for retry: ' + pendingLogoFile.name;
      }
      setMessage(error.message, error.status === 409 ? 'warning' : 'danger');
    } finally {
      if (submitButton) submitButton.disabled = false;
    }
  }

  function setEmailTestResult(message, success) {
    const result = $('emailTestResult');
    result.textContent = message;
    result.className = 'alert mt-3 alert-' + (success ? 'success' : 'danger');
  }

  function setTestEmailNotice(message, kind) {
    const notice = $('sendTestEmailNotice');
    notice.textContent = message;
    notice.className = message ? 'alert alert-' + kind : 'alert d-none';
  }

  function updateTestEmailPreview() {
    if (!settings) return;
    const companyName = settings.company.company_name || 'AwsumSolution';
    const logoPath = settings.company.logo_url || '';
    let logo = '';
    if (logoPath) {
      try {
        const logoUrl = new URL(logoPath, window.location.origin);
        if (logoUrl.origin === window.location.origin) {
          logo = '<img src="' + esc(logoUrl.href) + '" alt="' + esc(companyName) +
            '" style="display:block;max-width:220px;max-height:80px;margin-bottom:20px">';
        }
      } catch (_) {
        logo = '';
      }
    }
    const message = $('testEmailMessage').value;
    $('testEmailPreviewFrom').textContent = $('testEmailFrom').value;
    $('testEmailPreviewTo').textContent = $('testEmailTo').value;
    $('testEmailPreviewSubject').textContent = $('testEmailSubject').value;
    $('testEmailPreviewBody').srcdoc = '<!doctype html><html><body style="font-family:Arial,sans-serif;color:#222">' +
      logo + '<h2>' + esc(companyName) + '</h2><p>' + esc(message).replace(/\n/g, '<br>') +
      '</p></body></html>';
    updateTestEmailSendState();
  }

  function updateTestEmailSendState() {
    const unsaved = sections.some(isDirty);
    const email = settings && settings.email;
    const missing = email && Array.isArray(email.missing) ? email.missing : [];
    const notices = [];
    if (unsaved) notices.push('Save all unsaved settings before sending. The test email uses the saved SMTP configuration.');
    if (!email || !email.ready) {
      notices.push('SMTP is not ready' + (missing.length ? ': ' + missing.join(' / ') : '.'));
    }
    setTestEmailNotice(notices.join(' '), unsaved || !email || !email.ready ? 'warning' : 'info');
    const form = $('sendTestEmailForm');
    $('sendTestEmailConfirm').disabled = testEmailSending || unsaved || !email || !email.ready ||
      !form.checkValidity() || !$('testEmailSubject').value.trim() || !$('testEmailMessage').value.trim();
  }

  function openTestEmailModal() {
    if (!settings) return;
    if (!testEmailInitialized) {
      $('testEmailFrom').value = settings.email.from_email || '';
      $('testEmailTo').value = $('test_recipient_email').value.trim();
      $('testEmailSubject').value = '[AwsumSolution] Test Email';
      $('testEmailMessage').value =
        'This is a test email from AwsumSolution.\nIf you received this message, email delivery is working.';
      $('sendTestEmailResult').textContent = '';
      $('sendTestEmailResult').className = 'alert d-none';
      testEmailInitialized = true;
    }
    updateTestEmailPreview();
    bootstrap.Modal.getOrCreateInstance($('sendTestEmailModal')).show();
  }

  async function sendTestEmail(event) {
    event.preventDefault();
    const form = $('sendTestEmailForm');
    if (!form.reportValidity() || $('sendTestEmailConfirm').disabled) return;
    testEmailSending = true;
    $('sendTestEmailConfirm').disabled = true;
    $('sendTestEmailConfirm').textContent = 'Sending…';
    $('sendTestEmailResult').textContent = '';
    $('sendTestEmailResult').className = 'alert d-none';
    try {
      const response = await api(API + '/email/send-test', {
        method: 'POST',
        body: JSON.stringify({
          recipient: $('testEmailTo').value.trim(),
          subject: $('testEmailSubject').value,
          message: $('testEmailMessage').value,
        }),
      });
      const acceptedMessage = '메일 서버에 접수되었습니다. 수신 주소: ' + response.recipient +
        '. 실제 수신 완료 여부는 확인되지 않았습니다.';
      $('sendTestEmailResult').textContent = acceptedMessage;
      $('sendTestEmailResult').className = 'alert alert-success';
      setEmailTestResult(acceptedMessage, true);
      testEmailInitialized = false;
    } catch (error) {
      $('sendTestEmailResult').textContent = error.result || error.message;
      $('sendTestEmailResult').className = 'alert alert-danger';
    } finally {
      testEmailSending = false;
      $('sendTestEmailConfirm').textContent = 'Send';
      updateTestEmailPreview();
    }
  }

  function bindForms() {
    document.querySelectorAll('[data-settings-section]').forEach((form) => {
      form.addEventListener('submit', (event) => {
        event.preventDefault();
        saveSection(form.getAttribute('data-settings-section'));
      });
      form.addEventListener('input', (event) => {
        if (event.target && event.target.id === 'website') setWebsiteError('');
        const section = form.getAttribute('data-settings-section');
        const result = document.querySelector('[data-save-result="' + section + '"]');
        if (result) {
          result.textContent = isDirty(section) ? 'Unsaved changes' : 'Saved.';
          result.classList.remove('text-danger');
        }
        if (section === 'email') {
          if (document.activeElement === $('smtp_port') && !updatingSmtpPort) smtpPortIsDefault = false;
          if (document.activeElement === $('from_email') &&
              (!$('smtp_username').value || $('smtp_username').value === suggestedSmtpUsername)) {
            $('smtp_username').value = $('from_email').value;
            suggestedSmtpUsername = $('smtp_username').value;
          }
        }
        if (section === 'storage') {
          const changed = $('data_root_path').value.trim() !== storageOriginalPath;
          if (changed) {
            $('storageChangeWarning').textContent = 'Existing path: ' + storageOriginalPath +
              '. New path: ' + $('data_root_path').value.trim() +
              '. Saving does not move or delete owner data, images (media), or image/path recovery maps; copy them before switching to the new path.';
          }
          $('storageChangeWarning').classList.toggle('d-none', !changed || !existingStorageData || environmentManaged);
          $('storageMigrationConfirmWrap').classList.toggle('d-none', !changed || !existingStorageData || environmentManaged);
        }
      });
    });

    document.querySelectorAll('#settingsTabs [data-bs-toggle="tab"]').forEach((tab) => {
      tab.addEventListener('show.bs.tab', (event) => {
        if (isDirty(activeSection) && !window.confirm('There are unsaved changes. Leave this tab without saving?')) {
          event.preventDefault();
          return;
        }
        activeSection = event.target.id.replace('-tab', '');
      });
    });
    window.addEventListener('beforeunload', (event) => {
      if (sections.some(isDirty)) {
        event.preventDefault();
        event.returnValue = '';
      }
    });

    $('checkStoragePath').addEventListener('click', async () => {
      const button = $('checkStoragePath');
      button.disabled = true;
      try {
        const status = await api(API + '/storage/check', {
          method: 'POST',
          body: JSON.stringify({ path: $('data_root_path').value }),
        });
        $('effectiveStoragePath').textContent = status.path;
        $('storageStatus').textContent = status.ok ? 'Exists · readable · writable' :
          (status.exists ? 'Exists, but not fully accessible' : 'Does not exist');
        setMessage(status.error || 'Path check completed. Temporary probe file removed.', status.ok ? 'success' : 'warning');
      } catch (error) {
        setMessage(error.message, 'danger');
      } finally {
        button.disabled = false;
      }
    });

    $('copyStoragePath').addEventListener('click', async () => {
      const value = $('effectiveStoragePath').textContent;
      try {
        await navigator.clipboard.writeText(value);
        setMessage('Effective path copied.', 'success');
      } catch (_) {
        window.prompt('Copy effective data path:', value);
      }
    });

    logoInput = AwsumImages.bindImageInput($('companyLogoFile'), message => setMessage(message, 'danger'));
    $('companyLogoFile').addEventListener('awsumimageapply', event => {
      const file = event.detail;
      pendingLogoFile = file;
      if (logoPreviewUrl) URL.revokeObjectURL(logoPreviewUrl);
      logoPreviewUrl = URL.createObjectURL(file);
      $('companyLogoPreview').src = logoPreviewUrl;
      $('companyLogoStatus').textContent = file.name + ' — 저장되지 않은 변경사항';
      $('companyLogoFile').value = '';
      const result = document.querySelector('[data-save-result="company"]');
      if (result) result.textContent = 'Unsaved changes';
    });

    $('encryption').addEventListener('change', () => {
      if (!smtpPortIsDefault) return;
      updatingSmtpPort = true;
      $('smtp_port').value = $('encryption').value === 'SSL-TLS' ? '465' : '587';
      updatingSmtpPort = false;
      $('smtp_port').dispatchEvent(new Event('input', { bubbles: true }));
    });

    $('smtp_username').addEventListener('input', () => {
      if ($('smtp_username').value !== suggestedSmtpUsername) suggestedSmtpUsername = '';
    });

    $('toggleSmtpPassword').addEventListener('click', () => {
      const password = $('smtp_password');
      const showPassword = password.type === 'password';
      password.type = showPassword ? 'text' : 'password';
      $('toggleSmtpPassword').setAttribute('aria-pressed', String(showPassword));
      $('toggleSmtpPassword').setAttribute(
        'aria-label',
        showPassword ? 'Hide entered SMTP password' : 'Show entered SMTP password',
      );
      $('smtpPasswordEyeIcon').hidden = showPassword;
      $('smtpPasswordEyeSlashIcon').hidden = !showPassword;
      password.focus();
    });

    $('testSmtpConnection').addEventListener('click', async () => {
      if (sections.some(isDirty)) {
        setEmailTestResult('Save or discard unsaved settings before testing the saved SMTP configuration.', false);
        return;
      }
      const button = $('testSmtpConnection');
      button.disabled = true;
      try {
        const result = await api(API + '/email/test-connection', { method: 'POST', body: '{}' });
        updateSettings(await api(API));
        setEmailTestResult(result.result + ' Tested at ' + result.tested_at + '.', result.success);
      } catch (error) {
        setEmailTestResult(error.message, false);
      } finally {
        button.disabled = false;
      }
    });

    $('sendSmtpTestEmail').addEventListener('click', openTestEmailModal);
    $('sendTestEmailForm').addEventListener('submit', sendTestEmail);
    ['testEmailTo', 'testEmailSubject', 'testEmailMessage'].forEach((id) => {
      $(id).addEventListener('input', updateTestEmailPreview);
    });
  }

  async function init() {
    bindForms();
    await loadSettings();
  }

  init();
})();
