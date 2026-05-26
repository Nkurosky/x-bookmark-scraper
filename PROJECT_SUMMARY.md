# Preliminary Project Summary

## Purpose

X Bookmark Scraper is a local archiving tool for saving X bookmarks into clean, AI-readable Markdown folders. The goal is to preserve useful bookmarked posts, images, and same-author thread context so the collection can later be reviewed, filtered, and ranked by a tool like ChatGPT, Claude, or Codex.

## Current Status

The scraper is working through the Chrome CDP workflow. A manually launched Chrome window exposes a remote debugging endpoint, and the script attaches to that browser instead of launching a fresh automation browser. This avoids the X/Google login issues that appeared with bundled Playwright Chromium and browser-channel launches.

Tested successfully with:

```powershell
.\.venv\Scripts\python.exe x_bookmark_scraper.py --limit 5 --cdp-url http://127.0.0.1:9222
```

The latest run scraped a batch of 5 bookmarks, and the local archive now contains 7 total scraped bookmarks including earlier test runs.

## What It Captures

- Bookmark post text
- Author display name and handle
- Post URL
- Timestamp/date
- Images saved beside the post
- Markdown image references
- Same-author thread context
- Basic video/GIF context notes
- A global `bookmarks_index.md`
- Progress state in `state/processed_bookmarks.json`

## Output Shape

Each bookmark is stored in its own generated folder:

```text
scraped_bookmarks/
  2026-05-25_short-post-slug_1234567890/
    scrape.md
    image-1.jpg
    image-2.jpg
```

The `scraped_bookmarks/` and `state/` folders are intentionally ignored by Git because they contain personal archive data.

## Resume Behavior

The script remembers processed post IDs in `state/processed_bookmarks.json`. Each run starts from the top of the X bookmarks page, scans newest to oldest, skips already processed bookmarks, and scrapes the next unprocessed posts up to the requested `--limit`.

## Recommended Workflow

Launch Chrome with remote debugging:

```powershell
Start-Process "$env:ProgramFiles\Google\Chrome\Application\chrome.exe" -ArgumentList @(
  "--remote-debugging-port=9222",
  "--user-data-dir=$PWD\.chrome_cdp_profile"
)
```

Log into X in that Chrome window, confirm bookmarks load, keep the window open, then run:

```powershell
.\.venv\Scripts\python.exe x_bookmark_scraper.py --limit 5 --cdp-url http://127.0.0.1:9222
```

## Known Caveats

- X can change its DOM, so selectors may need maintenance over time.
- Videos/GIFs are not downloaded; the scraper records available context instead.
- Thread capture is same-author only and intentionally avoids general user replies.
- Some visible UI text from X can appear in `scrape.md`; future cleanup can improve readability.
- The CDP Chrome window must remain open while scraping.

## Next Improvements

- Add a post-processing cleanup pass to remove UI noise like Follow, Bookmark this, views, and engagement counts.
- Add a `--dry-run` mode that lists the next bookmarks without saving them.
- Add an optional combined export file for sending a batch directly to ChatGPT or Claude.
- Improve video/GIF metadata capture.
- Add lightweight tests around URL extraction, slug generation, state handling, and Markdown rendering.
