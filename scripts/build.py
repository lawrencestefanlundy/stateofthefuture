#!/usr/bin/env python3
"""Build the State of the Future archive site from a Substack export.

Produces:
  data/posts.json            manifest consumed by index.html
  posts/<slug>.html          static page per post (Substack body wrapped in site shell)
  images/<slug>.<ext>        hero image mirrored from Substack S3

Run from project root: python3 scripts/build.py
"""

from __future__ import annotations

import csv
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "source"
POSTS_HTML_SRC = SRC / "posts-html"
POSTS_OUT = ROOT / "posts"
IMAGES_OUT = ROOT / "images"
DATA_OUT = ROOT / "data"
SITE_TITLE = "State of the Future"
SITE_TAGLINE = "Essays, interviews, and Friday Four dispatches on the technology shaping the future."
SUBSTACK_URL = "https://stateofthefuture.substack.com"


# --- category derivation -----------------------------------------------------

# Title-level heuristics for interviews where the slug doesn't have a clear
# "interview-" / "in-conversation-with-" marker. Catches:
#   "(feat. Noam …)"            — feat. with parenthesis
#   "w/ Andrew Bennett"          — w/ followed by a capitalised name
#   "Conversation with Manu, …"  — explicit conversation phrasing
#   "with Prateek of Proteins1"  — "with X of/at/from/," style introducing a guest
INTERVIEW_TITLE_PATTERNS = [
    re.compile(r"\(\s*feat\.?\s", re.IGNORECASE),
    re.compile(r"\bw/\s+[A-Z]"),
    re.compile(r"Conversation with\s+[A-Z]", re.IGNORECASE),
    re.compile(r"\swith\s+[A-Z]\w+(?:\s+(?:of|at|from|,|—|–))"),
]


def looks_like_interview_title(title: str) -> bool:
    if not title:
        return False
    return any(p.search(title) for p in INTERVIEW_TITLE_PATTERNS)


def derive_category(slug: str, title: str = "") -> str:
    s = slug.lower()
    t = (title or "").lower()
    if "friday-four" in s or s.startswith("four-things"):
        return "Friday Four"
    if "friday four" in t or t.startswith("four things"):
        return "Friday Four"
    if "in-conversation-with" in s or s.startswith("interview-"):
        return "Interview"
    if looks_like_interview_title(title):
        return "Interview"
    return "Essay"


def category_slug(category: str) -> str:
    return category.lower().replace(" ", "-")


# Topic taxonomy — each topic has a list of substring patterns checked against
# slug + title. A post can carry multiple topics. Friday Fours skip topic tagging
# (they're roundups by nature). Order here also drives the chip ordering in the UI.
TOPICS: list[tuple[str, str, list[str]]] = [
    ("AI & compute",     "ai-compute", [
        "ai-chips", "computeram", "compute-gradient", "fungible-compute",
        "mortal-computing", "mortal", "model-t", "moores-law", "chiplets",
        "hbm", "high-bandwidth", "model", "inference", "deploy", "frontier",
        "edge-ai", "agents", "agent", "deepseek", "sovereign", "babelfish",
        "what-if-ai", "ai-chips-computeram", "inward-collapse",
        "modular-semiconductors", "the-real-ai-bottleneck", "data-movement",
        "the-future-of-computing", "uk-opportunity-in-ai", "lfg-for-semiconductors",
        "ai-compound-semiconductors", "neural", "wen-babelfish",
        # BCIs / wearable AI / silent speech are AI & compute hardware
        "hearable", "brain computer", "brain-computer", "augmented reality",
        "silent speech", "silent-speech", "neural radiance", "nerf",
    ]),
    ("Photonics",        "photonics", [
        "photonic", "photonics", "optical-computing", "gallium-nitride",
        "the-future-of-computing-is-glass", "silicon-photonic", "light-based",
    ]),
    ("Labour & economy", "labour-economy", [
        "employment", "young-workers", "young-people", "white-collar",
        "junior", "occupational-downgrading", "unbundling-the-job",
        "blue-collar", "dirty-work", "edtech", "educating", "labour",
        "jobs", "panic-stage", "tragic-twenties",
    ]),
    ("Quantum",          "quantum", [
        "quantum", "qubit", "qubits", "willow", "cubits-in-a-fridge",
    ]),
    ("Fusion & nuclear", "fusion-nuclear", [
        "fusion", "nuclear", "atomic-energy", "atomic",
    ]),
    ("Privacy",          "privacy", [
        "privacy", "confidential-ai", "trusted-execution", "decentralised-ai",
        "private", "data-privacy", "encrypted",
    ]),
    ("Materials",        "materials", [
        "carbon-nanotube", "nanotube", "gallium-nitride", "advanced-materials",
        "compound-semiconductors",
    ]),
    ("VC & investing",   "vc", [
        "consensus-capital", "data-driven-vc", "venture-capital",
        "fund-frontier", "lux-capital", "speedinvest", "doing-research-in",
        "expeditions",
    ]),
    ("Health & bio",     "health-bio", [
        "proteins", "blood", "ambient-health", "detecting-proteins",
    ]),
]


