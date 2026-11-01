(() => {
  'use strict';
  const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));

  function printHtml(html) {
    const popup = window.open('', '_blank', 'width=1100,height=780');
    if (!popup) {
      alert('Please allow pop-ups to print this report.');
      return;
    }
    if (!html.includes('id="awsum-report-context"')) {
      const context = document.getElementById('awsum-report-context');
      if (!context) {
        popup.close();
        throw new Error('Report context is unavailable. Refresh or sign in again.');
      }
      const assets = '<link rel="stylesheet" href="/static/css/report-print.css?v=2">' + context.outerHTML;
      html = /<\/head\s*>/i.test(html) ? html.replace(/<\/head\s*>/i, () => assets + '</head>') : assets + html;
    }
    const ready = async () => {
      await popup.document.fonts.ready;
      popup.focus();
      popup.print();
    };
    // Load waits for the common stylesheet as well as report assets.
    popup.document.open();
    popup.addEventListener('load', ready, { once: true });
    popup.document.write(html);
    popup.document.close();
  }

  function printTable(title, headers, rows) {
    const head = headers.map((v) => `<th>${escapeHtml(v)}</th>`).join('');
    const body = rows.map((row) => '<tr>' + row.map((v) => `<td>${escapeHtml(v)}</td>`).join('') + '</tr>').join('');
    printHtml(`<!doctype html><html><head><meta charset="UTF-8"><title>${escapeHtml(title)}</title>
      <style>@page { size: landscape; }</style></head><body class="awsum-report">
      <h1>${escapeHtml(title)}</h1><p>Total Records: ${rows.length}</p>
      <table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></body></html>`);
  }

  function spreadsheetStyle() {
    const style = document.getElementById('awsum-spreadsheet-print');
    if (!style) throw new Error('Report context is unavailable. Refresh or sign in again.');
    return style.outerHTML;
  }

  window.AwsumReports = Object.freeze({ printHtml, printTable, spreadsheetStyle });
  if (document.body.hasAttribute('data-report-auto-print')) {
    window.addEventListener('load', async () => {
      await document.fonts.ready;
      window.print();
    }, { once: true });
  }
})();
