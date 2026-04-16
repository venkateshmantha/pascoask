"""
Scraper for Pasco County Board of County Commissioners
meeting minutes and agendas.

Source:  https://www.pascocountyfl.gov/government/agendas_minutes.php
         https://pascocofl.portal.civicclerk.com/

Strategy
--------
1. Fetch the main agendas/minutes listing page (HTML).
2. Parse links to individual meeting pages or direct PDF links.
3. For each meeting: download the PDF, extract text via pdfplumber.
4. Parse out the meeting date, agenda items, and vote results.
5. Produce one Document per meeting.

Each Document id: "bcc_minutes_{YYYY}_{MM}_{DD}"
"""
from __future__ import annotations

import asyncio
import io
import logging
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator
from urllib.parse import urljoin, urlparse

import pdfplumber
from bs4 import BeautifulSoup

from config import settings
from ingestion.models import Document
from ingestion.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

# Primary listing page
AGENDAS_URL = "https://www.pascocountyfl.gov/government/agendas_minutes.php"

# CivicClerk portal (used as fallback / supplemental)
CIVICCLERK_BASE = "https://pascocofl.portal.civicclerk.com"

# We look for PDF links that match minutes (not agendas, staff reports, etc.)
MINUTES_PDF_PATTERN = re.compile(r"minutes", re.IGNORECASE)

# Date patterns in filenames and anchor text
DATE_PATTERNS = [
    re.compile(r"(\d{1,2})[/_\-](\d{1,2})[/_\-](\d{2,4})"),   # M/D/YYYY
    re.compile(r"(\d{4})[/_\-](\d{1,2})[/_\-](\d{1,2})"),      # YYYY-M-D
    re.compile(
        r"(january|february|march|april|may|june|july|august|"
        r"september|october|november|december)\s+(\d{1,2}),?\s+(\d{4})",
        re.IGNORECASE,
    ),
]

MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


def _parse_date(text: str) -> str:
    """Try to extract an ISO date string from arbitrary text."""
    for pat in DATE_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        try:
            groups = m.groups()
            if groups[0].isdigit() and len(groups[0]) == 4:
                # YYYY-M-D
                year, month, day = int(groups[0]), int(groups[1]), int(groups[2])
            elif groups[0].isalpha():
                # Month name D, YYYY
                month = MONTH_MAP[groups[0].lower()]
                day = int(groups[1])
                year = int(groups[2])
            else:
                # M/D/YYYY
                month, day, year = int(groups[0]), int(groups[1]), int(groups[2])
                if year < 100:
                    year += 2000
            return datetime(year, month, day).strftime("%Y-%m-%d")
        except (ValueError, KeyError):
            continue
    return ""


def _make_doc_id(date_str: str, url: str) -> str:
    """Create a stable document ID."""
    if date_str:
        return "bcc_minutes_" + date_str.replace("-", "_")
    # Fallback: hash the URL
    import hashlib
    return "bcc_minutes_" + hashlib.md5(url.encode()).hexdigest()[:8]


def _extract_text_from_pdf_bytes(pdf_bytes: bytes) -> str:
    """Extract all text from a PDF given its raw bytes."""
    text_parts: list[str] = []
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text(x_tolerance=2, y_tolerance=3)
                if page_text:
                    text_parts.append(page_text)
    except Exception as exc:
        logger.warning(f"pdfplumber extraction error: {exc}")
    return "\n\n".join(text_parts)


def _is_meaningful(text: str, min_words: int = 100) -> bool:
    """Return True if the text has enough content to be worth storing."""
    return len(text.split()) >= min_words