def derive_topics(slug: str, title: str = "", category: str = "") -> list[str]:
    """Return a list of topic_slug values matching this post.
    Friday Fours are intentionally not topic-tagged — they're roundups
    spanning multiple topics by design."""
    if category == "Friday Four":
        return []
    haystack = (slug + " " + (title or "")).lower()
    matches = []
    for label, tslug, patterns in TOPICS:
        if any(p in haystack for p in patterns):
            matches.append(tslug)
    return matches


def topic_label(tslug: str) -> str:
    for label, slug, _ in TOPICS:
        if slug == tslug:
            return label
    return tslug


def topic_slug_list() -> list[tuple[str, str]]:
    return [(label, slug) for label, slug, _ in TOPICS]


def load_summaries(root: Path) -> dict[str, str]:
    """Read data/summaries.json (slug -> {summary, model}). Returns slug ->
    summary text. Generated by scripts/summarize.py — when missing the build
    falls back to the heuristic excerpt."""
    path = root / "data" / "summaries.json"
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {slug: (entry.get("summary") or "").strip() for slug, entry in raw.items() if entry.get("summary")}


def load_featured(root: Path) -> set[str]:
    """Read data/featured.txt — one post slug per line, hash-comments allowed.
    Posts in this file get featured=true in the manifest."""
    path = root / "data" / "featured.txt"
    if not path.exists():
        return set()
    out: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.add(line)
    return out


def compute_related(post: dict, all_posts: list[dict], limit: int = 3) -> list[dict]:
    """Return up to `limit` related posts. Score = topic overlap (heaviest) +
    same category bonus + temporal proximity (within ~6 months tiebreaker)."""
    if not all_posts:
        return []
    target_topics = set(post.get("topics", []))
    target_cat = post["category"]
    target_date = post["date"]
    scored: list[tuple[float, dict]] = []
    for other in all_posts:
        if other["slug"] == post["slug"]:
            continue
        score = 0.0
        overlap = len(target_topics & set(other.get("topics", [])))
        score += overlap * 3
        if other["category"] == target_cat:
            score += 1
        # Temporal proximity tiebreaker: closer date = higher score.
        try:
            t1 = datetime.fromisoformat(target_date)
            t2 = datetime.fromisoformat(other["date"])
            days = abs((t1 - t2).days)
            score += max(0, 1 - days / 365)  # within a year contributes up to +1
        except Exception:
            pass
        if score > 0:
            scored.append((score, other))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [
        {
            "slug": o["slug"],
            "title": o["title"],
            "subtitle": o.get("subtitle", ""),
            "date_pretty": o.get("date_pretty", ""),
            "category": o["category"],
            "category_slug": o["category_slug"],
            "url": o["url"],
        }
        for _, o in scored[:limit]
    ]


# --- hero image extraction ---------------------------------------------------

# Match the FIRST <img ... src="..."> in the document — Substack puts the hero
# image at the very top wrapped in .captioned-image-container.
HERO_IMG_RE = re.compile(
    r'<img\b[^>]*?\bsrc="(?P<src>[^"]+)"',
    re.IGNORECASE | re.DOTALL,
)

# Inside <img data-attrs="{...}"> Substack stores the canonical (un-resized)
# S3 URL. Prefer it over the CDN-transformed src when available.
DATA_ATTRS_RE = re.compile(
    r'<img\b[^>]*?\bdata-attrs="(?P<json>[^"]+)"',
    re.IGNORECASE | re.DOTALL,
)
# srcset entries Substack generates: ".../w_1456,c_limit,f_auto,...png 1456w".
# We pull a width-tagged URL out of the first <source srcset> in the document.
SRCSET_RE = re.compile(
    r'<source\b[^>]*?\bsrcset="(?P<srcset>[^"]+)"',
    re.IGNORECASE | re.DOTALL,
)


