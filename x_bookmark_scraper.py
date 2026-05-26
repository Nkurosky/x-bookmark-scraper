from __future__ import annotations

import argparse
import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import Browser, BrowserContext, Page, async_playwright


BOOKMARKS_URL = "https://x.com/i/bookmarks"
DEFAULT_OUTPUT_DIR = Path("scraped_bookmarks")
DEFAULT_STATE_FILE = Path("state") / "processed_bookmarks.json"
DEFAULT_PROFILE_DIR = Path(".x_browser_profile")
DEFAULT_BROWSER_CHANNEL = "chromium"


@dataclass
class TweetSummary:
    post_id: str
    url: str
    author_handle: str | None
    text: str


@dataclass
class BookmarkPageDiagnostics:
    url: str
    title: str
    article_count: int
    has_login_prompt: bool
    has_empty_message: bool
    has_error_message: bool
    visible_text_sample: str


@dataclass
class BrowserSession:
    context: BrowserContext
    browser: Browser | None = None
    close_browser: bool = True

    async def close(self) -> None:
        if self.browser is not None:
            if self.close_browser:
                await self.browser.close()
        else:
            await self.context.close()


def slugify(value: str, max_len: int = 64) -> str:
    value = value.lower()
    value = re.sub(r"https?://\S+", "", value)
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    return (value[:max_len].strip("-") or "bookmark")


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"processed": {}, "runs": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def extract_status_id(url: str) -> str | None:
    match = re.search(r"/status/(\d+)", url)
    return match.group(1) if match else None


def normalize_status_url(url: str) -> str:
    parsed = urlparse(url)
    path_match = re.search(r"(/[^/]+/status/\d+)", parsed.path)
    if not path_match:
        return url
    return f"https://x.com{path_match.group(1)}"


def markdown_escape(text: str) -> str:
    return text.replace("\r\n", "\n").strip()


def browser_channel_arg(channel: str) -> str | None:
    return None if channel == "chromium" else channel


async def open_browser_session(
    playwright: Any,
    profile_dir: Path,
    headed: bool,
    browser_channel: str,
    cdp_url: str | None,
) -> BrowserSession:
    if cdp_url:
        browser = await playwright.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        return BrowserSession(context=context, browser=browser, close_browser=False)

    context = await playwright.chromium.launch_persistent_context(
        str(profile_dir),
        channel=browser_channel_arg(browser_channel),
        headless=not headed,
        viewport={"width": 1280, "height": 900},
    )
    return BrowserSession(context=context)


async def login(profile_dir: Path, headed: bool, browser_channel: str, cdp_url: str | None) -> None:
    async with async_playwright() as p:
        session = await open_browser_session(p, profile_dir, headed, browser_channel, cdp_url)
        context = session.context
        page = await context.new_page()
        await page.goto(BOOKMARKS_URL, wait_until="domcontentloaded")
        print("Browser opened. Sign into X if needed, then return here and press Enter.")
        await asyncio.to_thread(input)
        await session.close()


async def collect_visible_bookmark_summaries(page: Page) -> list[TweetSummary]:
    articles = await page.locator('article[data-testid="tweet"]').all()
    summaries: list[TweetSummary] = []

    for article in articles:
        links = await article.locator('a[href*="/status/"]').all()
        status_url: str | None = None
        for link in links:
            href = await link.get_attribute("href")
            if href and extract_status_id(href):
                status_url = normalize_status_url(href)
                break
        if not status_url:
            continue

        post_id = extract_status_id(status_url)
        if not post_id:
            continue

        handle = await first_handle_from_article(article)
        text = await article.inner_text(timeout=3_000)
        summaries.append(
            TweetSummary(
                post_id=post_id,
                url=status_url,
                author_handle=handle,
                text=compact_visible_text(text),
            )
        )

    unique: dict[str, TweetSummary] = {}
    for summary in summaries:
        unique.setdefault(summary.post_id, summary)
    return list(unique.values())


async def article_status_ids(article: Any) -> set[str]:
    ids: set[str] = set()
    links = await article.locator('a[href*="/status/"]').all()
    for link in links:
        href = await link.get_attribute("href")
        post_id = extract_status_id(href or "")
        if post_id:
            ids.add(post_id)
    return ids


