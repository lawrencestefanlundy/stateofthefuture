#!/usr/bin/env python3
"""Generate one-paragraph card summaries via Claude Haiku.

Body source order:
  1. data/source/posts-html/<id>.<slug>.html (Substack export — local rebuilds)
  2. posts/<slug>.html (rendered page in the repo — works in CI / cron)

Sends the cleaned body + a cached editorial-voice system prompt to Claude
Haiku 4.5 and writes results to data/summaries.json keyed by slug.

After generating new summaries this script also patches the affected post
pages and the manifest so the new dek is reflected immediately, without
needing a full build.py rerun (full build needs data/source/, which is
gitignored and absent in CI).

Run: python3 scripts/summarize.py            # summarise posts not yet done
     python3 scripts/summarize.py --redo     # force-regenerate all summaries
     python3 scripts/summarize.py --slug X   # regenerate one slug

ANTHROPIC_API_KEY (preferred) or CLAUDE_CODE_OAUTH_TOKEN must be in env.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from html import unescape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_HTML_DIR = ROOT / "data" / "source" / "posts-html"
RENDERED_POSTS_DIR = ROOT / "posts"
MANIFEST_PATH = ROOT / "data" / "posts.json"
OUT_PATH = ROOT / "data" / "summaries.json"

# Reuse build.py's render_post_page so a post page can be re-rendered with
# the new summary in place without a full build.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build  # noqa: E402

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-haiku-4-5-20251001"
MAX_BODY_CHARS = 12_000  # ~3K tokens — Haiku context is huge but keep it cheap

SYSTEM_PROMPT = """You write one-paragraph card summaries for the State of the Future archive — \
the personal Substack of Lawrence Lundy-Bryan, a deep-tech VC who writes about semiconductors, \
photonics, quantum computing, AI labour markets, nuclear fusion, privacy-enhancing tech, and \
venture capital. His voice is sharp, specific, contrarian, and confident — closer to FT \
Weekend or Stratechery than to a startup blog.

You will receive the plain-text body of one essay or interview. Write a single-paragraph \
summary that will appear under the title on an archive card. The summary must:

1. State the essay's central thesis or specific finding — what is the argument, not the topic?
2. Name the concrete technology, person, company, or data point the piece anchors on.
3. Be 35–60 words. Two or three sentences. No more.
4. Skip Lawrence's standard greetings and bio block ("Hello friends, colleagues and enemies", \
"I'm Lawrence, a pleasure", "I invest in people making the world… better", contact details, \
"x x", "wave to me on X"). These are boilerplate that appears in every essay; do not include them.
5. Avoid hype-words: "explores", "delves into", "fascinating insights", "deep dive", "unpacks", \
"a must-read". State what the piece *says*, not that it says it.
6. Be readable as a magazine dek. Reader should know whether they want to click after reading it.
7. Do not start with "This essay…" or "In this piece…". Start with the substantive claim.

