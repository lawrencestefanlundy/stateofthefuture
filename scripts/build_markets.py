#!/usr/bin/env python3
"""Build the /markets/ section of the SotF site from data/stocks.json.

Generates:
  markets/index.html           grid of all stocks, filterable by theme
  markets/<ticker>.html        per-stock page (thesis, market data, essays)
  assets/markets.js            client-side filter + sort logic
"""

import json, html, re
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "stocks.json"
MARKETS = ROOT / "markets"
ASSETS = ROOT / "assets"
POSTS_MANIFEST = ROOT / "data" / "posts.json"

SUBSTACK_URL = "https://stateofthefuture.substack.com"

# Load
stocks = json.loads(DATA.read_text(encoding="utf-8"))
posts_manifest = json.loads(POSTS_MANIFEST.read_text(encoding="utf-8"))
post_by_slug = {p["slug"]: p for p in posts_manifest["posts"]}

MARKETS.mkdir(exist_ok=True)


def fmt_mcap(v):
    try:
        v = float(v) if v is not None else None
    except (ValueError, TypeError):
        return "—"
    if not v: return "—"
    if v >= 1e12: return f"${v/1e12:.2f}T"
    if v >= 1e9:  return f"${v/1e9:.1f}B"
    if v >= 1e6:  return f"${v/1e6:.0f}M"
    return f"${v:.0f}"

def fmt_pe(v):
    try:
        v = float(v) if v is not None else None
    except (ValueError, TypeError):
        return "—"
    if v is None: return "—"
    return f"{v:.0f}" if v >= 100 else f"{v:.1f}"

def fmt_pct(v):
    try:
        v = float(v) if v is not None else None
    except (ValueError, TypeError):
        return "—"
    if v is None: return "—"
    return f"{v*100:+.1f}%"

def fmt_price(v, cur):
    try:
        v = float(v) if v is not None else None
    except (ValueError, TypeError):
        return "—"
    if v is None: return "—"
    sym = {"USD": "$", "GBP": "£", "EUR": "€", "JPY": "¥", "KRW": "₩"}.get(cur, "")
    return f"{sym}{v:,.2f}"

def filename_for(ticker):
    return ticker.replace(".", "_").replace(":", "_").lower() + ".html"


# ── HEAD + masthead shared partial ──────────────────────────────────

NAV = """<nav class="masthead-nav">
        <a href="../index.html">Archive</a>
        <a href="../ecosystem/index.html">UK Ecosystem</a>
        <a href="index.html" aria-current="page">Markets</a>
        <a href="{substack}/podcast">Podcast</a>
        <a href="{substack}/subscribe" class="subscribe-pill">Subscribe</a>
      </nav>""".format(substack=SUBSTACK_URL)

AFFIL = """<div class="masthead-affiliations">
        <a href="https://cloudberry.vc" target="_blank" rel="noopener" class="affiliation-pill">Cloudberry</a>
        <a href="https://lunar.vc" target="_blank" rel="noopener" class="affiliation-pill">Lunar Ventures</a>
      </div>"""

def head(title, description):
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{html.escape(title)} — State of the Future</title>
<meta name="description" content="{html.escape(description)}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=EB+Garamond:ital,wght@0,500;0,600;0,700;1,500;1,600&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="../assets/styles.css">
<link rel="stylesheet" href="../assets/markets.css">
</head>"""

def masthead():
    return f"""<header class="masthead">
  <div class="container">
    <div class="masthead-inner">
      <a href="../index.html" class="masthead-title">
        <span class="masthead-name">State of the Future</span>
        <span class="masthead-byline">by Lawrence Lundy-Bryan</span>
      </a>
      {AFFIL}
      {NAV}
    </div>
  </div>