_SRCSET_PAIR_RE = re.compile(r'(\S+)\s+(\d+)w')


def _pick_srcset_url(srcset: str, target_w: int = 1456) -> str | None:
    """Return the URL whose descriptor is closest to (preferring <=) target_w.
    Substack URLs contain commas (transformation params), so we can't split on
    commas — instead we match each `URL <N>w` pair. URLs never contain spaces."""
    candidates = []
    for url, w_str in _SRCSET_PAIR_RE.findall(srcset):
        try:
            w = int(w_str)
        except ValueError:
            continue
        candidates.append((w, html.unescape(url)))
    if not candidates:
        return None
    candidates.sort()
    le = [c for c in candidates if c[0] <= target_w]
    if le:
        return le[-1][1]
    return candidates[0][1]


def extract_hero(post_html: str) -> tuple[str | None, str | None]:
    """Return (canonical_url, mirror_url). canonical is the S3 original (good
    for og:image); mirror is a Substack-CDN resized version we actually fetch."""
    canonical = None
    m = DATA_ATTRS_RE.search(post_html)
    if m:
        try:
            attrs = json.loads(html.unescape(m.group("json")))
            url = attrs.get("src")
            if url and url.startswith("http"):
                canonical = url
        except json.JSONDecodeError:
            pass
    if not canonical:
        m = HERO_IMG_RE.search(post_html)
        if m:
            canonical = html.unescape(m.group("src"))

    mirror = None
    m = SRCSET_RE.search(post_html)
    if m:
        mirror = _pick_srcset_url(m.group("srcset"), target_w=1456)
    if not mirror:
        mirror = canonical
    return canonical, mirror


# --- image mirroring ---------------------------------------------------------

def ext_from_url(url: str) -> str:
    path = urllib.parse.urlparse(url).path
    ext = os.path.splitext(path)[1].lower()
    if ext in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
        return ".jpg" if ext == ".jpeg" else ext
    return ".jpg"


def mirror_image(url: str, slug: str) -> str | None:
    if not url:
        return None
    ext = ext_from_url(url)
    dest = IMAGES_OUT / f"{slug}{ext}"
    if dest.exists() and dest.stat().st_size > 0:
        return f"images/{dest.name}"
    IMAGES_OUT.mkdir(parents=True, exist_ok=True)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        dest.write_bytes(data)
        return f"images/{dest.name}"
    except Exception as e:
        print(f"  ! failed to mirror image for {slug}: {e}", file=sys.stderr)
        return None


# --- image quality check (stdlib only) -------------------------------------

import struct  # noqa: E402

# Minimum acceptable hero width. Substack heroes are typically mirrored at
# 1456w; anything well under 800w almost certainly looks blurry on a 3-col
# spotlight or a 1080px-wide article hero.
MIN_HERO_WIDTH = 800