async def first_handle_from_article(article: Any) -> str | None:
    links = await article.locator("a").all()
    for link in links:
        href = await link.get_attribute("href")
        if not href:
            continue
        match = re.fullmatch(r"/([A-Za-z0-9_]{1,15})", href)
        if match:
            return f"@{match.group(1)}"
    return None


def compact_visible_text(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)


async def find_next_unscraped_bookmarks(
    page: Page,
    processed_ids: set[str],
    limit: int,
    max_scrolls: int,
) -> list[TweetSummary]:
    await page.goto(BOOKMARKS_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(2_000)

    found: dict[str, TweetSummary] = {}
    previous_height = 0
    unchanged_scrolls = 0

    for _ in range(max_scrolls):
        for summary in await collect_visible_bookmark_summaries(page):
            if summary.post_id not in processed_ids:
                found.setdefault(summary.post_id, summary)
                if len(found) >= limit:
                    return list(found.values())

        await page.mouse.wheel(0, 1600)
        await page.wait_for_timeout(1_500)
        height = await page.evaluate("document.body.scrollHeight")
        if height == previous_height:
            unchanged_scrolls += 1
        else:
            unchanged_scrolls = 0
            previous_height = height
        if unchanged_scrolls >= 4:
            break

    return list(found.values())


async def diagnose_bookmarks_page(page: Page) -> BookmarkPageDiagnostics:
    article_count = await page.locator('article[data-testid="tweet"]').count()
    body_text = compact_visible_text(await page.locator("body").inner_text(timeout=5_000))
    lowered = body_text.lower()
    return BookmarkPageDiagnostics(
        url=page.url,
        title=await page.title(),
        article_count=article_count,
        has_login_prompt=any(
            phrase in lowered
            for phrase in [
                "sign in to x",
                "sign in",
                "log in",
                "redirect_after_login",
                "create account",
                "join x today",
                "continue with phone",
                "continue with apple",
                "email or username",
                "see what's happening",
            ]
        )
        or "onboarding" in page.url
        or "redirect_after_login" in page.url,
        has_empty_message=any(
            phrase in lowered
            for phrase in [
                "save posts for later",
                "you haven't added any posts to your bookmarks",
                "you haven’t added any posts to your bookmarks",
                "when you do, they'll show up here",
                "when you do, they’ll show up here",
            ]
        ),
        has_error_message=any(
            phrase in lowered
            for phrase in [
                "something went wrong",
                "try reloading",
                "rate limit",
                "temporarily unavailable",
                "temporarily limited your login",
            ]
        ),
        visible_text_sample=body_text[:500],
    )


def format_bookmarks_diagnostics(diagnostics: BookmarkPageDiagnostics, browser_channel: str) -> str:
    flags = []
    if diagnostics.has_login_prompt:
        flags.append("login prompt detected")
    if diagnostics.has_empty_message:
        flags.append("empty-bookmarks message detected")
    if diagnostics.has_error_message:
        flags.append("error/rate-limit text detected")
    flags_text = ", ".join(flags) if flags else "no obvious login/empty/error text"

    if diagnostics.has_login_prompt:
        headline = "The Playwright browser profile is not signed into X."
        channel_flag = "" if browser_channel == "chromium" else f" --browser-channel {browser_channel}"
        next_step = f"Run: .\\.venv\\Scripts\\python.exe x_bookmark_scraper.py --login{channel_flag}"
    elif diagnostics.has_error_message:
        headline = "X showed an error or rate-limit page while loading bookmarks."
        next_step = "Try again later, or run with --headed to inspect the page."
    else:
        headline = "No new unprocessed bookmarks found."
        next_step = "If this looks wrong, run with --headed and confirm the bookmarks page is visible."

    return (
        f"{headline}\n"
        f"- Next step: {next_step}\n"
        f"- Current page: {diagnostics.url}\n"
        f"- Title: {diagnostics.title or 'Unknown'}\n"
        f"- Visible tweet articles: {diagnostics.article_count}\n"
        f"- Page clues: {flags_text}\n"
        f"- Visible text sample: {diagnostics.visible_text_sample or '(no visible text)'}"
    )


async def extract_article_data(article: Any, context: BrowserContext, folder: Path, image_prefix: str) -> dict[str, Any]:
    text = compact_visible_text(await article.inner_text(timeout=5_000))
    handle = await first_handle_from_article(article)
    display_name = await first_display_name(article)
    timestamp = await first_timestamp(article)
    image_paths = await download_article_images(article, context, folder, image_prefix)
    video_count = await article.locator("video").count()
    gif_or_video_labels = await collect_media_labels(article)

    return {
        "display_name": display_name,
        "handle": handle,
        "timestamp": timestamp,
        "text": text,
        "images": image_paths,
        "video_count": video_count,
        "media_labels": gif_or_video_labels,
    }


async def extract_article_metadata(article: Any) -> dict[str, Any]:
    text = compact_visible_text(await article.inner_text(timeout=5_000))
    return {
        "display_name": await first_display_name(article),
        "handle": await first_handle_from_article(article),
        "timestamp": await first_timestamp(article),
        "text": text,
    }


async def first_display_name(article: Any) -> str | None:
    user_name = article.locator('[data-testid="User-Name"]').first
    if await user_name.count() == 0:
        return None
    text = compact_visible_text(await user_name.inner_text(timeout=3_000))
    for line in text.splitlines():
        if line and not line.startswith("@"):
            return line
    return None


async def first_timestamp(article: Any) -> str | None:
    time_el = article.locator("time").first
    if await time_el.count() == 0:
        return None
    return await time_el.get_attribute("datetime")


async def collect_media_labels(article: Any) -> list[str]:
    labels: list[str] = []
    candidates = await article.locator('[aria-label], [alt]').all()
    for candidate in candidates:
        label = await candidate.get_attribute("aria-label") or await candidate.get_attribute("alt")
        if not label:
            continue
        normalized = label.strip()
        if normalized and any(word in normalized.lower() for word in ["video", "gif", "media", "image"]):
            labels.append(normalized)
    return sorted(set(labels))


async def download_article_images(article: Any, context: BrowserContext, folder: Path, prefix: str) -> list[str]:
    folder.mkdir(parents=True, exist_ok=True)
    image_paths: list[str] = []
    image_urls: list[str] = []

    imgs = await article.locator('img[src*="pbs.twimg.com/media"]').all()
    for img in imgs:
        src = await img.get_attribute("src")
        if src and src not in image_urls:
            image_urls.append(src)

    for index, src in enumerate(image_urls, start=1):
        ext = guess_image_extension(src)
        filename = f"{prefix}-{index}{ext}"
        path = folder / filename
        try:
            response = await context.request.get(src)
            if response.ok:
                path.write_bytes(await response.body())
                image_paths.append(filename)
        except Exception as exc:
            print(f"Could not download image {src}: {exc}")

    return image_paths


def guess_image_extension(url: str) -> str:
    if "format=png" in url:
        return ".png"
    if "format=webp" in url:
        return ".webp"
    return ".jpg"


async def scrape_bookmark_detail(
    context: BrowserContext,
    summary: TweetSummary,
    output_dir: Path,
    thread_scrolls: int,
) -> dict[str, Any]:
    page = await context.new_page()
    await page.goto(summary.url, wait_until="domcontentloaded")
    await page.wait_for_timeout(2_500)

    articles = await page.locator('article[data-testid="tweet"]').all()
    main_article = None

    for article in articles:
        if summary.post_id in await article_status_ids(article):
            main_article = article
            break

    if main_article is None:
        main_article = page.locator('article[data-testid="tweet"]').first

    main_data_preview = await extract_article_metadata(main_article)
    author_handle = main_data_preview["handle"] or summary.author_handle
    timestamp = main_data_preview["timestamp"]
    date_part = timestamp[:10] if timestamp else datetime.now(timezone.utc).date().isoformat()
    slug = slugify(main_data_preview["text"] or summary.text)
    folder = unique_folder(output_dir, f"{date_part}_{slug}_{summary.post_id}")

    folder.mkdir(parents=True, exist_ok=True)
    main_data = await extract_article_data(main_article, context, folder, "image")
    thread_articles = await collect_same_author_thread_context(
        page=page,
        context=context,
        folder=folder,
        author_handle=author_handle,
        main_post_id=summary.post_id,
        main_text=main_data["text"],
        max_scrolls=thread_scrolls,
    )

    scrape_md = render_scrape_markdown(summary, main_data, thread_articles)
    (folder / "scrape.md").write_text(scrape_md, encoding="utf-8")
    await page.close()

    return {
        "post_id": summary.post_id,
        "url": summary.url,
        "folder": str(folder),
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "author_handle": main_data["handle"] or summary.author_handle,
        "display_name": main_data["display_name"],
        "timestamp": main_data["timestamp"],
        "text_preview": (main_data["text"] or summary.text)[:240],
    }


async def collect_same_author_thread_context(
    page: Page,
    context: BrowserContext,
    folder: Path,
    author_handle: str | None,
    main_post_id: str,
    main_text: str,
    max_scrolls: int,
) -> list[dict[str, Any]]:
    if not author_handle:
        return []

    thread_articles: list[dict[str, Any]] = []
    seen_keys = {main_post_id, main_text}

    for _ in range(max_scrolls + 1):
        articles = await page.locator('article[data-testid="tweet"]').all()
        for article in articles:
            handle = await first_handle_from_article(article)
            if handle != author_handle:
                continue

            ids = await article_status_ids(article)
            if main_post_id in ids:
                continue

            text = compact_visible_text(await article.inner_text(timeout=5_000))
            key = next(iter(ids), text)
            if not text or key in seen_keys or text in seen_keys:
                continue

            seen_keys.add(key)
            seen_keys.add(text)
            data = await extract_article_data(article, context, folder, f"thread-{len(thread_articles) + 1}-image")
            thread_articles.append(data)

        await page.mouse.wheel(0, 1400)
        await page.wait_for_timeout(1_000)

    return thread_articles


def unique_folder(base: Path, name: str) -> Path:
    candidate = base / name
    if not candidate.exists():
        return candidate
    for index in range(2, 10_000):
        candidate = base / f"{name}-{index}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not find a unique folder name for {name}")


