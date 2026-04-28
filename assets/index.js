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
  const chips = Array.from(document.querySelectorAll('.filter-chip[data-filter]'));

  document.getElementById('year').textContent = String(new Date().getFullYear());

  let posts = [];
  let topics = [];
  let activeFilter = 'all';
  let activeTopic = '';        // '' = all topics
  let activeSort = 'newest';   // 'newest' | 'oldest'
  let featuredOnly = false;
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
    topics = Array.isArray(manifest.topics) ? manifest.topics : [];
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

  document.getElementById('count-all').textContent = '(' + totals.all + ')';
  document.getElementById('count-Essay').textContent = '(' + (totals['Essay'] || 0) + ')';
  document.getElementById('count-Interview').textContent = '(' + (totals['Interview'] || 0) + ')';
  document.getElementById('count-Friday-Four').textContent = '(' + (totals['Friday Four'] || 0) + ')';

  // Compute scope label dynamically: "84 posts since YYYY".
  const earliestYear = posts.reduce(function (acc, p) {
    if (!p.date) return acc;
    const y = parseInt(p.date.substring(0, 4), 10);
    return (acc === null || y < acc) ? y : acc;
  }, null);
  const scopeEl = document.getElementById('issue-scope');
  if (scopeEl) {
    scopeEl.textContent = totals.all + ' posts since ' + (earliestYear || new Date().getFullYear());
  }

  // Featured CTA — show count of curated picks. Hide the banner if none.
  const featuredCount = posts.filter(function (p) { return p.featured; }).length;
  const featuredCountEl = document.getElementById('featured-count');
  const featuredBanner = document.querySelector('.featured-banner');
  if (featuredCount > 0 && featuredCountEl) {
    const wordMap = { 1: 'one', 2: 'two', 3: 'three', 4: 'four', 5: 'five', 6: 'six', 7: 'seven', 8: 'eight', 9: 'nine', 10: 'ten' };
    featuredCountEl.textContent = wordMap[featuredCount] || String(featuredCount);
  } else if (featuredBanner) {
    featuredBanner.style.display = 'none';
  }

  // Build topic chips dynamically from manifest.topics + counts.
  const topicCounts = posts.reduce(function (acc, p) {
    (p.topics || []).forEach(function (t) { acc[t] = (acc[t] || 0) + 1; });
    return acc;
  }, {});
  const topicList = document.getElementById('topic-list');
  if (topicList) {
    clear(topicList);
    const allTopicChip = el('button', {
      className: 'filter-chip',
      attrs: { 'data-topic': '', 'aria-pressed': 'true', 'type': 'button' },
      text: 'All topics',
    });
    topicList.appendChild(allTopicChip);
    topics.forEach(function (t) {
      const c = topicCounts[t.slug] || 0;
      if (c === 0) return;
      const chip = el('button', {
        className: 'filter-chip',
        attrs: { 'data-topic': t.slug, 'aria-pressed': 'false', 'type': 'button' },
      });
      chip.appendChild(document.createTextNode(t.label + ' '));
      chip.appendChild(el('span', { className: 'count', text: '(' + c + ')' }));
      topicList.appendChild(chip);
    });
  }
  const topicChips = Array.from(document.querySelectorAll('[data-topic]'));
  const featuredBtn = document.querySelector('button.filter-chip[data-filter-featured]');
  const featuredCta = document.querySelector('.featured-cta');
  const sortBtns = Array.from(document.querySelectorAll('.sort-btn'));
  const topicToggle = document.getElementById('topic-toggle');
  const topicBar = document.querySelector('.topic-bar');

  // -- URL hash <-> state sync --------------------------------------------

  function readHash() {
    const h = window.location.hash.slice(1);
    if (!h) return;
    const params = new URLSearchParams(h);
    const f = params.get('filter');
    const q = params.get('q');
    const t = params.get('topic');
    const f2 = params.get('featured');
    const s = params.get('sort');
    if (f === 'all' || f === 'Essay' || f === 'Interview' || f === 'Friday Four') {
      activeFilter = f;
    }
    if (t && topics.some(function (x) { return x.slug === t; })) {
      activeTopic = t;
    }
    if (s === 'oldest' || s === 'newest') activeSort = s;
    featuredOnly = f2 === '1';
    if (q) {
      query = q;
      search.value = q;
    }
  }

  function writeHash() {
    const params = new URLSearchParams();
    if (activeFilter !== 'all') params.set('filter', activeFilter);
    if (activeTopic) params.set('topic', activeTopic);
    if (activeSort !== 'newest') params.set('sort', activeSort);
    if (featuredOnly) params.set('featured', '1');
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

  function ctaFor(category) {
    return category === 'Interview' ? 'Read interview →'
      : category === 'Friday Four' ? 'Read dispatch →'
      : 'Read essay →';
  }

  function buildCardBody(p) {
    const body = el('div', { className: 'card-body' });
    body.appendChild(el('div', { className: 'card-title', text: p.title || '' }));
    const description = p.excerpt || p.subtitle;
    if (description) body.appendChild(el('div', { className: 'card-subtitle', text: description }));
    body.appendChild(el('span', { className: 'card-cta', text: ctaFor(p.category) }));
    return body;
  }

  function buildCardFoot(p) {
    const foot = el('div', { className: 'card-foot' });
    foot.appendChild(el('span', { className: 'card-cat ' + categoryClass(p.category || 'Essay'), text: p.category || '' }));
    if (p.date_pretty) foot.appendChild(el('span', { className: 'card-date', text: p.date_pretty }));
    return foot;
  }

  function buildCard(p) {
    const card = el('a', { className: 'card', attrs: { href: p.url || '#' } });
    if (p.category) card.dataset.category = p.category;
    card.appendChild(buildCardBody(p));
    card.appendChild(buildCardFoot(p));
    return card;
  }

  function buildSpotlight(p) {
    // Spotlight = first post of the current filter. Image on top with
    // "Latest | Category" tags overlaid; deep teal panel below.
    const card = el('a', { className: 'card spotlight', attrs: { href: p.url || '#' } });
    if (p.category) card.dataset.category = p.category;

    const heroSrc = p.hero_local || p.hero_remote;
    const imageWrap = el('div', { className: heroSrc ? 'card-image' : 'card-image placeholder' });
    const tags = el('div', { className: 'spotlight-tags' });
    tags.appendChild(el('span', { className: 'spotlight-tag', text: 'Latest' }));
    tags.appendChild(el('span', { className: 'spotlight-tag tag-light', text: p.category || '' }));
    imageWrap.appendChild(tags);
    if (heroSrc) {
      const img = el('img', { attrs: { src: heroSrc, alt: '', loading: 'eager' } });
      img.addEventListener('error', function () { img.remove(); });
      imageWrap.appendChild(img);
    }
    card.appendChild(imageWrap);

    card.appendChild(buildCardBody(p));
    card.appendChild(buildCardFoot(p));
    return card;
  }

  function render() {
    const q = query.trim().toLowerCase();
    const filtered = posts.filter(function (p) {
      if (activeFilter !== 'all' && p.category !== activeFilter) return false;
      if (activeTopic && !(p.topics || []).includes(activeTopic)) return false;
      if (featuredOnly && !p.featured) return false;
      if (!q) return true;
      const hay = (p.title + ' ' + (p.subtitle || '') + ' ' + (p.excerpt || '')).toLowerCase();
      return hay.indexOf(q) !== -1;
    });

    if (activeSort === 'oldest') {
      filtered.sort(function (a, b) { return (a.date || '').localeCompare(b.date || ''); });
    } else {
      filtered.sort(function (a, b) { return (b.date || '').localeCompare(a.date || ''); });
    }

    chips.forEach(function (c) {
      c.setAttribute('aria-pressed', String(c.dataset.filter === activeFilter));
    });
    topicChips.forEach(function (c) {
      c.setAttribute('aria-pressed', String((c.dataset.topic || '') === activeTopic));
    });
    sortBtns.forEach(function (b) {
      b.setAttribute('aria-pressed', String(b.dataset.sort === activeSort));
    });
    if (featuredBtn) featuredBtn.setAttribute('aria-pressed', String(featuredOnly));
    if (featuredCta) featuredCta.setAttribute('aria-pressed', String(featuredOnly));

    clear(grid);
    if (filtered.length === 0) {
      grid.appendChild(el('div', { className: 'empty-state', text: 'Nothing matches that filter.' }));
    } else {
      const frag = document.createDocumentFragment();
      // First filtered post = spotlight (col 1, span 2 rows). Rest = regular cards.
      frag.appendChild(buildSpotlight(filtered[0]));
      for (let i = 1; i < filtered.length; i++) frag.appendChild(buildCard(filtered[i]));
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

  topicChips.forEach(function (c) {
    c.addEventListener('click', function () {
      activeTopic = c.dataset.topic || '';
      render();
    });
  });

  function toggleFeatured() {
    featuredOnly = !featuredOnly;
    render();
  }
  if (featuredBtn) featuredBtn.addEventListener('click', toggleFeatured);
  if (featuredCta) featuredCta.addEventListener('click', toggleFeatured);

  sortBtns.forEach(function (b) {
    b.addEventListener('click', function () {
      activeSort = b.dataset.sort || 'newest';
      render();
    });
  });

  if (topicToggle && topicBar) {
    topicToggle.addEventListener('click', function () {
      const open = topicBar.classList.toggle('is-open');
      topicToggle.setAttribute('aria-expanded', String(open));
    });
  }

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
