"""
Scraper for Pasco County Board of County Commissioners meeting minutes.

Source: CivicClerk OData API — pascocofl.api.civicclerk.com/v1
  - GET /Events  (filtered to BCC meetings, 2020-present)
  - publishedFiles[].url  (relative path on CivicClerk CDN)
"""
from __future__ import annotations

import io
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator

import pdfplumber

from config import settings
from ingestion.models import Document
from ingestion.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

API_BASE = "https://pascocofl.api.civicclerk.com/v1"
CDN_BASE = "https://civicclerkcdn.azureedge.net/publicportal-live/"
PORTAL_BASE = "https://pascocofl.portal.civicclerk.com"

# Matches any Board of County Commissioners event
BCC_KEYWORDS = ["board of county commissioners"]

DATE_FROM = "2020-01-01T00:00:00Z"


class BCCMinutesScraper(BaseScraper):
    source = "bcc_minutes"

    def __init__(self, db, pdf_cache_dir: Path | None = None, max_events: int = 0) -> None:
        super().__init__(db)
        self.pdf_cache_dir = pdf_cache_dir or settings.pdf_cache_dir / "bcc_minutes"
        self.pdf_cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_events = max_events if max_events > 0 else 999_999
        self._event_cache: dict[str, dict] = {}  # url -> full event dict

    async def discover_urls(self) -> AsyncIterator[str]:
        from datetime import timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        page_url = (
            f"{API_BASE}/Events"
            f"?$filter=eventDate ge {DATE_FROM} and eventDate le {today}"
            f"&$orderby=eventDate desc"
            f"&$top=50"
        )
        count = 0
        while page_url and count < self.max_events:
            resp = await self._rate_limited_get(page_url)
            data = resp.json()
            events = data.get("value", [])
            logger.info("[bcc_minutes] Got %d events", len(events))
            for event in events:
                if count >= self.max_events:
                    return
                name = (event.get("eventName") or "").lower()
                if not any(kw in name for kw in BCC_KEYWORDS):
                    continue
                if not event.get("publishedFiles"):
                    continue  # skip past meetings with no uploaded documents
                url = f"{PORTAL_BASE}/event/{event['id']}"
                self._event_cache[url] = event
                yield url
                count += 1
            page_url = data.get("@odata.nextLink")

    async def scrape_one(self, url: str) -> Document | None:
        # Use cached event data from discover_urls (contains publishedFiles)
        event = self._event_cache.get(url)
        if not event:
            # Fallback for re-runs where cache is cold
            event_id_str = url.split("/")[-1]
            resp = await self._rate_limited_get(f"{API_BASE}/Events/{event_id_str}")
            event = resp.json()

        event_id = event.get("id")
        event_name = event.get("eventName", f"BCC Meeting {event_id}")
        event_date = _parse_event_date(event.get("eventDate", ""))
        published_files = event.get("publishedFiles") or []

        logger.info("[bcc_minutes] Event %s '%s': %d published files", event_id, event_name, len(published_files))

        # Download the first available PDF (prefer Minutes over Agenda)
        pdf_text, pdf_url = "", ""
        files_sorted = sorted(
            published_files,
            key=lambda f: 0 if "minute" in (f.get("type") or "").lower() else 1,
        )
        for f in files_sorted:
            rel_url = f.get("url", "")
            if not rel_url:
                logger.info("[bcc_minutes]   File has no url field: %s", f)
                continue
            # Try CDN URL and API stream URL
            for base in [CDN_BASE, f"{API_BASE}/"]:
                full_url = base + rel_url
                logger.info("[bcc_minutes]   Trying PDF: %s", full_url)
                text = await self._download_pdf(full_url, str(event_id))
                if text:
                    pdf_text, pdf_url = text, full_url
                    logger.info("[bcc_minutes]   Got %d words from PDF", len(text.split()))
                    break
            if pdf_text:
                break

        if not pdf_text or len(pdf_text.split()) < 50:
            # Fall back to metadata-only document so chunking/retrieval can proceed
            logger.info(
                "[bcc_minutes] No PDF for event %s — building metadata document", event_id
            )
            pdf_text = _build_metadata_body(event)

        doc_id = (
            f"bcc_minutes_{event_date.replace('-', '_')}"
            if event_date
            else f"bcc_minutes_event_{event_id}"
        )

        return Document(
            id=doc_id,
            source="bcc_minutes",
            doc_type="meeting_minutes",
            title=_build_title(event_name, event_date),
            body=pdf_text,
            date=event_date,
            url=pdf_url or f"{PORTAL_BASE}/event/{event_id}",
            metadata={
                "event_id": event_id,
                "event_name": event_name,
                "portal_url": f"{PORTAL_BASE}/event/{event_id}",
                "file_count": len(published_files),
                "page_count": _estimate_pages(pdf_text),
                "agenda_items": _extract_agenda_items(pdf_text),
                "pdf_available": bool(pdf_text and len(pdf_text.split()) >= 50),
            },
        )

    async def _download_pdf(self, url: str, prefix: str = "") -> str:
        import hashlib
        cache_key = hashlib.md5(url.encode()).hexdigest()
        cache_path = self.pdf_cache_dir / f"{prefix}_{cache_key}.pdf"
        try:
            if cache_path.exists():
                pdf_bytes = cache_path.read_bytes()
            else:
                # Use client directly — no retry on 4xx (they're permanent for these CDN blobs)
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
            logger.warning("PDF error %s: %s", url, exc)
            return ""


def _parse_event_date(raw: str) -> str:
    if not raw:
        return ""
    m = re.search(r"(\d{4}-\d{2}-\d{2})", raw)
    return m.group(1) if m else ""


def _build_title(event_name: str, date_str: str) -> str:
    if date_str:
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            return f"{event_name} — {dt.strftime('%B %-d, %Y')}"
        except ValueError:
            pass
    return event_name


def _estimate_pages(text: str) -> int:
    ff = text.count("\x0c")
    return ff if ff > 0 else max(1, len(text.split()) // 300)


def _extract_agenda_items(text: str) -> list[str]:
    matches = re.findall(r"\b(?:item\s+)?(\d{1,2}[A-Z]?)\b", text[:5000], re.IGNORECASE)
    seen: set[str] = set()
    result = []
    for m in matches:
        u = m.upper()
        if u not in seen:
            seen.add(u)
            result.append(u)
    return result[:30]


def _build_metadata_body(event: dict) -> str:
    lines = []
    name = event.get("eventName", "")
    date = _parse_event_date(event.get("eventDate", ""))
    location = event.get("eventLocation", "") or event.get("location", "")
    category = event.get("categoryName", "")
    description = event.get("eventDescription", "") or event.get("description", "")

    if name:
        lines.append(f"Meeting: {name}")
    if date:
        lines.append(f"Date: {date}")
    if location:
        lines.append(f"Location: {location}")
    if category:
        lines.append(f"Category: {category}")
    if description:
        lines.append(f"\n{description.strip()}")

    files = event.get("publishedFiles") or []
    if files:
        lines.append("\nPublished Documents:")
        for f in files:
            title = f.get("name") or f.get("type") or f.get("fileName") or ""
            if title:
                lines.append(f"  - {title}")

    return "\n".join(lines)


async def run_bcc_scraper(db, force: bool = False, max_events: int = 0) -> dict[str, int]:
    scraper = BCCMinutesScraper(db, max_events=max_events)
    return await scraper.run(force=force)
