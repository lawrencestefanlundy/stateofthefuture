/* index.js — render the State of the Future archive grid.
   Reads data/posts.json. Filters by category and free-text search.
   Filter and search state syncs to the URL hash so links are shareable.
   Cards are built with DOM APIs (textContent / setAttribute) — no innerHTML
   interpolation of post data, so manifest content cannot be interpreted as HTML.
*/

(async function () {
  const grid = document.getElementById('grid');
  const search = document.getElementById('search');
  const resultCount = document.getElementById('result-count');
  const chips = Array.from(document.querySelectorAll('.filter-chip'));

  document.getElementById('year').textContent = String(new Date().getFullYear());

  let posts = [];
  let activeFilter = 'all';
  let query = '';

  function el(tag, opts) {
    const node = document.createElement(tag);
    if (!opts) return node;
    if (opts.className) node.className = opts.className;
    if (opts.text != null) node.textContent = String(opts.text);
    if (opts.attrs) {
      for (const [k, v] of Object.entries(opts.attrs)) node.setAttribute(k, v);
    }
    if (opts.children) {
      for (const child of opts.children) {
        if (child) node.appendChild(child);
      }
    }
    return node;
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  try {
    const res = await fetch('data/posts.json', { cache: 'no-cache' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const manifest = await res.json();
    posts = Array.isArray(manifest.posts) ? manifest.posts : [];
  } catch (err) {
    clear(grid);
    grid.appendChild(el('div', { className: 'empty-state', text: 'Could not load archive — ' + err.message }));
    console.error(err);
    return;
  }

  const totals = posts.reduce(function (acc, p) {
    acc.all++;
    acc[p.category] = (acc[p.category] || 0) + 1;
    return acc;
  }, { all: 0 });

  document.getElementById('stat-total').textContent = totals.all;
  document.getElementById('stat-essay').textContent = totals['Essay'] || 0;
  document.getElementById('stat-interview').textContent = totals['Interview'] || 0;
  document.getElementById('stat-friday-four').textContent = totals['Friday Four'] || 0;

  document.getElementById('count-all').textContent = '(' + totals.all + ')';
  document.getElementById('count-Essay').textContent = '(' + (totals['Essay'] || 0) + ')';
  document.getElementById('count-Interview').textContent = '(' + (totals['Interview'] || 0) + ')';
  document.getElementById('count-Friday-Four').textContent = '(' + (totals['Friday Four'] || 0) + ')';

  // -- URL hash <-> state sync --------------------------------------------

  function readHash() {
    const h = window.location.hash.slice(1);
    if (!h) return;
    const params = new URLSearchParams(h);
    const f = params.get('filter');
    const q = params.get('q');
    if (f === 'all' || f === 'Essay' || f === 'Interview' || f === 'Friday Four') {
      activeFilter = f;
    }
    if (q) {
      query = q;
      search.value = q;
    }
  }

  function writeHash() {
    const params = new URLSearchParams();
    if (activeFilter !== 'all') params.set('filter', activeFilter);
    if (query) params.set('q', query);
    const next = params.toString();
    const target = next ? '#' + next : '#archive';
    if (window.location.hash !== target) {
      history.replaceState(null, '', target);
    }
  }

  // -- card rendering (no innerHTML; all post data goes through textContent / setAttribute) ----

  function categoryClass(cat) {
    return 'cat-' + String(cat).toLowerCase().replace(/\s+/g, '-');
  }

  function buildCard(p) {
    const card = el('a', { className: 'card', attrs: { href: p.url || '#' } });
    if (p.category) card.dataset.category = p.category;

    const heroSrc = p.hero_local || p.hero_remote;
    if (heroSrc) {
      const wrap = el('div', { className: 'card-image' });
      const img = el('img', { attrs: { src: heroSrc, alt: '', loading: 'lazy' } });
      wrap.appendChild(img);
      card.appendChild(wrap);
    } else {
      const wrap = el('div', { className: 'card-image placeholder' });
      wrap.appendChild(el('span', { text: p.category || '' }));
      card.appendChild(wrap);
    }

    const body = el('div', { className: 'card-body' });
    const meta = el('div', { className: 'card-meta' });
    meta.appendChild(el('span', { className: 'cat-tag ' + categoryClass(p.category || 'Essay'), text: p.category || '' }));
    meta.appendChild(el('span', { className: 'card-date', text: p.date_pretty || '' }));
    body.appendChild(meta);
    body.appendChild(el('div', { className: 'card-title', text: p.title || '' }));
    if (p.subtitle) body.appendChild(el('div', { className: 'card-subtitle', text: p.subtitle }));
    card.appendChild(body);
    return card;
  }

  function render() {
    const q = query.trim().toLowerCase();
    const filtered = posts.filter(function (p) {
      if (activeFilter !== 'all' && p.category !== activeFilter) return false;
      if (!q) return true;
      const hay = (p.title + ' ' + (p.subtitle || '')).toLowerCase();
      return hay.indexOf(q) !== -1;
    });

    chips.forEach(function (c) {
      c.setAttribute('aria-pressed', String(c.dataset.filter === activeFilter));
    });

    clear(grid);
    if (filtered.length === 0) {
      grid.appendChild(el('div', { className: 'empty-state', text: 'Nothing matches that filter.' }));
    } else {
      const frag = document.createDocumentFragment();
      filtered.forEach(function (p) { frag.appendChild(buildCard(p)); });
      grid.appendChild(frag);
    }
    resultCount.textContent = filtered.length + ' ' + (filtered.length === 1 ? 'post' : 'posts');
    writeHash();
  }

  // -- events --------------------------------------------------------------

  chips.forEach(function (c) {
    c.addEventListener('click', function () {
      activeFilter = c.dataset.filter;
      render();
    });
  });

  let searchTimer = null;
  search.addEventListener('input', function () {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(function () {
      query = search.value;
      render();
    }, 120);
  });

  window.addEventListener('hashchange', function () {
    const before = activeFilter + '|' + query;
    readHash();
    if (activeFilter + '|' + query !== before) render();
  });

  readHash();
  render();
})();