</header>"""


# ── INDEX PAGE ──────────────────────────────────────────────────────

def _basket_stats(theme_slug: str) -> dict:
    """Aggregate basket-level metrics for one theme.
    Returns: count, total_mcap_usd, mean_52w, median_52w, top_ticker_by_mcap,
    top_performer (ticker, change), worst_performer (ticker, change),
    latest_essay (slug, title, date)."""
    members = [t for t in stocks["tickers"] if theme_slug in t.get("sotf_themes", [])]
    if not members:
        return None
    mcaps = [m["market_cap_usd"] for m in members if m.get("market_cap_usd")]
    changes = [(m["ticker"], m["fifty_two_week_change"]) for m in members if m.get("fifty_two_week_change") is not None]
    changes_sorted = sorted(changes, key=lambda x: x[1])
    mean_52w = sum(c for _, c in changes) / len(changes) if changes else None
    median_52w = changes_sorted[len(changes_sorted)//2][1] if changes_sorted else None
    top_mcap = max(members, key=lambda m: m.get("market_cap_usd") or 0) if mcaps else None
    # Find the latest essay tagged with this theme
    latest_essay = None
    site_theme_to_topic = {
        "ai-compute": "ai-compute",
        "photonics": "photonics",
        "quantum": "quantum",
        "fusion-nuclear": "fusion-nuclear",
        "privacy": "privacy",
    }
    topic_slug = site_theme_to_topic.get(theme_slug)
    if topic_slug:
        matches = [p for p in posts_manifest["posts"] if topic_slug in (p.get("topics") or [])]
        matches.sort(key=lambda p: p.get("date", ""), reverse=True)
        if matches:
            latest_essay = matches[0]
    return {
        "slug": theme_slug,
        "count": len(members),
        "total_mcap_usd": sum(mcaps),
        "mean_52w": mean_52w,
        "median_52w": median_52w,
        "top_mcap": {"ticker": top_mcap["ticker"], "name": top_mcap["name"], "market_cap_usd": top_mcap["market_cap_usd"]} if top_mcap else None,
        "top_performer": {"ticker": changes_sorted[-1][0], "change": changes_sorted[-1][1]} if changes_sorted else None,
        "worst_performer": {"ticker": changes_sorted[0][0], "change": changes_sorted[0][1]} if changes_sorted else None,
        "latest_essay": {"slug": latest_essay["slug"], "title": latest_essay["title"], "date": latest_essay.get("date", "")} if latest_essay else None,
    }


def render_index():
    themes = stocks["themes"]
    refreshed = stocks.get("market_data_refreshed_at", "")
    refreshed_pretty = ""
    if refreshed:
        try:
            dt = datetime.fromisoformat(refreshed.replace("Z","+00:00"))
            refreshed_pretty = dt.strftime("%-d %b %Y")
        except Exception:
            refreshed_pretty = refreshed

    # Theme baskets — one card per theme
    basket_cards = []
    for theme in themes:
        b = _basket_stats(theme["slug"])
        if not b:
            continue
        mean_str = fmt_pct(b["mean_52w"])
        mean_class = "up" if (b["mean_52w"] or 0) > 0 else "down" if (b["mean_52w"] or 0) < 0 else ""
        top = b.get("top_performer") or {}
        worst = b.get("worst_performer") or {}
        top_str = f'<span class="basket-best">↑ {html.escape(top["ticker"])} {fmt_pct(top["change"])}</span>' if top else ''
        worst_str = f'<span class="basket-worst">↓ {html.escape(worst["ticker"])} {fmt_pct(worst["change"])}</span>' if worst else ''
        latest = b.get("latest_essay")
        latest_str = ""
        if latest:
            latest_str = f'<a class="basket-essay" href="../posts/{html.escape(latest["slug"])}.html"><span class="basket-essay-label">Latest</span><span class="basket-essay-title">{html.escape(latest["title"])}</span></a>'
        basket_cards.append(f"""<button class="basket-card" data-theme="{html.escape(theme["slug"])}" aria-label="Filter to {html.escape(theme["label"])}">
  <div class="basket-head">
    <div class="basket-name">{html.escape(theme["label"])}</div>
    <div class="basket-count">{b["count"]} stocks · {fmt_mcap(b["total_mcap_usd"])}</div>
  </div>
  <div class="basket-headline {mean_class}">{mean_str}<span class="basket-headline-sub">52w mean</span></div>
  <div class="basket-spread">{top_str}{worst_str}</div>
  {latest_str}
</button>""")

    theme_chips = "".join(
        f'<button class="filter-chip" data-theme="{t["slug"]}" aria-pressed="false">{html.escape(t["label"])} <span class="count">({t["count"]})</span></button>'
        for t in themes
    )

    rows = []
    for t in stocks["tickers"]:
        ticker = t["ticker"]
        themes_data = " ".join(t["sotf_themes"])
        themes_str = " · ".join(t["sotf_theme_labels"])
        mcap = fmt_mcap(t.get("market_cap_usd"))
        pe = fmt_pe(t.get("trailing_pe"))
        fwdpe = fmt_pe(t.get("forward_pe"))
        chg = fmt_pct(t.get("fifty_two_week_change"))
        chg_class = "up" if (t.get("fifty_two_week_change") or 0) > 0 else "down" if (t.get("fifty_two_week_change") or 0) < 0 else ""
        n_essays = len(t.get("essays", []))
        essay_badge = f'<span class="essay-badge" title="Discussed in {n_essays} essay{"s" if n_essays != 1 else ""}">{n_essays} essay{"s" if n_essays != 1 else ""}</span>' if n_essays else '<span class="essay-badge muted">—</span>'

        rows.append(f"""<a class="stock-row" href="{html.escape(filename_for(ticker))}" data-themes="{themes_data}">
  <div class="col-ticker">{html.escape(ticker)}</div>
  <div class="col-name"><span class="name">{html.escape(t["name"])}</span><span class="country">{html.escape(t["country"])}</span></div>
  <div class="col-themes">{html.escape(themes_str)}</div>
  <div class="col-mcap">{mcap}</div>
  <div class="col-pe">{pe}</div>
  <div class="col-fwdpe">{fwdpe}</div>
  <div class="col-chg {chg_class}">{chg}</div>
  <div class="col-essays">{essay_badge}</div>
