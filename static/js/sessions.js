(() => {
  'use strict';
  const root = document.getElementById('sessionPage');
  if (!root) return;
  const $ = (id) => document.getElementById(id);
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const timezone = root.dataset.displayTimezone || 'UTC';
  const dateFormat = new Intl.DateTimeFormat('en-CA', {
    timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
  });
  $('sessionTimezone').textContent = timezone;
  const when = (v) => {
    if (!v) return '';
    const utc = /(?:Z|[+-]\d{2}:\d{2})$/i.test(v) ? v : v + 'Z';
    const parts = Object.fromEntries(dateFormat.formatToParts(new Date(utc)).map((p) => [p.type, p.value]));
    return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second} ${timezone}`;
  };
  const location = (r) => r.client_location || 'Location unavailable';
  const browser = (r) => [r.browser_name, r.browser_version].filter(Boolean).join(' ');
  const os = (r) => [r.os_name, r.os_version].filter(Boolean).join(' ');
  const compactClient = (r) => [
    [r.browser_name, r.browser_version?.split('.')[0]].filter(Boolean).join(' '), os(r),
  ].filter(Boolean).join(' / ') || 'Unknown client';
  const headers = ['User', 'Organization / Store', 'Client / Device', 'Client Public IP', 'Approximate Client Location', 'Login Time', 'Last Activity', 'Status'];
  let items = [];
  let loading = false;
  const values = (r) => [
    r.user_name || r.user_email || 'Unknown user',
    [r.organization_name, r.context_label].filter(Boolean).join(' / '),
    compactClient(r), r.client_public_ip || 'Not Available', location(r),
    when(r.login_at), when(r.last_active_at), r.status,
  ];
  function message(text, kind = 'danger') { AwsumToast.show(text, { kind }); } // Common Toast
  async function load() {
    if (loading) return;
    loading = true;
    $('sessionRefresh').disabled = true;
    root.querySelectorAll('[data-session-export]').forEach((btn) => { btn.disabled = true; });
    const params = new URLSearchParams({q: $('sessionSearch').value, status: $('sessionStatus').value});
    try {
      const res = await fetch('/platform/sessions?' + params, {cache: 'no-store', credentials: 'same-origin'});
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Unable to load sessions.');
      items = data;
      $('sessionRows').innerHTML = items.map((r) =>
        '<tr>' + [
          `${esc(r.user_name || r.user_email || 'Unknown user')}${r.current ? ' <span class="badge text-bg-primary">Current</span>' : ''}`,
          esc([r.organization_name, r.context_label].filter(Boolean).join(' / ')),
          esc(compactClient(r)),
          `${esc(r.client_public_ip || 'Not Available')}<div class="small text-muted">${esc(location(r))}</div>`,
          esc(when(r.login_at)), esc(when(r.last_active_at)), esc(r.status),
        ].map((v) => `<td style="overflow-wrap:anywhere">${v}</td>`).join('') +
        `<td class="no-print text-nowrap"><button type="button" class="btn btn-sm btn-outline-secondary" data-details="${r.id}">View Details</button> ` +
        (r.status === 'ACTIVE' && root.dataset.canTerminate === 'true' ? `<button type="button" class="btn btn-sm btn-outline-danger" data-terminate="${r.id}">Terminate</button>` : '') +
        '</td></tr>').join('') || '<tr><td colspan="8" class="text-muted text-center">No sessions found.</td></tr>';
      $('sessionCount').textContent = `${items.length} sessions`;
    } catch (err) {
      message(err.message);
    } finally {
      loading = false;
      $('sessionRefresh').disabled = false;
      root.querySelectorAll('[data-session-export]').forEach((btn) => { btn.disabled = false; });
      const current = new URLSearchParams({q: $('sessionSearch').value, status: $('sessionStatus').value});
      if (current.toString() !== params.toString()) load();
    }
  }
  $('sessionRows').addEventListener('click', async (e) => {
    const details = e.target.closest('[data-details]');
    if (details) {
      const r = items.find((x) => x.id === Number(details.dataset.details));
      const sections = {
        Session: {
          User: r.user_name, Email: r.user_email, Context: r.login_context,
          Organization: r.organization_name, 'Role / Store': r.context_label, Status: r.status,
        },
        Network: {
          'Client Public IP': r.client_public_ip, 'Client Local IP': r.client_local_ip,
          'Client Location': location(r), 'Connection Type': r.connection_type,
        },
        Client: {
          OS: os(r), Browser: browser(r), 'Device Name': r.device_name,
          'Device UUID': r.device_uuid, 'Browser Client ID': r.browser_client_id, 'App Version': r.app_version,
        },
        Activity: {
          'Login Time': when(r.login_at), 'Last Activity': when(r.last_active_at),
          'Maximum Expiry': when(r.expires_at), 'Logout Time': when(r.logout_at),
          'Terminated At': when(r.terminated_at), 'Terminated By (User ID)': r.terminated_by,
          'Session ID': r.id,
        },
        Technical: {
          'Client User Agent': r.user_agent, 'Client IP Source': r.client_ip_source,
          'Transport Peer IP (not client location)': r.transport_peer_ip,
        },
      };
      $('sessionDetailsBody').innerHTML = Object.entries(sections).map(([title, fields]) =>
        `<section class="mb-3"><h3 class="fs-6 fw-bold border-bottom pb-2">${esc(title)}</h3><dl class="row mb-0">` +
        Object.entries(fields).map(([k, v]) => `<dt class="col-sm-4">${esc(k)}</dt><dd class="col-sm-8 text-break">${esc(v ?? 'Not Available') || 'Not Available'}</dd>`).join('') +
        '</dl></section>').join('');
      bootstrap.Modal.getOrCreateInstance($('sessionDetails')).show();
    }
    const button = e.target.closest('[data-terminate]');
    if (!button) return;
    const row = items.find((x) => x.id === Number(button.dataset.terminate));
    if (!confirm(row.current ? 'Terminate your current session? You will need to sign in again.' : 'Terminate this active web session?')) return;
    button.disabled = true;
    try {
      const res = await fetch(`/platform/session/${row.id}/terminate`, {method: 'POST', credentials: 'same-origin'});
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Unable to terminate session.');
      message('Session terminated.', 'success');
      await load();
    } catch (err) {
      message(err.message);
    } finally {
      button.disabled = false;
    }
  });
  $('sessionRefresh').addEventListener('click', load);
  $('sessionStatus').addEventListener('change', load);
  let timer;
  $('sessionSearch').addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(load, 250); });
  root.querySelectorAll('[data-session-export]').forEach((btn) => btn.addEventListener('click', () => {
    const rows = items.map(values);
    if (btn.dataset.sessionExport === 'pdf') {
      AwsumReports.printTable('Sessions Report', headers, rows);
      return;
    }
    const csv = (v) => '"' + String(v ?? '').replace(/^[=+@-]/, (c) => "'" + c).replace(/"/g, '""') + '"';
    const blob = new Blob(['\ufeff' + [headers, ...rows].map((r) => r.map(csv).join(',')).join('\r\n')], {type:'text/csv;charset=utf-8'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'web-sessions.csv'; a.click(); URL.revokeObjectURL(url);
  }));
  load();
})();
