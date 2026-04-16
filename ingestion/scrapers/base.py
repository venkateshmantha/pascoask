"""
Base scraper with shared HTTP client, rate limiting, retry logic,
and resumability via the scrape_log table.
"""
from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import AsyncIterator

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config import settings
from ingestion.models import Document
from storage.db import already_scraped, log_scrape, upsert_document
import sqlite_utils

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "PascoAsk/0.1 (public records research tool; "
        "contact: github.com/pascoask)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/pdf,*/*",
}


class BaseScraper(ABC):
    """
    Abstract base class for all PascoAsk scrapers.

    Subclasses implement:
      - discover_urls()  → async generator of URLs to scrape
      - scrape_one()     → fetch one URL and return a Document (or None)
    """

    source: str  # must be set by subclass

    def __init__(self, db: sqlite_utils.Database) -> None:
        self.db = db
        self._client: httpx.AsyncClient | None = None
        self._last_request_at: float = 0.0

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers=HEADERS,
                timeout=httpx.Timeout(30.0, connect=10.0),
                follow_redirects=True,
            )
        return self._client

    async def _rate_limited_get(self, url: str, **kwargs) -> httpx.Response:
        """Perform a GET with per-request rate limiting."""
        elapsed = time.monotonic() - self._last_request_at
        delay = settings.request_delay_seconds - elapsed
        if delay > 0:
            await asyncio.sleep(delay)

        client = await self._get_client()
        response = await self._fetch_with_retry(client, url, **kwargs)
        self._last_request_at = time.monotonic()
        return response

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    async def _fetch_with_retry(
        self, client: httpx.AsyncClient, url: str, **kwargs
    ) -> httpx.Response:
        response = await client.get(url, **kwargs)
        response.raise_for_status()
        return response

    @abstractmethod
    async def discover_urls(self) -> AsyncIterator[str]:
        """Yield URLs of individual documents to scrape."""
        ...

    @abstractmethod
    async def scrape_one(self, url: str) -> Document | None:
        """
        Fetch and parse a single URL into a Document.
        Return None to skip (e.g. not enough content).
        """
        ...

    async def run(self, force: bool = False) -> dict[str, int]:
        """
        Main entry point. Discover URLs, skip already-scraped ones,
        call scrape_one(), persist to SQLite.

        Args:
            force: If True, re-scrape even URLs in the scrape_log.

        Returns:
            dict with counts: scraped, skipped, errors
        """
        counts = {"scraped": 0, "skipped": 0, "errors": 0}

        logger.info(f"[{self.source}] Starting scrape run (force={force})")

        async for url in self.discover_urls():
            if not force and already_scraped(self.db, url):
                logger.debug(f"[{self.source}] Skipping (already done): {url}")
                counts["skipped"] += 1
                continue

            try:
                doc = await self.scrape_one(url)
                if doc is None:
                    log_scrape(self.db, url, self.source, "skipped")
                    counts["skipped"] += 1
                    continue

                upsert_document(self.db, doc.to_db_row())
                log_scrape(self.db, url, self.source, "success")
                counts["scraped"] += 1
                logger.info(f"[{self.source}] Saved: {doc.title[:80]}")

            except Exception as exc:
                logger.error(f"[{self.source}] Error scraping {url}: {exc}")
                log_scrape(self.db, url, self.source, "error", str(exc))
                counts["errors"] += 1

        await self._close()
        logger.info(f"[{self.source}] Done. {counts}")
        return counts

    async def _close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
