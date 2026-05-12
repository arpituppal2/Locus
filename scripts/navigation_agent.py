#!/usr/bin/env python3
"""Playwright Chromium navigation + multi-source page extraction."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import quote_plus

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from scripts.event_logger import EventLogger
from scripts.resource_policy import resource_budget

ROOT = Path(__file__).resolve().parent.parent
RT_FILE = ROOT / "configs" / "runtime.json"
RUNTIME = json.loads(RT_FILE.read_text()) if RT_FILE.exists() else {}
OUT_DIR = ROOT / RUNTIME.get("outputs_dir", "outputs")
RESOURCE_BUDGET = resource_budget()
LOW_RAM_MODE = RESOURCE_BUDGET.low_ram_mode
HEADLESS_BROWSER = bool(RUNTIME.get("headless", False)) or (
    LOW_RAM_MODE and bool(RUNTIME.get("auto_headless_low_ram", True))
)
MAX_RESULT_URLS = 3 if LOW_RAM_MODE else 5
MAX_FETCH_TABS = 1 if LOW_RAM_MODE else 3
MAX_SOURCE_CHARS = 9000 if LOW_RAM_MODE else 18000

SEARCH_BASE = "https://www.google.com/search?q="


@dataclass
class SourceResult:
    url: str
    title: str
    content: str
    fetch_time_ms: int


CLEAN_TEXT_SCRIPT = """
() => {
  const unwanted = document.querySelectorAll(
    'nav, footer, header, script, style, iframe, .cookie-banner, #cookie'
  );
  unwanted.forEach(el => el.remove());
  return document.body.innerText.trim();
}
"""

RESULT_URLS_SCRIPT = r"""
() => {
  const links = Array.from(document.querySelectorAll('a[href]'));
  const seen = new Set();
  const urls = [];

  for (const a of links) {
    const href = a.getAttribute('href') || '';
    let candidate = '';

    if (href.startsWith('/url?')) {
      const parsed = new URL(href, location.origin);
      candidate = parsed.searchParams.get('q') || '';
    } else if (/^https?:\/\//.test(href) && a.querySelector('h3')) {
      candidate = href;
    }

    if (!candidate) continue;

    try {
      const parsedCandidate = new URL(candidate);
      const host = parsedCandidate.hostname.toLowerCase();
      if (host.includes('google.com')) continue;
      if (seen.has(candidate)) continue;
      seen.add(candidate);
      urls.push(candidate);
    } catch (_) {
      continue;
    }

    if (urls.length >= 5) break;
  }

  return urls;
}
"""


async def _emit(
    callback: Callable[[str], Awaitable[None] | None] | None,
    message: str,
) -> None:
    if callback is None:
        return
    result = callback(message)
    if asyncio.iscoroutine(result):
        await result


def get_browser_and_page(p: "playwright.sync_api.Playwright"):
    browser = p.chromium.launch(
        headless=HEADLESS_BROWSER,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--disable-extensions",
            "--disable-sync",
            "--disable-background-networking",
            "--no-sandbox",
            "--disable-gpu-sandbox",
            "--use-angle=metal",
            "--enable-features=UseOzonePlatform",
        ],
    )
    ctx = browser.new_context(
        viewport={"width": 1280, "height": 900},
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36",
    )
    return browser, ctx, ctx.new_page(), "chromium"


async def _get_browser_and_page_async(p) -> tuple[Browser, BrowserContext, Page, str]:
    browser = await p.chromium.launch(
        headless=HEADLESS_BROWSER,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--disable-extensions",
            "--disable-sync",
            "--disable-background-networking",
            "--no-sandbox",
            "--disable-gpu-sandbox",
            "--use-angle=metal",
            "--enable-features=UseOzonePlatform",
        ],
    )
    ctx = await browser.new_context(
        viewport={"width": 1280, "height": 900},
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36",
    )
    page = await ctx.new_page()
    return browser, ctx, page, "chromium"


async def _fetch_source(
    ctx: BrowserContext,
    url: str,
    logger: EventLogger,
    thinking_cb: Callable[[str], Awaitable[None] | None] | None,
) -> SourceResult | None:
    page = await ctx.new_page()
    started = time.perf_counter()
    try:
        await page.goto(url, timeout=15000, wait_until="domcontentloaded")
        title = await page.title()
        canonical = await page.evaluate(
            """
            () => {
              const canonical = document.querySelector('link[rel="canonical"]');
              return canonical?.href || window.location.href;
            }
            """,
            timeout=5000,
        )
        content = await page.evaluate(CLEAN_TEXT_SCRIPT, timeout=5000)
        content = str(content or "")[:MAX_SOURCE_CHARS]
        fetch_time_ms = int((time.perf_counter() - started) * 1000)
        await _emit(thinking_cb, f"Read source: {canonical or url}")
        return SourceResult(
            url=str(canonical or url),
            title=str(title or "Untitled"),
            content=str(content or ""),
            fetch_time_ms=fetch_time_ms,
        )
    except Exception as exc:
        logger.log("source_fetch_failed", url=url, error=str(exc))
        return None
    finally:
        try:
            await page.close()
        except Exception:
            pass


async def collect_sources_for_subquery(
    sub_query: str,
    thinking_cb: Callable[[str], Awaitable[None] | None] | None = None,
    logger: EventLogger | None = None,
) -> list[SourceResult]:
    logger = logger or EventLogger(OUT_DIR)

    async with async_playwright() as p:
        browser: Browser | None = None
        ctx: BrowserContext | None = None
        page: Page | None = None
        try:
            browser, ctx, page, _ = await _get_browser_and_page_async(p)
            encoded = quote_plus(sub_query)
            search_url = f"{SEARCH_BASE}{encoded}"

            await _emit(thinking_cb, f"Searching: {sub_query}")
            await page.goto(search_url, timeout=15000, wait_until="domcontentloaded")
            result_urls_raw = await page.evaluate(RESULT_URLS_SCRIPT, timeout=5000)
            result_urls = [str(u) for u in (result_urls_raw or [])][:MAX_RESULT_URLS]
            logger.log("search_results", query=sub_query, count=len(result_urls))

            if not result_urls:
                await _emit(thinking_cb, f"No organic results found for sub-query: {sub_query}")
                return []

            await _emit(
                thinking_cb,
                f"Opening {min(MAX_FETCH_TABS, len(result_urls))} tabs for sub-query: {sub_query}",
            )
            tab_limit = asyncio.Semaphore(MAX_FETCH_TABS)

            async def _bounded_fetch(target_url: str) -> SourceResult | None:
                async with tab_limit:
                    return await _fetch_source(ctx, target_url, logger, thinking_cb)

            tasks = [asyncio.create_task(_bounded_fetch(url)) for url in result_urls]
            fetched = await asyncio.gather(*tasks, return_exceptions=False)
            sources = [src for src in fetched if isinstance(src, SourceResult) and src.content.strip()]
            logger.log("subquery_complete", query=sub_query, sources=len(sources))
            return sources
        finally:
            if ctx is not None:
                for open_page in list(ctx.pages):
                    try:
                        await open_page.close()
                    except Exception:
                        pass
                try:
                    await ctx.close()
                except Exception:
                    pass
            if browser is not None:
                try:
                    await browser.close()
                except Exception:
                    pass


def run_mission(plan: dict, root: Path | None = None):
    """Legacy compatibility wrapper.

    The new orchestration pipeline lives in scripts/orchestrator.py.
    This function preserves import compatibility for older entry points.
    """
    query = plan.get("mission_name") or plan.get("query") or ""
    if not query:
        return ""

    async def _run() -> str:
        logger = EventLogger((root or ROOT) / "outputs")
        sources = await collect_sources_for_subquery(query, logger=logger)
        payload = [asdict(src) for src in sources]
        out_dir = (root or ROOT) / "outputs"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "raw_sources.json"
        out_path.write_text(json.dumps(payload, indent=2))
        return out_path.read_text()

    return asyncio.run(_run())