def _image_size(path: Path) -> tuple[int, int] | None:
    """Return (width, height) for PNG/JPEG/GIF/WebP without external deps.
    Returns None on unrecognised or malformed files."""
    try:
        with path.open("rb") as f:
            head = f.read(32)
            # PNG: 8-byte sig, then IHDR with width/height at offset 16.
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                if len(head) < 24:
                    return None
                w, h = struct.unpack(">II", head[16:24])
                return w, h
            # GIF: width/height little-endian at offset 6.
            if head[:6] in (b"GIF87a", b"GIF89a"):
                w, h = struct.unpack("<HH", head[6:10])
                return w, h
            # WebP: RIFF .... WEBP, then VP8/VP8L/VP8X chunk.
            if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
                chunk = head[12:16]
                if chunk == b"VP8 ":
                    # lossy: width/height at offset 26 (after frame tag).
                    f.seek(26)
                    w, h = struct.unpack("<HH", f.read(4))
                    return w & 0x3FFF, h & 0x3FFF
                if chunk == b"VP8L":
                    f.seek(21)
                    b1, b2, b3, b4 = f.read(4)
                    w = 1 + (((b2 & 0x3F) << 8) | b1)
                    h = 1 + (((b4 & 0x0F) << 10) | (b3 << 2) | ((b2 & 0xC0) >> 6))
                    return w, h
                if chunk == b"VP8X":
                    f.seek(24)
                    b = f.read(6)
                    w = 1 + (b[0] | (b[1] << 8) | (b[2] << 16))
                    h = 1 + (b[3] | (b[4] << 8) | (b[5] << 16))
                    return w, h
                return None
            # JPEG: walk segment markers until we find a SOF.
            if head[:2] == b"\xff\xd8":
                f.seek(2)
                while True:
                    while True:
                        b = f.read(1)
                        if not b:
                            return None
                        if b == b"\xff":
                            break
                    while True:
                        b = f.read(1)
                        if not b:
                            return None
                        if b != b"\xff":
                            break
                    m = b[0]
                    if m == 0x00 or m == 0xFF:
                        continue
                    # SOF markers (skip DHT/DQT/etc which use 0xC4/0xC8/0xCC).
                    if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):
                        f.read(3)  # segment length(2) + precision(1)
                        bb = f.read(4)
                        if len(bb) < 4:
                            return None
                        h, w = struct.unpack(">HH", bb)
                        return w, h
                    # Other segment: skip its payload.
                    seg_len_raw = f.read(2)
                    if len(seg_len_raw) < 2:
                        return None
                    seg_len = struct.unpack(">H", seg_len_raw)[0]
                    if seg_len < 2:
                        return None
                    f.seek(seg_len - 2, 1)
        return None
    except (OSError, struct.error):
        return None


def hero_quality(local_path: str | None) -> dict:
    """Return {width, height, ok, reason} describing a mirrored hero.
    `ok` is False when the image is missing or under MIN_HERO_WIDTH."""
    if not local_path:
        return {"width": 0, "height": 0, "ok": False, "reason": "missing"}
    p = ROOT / local_path
    if not p.exists():
        return {"width": 0, "height": 0, "ok": False, "reason": "missing"}
    size = _image_size(p)
    if not size:
        return {"width": 0, "height": 0, "ok": False, "reason": "unreadable"}
    w, h = size
    if w < MIN_HERO_WIDTH:
        return {"width": w, "height": h, "ok": False, "reason": f"low-res ({w}px wide)"}
    return {"width": w, "height": h, "ok": True, "reason": ""}


# --- post body cleanup -------------------------------------------------------

# Remove the Substack-specific overlay buttons (restack, view-image) that are
# baked into every captioned image.
OVERLAY_BUTTONS_RE = re.compile(
    r'<div class="image-link-expand">.*?</div></div>',
    re.DOTALL,
)
# Strip data-attrs blobs (huge, useless outside Substack).
DATA_ATTRS_STRIP_RE = re.compile(r'\s+data-attrs="[^"]*"', re.DOTALL)
# Subscribe widgets in the export render as empty divs that look broken; drop.
SUBSCRIBE_WIDGET_RE = re.compile(
    r'<div class="subscribe-widget"[^>]*>.*?</div>\s*</div>',
    re.DOTALL,
)
# Drop Substack-internal "captioned-button-wrap" CTAs.
CAPTIONED_BUTTON_RE = re.compile(
    r'<div class="captioned-button-wrap"[^>]*>.*?</div>',
    re.DOTALL,
)
# Drop the first hero image (we render it in the post header instead).
FIRST_IMAGE_RE = re.compile(
    r'^\s*<div class="captioned-image-container">.*?</figure></div>',
    re.DOTALL,
)


def clean_body(post_html: str) -> str:
    body = FIRST_IMAGE_RE.sub("", post_html, count=1)
    body = OVERLAY_BUTTONS_RE.sub("", body)
    body = DATA_ATTRS_STRIP_RE.sub("", body)
    body = SUBSCRIBE_WIDGET_RE.sub("", body)
    body = CAPTIONED_BUTTON_RE.sub("", body)
    return body.strip()


# --- title extraction --------------------------------------------------------

def first_paragraph_text(post_html: str, max_chars: int = 220) -> str:
    m = re.search(r"<p[^>]*>(.*?)</p>", post_html, re.DOTALL | re.IGNORECASE)
    if not m:
        return ""
    text = re.sub(r"<[^>]+>", "", m.group(1))
    text = html.unescape(text).strip()
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text