</a>""")

    page = f"""{head("Markets", "Public companies with exposure to the State of the Future themes — semiconductors, photonics, AI compute, quantum, fusion-nuclear, privacy.")}
<body class="page-markets">

{masthead()}

<section class="issue-bar">
  <div class="container issue-bar-inner">
    <span class="label">Markets</span>
    <span class="sep">·</span>
    <span class="label">{len(stocks["tickers"])} stocks tied to the SotF themes</span>
    <span class="sep">·</span>
    <span class="label muted">refreshed {refreshed_pretty}</span>
  </div>
</section>

<section class="topic-bar">
  <div class="container topic-bar-inner">
    <span class="topic-bar-label">Themes</span>
    <div class="topic-list" id="theme-list">
      <button class="filter-chip" data-theme="" aria-pressed="true">All themes <span class="count">({len(stocks["tickers"])})</span></button>
      {theme_chips}
    </div>
  </div>
</section>

<section class="basket-bar">
  <div class="container">
    <div class="basket-grid">
      {"".join(basket_cards)}
    </div>
  </div>
</section>

<main class="markets-wrap">
  <div class="container">
    <div class="stock-table" id="stock-table">
      <div class="stock-head">
        <div class="col-ticker">Ticker</div>
        <div class="col-name">Company</div>
        <div class="col-themes">Themes</div>
        <div class="col-mcap sortable" data-sort="market_cap_usd">Market cap</div>
        <div class="col-pe sortable" data-sort="trailing_pe">P/E (T)</div>
        <div class="col-fwdpe sortable" data-sort="forward_pe">P/E (F)</div>
        <div class="col-chg sortable" data-sort="fifty_two_week_change">52w Δ</div>
        <div class="col-essays">Coverage</div>
      </div>
      {"".join(rows)}
    </div>
    <p class="markets-note">Market data refresh: daily via Yahoo Finance. Essay coverage scanned across the full SotF archive. Not investment advice.</p>
  </div>
</main>

<footer class="site-footer">
  <div class="container">
    <span>© <span id="year"></span> Lawrence Lundy-Bryan</span>
    <span>stateofthefuture.io</span>
  </div>
</footer>

<script src="../assets/markets.js"></script>
<script>document.getElementById('year').textContent = String(new Date().getFullYear());</script>
</body>
</html>
"""
    (MARKETS / "index.html").write_text(page, encoding="utf-8")


# ── PER-STOCK PAGES ─────────────────────────────────────────────────

def render_stock(t):
    ticker = t["ticker"]
    themes_str = " · ".join(t["sotf_theme_labels"])

    # Recent quarters block — populated by ingest_earnings.py + sync
    quarters_html = ""
    qs = t.get("quarters_md") or []
    if qs:
        # Render the first 4 quarters (most recent) as collapsible markdown.
        # Each entry is already a "### Q1 2026 — reported … " block; we wrap
        # in a styled container and convert markdown bold + bullet points to HTML.
        import re as _re
        rendered = []
        for q in qs[:4]:
            body = q.replace("### ", "<h3 class='quarter-title'>", 1).replace("\n", "</h3>", 1) if q.startswith("### ") else q
            # Markdown bold → <strong>
            body = _re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", body)
            # Markdown bullet lines starting with "- " or "- > "
            lines = body.split("\n")
            out_lines = []
            in_ul = False
            for ln in lines:
                if ln.startswith("- "):
                    if not in_ul:
                        out_lines.append("<ul>"); in_ul = True
                    out_lines.append("<li>" + ln[2:] + "</li>")
                else:
                    if in_ul:
                        out_lines.append("</ul>"); in_ul = False
                    out_lines.append(ln)
            if in_ul:
                out_lines.append("</ul>")
            rendered.append(f'<div class="quarter-entry">{"".join(out_lines)}</div>')
        quarters_html = f"""<section class="stock-quarters">
  <h2>Recent quarters</h2>
  <div class="quarters-list">
    {"".join(rendered)}
  </div>