def render_scrape_markdown(summary: TweetSummary, main: dict[str, Any], thread: list[dict[str, Any]]) -> str:
    lines = [
        "# X Bookmark",
        "",
        f"- Post URL: {summary.url}",
        f"- Author: {main.get('display_name') or 'Unknown'} ({main.get('handle') or summary.author_handle or 'unknown'})",
        f"- Timestamp: {main.get('timestamp') or 'Unknown'}",
        "",
        "## Bookmarked Post",
        "",
        markdown_escape(main.get("text") or summary.text or ""),
        "",
    ]

    if main.get("images"):
        lines.extend(["## Images", ""])
        for image in main["images"]:
            lines.append(f"![{image}]({image})")
        lines.append("")

    if main.get("video_count") or main.get("media_labels"):
        lines.extend(["## Video or GIF Context", ""])
        if main.get("video_count"):
            lines.append(f"- Embedded video/GIF elements detected: {main['video_count']}")
        for label in main.get("media_labels", []):
            lines.append(f"- {label}")
        lines.append("")

    if thread:
        lines.extend(["## Same-Author Thread Context", ""])
        for index, item in enumerate(thread, start=1):
            lines.append(f"### Thread Post {index}")
            lines.append("")
            lines.append(f"- Author: {item.get('display_name') or 'Unknown'} ({item.get('handle') or 'unknown'})")
            lines.append(f"- Timestamp: {item.get('timestamp') or 'Unknown'}")
            lines.append("")
            lines.append(markdown_escape(item.get("text") or ""))
            lines.append("")
            for image in item.get("images", []):
                lines.append(f"![{image}]({image})")
            if item.get("images"):
                lines.append("")
            if item.get("video_count") or item.get("media_labels"):
                lines.append("Video/GIF context:")
                if item.get("video_count"):
                    lines.append(f"- Embedded video/GIF elements detected: {item['video_count']}")
                for label in item.get("media_labels", []):
                    lines.append(f"- {label}")
                lines.append("")

    return "\n".join(lines).strip() + "\n"


