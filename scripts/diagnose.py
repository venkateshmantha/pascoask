"""
Diagnostic script — fetches the BCC and CivicClerk pages and prints
all links found, so we can see the actual site structure.

Usage:
    python scripts/diagnose.py
"""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*",
}

async def fetch(url: str) -> str:
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=30) as client:
        r = await client.get(url)
        print(f"  Status: {r.status_code}  Final URL: {r.url}")
        return r.text

async def check_bcc():
    print("\n" + "="*60)
    print("BCC MINUTES PAGE")
    print("="*60)
    url = "https://www.pascocountyfl.gov/government/agendas_minutes.php"
    html = await fetch(url)
    soup = BeautifulSoup(html, "lxml")

    print("\n--- ALL LINKS (href) ---")
    links = [(a.get_text(strip=True)[:60], a["href"]) for a in soup.find_all("a", href=True)]
    for text, href in links[:80]:
        print(f"  [{text}] -> {href}")

    print(f"\n--- PDF LINKS ONLY ---")
    pdfs = [(a.get_text(strip=True)[:60], a["href"]) for a in soup.find_all("a", href=True) if a["href"].lower().endswith(".pdf")]
    for text, href in pdfs:
        print(f"  [{text}] -> {href}")
    print(f"\nTotal links: {len(links)}, PDFs: {len(pdfs)}")

async def check_civicclerk():
    print("\n" + "="*60)
    print("CIVICCLERK PORTAL")
    print("="*60)

    # Try the main page
    print("\n-- Main page --")
    html = await fetch("https://pascocofl.portal.civicclerk.com")
    soup = BeautifulSoup(html, "lxml")
    links = [(a.get_text(strip=True)[:60], a["href"]) for a in soup.find_all("a", href=True)]
    for text, href in links[:30]:
        print(f"  [{text}] -> {href}")

    # Try common API endpoints
    print("\n-- API probe --")
    async with httpx.AsyncClient(headers={**HEADERS, "Accept": "application/json"}, follow_redirects=True, timeout=15) as client:
        for path in [
            "/api/v2/PublishedEvents",
            "/api/v2/Events",
            "/api/v1/Events",
            "/api/v2/Meetings",
            "/api/events",
        ]:
            try:
                r = await client.get(f"https://pascocofl.portal.civicclerk.com{path}")
                print(f"  {path} -> {r.status_code} ({len(r.content)} bytes) | {r.text[:120]}")
            except Exception as e:
                print(f"  {path} -> ERROR: {e}")

async def check_clerk():
    print("\n" + "="*60)
    print("PASCO CLERK & COMPTROLLER (pascoclerk.com)")
    print("="*60)
    html = await fetch("https://www.pascoclerk.com")
    soup = BeautifulSoup(html, "lxml")
    links = [(a.get_text(strip=True)[:60], a["href"]) for a in soup.find_all("a", href=True)]
    print("\n--- ALL LINKS ---")
    for text, href in links[:60]:
        print(f"  [{text}] -> {href}")

    # Look for minutes-related links
    print("\n--- MINUTES-RELATED LINKS ---")
    for text, href in links:
        combined = (text + " " + href).lower()
        if any(kw in combined for kw in ["minutes", "agenda", "bcc", "board", "meeting", "commissioner"]):
            print(f"  [{text}] -> {href}")

async def check_civicclerk_html():
    print("\n" + "="*60)
    print("CIVICCLERK PORTAL - HTML INSPECTION")
    print("="*60)
    html = await fetch("https://pascocofl.portal.civicclerk.com")
    soup = BeautifulSoup(html, "lxml")
    # Look for script tags with API URLs
    print("\n--- SCRIPT SRC TAGS ---")
    for s in soup.find_all("script", src=True):
        print(f"  {s['src']}")
    # Look for any data- attributes or API references in inline scripts
    print("\n--- INLINE SCRIPT SNIPPETS (first 200 chars each) ---")
    for s in soup.find_all("script", src=False):
        text = s.get_text()[:200].strip()
        if text:
            print(f"  {text}")
    # Check for iframes
    print("\n--- IFRAMES ---")
    for f in soup.find_all("iframe"):
        print(f"  src={f.get('src')} data-src={f.get('data-src')}")
    # Any links
    links = [(a.get_text(strip=True)[:60], a.get("href","")) for a in soup.find_all("a", href=True)]
    print(f"\n--- LINKS ({len(links)} total) ---")
    for text, href in links[:20]:
        print(f"  [{text}] -> {href}")

async def check_civicclerk_api():
    """Grep the JS bundle for API patterns and try more endpoints."""
    print("\n" + "="*60)
    print("CIVICCLERK - JS BUNDLE API SCAN")
    print("="*60)
    import re

    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=30) as client:
        # Fetch the main JS bundle
        try:
            r = await client.get("https://pascocofl.portal.civicclerk.com/static/js/main.2acd9b93.js")
            js = r.text
            print(f"Bundle size: {len(js):,} chars")
            # Find anything that looks like an API path
            api_paths = re.findall(r'["\`](/api/[^"\'`\s]{3,60})["\`]', js)
            print(f"\n--- API PATHS IN BUNDLE ({len(api_paths)} found) ---")
            for p in sorted(set(api_paths))[:40]:
                print(f"  {p}")
            # Find any URL patterns
            urls = re.findall(r'https?://[^\s"\'`]{10,80}', js)
            print(f"\n--- EXTERNAL URLS IN BUNDLE (sample) ---")
            for u in sorted(set(urls))[:20]:
                print(f"  {u}")
        except Exception as e:
            print(f"Bundle fetch failed: {e}")

        # Try more API endpoints
        print("\n--- EXTENDED API PROBE ---")
        headers_json = {**HEADERS, "Accept": "application/json"}
        for path in [
            "/api/v2/EventCategories",
            "/api/v2/Events?%24top=5",
            "/api/v2/MeetingTypes",
            "/api/v2/Documents?%24top=5",
            "/odata/v1/Events",
            "/graphql",
            "/api/search?q=minutes",
        ]:
            try:
                r = await client.get(
                    f"https://pascocofl.portal.civicclerk.com{path}",
                    headers=headers_json,
                )
                print(f"  {path} -> {r.status_code} | {r.text[:150]}")
            except Exception as e:
                print(f"  {path} -> ERROR: {e}")