</section>"""

    # Recent essays block
    essays_html = ""
    if t.get("essays"):
        items = []
        for slug in t["essays"]:
            post = post_by_slug.get(slug)
            if not post:
                continue
            items.append((post.get("date", ""), slug, post.get("title", ""), post.get("category", "")))
        items.sort(key=lambda x: x[0], reverse=True)
        rows = []
        for date, slug, title, cat in items:
            rows.append(f"""<a class="essay-link" href="../posts/{html.escape(slug)}.html">
  <span class="essay-date">{html.escape(date)}</span>
  <span class="essay-cat">{html.escape(cat)}</span>
  <span class="essay-title">{html.escape(title)}</span>
</a>""")
        essays_html = f"""<section class="stock-essays">
  <h2>Discussed in {len(items)} essay{'s' if len(items) != 1 else ''}</h2>
  <div class="essay-list">
    {"".join(rows)}
  </div>
</section>"""
    else:
        essays_html = """<section class="stock-essays">
  <h2>Not yet discussed</h2>
  <p class="empty">No SotF essays have mentioned this stock by name yet.</p>
</section>"""

    # Market-data grid
    def stat(label, value, hint=""):
        h = f' title="{html.escape(hint)}"' if hint else ""
        return f'<div class="stat"{h}><div class="stat-label">{html.escape(label)}</div><div class="stat-value">{value}</div></div>'

    market_block = "".join([
        stat("Price", fmt_price(t.get("price"), t.get("currency") or "USD")),
        stat("Market cap", fmt_mcap(t.get("market_cap_usd"))),
        stat("P/E (trailing)", fmt_pe(t.get("trailing_pe"))),
        stat("P/E (forward)", fmt_pe(t.get("forward_pe"))),
        stat("PEG", fmt_pe(t.get("peg_ratio")), "Forward P/E ÷ expected EPS growth. Sub-1 = growth at reasonable price."),
        stat("EV / Revenue", fmt_pe(t.get("ev_to_revenue"))),
        stat("Revenue TTM", fmt_mcap(t.get("revenue_ttm_usd"))),
        stat("Gross margin", fmt_pct(t.get("gross_margin"))),
        stat("Op margin", fmt_pct(t.get("operating_margin"))),
        stat("52w change", fmt_pct(t.get("fifty_two_week_change"))),
    ])

    rec = t.get("recommendation_key") or ""
    rec_class = "rec-" + rec.replace("_", "-") if rec else ""
    analyst_block = ""
    if t.get("analyst_count"):
        analyst_block = f"""<div class="analyst-block">
      <span class="rec-badge {rec_class}">{html.escape(rec.replace("_", " ").title() or "—")}</span>
      <span class="analyst-target">Mean target: {fmt_price(t.get("analyst_mean_target"), t.get("currency") or "USD")} · {t.get("analyst_count")} analysts</span>
    </div>"""

    page = f"""{head(f"{t['name']} ({ticker})", t.get("why_it_matters", "") or f"{t['name']} — public-company exposure to {themes_str}")}
<body class="page-stock">

{masthead()}

<section class="issue-bar">
  <div class="container issue-bar-inner">
    <span class="label"><a href="index.html" style="color:inherit;text-decoration:none;">← Markets</a></span>
    <span class="sep">·</span>
    <span class="label">{html.escape(themes_str)}</span>
    <span class="sep">·</span>
    <span class="label muted">{html.escape(t["exchange"])}: {html.escape(ticker)}</span>
  </div>
</section>

<article class="stock-article">
  <div class="container stock-article-inner">

    <header class="stock-header">
      <div class="stock-ticker">{html.escape(ticker)}</div>
      <h1 class="stock-name">{html.escape(t["name"])}</h1>
      <div class="stock-country">{html.escape(t["country"])} · {html.escape(t["sub_category"])}</div>
      <p class="stock-why">{html.escape(t.get("why_it_matters", ""))}</p>
      {analyst_block}
    </header>

    <section class="stock-market-data">
      <div class="stats-grid">
        {market_block}
      </div>
    </section>

    {quarters_html}

    {essays_html}

  </div>
</article>

<footer class="site-footer">
  <div class="container">
    <span>© <span id="year"></span> Lawrence Lundy-Bryan</span>
    <span>Market data: Yahoo Finance · daily refresh</span>
  </div>
</footer>

<script>document.getElementById('year').textContent = String(new Date().getFullYear());</script>
</body>
</html>
"""
    (MARKETS / filename_for(ticker)).write_text(page, encoding="utf-8")


# ── BUILD ───────────────────────────────────────────────────────────

render_index()
for t in stocks["tickers"]:
    render_stock(t)

print(f"built /markets/index.html + {len(stocks['tickers'])} per-stock pages")
