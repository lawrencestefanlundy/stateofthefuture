#!/usr/bin/env python3
"""Sync new Substack posts into the archive via the public RSS feed.

Substack's feed exposes the most recent ~22 posts with the full HTML body in
<content:encoded>. We diff its slugs against data/posts.json and append anything
new — generating the post page, mirroring the hero image, and updating the
manifest in place. The full export-driven build remains the source of truth for
older posts; sync.py only adds.

Run: python3 scripts/sync.py
Exits 0 with no changes if everything is already in the manifest.
"""

from __future__ import annotations

import json
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

# Reuse the build pipeline so the rendered output is identical to a full rebuild.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "data" / "posts.json"
RSS_URL = "https://stateofthefuture.substack.com/feed"

NS = {
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
}


def fetch_rss(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def slug_from_link(link: str) -> str:
    # https://stateofthefuture.substack.com/p/<slug>
    return link.rstrip("/").rsplit("/", 1)[-1]


def parse_rss_date(s: str) -> tuple[str, str]:
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S GMT"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%Y-%m-%d"), dt.strftime("%-d %b %Y")
        except ValueError:
            continue
    return s, s


def main() -> int:
    if not MANIFEST_PATH.exists():
        sys.exit("missing data/posts.json — run scripts/build.py first")

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    existing_slugs = {p["slug"] for p in manifest["posts"]}

    print(f"manifest has {len(existing_slugs)} posts")
    feed = fetch_rss(RSS_URL)
    root = ET.fromstring(feed)

    added = []
    for item in root.iter("item"):
        title_el = item.find("title")
        link_el = item.find("link")
        if title_el is None or link_el is None or not link_el.text:
            continue
        slug = slug_from_link(link_el.text)
        if slug in existing_slugs:
            continue

        title = (title_el.text or "").strip()
        subtitle = (item.findtext("description", default="") or "").strip()
        pub = item.findtext("pubDate", default="") or ""
        iso, pretty = parse_rss_date(pub)

        content_el = item.find("content:encoded", NS)
        body_html = (content_el.text if content_el is not None else "") or ""
        if not body_html:
            print(f"  - skip {slug} (RSS item has no body)", file=sys.stderr)
            continue

        canonical, mirror_src = build.extract_hero(body_html)
        hero_local = build.mirror_image(mirror_src, slug) if mirror_src else None

        category = build.derive_category(slug, title)
        post = {
            "id": slug,  # numeric ID isn't in the RSS; use slug as a stable proxy
            "slug": slug,
            "title": title,
            "subtitle": subtitle,
            "date": iso,
            "date_pretty": pretty,
            "category": category,
            "category_slug": build.category_slug(category),
            "hero_remote": canonical,
            "hero_local": hero_local,
            "url": f"posts/{slug}.html",
            "substack_url": f"https://stateofthefuture.substack.com/p/{slug}",
        }

        # Render the post page (same template as full build).
        body = build.clean_body(body_html)
        out_path = ROOT / "posts" / f"{slug}.html"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(build.render_post_page(post, body), encoding="utf-8")
        added.append(post)
        print(f"  + {category:11s} {iso}  {slug}")

    if not added:
        print("no new posts.")
        return 0

    posts = manifest["posts"] + added
    posts.sort(key=lambda p: p["date"], reverse=True)
    manifest["posts"] = posts
    manifest["post_count"] = len(posts)
    manifest["generated_at"] = datetime.utcnow().isoformat() + "Z"
    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nadded {len(added)} new post(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