async def check_transparency():
    print("\n" + "="*60)
    print("TRANSPARENCY PORTAL")
    print("="*60)
    html = await fetch("https://www.pascocountyfl.gov/government/transparency_portal.php")
    soup = BeautifulSoup(html, "lxml")
    links = [(a.get_text(strip=True)[:60], a["href"]) for a in soup.find_all("a", href=True)]
    print("\n--- MINUTES/AGENDA/MEETING LINKS ---")
    for text, href in links:
        combined = (text + " " + href).lower()
        if any(kw in combined for kw in ["minutes", "agenda", "meeting", "bcc", "commissioner", "civicclerk", "portal"]):
            print(f"  [{text}] -> {href}")
    print(f"\nTotal links on page: {len(links)}")

async def check_civicclerk_schema():  # noqa: C901
    print("\n" + "="*60)
    print("CIVICCLERK API SCHEMA + FIRST EVENT")
    print("="*60)
    base = "https://pascocofl.api.civicclerk.com/v1"
    h = {**HEADERS, "Accept": "application/json"}
    async with httpx.AsyncClient(headers=h, follow_redirects=True, timeout=15) as client:

        # Full list of entity sets
        r = await client.get(base)
        sets = r.json().get("value", [])
        print("\n--- ALL ENTITY SETS ---")
        for s in sets:
            print(f"  {s['name']} ({s['kind']}) -> {s['url']}")

        # First BCC event full record
        r = await client.get(f"{base}/Events?$filter=eventDate ge 2024-01-01T00:00:00Z&$orderby=eventDate desc&$top=1")
        events = r.json().get("value", [])
        if events:
            event = events[0]
            event_id = event["id"]
            print(f"\n--- FULL EVENT RECORD (id={event_id}) ---")
            import json
            print(json.dumps(event, indent=2))

            # Try OData $expand for every entity set
            print(f"\n--- $EXPAND PROBES for event {event_id} ---")
            for name in [s["name"] for s in sets]:
                try:
                    r2 = await client.get(f"{base}/Events({event_id})?$expand={name}")
                    if r2.status_code == 200 and name.lower() in r2.text.lower():
                        print(f"  $expand={name} -> 200 MATCH | {r2.text[:200]}")
                    else:
                        print(f"  $expand={name} -> {r2.status_code}")
                except Exception as e:
                    print(f"  $expand={name} -> ERROR: {e}")

            # Try navigation properties directly
            print(f"\n--- NAVIGATION PROPERTY PROBES ---")
            for nav in ["Files", "Documents", "Attachments", "AgendaItems", "EventItems",
                        "Minutes", "Agendas", "Packets", "Media", "Actions"]:
                try:
                    r2 = await client.get(f"{base}/Events({event_id})/{nav}")
                    print(f"  /{nav} -> {r2.status_code} | {r2.text[:150]}")
                except Exception as e:
                    print(f"  /{nav} -> ERROR: {e}")

async def check_past_bcc_event():
    print("\n" + "="*60)
    print("PAST BCC EVENT - FILE STRUCTURE")
    print("="*60)
    base = "https://pascocofl.api.civicclerk.com/v1"
    h = {**HEADERS, "Accept": "application/json"}
    async with httpx.AsyncClient(headers=h, follow_redirects=True, timeout=15) as client:
        # Get a past BCC event that has actually happened
        r = await client.get(
            f"{base}/Events"
            "?$filter=eventDate ge 2024-01-01T00:00:00Z and eventDate le 2025-01-01T00:00:00Z"
            "&$orderby=eventDate desc&$top=5"
        )
        import json
        events = r.json().get("value", [])
        print(f"Found {len(events)} events in 2024")
        for event in events:
            eid = event["id"]
            name = event["eventName"]
            date = event["eventDate"]
            pub_files = event.get("publishedFiles", [])
            agenda = event.get("agendaFile", {})
            minutes = event.get("minutesFile", {})
            print(f"\n  Event {eid}: {name} | {date}")
            print(f"    agendaFile.fileName  = {agenda.get('fileName')}")
            print(f"    agendaFile.agendaId  = {agenda.get('agendaId')}")
            print(f"    minutesFile.fileName = {minutes.get('fileName')}")
            print(f"    publishedFiles count = {len(pub_files)}")
            if pub_files:
                print(f"    publishedFiles[0]    = {json.dumps(pub_files[0], indent=6)}")

        # Also check Meetings and Sections entity sets
        print("\n--- Meetings entity ---")
        r2 = await client.get(f"{base}/Meetings?$top=2")
        print(f"  /Meetings -> {r2.status_code} | {r2.text[:300]}")

        print("\n--- Sections entity ---")
        r3 = await client.get(f"{base}/Sections?$top=2")
        print(f"  /Sections -> {r3.status_code} | {r3.text[:300]}")

async def main():
    await check_past_bcc_event()

if __name__ == "__main__":
    asyncio.run(main())
