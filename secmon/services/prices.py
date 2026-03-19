from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

from ..config import Settings


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PriceQuote:
    ticker: str
    provider: str
    currency: str | None
    exchange: str | None
    market_state: str | None
    regular_market_price: float | None
    previous_close: float | None
    change_amount: float | None
    change_percent: float | None
    day_low: float | None
    day_high: float | None
    volume: int | None
    market_cap: float | None
    raw_payload: dict


class BasePriceService:
    provider_name = "disabled"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.available = False
        self.unavailable_reason = "Price provider is not configured."

    def fetch_quotes(self, tickers: list[str]) -> list[PriceQuote]:
        raise NotImplementedError


class YahooFinancePriceService(BasePriceService):
    provider_name = "yahoo"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.available = True
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": settings.sec_user_agent,
                "Accept": "application/json",
            }
        )

    def fetch_quotes(self, tickers: list[str]) -> list[PriceQuote]:
        quotes: list[PriceQuote] = []
        for ticker in tickers:
            response = self.session.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
                params={"range": "1d", "interval": "1m"},
                timeout=self.settings.request_timeout_seconds,
            )
            if not response.ok:
                raise RuntimeError(f"Yahoo Finance price API error {response.status_code}: {response.text[:400]}")

            payload = response.json()
            result = (payload.get("chart", {}) or {}).get("result", [])
            if not result:
                logger.warning("Yahoo chart endpoint returned no result for %s", ticker)
                continue

            item = result[0]
            meta = item.get("meta", {}) or {}
            symbol = str(meta.get("symbol", ticker)).upper().strip()
            if not symbol:
                continue
            quotes.append(
                PriceQuote(
                    ticker=symbol,
                    provider=self.provider_name,
                    currency=_opt_str(meta.get("currency")),
                    exchange=_opt_str(meta.get("fullExchangeName") or meta.get("exchangeName")),
                    market_state=_opt_str(meta.get("marketState")),
                    regular_market_price=_opt_float(meta.get("regularMarketPrice")),
                    previous_close=_opt_float(meta.get("previousClose")),
                    change_amount=_compute_change_amount(meta.get("regularMarketPrice"), meta.get("previousClose")),
                    change_percent=_compute_change_percent(meta.get("regularMarketPrice"), meta.get("previousClose")),
                    day_low=_opt_float(meta.get("regularMarketDayLow")),
                    day_high=_opt_float(meta.get("regularMarketDayHigh")),
                    volume=_opt_int(meta.get("regularMarketVolume")),
                    market_cap=_opt_float(meta.get("marketCap")),
                    raw_payload=item,
                )
            )
        return quotes


class DisabledPriceService(BasePriceService):
    def __init__(self, settings: Settings, reason: str) -> None:
        super().__init__(settings)
        self.unavailable_reason = reason

    def fetch_quotes(self, tickers: list[str]) -> list[PriceQuote]:
        raise RuntimeError(self.unavailable_reason)


def build_price_service(settings: Settings) -> BasePriceService:
    provider = settings.price_provider.strip().lower()
    if provider in {"yahoo", "yahoo_finance"}:
        return YahooFinancePriceService(settings)
    if provider in {"disabled", "off", "none"}:
        return DisabledPriceService(settings, "Price provider is disabled.")
    return DisabledPriceService(settings, f"Unsupported price provider: {settings.price_provider}")


def _opt_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _opt_float(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _opt_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _compute_change_amount(price: object, previous_close: object) -> float | None:
    regular = _opt_float(price)
    previous = _opt_float(previous_close)
    if regular is None or previous is None:
        return None
    return regular - previous


def _compute_change_percent(price: object, previous_close: object) -> float | None:
    regular = _opt_float(price)
    previous = _opt_float(previous_close)
    if regular is None or previous in {None, 0}:
        return None
    return ((regular - previous) / previous) * 100
