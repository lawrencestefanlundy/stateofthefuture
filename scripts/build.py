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

def derive_category(slug: str, title: str = "") -> str:
    s = slug.lower()
    t = (title or "").lower()
    if "friday-four" in s or s.startswith("four-things"):
        return "Friday Four"
    if "friday four" in t or t.startswith("four things"):
        return "Friday Four"
    if "in-conversation-with" in s or s.startswith("interview-"):
        return "Interview"
    return "Essay"


def category_slug(category: str) -> str:
    return category.lower().replace(" ", "-")


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


# --- templating --------------------------------------------------------------

def render_post_page(post: dict, body_html: str) -> str:
    cat = post["category"]
    cat_slug = category_slug(cat)
    pretty_date = post["date_pretty"]
    title = html.escape(post["title"])
    subtitle = html.escape(post["subtitle"]) if post["subtitle"] else ""
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
        f'<div class="post-hero"><img src="{html.escape(hero_src)}" alt=""></div>' if hero_src else ""
    )
    og_image = ""
    if hero_local:
        og_image = f'<meta property="og:image" content="../{hero_local}">'
    elif hero_remote:
        og_image = f'<meta property="og:image" content="{html.escape(hero_remote)}">'
    subtitle_html = (
        f'<p class="post-subtitle">{subtitle}</p>' if subtitle else ""
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
  <div class="container masthead-inner">
    <a href="../index.html" class="masthead-title">
      <span class="masthead-eyebrow">Lawrence Lundy-Bryan</span>
      <span class="masthead-name">State of the Future</span>
    </a>
    <nav class="masthead-nav">
      <a href="../index.html">Archive</a>
      <a href="{SUBSTACK_URL}/subscribe" class="subscribe-pill">Subscribe</a>
    </nav>
  </div>
</header>

<article class="post">
  <div class="container post-container">
    <div class="post-meta-row">
      <span class="cat-tag cat-{cat_slug}">{cat}</span>
      <span class="sep"></span>
      <span class="post-date">{pretty_date}</span>
    </div>
    <h1 class="post-title">{title}</h1>
    {subtitle_html}
    <div class="post-byline">By <a href="https://stateofthefuture.substack.com" target="_blank" rel="noopener">Lawrence Lundy-Bryan</a></div>
    {hero_html}
    <div class="post-body">
{body_html}
    </div>
    <div class="post-footer">
      <div class="subscribe-block">
        <h2>Get the next dispatch</h2>
        <p>Essays and interviews on the technologies that will shape the next decade. No spam, unsubscribe anytime.</p>
        <iframe src="{SUBSTACK_URL}/embed" width="100%" height="320" style="border:none; background:#FFFFFF;" frameborder="0" scrolling="no"></iframe>
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

        iso, pretty = parse_date(meta.get("post_date", ""))
        category = derive_category(slug, meta.get("title", ""))
        post = {
            "id": post_id,
            "slug": slug,
            "title": meta["title"].strip(),
            "subtitle": (meta.get("subtitle") or "").strip(),
            "date": iso,
            "date_pretty": pretty,
            "category": category,
            "category_slug": category_slug(category),
            "hero_remote": hero_remote,
            "hero_local": hero_local,
            "url": f"posts/{slug}.html",
            "substack_url": f"{SUBSTACK_URL}/p/{slug}",
        }

        body = clean_body(post_html)
        out_path = POSTS_OUT / f"{slug}.html"
        out_path.write_text(render_post_page(post, body), encoding="utf-8")
        posts.append(post)
        print(f"  + {category:11s} {iso}  {slug}")

    posts.sort(key=lambda p: p["date"], reverse=True)

    manifest = {
        "site_title": SITE_TITLE,
        "tagline": SITE_TAGLINE,
        "substack_url": SUBSTACK_URL,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "post_count": len(posts),
        "posts": posts,
    }
    (DATA_OUT / "posts.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    counts = {}
    for p in posts:
        counts[p["category"]] = counts.get(p["category"], 0) + 1
    print(f"\nbuilt {len(posts)} posts: {counts}")


if __name__ == "__main__":
    skip_images = "--skip-images" in sys.argv
    main(skip_images=skip_images)
