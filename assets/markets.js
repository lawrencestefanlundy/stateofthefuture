/* Client-side filter + sort for /markets/index.html. */
(function () {
  const table = document.getElementById('stock-table');
  if (!table) return;
  const rows = Array.from(table.querySelectorAll('.stock-row'));
  const chips = Array.from(document.querySelectorAll('[data-theme]'));
  const sortHeaders = Array.from(table.querySelectorAll('.sortable'));

  let activeTheme = '';
  let sortBy = 'market_cap_usd';
  let sortDir = -1;  // -1 desc, +1 asc

  function readHash() {
    const h = window.location.hash.slice(1);
    const params = new URLSearchParams(h);
    const t = params.get('theme');
    const s = params.get('sort');
    const d = params.get('dir');
    if (t) activeTheme = t;
    if (s) sortBy = s;
    if (d === 'asc') sortDir = 1; else if (d === 'desc') sortDir = -1;
  }
  function writeHash() {
    const p = new URLSearchParams();
    if (activeTheme) p.set('theme', activeTheme);
    if (sortBy !== 'market_cap_usd') p.set('sort', sortBy);
    if (sortDir === 1) p.set('dir', 'asc');
    const next = p.toString();
    history.replaceState(null, '', next ? '#' + next : '#');
  }

  function rowValue(row, key) {
    // Pull numeric value from the relevant column based on display text.
    const map = {
      'market_cap_usd': '.col-mcap',
      'trailing_pe': '.col-pe',
      'forward_pe': '.col-fwdpe',
      'fifty_two_week_change': '.col-chg',
    };
    const cell = row.querySelector(map[key]);
    if (!cell) return null;
    const txt = cell.textContent.trim();
    if (txt === '—') return null;
    // Parse "$5.22T", "$835.9B", "32.4", "+50.2%"
    let n = parseFloat(txt.replace(/[$,%+]/g, ''));
    if (isNaN(n)) return null;
    if (txt.includes('T')) n *= 1e12;
    else if (txt.includes('B')) n *= 1e9;
    else if (txt.includes('M')) n *= 1e6;
    return n;
  }

  function render() {
    // Filter
    rows.forEach(function (row) {
      const themes = (row.dataset.themes || '').split(' ');
      const ok = !activeTheme || themes.includes(activeTheme);
      row.style.display = ok ? '' : 'none';
    });
    // Sort
    const visible = rows.filter(function (r) { return r.style.display !== 'none'; });
    visible.sort(function (a, b) {
      const va = rowValue(a, sortBy);
      const vb = rowValue(b, sortBy);
      if (va === null && vb === null) return 0;
      if (va === null) return 1;
      if (vb === null) return -1;
      return (va - vb) * sortDir;
    });
    // Re-append in new order (only visible ones; hidden stay where they were)
    visible.forEach(function (r) { table.appendChild(r); });
    // Update chip state
    chips.forEach(function (c) {
      c.setAttribute('aria-pressed', String((c.dataset.theme || '') === activeTheme));
    });
    // Update sort header indicators
    sortHeaders.forEach(function (h) {
      h.classList.remove('active', 'asc');
      if (h.dataset.sort === sortBy) {
        h.classList.add('active');
        if (sortDir === 1) h.classList.add('asc');
      }
    });
    writeHash();
  }

  // Note: `chips` selector [data-theme] also matches .basket-card. When a
  // basket card is clicked we scroll the table into view; for the small
  // top-row chips we don't (the user is already looking at them).
  chips.forEach(function (c) {
    c.addEventListener('click', function () {
      activeTheme = c.dataset.theme || '';
      if (c.classList.contains('basket-card')) {
        setTimeout(function () {
          table.scrollIntoView({behavior: 'smooth', block: 'start'});
        }, 50);
      }
      render();
    });
  });
  sortHeaders.forEach(function (h) {
    h.addEventListener('click', function () {
      const k = h.dataset.sort;
      if (k === sortBy) sortDir = -sortDir;
      else { sortBy = k; sortDir = -1; }
      render();
    });
  });
  window.addEventListener('hashchange', function () {
    readHash(); render();
  });

  readHash();
  render();
})();
