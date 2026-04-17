"""
CivicClerk scraper — all non-BCC event types (Planning Commission, MPO, workshops, etc.)

Source: CivicClerk OData API — pascocofl.api.civicclerk.com/v1
"""
from __future__ import annotations

import io
import logging
from typing import AsyncIterator

import pdfplumber
import sqlite_utils

from config import settings
from ingestion.models import Document
from ingestion.scrapers.base import BaseScraper
from ingestion.scrapers.bcc_minutes import (
    API_BASE, CDN_BASE, PORTAL_BASE, BCC_KEYWORDS, DATE_FROM,
    _parse_event_date, _build_title, _build_metadata_body,
)

logger = logging.getLogger(__name__)


class CivicClerkScraper(BaseScraper):
    source = "civicclerk"

    def __init__(self, db) -> None:
        super().__init__(db)
        self._event_cache: dict[str, dict] = {}

    async def discover_urls(self) -> AsyncIterator[str]:
        page_url = (
            f"{API_BASE}/Events"
            f"?$filter=eventDate ge {DATE_FROM}"
            f"&$orderby=eventDate desc"
        )
        while page_url:
            resp = await self._rate_limited_get(page_url)
            data = resp.json()
            events = data.get("value", [])
            for event in events:
                name = (event.get("eventName") or "").lower()
                if any(kw in name for kw in BCC_KEYWORDS):
                    continue  # handled by bcc_minutes scraper
                if not event.get("publishedFiles"):
                    continue  # skip events with no documents
                url = f"{PORTAL_BASE}/event/{event['id']}"
                self._event_cache[url] = event
                yield url
            page_url = data.get("@odata.nextLink")

    async def scrape_one(self, url: str) -> Document | None:
        event = self._event_cache.get(url)
        if not event:
            event_id_str = url.split("/")[-1]
            resp = await self._rate_limited_get(f"{API_BASE}/Events/{event_id_str}")
            event = resp.json()

        event_id = event.get("id")
        event_name = event.get("eventName", f"Event {event_id}")
        event_date = _parse_event_date(event.get("eventDate", ""))
        published_files = event.get("publishedFiles") or []

        pdf_text, pdf_url = "", ""
        for f in published_files:
            rel_url = f.get("url", "")
            if not rel_url:
                continue
            full_url = CDN_BASE + rel_url
            text = await self._download_pdf(full_url, str(event_id))
            if text:
                pdf_text, pdf_url = text, full_url
                break

        if not pdf_text or len(pdf_text.split()) < 50:
            pdf_text = _build_metadata_body(event)

        return Document(
            id=f"civicclerk_event_{event_id}",
            source="civicclerk",
            doc_type="staff_report",
            title=_build_title(event_name, event_date)[:200],
            body=pdf_text,
            date=event_date,
            url=pdf_url or f"{PORTAL_BASE}/event/{event_id}",
            metadata={
                "event_id": event_id,
                "event_name": event_name,
                "portal_url": f"{PORTAL_BASE}/event/{event_id}",
                "category": event.get("categoryName", ""),
                "pdf_available": bool(pdf_url),
            },
        )

    async def _download_pdf(self, url: str, prefix: str = "") -> str:
        import hashlib
        cache_key = hashlib.md5(url.encode()).hexdigest()
        cache_path = settings.pdf_cache_dir / f"cc_{prefix}_{cache_key}.pdf"
        try:
            if cache_path.exists():
                pdf_bytes = cache_path.read_bytes()
            else:
                client = await self._get_client()
                resp = await client.get(url)
                if resp.status_code != 200:
                    logger.debug("PDF %s -> %s", url, resp.status_code)
                    return ""
                pdf_bytes = resp.content
                cache_path.write_bytes(pdf_bytes)
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                return "\n".join(
                    p.extract_text(x_tolerance=2, y_tolerance=3) or "" for p in pdf.pages
                )
        except Exception as exc:
            logger.debug("PDF error %s: %s", url, exc)
            return ""


async def run_civicclerk_scraper(
    db: sqlite_utils.Database, force: bool = False
) -> dict[str, int]:
    scraper = CivicClerkScraper(db)
    return await scraper.run(force=force)