def card_excerpt(subtitle: str, body_html: str, target_chars: int = 320) -> str:
    """Description shown on archive cards. Returns the subtitle if it's
    substantial; otherwise enriches it with body text up to ~target_chars
    so every card has a meaningful blurb. Used in place of a thin one-line
    subtitle (e.g. "Dispatch from 11th April 2026")."""
    sub = (subtitle or "").strip()
    if len(sub) >= 90:
        return sub
    # Pull plain-text body paragraphs in order, skipping very short ones
    # (which are usually section markers like "—" or "Hello friends,").
    paragraphs = []
    for m in re.finditer(r"<p[^>]*>(.*?)</p>", body_html, re.DOTALL | re.IGNORECASE):
        plain = re.sub(r"<[^>]+>", "", m.group(1))
        plain = html.unescape(plain).strip()
        if len(plain) >= 40:
            paragraphs.append(plain)
        if sum(len(p) for p in paragraphs) > target_chars * 1.5:
            break
    body_text = " ".join(paragraphs)
    if not body_text:
        return sub
    if sub:
        excerpt = sub.rstrip(".!? ") + ". " + body_text
    else:
        excerpt = body_text
    if len(excerpt) > target_chars:
        excerpt = excerpt[: target_chars - 1].rstrip() + "…"
    return excerpt


def reading_time_minutes(post_html: str, wpm: int = 230) -> int:
    """Rough read-time estimate. Strip tags and count words."""
    text = re.sub(r"<[^>]+>", " ", post_html)
    text = html.unescape(text)
    words = len(text.split())
    return max(1, round(words / wpm))


# --- templating --------------------------------------------------------------

def render_post_page(post: dict, body_html: str, related: list[dict] | None = None) -> str:
    cat = post["category"]
    cat_slug = category_slug(cat)
    pretty_date = post["date_pretty"]
    read_min = reading_time_minutes(body_html)
    title = html.escape(post["title"])
    subtitle = html.escape(post["subtitle"]) if post["subtitle"] else ""
    related = related or []
    topic_labels = [topic_label(t) for t in post.get("topics", [])]
    # local paths in the manifest are relative to project root ("images/x.jpg");
    # post pages live at /posts/, so prepend ../. Remote URLs go through unchanged.
    hero_local = post.get("hero_local")
    hero_remote = post.get("hero_remote")
    if hero_local:
        hero_src = f"../{hero_local}"
    elif hero_remote:
        hero_src = hero_remote
    else:
        hero_src = ""
    hero_html = (
        f'<div class="article-hero"><div class="container article-hero-inner"><img src="{html.escape(hero_src)}" alt=""></div></div>' if hero_src else ""
    )
    og_image = ""
    if hero_local:
        og_image = f'<meta property="og:image" content="../{hero_local}">'
    elif hero_remote:
        og_image = f'<meta property="og:image" content="{html.escape(hero_remote)}">'
    subtitle_html = (
        f'<p class="post-subtitle">{subtitle}</p>' if subtitle else ""
    )
    excerpt_html = (
        f'<div class="article-excerpt">{subtitle}</div>' if subtitle else ""
    )
    topic_chips_html = ""
    if topic_labels:
        chips = "".join(
            f'<a class="ae-topic" href="../index.html#topic={html.escape(t_slug)}">{html.escape(t_label)}</a>'
            for t_label, t_slug in zip(
                topic_labels,
                [s for s in post.get("topics", [])],
            )
        )
        topic_chips_html = f'<div class="ae-topics">{chips}</div>'

    related_html = ""
    if related:
        cards = []
        for r in related:
            r_title = html.escape(r["title"])
            r_subtitle = html.escape(r.get("subtitle") or "")
            r_date = html.escape(r.get("date_pretty") or "")
            r_cat = html.escape(r["category"])
            r_cat_slug = html.escape(r["category_slug"])
            sub_html = f'<div class="related-card-subtitle">{r_subtitle}</div>' if r_subtitle else ""
            cards.append(
                f'''<a class="related-card" href="../{r['url']}">
  <div class="related-card-cat cat-{r_cat_slug}">{r_cat}</div>
  <h3 class="related-card-title">{r_title}</h3>
  {sub_html}
  <div class="related-card-date">{r_date}</div>
</a>'''
            )
        related_html = (
            '<section class="related-posts">'
            '<div class="container">'
            '<div class="related-head"><span class="related-label">Read next</span></div>'
            f'<div class="related-grid">{"".join(cards)}</div>'
            '</div>'
            '</section>'
        )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} — {SITE_TITLE}</title>
