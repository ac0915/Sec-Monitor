from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import feedparser
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..config import Settings
from ..watchlist import TICKER_NAMES, WATCHLIST


# ── Form type alert prefixes for Telegram messages ──────────────────────────
FORM_ALERT_PREFIXES: dict[str, str] = {
    "4":      "🕵️ INSIDER TRADE",
    "3":      "🕵️ INITIAL INSIDER",
    "5":      "🕵️ ANNUAL INSIDER",
    "S-3":    "⚠️ DILUTION WARNING",
    "S-1":    "🚀 IPO FILING",
    "424B1":  "🚨 SHARE OFFERING",
    "424B2":  "🚨 SHARE OFFERING",
    "424B3":  "🚨 SHARE OFFERING",
    "424B4":  "🚨 SHARE OFFERING",
    "424B5":  "🚨 SHARE OFFERING",
    "SC 13D": "🏛️ ACTIVIST STAKE",
    "SC 13G": "📊 LARGE HOLDER",
    "6-K":    "🌏 FOREIGN FILING",
    "8-K":    "⚡ CATALYST",
    "10-K":   "📋 ANNUAL REPORT",
    "10-Q":   "📊 QUARTERLY REPORT",
}


def get_form_alert_prefix(form_type: str) -> str:
    """Return an emoji-prefixed alert label for a given SEC form type."""
    for key, label in FORM_ALERT_PREFIXES.items():
        if form_type.upper().startswith(key.upper()):
            return label
    return "📄 SEC FILING"


@dataclass(slots=True)
class FilingCandidate:
    accession_number: str | None
    ticker: str
    company_name: str
    title: str
    form_type: str
    entry_link: str
    summary: str
    sec_items: list[str]
    tier: int
    published_at: datetime
    alert_prefix: str  # ← NEW: pre-computed alert label


@dataclass(slots=True)
class PrimaryDocument:
    url: str | None
    text: str | None


def determine_tier(items_list: list[str], form_type: str) -> int:
    # ── 8-K item-based tiers ─────────────────────────────────────────────────
    tier1_codes = {"1.03", "4.02", "8.01", "1.04", "2.01"}
    tier2_codes = {"1.01", "3.01", "5.02", "1.02", "3.02", "5.01"}

    for item in items_list:
        if item in tier1_codes:
            return 1
    for item in items_list:
        if item in tier2_codes:
            return 2

    ft = form_type.upper()

    # ── Tier 1: Highest impact ───────────────────────────────────────────────
    # Insider trades (Form 4) — executives buying/selling their own stock
    # Share offerings (S-3, 424B) — dilution warning for small caps
    # Activist stake (SC 13D) — someone buying 5%+ for control
    # Annual/quarterly reports — always high priority
    if (
        ft in {"4", "10-K", "10-Q", "SC 13D"}
        or ft.startswith("424B")
        or ft == "S-3"
    ):
        return 1

    # ── Tier 2: Medium impact ────────────────────────────────────────────────
    # Initial/annual insider (Form 3/5)
    # IPO filing (S-1)
    # Large passive holder (SC 13G) — less aggressive than 13D
    # Foreign filing (6-K) — overseas companies like TSM, NIO
    if ft in {"3", "5", "S-1", "SC 13G", "6-K", "13F-HR"}:
        return 2

    return 3


class SECClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": settings.sec_user_agent})
        retry = Retry(
            total=3,
            backoff_factor=0.6,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def fetch_current_candidates(self) -> tuple[int, list[FilingCandidate]]:
        total_entries_seen = 0
        candidates: list[FilingCandidate] = []
        seen_entry_links: set[str] = set()

        for page_index in range(max(1, self.settings.sec_feed_pages)):
            page_url = self._build_feed_page_url(page_index)
            response = self.session.get(
                page_url,
                timeout=self.settings.request_timeout_seconds,
            )
            response.raise_for_status()

            feed = feedparser.parse(response.content)
            entries = list(feed.entries)[: self.settings.sec_feed_page_size]
            total_entries_seen += len(entries)

            for entry in entries:
                title = getattr(entry, "title", "").strip()
                if not title:
                    continue

                match = self._match_watchlist(title)
                if not match:
                    continue

                ticker, company_name = match
                form_type = title.split(" - ")[0].strip() if " - " in title else "Unknown"
                summary = unescape(getattr(entry, "summary", title))
                sec_items = sorted(set(re.findall(r"Item\s*(\d\.\d{2})", summary, re.IGNORECASE)))
                entry_link = getattr(entry, "link", "").strip()
                if not entry_link or entry_link in seen_entry_links:
                    continue
                seen_entry_links.add(entry_link)

                published_at = self._parse_entry_datetime(entry)
                accession_number = self._extract_accession_number(entry_link)
                tier = determine_tier(sec_items, form_type)
                alert_prefix = get_form_alert_prefix(form_type)

                candidates.append(
                    FilingCandidate(
                        accession_number=accession_number,
                        ticker=ticker,
                        company_name=company_name,
                        title=title,
                        form_type=form_type,
                        entry_link=entry_link,
                        summary=summary,
                        sec_items=sec_items,
                        tier=tier,
                        published_at=published_at,
                        alert_prefix=alert_prefix,  # ← NEW
                    )
                )

        return total_entries_seen, candidates

    def fetch_primary_document(self, index_url: str) -> PrimaryDocument:
        response = self.session.get(index_url, timeout=self.settings.request_timeout_seconds)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")

        table = soup.find("table", class_="tableFile")
        if table is None:
            return PrimaryDocument(url=None, text=None)

        # Try to find HTML documents first, then fallback to TXT
        for row in table.find_all("tr")[1:]:
            cols = row.find_all("td")
            if len(cols) < 3:
                continue

            file_link = cols[2].find("a")
            if file_link is None:
                continue

            href = file_link.get("href", "")
            file_name = href.split("/")[-1].lower()
            if not file_name.endswith((".htm", ".html", ".txt")):
                continue
            if "xbrl" in file_name or "_pre" in file_name:
                continue

            document_url = urljoin("https://www.sec.gov", href)
            document_url = document_url.replace("/ix?doc=", "")
            document_url = document_url.replace("/ixviewer/ix.html?doc=", "")

            try:
                document_response = self.session.get(
                    document_url,
                    timeout=self.settings.request_timeout_seconds,
                )
                document_response.raise_for_status()

                if file_name.endswith(".txt"):
                    # For TXT files, use the text directly
                    clean_text = re.sub(r"\s+", " ", document_response.text).strip()
                else:
                    # For HTML files, parse with BeautifulSoup
                    doc_soup = BeautifulSoup(document_response.content, "html.parser")
                    clean_text = re.sub(r"\s+", " ", doc_soup.get_text(separator=" ", strip=True)).strip()
                
                return PrimaryDocument(
                    url=document_url,
                    text=clean_text[: self.settings.max_document_chars] if clean_text else None,
                )
            except Exception as e:
                logger.warning(f"Failed to fetch document {document_url}: {e}")
                continue

        return PrimaryDocument(url=None, text=None)

    def _match_watchlist(self, title: str) -> tuple[str, str] | None:
        # 1. Match company names safely using word boundaries
        for ticker, company_name in TICKER_NAMES.items():
            if re.search(rf"\b{re.escape(company_name)}\b", title, re.IGNORECASE):
                return ticker, company_name

        # 2. Match Tickers STRICTLY (Case-sensitive, word boundaries)
        for ticker in WATCHLIST:
            if re.search(rf"\b{re.escape(ticker)}\b", title):
                return ticker, TICKER_NAMES.get(ticker, ticker)

        return None

    def _parse_entry_datetime(self, entry: object) -> datetime:
        if getattr(entry, "updated_parsed", None):
            return datetime.fromtimestamp(calendar.timegm(entry.updated_parsed), tz=timezone.utc)

        for field in ("updated", "published"):
            value = getattr(entry, field, None)
            if not value:
                continue
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
            except ValueError:
                try:
                    return parsedate_to_datetime(value).astimezone(timezone.utc)
                except (TypeError, ValueError):
                    continue

        return datetime.now(timezone.utc)

    def _extract_accession_number(self, entry_link: str) -> str | None:
        match = re.search(r"(\d{10}-\d{2}-\d{6})", entry_link)
        if match:
            return match.group(1)
        tail = entry_link.split("/")[-1].split(".")[0]
        return tail or None

    def _build_feed_page_url(self, page_index: int) -> str:
        parts = urlsplit(self.settings.sec_feed_url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        page_size = max(1, self.settings.sec_feed_page_size)
        query["start"] = str(page_index * page_size)
        query["count"] = str(page_size)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))