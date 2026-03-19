from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from urllib.parse import urljoin

import feedparser
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..config import Settings
from ..watchlist import TICKER_NAMES, WATCHLIST


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


@dataclass(slots=True)
class PrimaryDocument:
    url: str | None
    text: str | None


def determine_tier(items_list: list[str], form_type: str) -> int:
    tier1_codes = {"1.03", "4.02", "8.01", "1.04", "2.01"}
    tier2_codes = {"1.01", "3.01", "5.02", "1.02", "3.02", "5.01"}

    for item in items_list:
        if item in tier1_codes:
            return 1
    for item in items_list:
        if item in tier2_codes:
            return 2

    if "10-K" in form_type or "10-Q" in form_type:
        return 1
    if form_type in {"4", "3", "5", "S-1", "S-3"}:
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
        response = self.session.get(
            self.settings.sec_feed_url,
            timeout=self.settings.request_timeout_seconds,
        )
        response.raise_for_status()

        feed = feedparser.parse(response.content)
        candidates: list[FilingCandidate] = []

        for entry in list(feed.entries)[: self.settings.max_feed_entries]:
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
            published_at = self._parse_entry_datetime(entry)
            accession_number = self._extract_accession_number(entry_link)

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
                    tier=determine_tier(sec_items, form_type),
                    published_at=published_at,
                )
            )

        return len(feed.entries), candidates

    def fetch_primary_document(self, index_url: str) -> PrimaryDocument:
        response = self.session.get(index_url, timeout=self.settings.request_timeout_seconds)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")

        table = soup.find("table", class_="tableFile")
        if table is None:
            return PrimaryDocument(url=None, text=None)

        for row in table.find_all("tr")[1:]:
            cols = row.find_all("td")
            if len(cols) < 3:
                continue

            file_link = cols[2].find("a")
            if file_link is None:
                continue

            href = file_link.get("href", "")
            file_name = href.split("/")[-1].lower()
            if not file_name.endswith((".htm", ".html")):
                continue
            if "xbrl" in file_name or "_pre" in file_name:
                continue

            document_url = urljoin("https://www.sec.gov", href)
            document_url = document_url.replace("/ix?doc=", "")
            document_url = document_url.replace("/ixviewer/ix.html?doc=", "")

            document_response = self.session.get(
                document_url,
                timeout=self.settings.request_timeout_seconds,
            )
            document_response.raise_for_status()

            doc_soup = BeautifulSoup(document_response.content, "html.parser")
            clean_text = re.sub(r"\s+", " ", doc_soup.get_text(separator=" ", strip=True)).strip()
            return PrimaryDocument(
                url=document_url,
                text=clean_text[: self.settings.max_document_chars] if clean_text else None,
            )

        return PrimaryDocument(url=None, text=None)

    def _match_watchlist(self, title: str) -> tuple[str, str] | None:
        lowered = title.lower()

        for ticker, company_name in TICKER_NAMES.items():
            if company_name.lower() in lowered:
                return ticker, company_name

        for ticker in WATCHLIST:
            if re.search(rf"\b{re.escape(ticker)}\b", title, re.IGNORECASE):
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