<meta name="description" content="{html.escape(post.get('subtitle') or first_paragraph_text(body_html))}">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{html.escape(post.get('subtitle') or first_paragraph_text(body_html))}">
{og_image}
<meta property="og:type" content="article">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=EB+Garamond:ital,wght@0,500;0,600;0,700;1,500&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="../assets/styles.css">
</head>
<body>
<header class="masthead">
  <div class="container">
    <div class="masthead-inner">
      <a href="../index.html" class="masthead-title">
        <span class="masthead-name">State of the Future</span>
        <span class="masthead-byline">by Lawrence Lundy-Bryan</span>
      </a>
      <nav class="masthead-nav">
        <a href="https://stateofthefuture.substack.com/podcast">Podcast</a>
        <a href="../index.html">Archive</a>
        <a href="{SUBSTACK_URL}/subscribe" class="subscribe-pill">Subscribe</a>
      </nav>
    </div>
  </div>
</header>

<article class="article single-article">
  {hero_html}
  <div class="article-header">
    <div class="container article-header-inner">
      <div class="article-eyebrow">
        <span class="ae-label">{cat}</span>
        {topic_chips_html}
      </div>
      <h1 class="article-title">{title}</h1>
      <div class="article-date">{pretty_date}</div>
      <div class="article-separator"></div>
      <div class="article-reading-time">{read_min} min read</div>
    </div>
  </div>

  <div class="article-content">
    <div class="container article-content-inner">
      {excerpt_html}
      <div class="article-body">
{body_html}
      </div>
    </div>
  </div>

  {related_html}

  <div class="article-footer">
    <div class="container">
      <div class="subscribe-block">
        <h2>Get the next dispatch</h2>
        <p>Essays and interviews on the technologies that will shape the next decade. No spam, unsubscribe anytime.</p>
        <iframe src="{SUBSTACK_URL}/embed" width="100%" height="320" style="border:none; background:#FFFAF6;" frameborder="0" scrolling="no"></iframe>
      </div>
      <div class="post-nav">
        <a href="../index.html">← Back to archive</a>
        <a href="{SUBSTACK_URL}/p/{post['slug']}" target="_blank" rel="noopener">View on Substack ↗</a>
      </div>
    </div>
  </div>
</article>

<footer class="site-footer">
  <div class="container">
    <span class="mono">© {datetime.now().year} Lawrence Lundy-Bryan</span>
    <span class="mono">stateofthefuture.io</span>
  </div>
