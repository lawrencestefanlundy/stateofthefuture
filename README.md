# State of the Future — archive site

Static site that hosts the full archive of [stateofthefuture.io](https://stateofthefuture.io) Substack content as filterable cards. Click-throughs open the post on this site, not on Substack.

Style anchored to the [UK Semiconductor Ecosystem](https://lawrencestefanlundy.github.io/uk-semiconductor-ecosystem/) page (slate + stone palette, single deep teal accent, EB Garamond / Inter / JetBrains Mono).

## Layout

```
index.html              archive grid: filter chips + search + cards
posts/<slug>.html       one static page per post (Substack body wrapped in site shell)
data/posts.json         manifest read by index.html
data/source/            Substack export (input only — not served)
assets/styles.css       single stylesheet
assets/index.js         filter / search / card rendering
images/<slug>.<ext>     hero image mirror (1456w from Substack CDN, ~200KB each)
scripts/build.py        full rebuild from data/source/
scripts/sync.py         daily delta from Substack RSS (~22 most recent items)
.github/workflows/sync.yml   cron @ 08:00 UTC, runs sync.py and commits
```

## Local preview

```sh
python3 -m http.server 8770
open http://localhost:8770/
```

## Rebuilding from a fresh export

1. Drop the Substack export contents into `data/source/`:
   - `posts.csv` (post index)
   - `posts/` directory of `<id>.<slug>.html` files
2. Run `python3 scripts/build.py`
   - Add `--skip-images` to skip mirroring (faster iteration on layout)

The build derives a category from each slug:
- `friday-four`, `four-things-friday` → **Friday Four**
- `interview-`, `in-conversation-with` → **Interview**
- everything else → **Essay**

## Auto-update

`.github/workflows/sync.yml` runs daily at 08:00 UTC, pulls the Substack RSS feed (`https://stateofthefuture.substack.com/feed`), and adds any new posts to the manifest + generates their static pages. Substack RSS only exposes the most recent ~22 posts, so older posts must come from a manual export.

## DNS cutover

Currently `stateofthefuture.io` points to Substack as the custom domain. To make this site the apex:

1. **In Substack:** Settings → Custom domain → change to `read.stateofthefuture.io` (or pick any subdomain). Substack will issue a new cert for it.
2. **In your DNS provider:** point `stateofthefuture.io` apex A records at GitHub Pages:
   - `185.199.108.153`
   - `185.199.109.153`
   - `185.199.110.153`
   - `185.199.111.153`
   - (AAAA for IPv6: `2606:50c0:8000::153` and three siblings)
3. **CNAME** `read.stateofthefuture.io` → `stateofthefuture.substack.com`
4. **In this repo:** Settings → Pages → custom domain `stateofthefuture.io`, enable HTTPS once Pages issues the cert (~15 min).
5. Inbound email links from past Substack issues use `stateofthefuture.substack.com/p/...` URLs, which keep working.