Return only the summary paragraph. No markdown, no quotes, no preamble, no labels."""


def strip_html(s: str) -> str:
    s = re.sub(r"<script\b[^<]*(?:(?!</script>)<[^<]*)*</script>", "", s, flags=re.I)
    s = re.sub(r"<style\b[^<]*(?:(?!</style>)<[^<]*)*</style>", "", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# Match the inner content of either <div class="article-body"> or the older
# <div class="post-body">, allowing nested divs.
_ARTICLE_BODY_RE = re.compile(
    r'<div class="(?:article|post)-body"[^>]*>(.*?)</div>\s*(?:<div class="(?:post|article)-(?:footer|nav))',
    re.DOTALL,
)


def body_for_slug(slug: str, manifest_post: dict) -> str | None:
    """Return raw body HTML for a post. Prefers the local Substack export
    (full original markup), falls back to the rendered post page (works in CI)."""
    stem = f"{manifest_post.get('id', '')}.{slug}"
    src = SRC_HTML_DIR / f"{stem}.html"
    if src.exists():
        return src.read_text(encoding="utf-8")
    rendered = RENDERED_POSTS_DIR / f"{slug}.html"
    if rendered.exists():
        page = rendered.read_text(encoding="utf-8")
        m = _ARTICLE_BODY_RE.search(page)
        if m:
            return m.group(1)
        # Last resort: strip the masthead and return the rest.
        return page
    return None


def render_with_summary(post: dict, body_html: str, related: list[dict]) -> str:
    """Render the post page using build.render_post_page so the article-body
    in the rendered HTML reflects whatever's in the manifest's `excerpt`."""
    return build.render_post_page(post, build.clean_body(body_html), related)


def call_haiku(auth_header: tuple[str, str], system_prompt: str, user_text: str, max_retries: int = 4) -> str:
    """Call Claude Haiku with prompt caching on the system block.
    auth_header is (header_name, header_value) — either x-api-key/<key> or
    authorization/Bearer <token>. Returns the assistant text."""
    body = json.dumps({
        "model": MODEL,
        "max_tokens": 200,
        "system": [
            {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}},
        ],
        "messages": [
            {"role": "user", "content": user_text},
        ],
    }).encode("utf-8")
    headers = {
        auth_header[0]: auth_header[1],
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    if auth_header[0].lower() == "authorization":
        headers["anthropic-beta"] = "oauth-2025-04-20"
    req = urllib.request.Request(API_URL, data=body, method="POST", headers=headers)

    delay = 1.0
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            text_blocks = [b.get("text", "") for b in payload.get("content", []) if b.get("type") == "text"]
            return " ".join(t.strip() for t in text_blocks if t.strip())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise
        except urllib.error.URLError:
            if attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise
    raise RuntimeError("call_haiku: exhausted retries")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--redo", action="store_true", help="regenerate all summaries")
    ap.add_argument("--slug", default=None, help="regenerate just this slug")
    args = ap.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY") or ""
    oauth_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or ""
    if api_key:
        auth = ("x-api-key", api_key)
    elif oauth_token:
        auth = ("authorization", f"Bearer {oauth_token}")
    else:
        sys.exit("Set ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN")

    if OUT_PATH.exists():
        existing = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    else:
        existing = {}

    if not MANIFEST_PATH.exists():
        sys.exit("missing data/posts.json — run scripts/build.py first")
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    posts_by_slug = {p["slug"]: p for p in manifest["posts"]}

    targets: list[tuple[str, dict, str]] = []
    for slug, post in posts_by_slug.items():
        if args.slug and slug != args.slug:
            continue
        if not args.redo and not args.slug and slug in existing and existing[slug].get("summary"):
            continue
        body_html = body_for_slug(slug, post)
        if body_html is None:
            print(f"  - skip {slug} (no body found)", file=sys.stderr)
            continue
        targets.append((slug, post, body_html))

    print(f"summarising {len(targets)} posts …")
    affected_slugs: set[str] = set()
    for slug, post, body_html in targets:
        body_text = strip_html(body_html)
        if len(body_text) > MAX_BODY_CHARS:
            body_text = body_text[:MAX_BODY_CHARS]
        user_text = (
            f"Title: {post.get('title', '')}\n"
            f"Subtitle: {post.get('subtitle', '')}\n\n"
            f"Body:\n{body_text}"
        )
        try:
            summary = call_haiku(auth, SYSTEM_PROMPT, user_text)
        except Exception as e:
            print(f"  ! {slug}: {e}", file=sys.stderr)
            continue
        if not summary:
            print(f"  ! {slug}: empty summary", file=sys.stderr)
            continue
        existing[slug] = {"summary": summary, "model": MODEL}
        affected_slugs.add(slug)
        print(f"  + {slug}: {summary[:120]}{'…' if len(summary) > 120 else ''}")
        # Persist after every call so we don't lose work on interruption.
        OUT_PATH.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")

    if not affected_slugs:
        print("\nno new summaries.")
        return 0

    # Patch the manifest entries with the new summaries + excerpt fallback.
    for slug in affected_slugs:
        post = posts_by_slug[slug]
        post["summary"] = existing[slug]["summary"]
        post["excerpt"] = existing[slug]["summary"]

    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Re-render the affected post pages so they pick up any header changes
    # tied to the updated manifest entry. Related posts list comes from
    # whatever's currently stored on the post (computed by build/sync).
    for slug in affected_slugs:
        post = posts_by_slug[slug]
        body_html = body_for_slug(slug, post)
        if body_html is None:
            continue
        related = []
        for r_slug in post.get("related", []):
            r = posts_by_slug.get(r_slug)
            if r:
                related.append({
                    "slug": r["slug"],
                    "title": r["title"],
                    "subtitle": r.get("subtitle", ""),
                    "date_pretty": r.get("date_pretty", ""),
                    "category": r["category"],
                    "category_slug": r["category_slug"],
                    "url": r["url"],
                })
        out_path = RENDERED_POSTS_DIR / f"{slug}.html"
        out_path.write_text(render_with_summary(post, body_html, related), encoding="utf-8")

    print(f"\nwrote {len(affected_slugs)} new summaries; updated manifest + post pages.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