</footer>
</body>
</html>
"""


# --- main --------------------------------------------------------------------

def parse_date(s: str) -> tuple[str, str]:
    """Return (iso_date, pretty_date)."""
    if not s:
        return "", ""
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return s, s
    return dt.strftime("%Y-%m-%d"), dt.strftime("%-d %b %Y")


def main(skip_images: bool = False) -> None:
    POSTS_OUT.mkdir(parents=True, exist_ok=True)
    DATA_OUT.mkdir(parents=True, exist_ok=True)
    IMAGES_OUT.mkdir(parents=True, exist_ok=True)

    csv_path = SRC / "posts.csv"
    if not csv_path.exists():
        sys.exit(f"missing {csv_path}")

    summaries = load_summaries(ROOT)
    by_stem: dict[str, dict] = {}
    with csv_path.open() as f:
        for row in csv.DictReader(f):
            if row.get("is_published") != "true":
                continue
            if not row.get("title"):
                continue
            # CSV's post_id column is "<numeric_id>.<slug>" — same as the HTML stem.
            by_stem[row["post_id"]] = row

    posts: list[dict] = []
    for html_path in sorted(POSTS_HTML_SRC.glob("*.html")):
        stem = html_path.stem
        if "." not in stem:
            continue
        numeric_id, slug = stem.split(".", 1)
        meta = by_stem.get(stem)
        if not meta:
            print(f"  - skip {slug} (not in CSV / unpublished)", file=sys.stderr)
            continue
        post_id = numeric_id

        post_html = html_path.read_text(encoding="utf-8")
        hero_canonical, hero_mirror_src = extract_hero(post_html)
        hero_remote = hero_canonical  # full-resolution S3 URL for og:image fallback
        hero_local = None
        if hero_mirror_src and not skip_images:
            hero_local = mirror_image(hero_mirror_src, slug)
        elif hero_mirror_src:
            # --skip-images mode: we still surface an already-mirrored file if
            # one exists, so the manifest stays accurate without hitting the CDN.
            ext = ext_from_url(hero_mirror_src)
            existing = IMAGES_OUT / f"{slug}{ext}"
            if existing.exists() and existing.stat().st_size > 0:
                hero_local = f"images/{existing.name}"
        hq = hero_quality(hero_local)

        iso, pretty = parse_date(meta.get("post_date", ""))
        category = derive_category(slug, meta.get("title", ""))
        subtitle = (meta.get("subtitle") or "").strip()
        topics = derive_topics(slug, meta.get("title", ""), category)
        # Card description preference order: LLM summary > Substack subtitle
        # enriched with body excerpt > raw subtitle.
        llm_summary = summaries.get(slug, "")
        excerpt = llm_summary or card_excerpt(subtitle, post_html)
        post = {
            "id": post_id,
            "slug": slug,
            "title": meta["title"].strip(),
            "subtitle": subtitle,
            "excerpt": excerpt,
            "summary": llm_summary,
            "date": iso,
            "date_pretty": pretty,
            "category": category,
            "category_slug": category_slug(category),
            "topics": topics,
            "featured": False,  # populated after the loop, once we know all slugs
            "hero_remote": hero_remote,
            "hero_local": hero_local,
            "hero_width": hq["width"],
            "hero_height": hq["height"],
            "hero_ok": hq["ok"],
            "url": f"posts/{slug}.html",
            "substack_url": f"{SUBSTACK_URL}/p/{slug}",
        }

        # Defer rendering until after the loop so related-posts can see all peers.
        post["_body"] = clean_body(post_html)
        posts.append(post)
        print(f"  + {category:11s} {iso}  {slug}")

    posts.sort(key=lambda p: p["date"], reverse=True)

    # Featured / "Start here" picks — read from data/featured.txt if present.
    featured_slugs = load_featured(ROOT)
    if featured_slugs:
        unknown = featured_slugs - {p["slug"] for p in posts}
        if unknown:
            print(f"  ! featured.txt references unknown slugs: {sorted(unknown)}", file=sys.stderr)
        for p in posts:
            p["featured"] = p["slug"] in featured_slugs

    # Render post pages (now that related posts can be computed against the full set).
    for post in posts:
        body = post.pop("_body")
        related = compute_related(post, posts, limit=3)
        post["related"] = [r["slug"] for r in related]
        out_path = POSTS_OUT / f"{post['slug']}.html"
        out_path.write_text(render_post_page(post, body, related), encoding="utf-8")

    manifest = {
        "site_title": SITE_TITLE,
        "tagline": SITE_TAGLINE,
        "substack_url": SUBSTACK_URL,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "post_count": len(posts),
        "topics": [{"label": l, "slug": s} for l, s in topic_slug_list()],
        "featured_count": sum(1 for p in posts if p.get("featured")),
        "posts": posts,
    }
    (DATA_OUT / "posts.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    counts = {}
    for p in posts:
        counts[p["category"]] = counts.get(p["category"], 0) + 1
    print(f"\nbuilt {len(posts)} posts: {counts}")

    # Hero image quality report — flag every post under MIN_HERO_WIDTH so the
    # user knows where to drop in better artwork. Doesn't fail the build.
    bad = []
    for p in posts:
        if p.get("hero_local") and not p.get("hero_ok", True):
            w = p.get("hero_width") or 0
            bad.append((p["slug"], w))
    if bad:
        print(f"\nhero quality: {len(bad)} post(s) below {MIN_HERO_WIDTH}px wide:")
        for slug, w in sorted(bad, key=lambda x: x[1]):
            print(f"  {w:5d}px  {slug}")
        print("  → drop a higher-res image into images/<slug>.<ext> to fix.")
    missing = [p["slug"] for p in posts if not p.get("hero_local")]
    if missing:
        print(f"\nhero quality: {len(missing)} post(s) with no hero image:")
        for slug in missing:
            print(f"          –  {slug}")


if __name__ == "__main__":
    skip_images = "--skip-images" in sys.argv
    main(skip_images=skip_images)