def update_index(output_dir: Path, processed: dict[str, Any]) -> None:
    rows = []
    for post_id, item in processed.items():
        folder = Path(item["folder"])
        scrape_path = folder / "scrape.md"
        title = slugify(item.get("text_preview") or post_id, max_len=80)
        timestamp = item.get("timestamp") or "Unknown"
        author = item.get("author_handle") or "unknown"
        rows.append((item.get("scraped_at", ""), f"- [{timestamp} {author} {title}]({scrape_path.as_posix()})"))

    rows.sort(reverse=True)
    content = ["# Scraped X Bookmarks", ""]
    content.extend(row for _, row in rows)
    content.append("")
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "bookmarks_index.md").write_text("\n".join(content), encoding="utf-8")


async def scrape(args: argparse.Namespace) -> int:
    output_dir = Path(args.output)
    state_file = Path(args.state)
    profile_dir = Path(args.profile)
    state = load_state(state_file)
    processed: dict[str, Any] = state.setdefault("processed", {})

    async with async_playwright() as p:
        session = await open_browser_session(
            p,
            profile_dir=profile_dir,
            headed=args.headed,
            browser_channel=args.browser_channel,
            cdp_url=args.cdp_url,
        )
        context = session.context
        page = await context.new_page()
        targets = await find_next_unscraped_bookmarks(
            page,
            processed_ids=set(processed),
            limit=args.limit,
            max_scrolls=args.max_scrolls,
        )

        if not targets:
            diagnostics = await diagnose_bookmarks_page(page)
            print(format_bookmarks_diagnostics(diagnostics, args.browser_channel))
            await session.close()
            if diagnostics.has_login_prompt or diagnostics.has_error_message:
                return 2
            return 0

        run_record = {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "limit": args.limit,
            "post_ids": [],
        }
        state.setdefault("runs", []).append(run_record)

        for summary in targets:
            print(f"Scraping {summary.url}")
            item = await scrape_bookmark_detail(context, summary, output_dir, args.thread_scrolls)
            processed[summary.post_id] = item
            run_record["post_ids"].append(summary.post_id)
            save_state(state_file, state)
            update_index(output_dir, processed)

        run_record["finished_at"] = datetime.now(timezone.utc).isoformat()
        save_state(state_file, state)
        update_index(output_dir, processed)
        await session.close()
        return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape X bookmarks into Markdown folders.")
    parser.add_argument("--limit", type=int, default=5, help="Maximum new bookmarks to scrape this run.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_DIR), help="Output folder for scraped bookmarks.")
    parser.add_argument("--state", default=str(DEFAULT_STATE_FILE), help="JSON state file tracking processed posts.")
    parser.add_argument("--profile", default=str(DEFAULT_PROFILE_DIR), help="Persistent Playwright browser profile folder.")
    parser.add_argument(
        "--browser-channel",
        choices=["chromium", "chrome", "msedge"],
        default=DEFAULT_BROWSER_CHANNEL,
        help="Browser to launch. Use chrome or msedge if X flags bundled Chromium as unusual.",
    )
    parser.add_argument(
        "--cdp-url",
        help="Attach to a manually launched Chrome/Edge remote debugging URL, such as http://127.0.0.1:9222.",
    )
    parser.add_argument("--max-scrolls", type=int, default=80, help="Maximum bookmark-page scroll attempts per run.")
    parser.add_argument("--thread-scrolls", type=int, default=6, help="Detail-page scrolls for same-author thread context.")
    parser.add_argument("--headed", action="store_true", help="Show the browser while scraping.")
    parser.add_argument("--login", action="store_true", help="Open browser profile for manual X login, then exit.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.limit < 1:
        raise SystemExit("--limit must be at least 1.")
    if args.max_scrolls < 1:
        raise SystemExit("--max-scrolls must be at least 1.")
    if args.thread_scrolls < 0:
        raise SystemExit("--thread-scrolls must be 0 or greater.")
    if args.login:
        asyncio.run(
            login(
                Path(args.profile),
                headed=True,
                browser_channel=args.browser_channel,
                cdp_url=args.cdp_url,
            )
        )
    else:
        raise SystemExit(asyncio.run(scrape(args)))


if __name__ == "__main__":
    main()
