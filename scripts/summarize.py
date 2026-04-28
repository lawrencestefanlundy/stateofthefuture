#!/usr/bin/env python3
"""Generate one-paragraph card summaries for every post via Claude Haiku.

Reads data/source/posts-html/<id>.<slug>.html (the original Substack export),
strips HTML, sends the body + a cached editorial-voice system prompt to
Claude Haiku 4.5, and writes the results to data/summaries.json keyed by slug.

Run: python3 scripts/summarize.py            # summarises every post once,
                                             # skipping ones already in
                                             # summaries.json.
     python3 scripts/summarize.py --redo     # force-regenerate all summaries.
     python3 scripts/summarize.py --slug X   # regenerate just one slug.

ANTHROPIC_API_KEY must be set in the environment.
"""

from __future__ import annotations

import argparse
import csv
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
SRC = ROOT / "data" / "source"
HTML_DIR = SRC / "posts-html"
CSV_PATH = SRC / "posts.csv"
OUT_PATH = ROOT / "data" / "summaries.json"

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


def load_posts_index() -> dict[str, dict]:
    """slug -> {title, subtitle}"""
    out: dict[str, dict] = {}
    with CSV_PATH.open() as f:
        for row in csv.DictReader(f):
            if row.get("is_published") != "true":
                continue
            stem = row["post_id"]
            if "." not in stem:
                continue
            _, slug = stem.split(".", 1)
            out[slug] = {
                "title": (row.get("title") or "").strip(),
                "subtitle": (row.get("subtitle") or "").strip(),
                "stem": stem,
            }
    return out


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

    posts = load_posts_index()
    targets = []
    for slug, meta in posts.items():
        html_path = HTML_DIR / f"{meta['stem']}.html"
        if not html_path.exists():
            continue
        if args.slug and slug != args.slug:
            continue
        if not args.redo and not args.slug and slug in existing and existing[slug].get("summary"):
            continue
        targets.append((slug, meta, html_path))

    print(f"summarising {len(targets)} posts …")
    written = 0
    for slug, meta, html_path in targets:
        body = strip_html(html_path.read_text(encoding="utf-8"))
        if len(body) > MAX_BODY_CHARS:
            body = body[:MAX_BODY_CHARS]
        user_text = (
            f"Title: {meta['title']}\n"
            f"Subtitle: {meta['subtitle']}\n\n"
            f"Body:\n{body}"
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
        written += 1
        print(f"  + {slug}: {summary[:120]}{'…' if len(summary) > 120 else ''}")
        # Persist after every call so we don't lose work on interruption.
        OUT_PATH.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nwrote {written} new summaries to {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