class BCCMinutesScraper(BaseScraper):
    """
    Scrapes BCC meeting minutes PDFs from the Pasco County website.
    """

    source = "bcc_minutes"

    def __init__(self, db, pdf_cache_dir: Path | None = None) -> None:
        super().__init__(db)
        self.pdf_cache_dir = pdf_cache_dir or settings.pdf_cache_dir / "bcc_minutes"
        self.pdf_cache_dir.mkdir(parents=True, exist_ok=True)

    # ── URL discovery ──────────────────────────────────────────────────────

    async def discover_urls(self) -> AsyncIterator[str]:
        """
        Yield PDF URLs for all BCC meeting minutes found on the listing page.
        """
        logger.info(f"[bcc_minutes] Fetching listing page: {AGENDAS_URL}")
        try:
            response = await self._rate_limited_get(AGENDAS_URL)
        except Exception as exc:
            logger.error(f"Failed to fetch agendas page: {exc}")
            return

        soup = BeautifulSoup(response.text, "lxml")
        pdf_urls: set[str] = set()

        # Find all anchor tags pointing to PDFs
        for a in soup.find_all("a", href=True):
            href: str = a["href"]
            link_text: str = a.get_text(strip=True)

            # Only follow PDF links
            if not href.lower().endswith(".pdf"):
                continue

            # Prefer links whose text or href suggests "minutes"
            combined = href + " " + link_text
            if not MINUTES_PDF_PATTERN.search(combined):
                continue

            full_url = urljoin(AGENDAS_URL, href)
            pdf_urls.add(full_url)

        # Also try to find paginated year links and recurse
        year_urls = self._find_year_links(soup)
        for year_url in year_urls:
            async for url in self._discover_from_year_page(year_url):
                pdf_urls.add(url)

        logger.info(f"[bcc_minutes] Discovered {len(pdf_urls)} PDF URLs")
        for url in sorted(pdf_urls):
            yield url

    def _find_year_links(self, soup: BeautifulSoup) -> list[str]:
        """Find links to per-year archive pages."""
        year_urls = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True)
            # Links like "2022", "2021", "Archive" etc.
            if re.search(r"\b20\d\d\b", text) or "archive" in text.lower():
                full = urljoin(AGENDAS_URL, href)
                if full not in year_urls:
                    year_urls.append(full)
        return year_urls

    async def _discover_from_year_page(self, url: str) -> AsyncIterator[str]:
        """Fetch a year-archive page and yield PDF links from it."""
        try:
            response = await self._rate_limited_get(url)
        except Exception as exc:
            logger.warning(f"Couldn't fetch year page {url}: {exc}")
            return

        soup = BeautifulSoup(response.text, "lxml")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            link_text = a.get_text(strip=True)
            if not href.lower().endswith(".pdf"):
                continue
            combined = href + " " + link_text
            if MINUTES_PDF_PATTERN.search(combined):
                yield urljoin(url, href)

    # ── Single-document scrape ─────────────────────────────────────────────

    async def scrape_one(self, url: str) -> Document | None:
        """Download a minutes PDF and return a Document."""

        # Check PDF cache first (avoid re-downloading on re-runs)
        cache_path = self._cache_path(url)
        if cache_path.exists():
            logger.debug(f"Using cached PDF: {cache_path}")
            pdf_bytes = cache_path.read_bytes()
        else:
            logger.info(f"Downloading PDF: {url}")
            try:
                response = await self._rate_limited_get(url)
                pdf_bytes = response.content
                cache_path.write_bytes(pdf_bytes)
            except Exception as exc:
                logger.error(f"Failed to download {url}: {exc}")
                return None

        # Extract text
        body = _extract_text_from_pdf_bytes(pdf_bytes)
        if not _is_meaningful(body):
            logger.debug(f"Skipping {url} — insufficient text ({len(body.split())} words)")
            return None

        # Infer date from URL and content
        date_str = _parse_date(url) or _parse_date(body[:500])

        # Build title
        title = self._infer_title(url, date_str, body)

        doc_id = _make_doc_id(date_str, url)

        return Document(
            id=doc_id,
            source=self.source,
            doc_type="meeting_minutes",
            title=title,
            body=body,
            date=date_str,
            url=url,
            metadata={
                "pdf_size_bytes": len(pdf_bytes),
                "page_count": self._estimate_page_count(body),
                "agenda_items": self._extract_agenda_item_numbers(body),
            },
        )

    # ── Helpers ────────────────────────────────────────────────────────────

    def _cache_path(self, url: str) -> Path:
        """Deterministic local cache path for a PDF URL."""
        from urllib.parse import quote
        safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", urlparse(url).path)[-80:]
        return self.pdf_cache_dir / f"{safe_name}.pdf"

    def _infer_title(self, url: str, date_str: str, body: str) -> str:
        """Build a human-readable title for the document."""
        if date_str:
            try:
                dt = datetime.strptime(date_str, "%Y-%m-%d")
                return f"BCC Meeting Minutes — {dt.strftime('%B %-d, %Y')}"
            except ValueError:
                pass
        # Fallback: use first non-empty line of the document body
        for line in body.splitlines():
            line = line.strip()
            if len(line) > 10:
                return line[:120]
        return f"BCC Meeting Minutes ({url.split('/')[-1]})"

    def _estimate_page_count(self, text: str) -> int:
        """Rough page count from form-feed characters or page markers."""
        ff_count = text.count("\x0c")
        if ff_count > 0:
            return ff_count
        # fallback: approximate by word count (avg ~300 words/page)
        return max(1, len(text.split()) // 300)

    def _extract_agenda_item_numbers(self, text: str) -> list[str]:
        """Pull out agenda item numbers like 'Item 4A', '4-A', etc."""
        matches = re.findall(
            r"\b(?:item\s+)?(\d{1,2}[A-Z]?)\b",
            text[:5000],
            re.IGNORECASE,
        )
        # Deduplicate preserving order
        seen: set[str] = set()
        result: list[str] = []
        for m in matches:
            upper = m.upper()
            if upper not in seen:
                seen.add(upper)
                result.append(upper)
        return result[:30]  # cap at 30


# ── Convenience runner ─────────────────────────────────────────────────────────

async def run_bcc_scraper(db, force: bool = False) -> dict[str, int]:
    scraper = BCCMinutesScraper(db)
    return await scraper.run(force=force)
