"""
Land Development Code (LDC) scraper.
Fetches ordinance PDFs from the Pasco County planning amendments page.
"""
from __future__ import annotations

import hashlib
import io
import logging
import re
from typing import AsyncIterator

import pdfplumber
from bs4 import BeautifulSoup

import sqlite_utils

from config import settings
from ingestion.models import Document
from ingestion.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

LDC_BASE_URL = "https://www.pascocountyfl.gov"
LDC_PAGE_URL = (
    "https://www.pascocountyfl.gov/services/planning_and_development/"
    "land_development_code_amendments.php"
)


class LDCScraper(BaseScraper):
    source = "ldc"

    async def discover_urls(self) -> AsyncIterator[str]:
        response = await self._rate_limited_get(LDC_PAGE_URL)
        soup = BeautifulSoup(response.text, "lxml")
        seen: set[str] = set()
        for a in soup.find_all("a", href=True):
            href: str = a["href"]
            if not href.lower().endswith(".pdf"):
                continue
            if href.startswith("http"):
                url = href
            elif href.startswith("/"):
                url = LDC_BASE_URL + href
            else:
                url = LDC_BASE_URL + "/" + href
            if url not in seen:
                seen.add(url)
                yield url

    async def scrape_one(self, url: str) -> Document | None:
        cache_key = hashlib.md5(url.encode()).hexdigest()
        cache_path = settings.pdf_cache_dir / f"ldc_{cache_key}.pdf"

        if cache_path.exists():
            pdf_bytes = cache_path.read_bytes()
        else:
            resp = await self._rate_limited_get(url)
            if "pdf" not in resp.headers.get("content-type", "").lower() and not url.lower().endswith(".pdf"):
                return None
            pdf_bytes = resp.content
            cache_path.write_bytes(pdf_bytes)

        text = _extract_pdf_text(pdf_bytes)
        if not text or len(text.strip()) < 100:
            logger.debug("Skipping low-content PDF: %s", url)
            return None

        section_num = _parse_section_number(url, text)
        doc_id = f"ldc_section_{section_num}" if section_num else f"ldc_{cache_key[:12]}"
        title = _parse_title(url, text, section_num)

        return Document(
            id=doc_id,
            source="ldc",
            doc_type="ordinance",
            title=title,
            body=text,
            date="",
            url=url,
            metadata={
                "section_number": section_num,
                "pdf_size_bytes": len(pdf_bytes),
            },
        )


def _extract_pdf_text(pdf_bytes: bytes) -> str:
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            return "\n".join(
                page.extract_text(x_tolerance=3, y_tolerance=3) or ""
                for page in pdf.pages
            )
    except Exception as exc:
        logger.warning("PDF extraction error: %s", exc)
        return ""


def _parse_section_number(url: str, text: str) -> str:
    filename = url.split("/")[-1].replace(".pdf", "")
    # e.g. "Section_602.11" or "602_11" in filename
    m = re.search(r"(\d+)[._](\d+)(?:[._](\d+))?", filename)
    if m:
        parts = [m.group(1), m.group(2)]
        if m.group(3):
            parts.append(m.group(3))
        return "_".join(parts)
    # Try text body header
    m = re.search(r"(?i)section\s+(\d+[\.\-]\d+(?:[\.\-]\d+)?)", text[:800])
    if m:
        return re.sub(r"[.\-]", "_", m.group(1))
    return re.sub(r"[^a-z0-9]", "_", filename.lower())[:40].strip("_")


def _parse_title(url: str, text: str, section_num: str) -> str:
    for line in text.split("\n")[:15]:
        line = line.strip()
        if len(line) > 10 and not line.startswith("%"):
            return line[:200]
    filename = url.split("/")[-1].replace(".pdf", "").replace("_", " ").replace("-", " ")
    return f"LDC {('Section ' + section_num) if section_num else filename}"


async def run_ldc_scraper(
    db: sqlite_utils.Database, force: bool = False
) -> dict[str, int]:
    scraper = LDCScraper(db)
    return await scraper.run(force=force)
