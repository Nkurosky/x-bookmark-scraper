# X Bookmark Scraper

![X Bookmark Scraper banner](assets/bookmark-scraper-banner.png)

## About

X Bookmark Scraper is a small personal archiving tool for turning X bookmarks into organized Markdown folders that are easy for humans and AI assistants to review. It is built for low-strain, resumable scraping: run a handful of bookmarks at a time, keep images beside each post, preserve thread context, and maintain a clean index for later filtering into high-yield, maybe-useful, or not-worth-it buckets.

Scrapes your X bookmarks in small resumable batches using a local Playwright browser profile.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
```

## Sign in once

```powershell
python x_bookmark_scraper.py --login
```

Log into X in the browser window, then press Enter in the terminal. The session is saved in `.x_browser_profile/`.

## Scrape bookmarks

```powershell
python x_bookmark_scraper.py --limit 5
```

The scraper only starts from `https://x.com/i/bookmarks`, scans newest to oldest, skips posts already stored in `state/processed_bookmarks.json`, and writes each bookmark to:

```text
scraped_bookmarks/
  2026-05-25_short-post-slug_1234567890/
    scrape.md
    image-1.jpg
    image-2.jpg
```

It also maintains `scraped_bookmarks/bookmarks_index.md`.

## Useful options

```powershell
python x_bookmark_scraper.py --limit 10
python x_bookmark_scraper.py --headed
python x_bookmark_scraper.py --thread-scrolls 10
python x_bookmark_scraper.py --output "C:\path\to\folder"
```

Use a small `--limit` to keep the laptop load low. New bookmarks added later will appear near the top of the bookmarks page and will be picked up before older unsaved backlog items.
