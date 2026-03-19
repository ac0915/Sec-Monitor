from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import math
import re

from ..models import Filing, utc_now


@dataclass(frozen=True)
class ThemeRule:
    key: str
    label: str
    description: str
    form_types: tuple[str, ...] = ()
    sec_items: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    weight: int = 0


THEME_RULES: tuple[ThemeRule, ...] = (
    ThemeRule(
        key="financing",
        label="Financing / Dilution",
        description="增发、债务、信用额度或资本结构变化。",
        form_types=("S-1", "S-3", "424B2", "424B3", "424B5", "424B7", "F-3"),
        sec_items=("1.01", "2.03", "3.02"),
        keywords=("offering", "dilution", "capital raise", "private placement", "warrant", "atm", "credit facility", "notes"),
        weight=16,
    ),
    ThemeRule(
        key="earnings",
        label="Earnings / Guidance",
        description="财报、指引或经营表现更新。",
        form_types=("10-Q", "10-K", "8-K"),
        sec_items=("2.02",),
        keywords=("earnings", "revenue", "guidance", "outlook", "margin", "quarterly", "annual report"),
        weight=12,
    ),
    ThemeRule(
        key="leadership",
        label="Leadership / Governance",
        description="高管、董事会、股东投票或治理结构变化。",
        form_types=("8-K", "DEF 14A"),
        sec_items=("5.02", "5.07"),
        keywords=("chief executive", "chief financial", "director", "resignation", "appointed", "board", "governance"),
        weight=13,
    ),
    ThemeRule(
        key="mna",
        label="M&A / Strategic Deal",
        description="并购、资产交易、重大合作或控制权变更。",
        form_types=("8-K",),
        sec_items=("1.01", "2.01", "5.01"),
        keywords=("acquisition", "merger", "asset purchase", "divestiture", "strategic", "transaction", "agreement"),
        weight=15,
    ),
    ThemeRule(
        key="compliance",
        label="Compliance / Distress",
        description="重述、退市、调查、破产或控制缺陷。",
        form_types=("8-K", "10-K", "10-Q"),
        sec_items=("1.03", "3.01", "4.02"),
        keywords=("restatement", "material weakness", "investigation", "delisting", "bankruptcy", "non-compliance"),
        weight=18,
    ),
    ThemeRule(
        key="insider",
        label="Insider Activity",
        description="内部人买卖或激励相关披露。",
        form_types=("4",),
        keywords=("director", "officer", "stock option", "restricted stock", "sale", "purchase"),
        weight=8,
    ),
)

FORM_WEIGHTS = {
    "8-K": 16,
    "10-Q": 14,
    "10-K": 16,
    "4": 9,
    "S-1": 15,
    "S-3": 13,
    "424B5": 13,
    "DEF 14A": 10,
}

TIER_WEIGHTS = {
    1: 40,
    2: 24,
    3: 10,
}

IMPACT_WEIGHTS = {
    "利空": 14,
    "中性": 6,
    "利好": 10,
}

LEVELS = (
    (85, "Critical"),
    (68, "High"),
    (45, "Elevated"),
    (0, "Monitoring"),
)


def serialize_theme(theme: ThemeRule, score: int) -> dict:
    return {
        "id": theme.key,
        "label": theme.label,
        "description": theme.description,
        "score": score,
    }


def normalize_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    stripped = re.sub(r"<[^>]+>", " ", value)
    stripped = re.sub(r"\s+", " ", stripped).strip()
    return stripped


def classify_filing_themes(filing: Filing) -> list[dict]:
    text = " ".join(
        filter(
            None,
            [
                filing.title,
                clean_text(filing.raw_feed_summary),
                filing.analysis.summary_text if filing.analysis else "",
                " ".join(filing.analysis.key_takeaways) if filing.analysis else "",
            ],
        )
    ).lower()
    sec_items = set(filing.sec_items or [])
    form_type = (filing.form_type or "").upper()

    matches: list[dict] = []
    for rule in THEME_RULES:
        score = 0
        if form_type in {item.upper() for item in rule.form_types}:
            score += max(5, rule.weight // 3)
        if sec_items.intersection(rule.sec_items):
            score += max(6, rule.weight // 2)
        keyword_hits = sum(1 for keyword in rule.keywords if keyword in text)
        if keyword_hits:
            score += min(rule.weight, 4 * keyword_hits)
        if score > 0:
            matches.append(serialize_theme(rule, score))

    if not matches:
        matches.append(
            {
                "id": "general",
                "label": "General Disclosure",
                "description": "未命中特定主题规则，但仍属于真实 SEC 披露。",
                "score": 4,
            }
        )

    matches.sort(key=lambda item: item["score"], reverse=True)
    return matches[:3]


def compute_filing_signal_score(filing: Filing) -> int:
    published_at = normalize_timestamp(filing.published_at)
    days_old = max(0.0, (utc_now() - published_at).total_seconds() / 86400)
    recency_bonus = 18 if days_old <= 1 else 13 if days_old <= 3 else 8 if days_old <= 7 else 4 if days_old <= 14 else 1
    form_bonus = FORM_WEIGHTS.get(filing.form_type, 8)
    tier_bonus = TIER_WEIGHTS.get(filing.tier, 8)
    impact = filing.analysis.impact_label if filing.analysis else "中性"
    impact_bonus = IMPACT_WEIGHTS.get(impact, 6)
    theme_bonus = sum(theme["score"] for theme in classify_filing_themes(filing)[:2])
    analysis_bonus = 4 if filing.analysis else 0
    text_bonus = 3 if filing.raw_document_text else 0
    score = tier_bonus + form_bonus + impact_bonus + recency_bonus + analysis_bonus + text_bonus + min(12, theme_bonus // 2)
    return int(min(100, max(1, score)))


def _sentiment_label(negative: int, positive: int) -> str:
    if negative > positive:
        return "Defensive"
    if positive > negative:
        return "Constructive"
    return "Mixed"


def build_signal_index(filings: list[Filing], *, limit: int = 12) -> list[dict]:
    recent_cutoff = utc_now() - timedelta(days=30)
    by_ticker: dict[str, list[Filing]] = defaultdict(list)
    for filing in filings:
        if normalize_timestamp(filing.published_at) >= recent_cutoff:
            by_ticker[filing.ticker].append(filing)

    ranked: list[dict] = []
    for ticker, ticker_filings in by_ticker.items():
        ticker_filings.sort(key=lambda item: item.published_at, reverse=True)
        event_scores = [compute_filing_signal_score(filing) for filing in ticker_filings[:6]]
        max_event = max(event_scores) if event_scores else 0
        tier1_count = sum(1 for filing in ticker_filings if filing.tier == 1)
        negative_count = sum(1 for filing in ticker_filings if filing.analysis and filing.analysis.impact_label == "利空")
        positive_count = sum(1 for filing in ticker_filings if filing.analysis and filing.analysis.impact_label == "利好")
        breadth_bonus = min(24, len(ticker_filings) * 3 + tier1_count * 5 + negative_count * 3)
        score = int(min(100, round(max_event * 0.72 + breadth_bonus)))
        level = next(name for threshold, name in LEVELS if score >= threshold)
        company_name = ticker_filings[0].company_name
        top_themes = _top_theme_labels(ticker_filings)
        ranked.append(
            {
                "ticker": ticker,
                "company_name": company_name,
                "score": score,
                "level": level,
                "sentiment": _sentiment_label(negative_count, positive_count),
                "filings_count": len(ticker_filings),
                "tier1_count": tier1_count,
                "latest_form": ticker_filings[0].form_type,
                "latest_published_at": ticker_filings[0].published_at.isoformat(),
                "themes": top_themes,
                "rationale": build_ticker_rationale(ticker_filings, score, level, top_themes),
            }
        )

    ranked.sort(key=lambda item: (item["score"], item["tier1_count"], item["latest_published_at"]), reverse=True)
    return ranked[:limit]


def build_ticker_rationale(ticker_filings: list[Filing], score: int, level: str, themes: list[str]) -> str:
    latest = ticker_filings[0]
    theme_text = " / ".join(themes) if themes else "General Disclosure"
    return (
        f"{latest.ticker} 当前为 {level} 关注级别，近 30 天共有 {len(ticker_filings)} 条真实披露，"
        f"最新为 {latest.form_type}。主导主题：{theme_text}。"
    )


def _top_theme_labels(filings: list[Filing], *, limit: int = 3) -> list[str]:
    theme_scores: dict[str, int] = defaultdict(int)
    theme_labels: dict[str, str] = {}
    for filing in filings:
        for theme in classify_filing_themes(filing):
            theme_scores[theme["id"]] += int(theme["score"])
            theme_labels[theme["id"]] = str(theme["label"])
    ordered = sorted(theme_scores.items(), key=lambda item: item[1], reverse=True)
    return [theme_labels[key] for key, _ in ordered[:limit]]


def build_theme_clusters(filings: list[Filing], *, limit: int = 6) -> list[dict]:
    recent_cutoff = utc_now() - timedelta(days=21)
    themed: dict[str, dict] = {}
    for filing in filings:
        if normalize_timestamp(filing.published_at) < recent_cutoff:
            continue
        for theme in classify_filing_themes(filing)[:2]:
            entry = themed.setdefault(
                theme["id"],
                {
                    "id": theme["id"],
                    "label": theme["label"],
                    "description": theme["description"],
                    "score": 0,
                    "count": 0,
                    "tier1_count": 0,
                    "tickers": set(),
                    "latest_published_at": filing.published_at,
                    "highlights": [],
                },
            )
            entry["score"] += compute_filing_signal_score(filing) + int(theme["score"])
            entry["count"] += 1
            entry["tier1_count"] += 1 if filing.tier == 1 else 0
            entry["tickers"].add(filing.ticker)
            entry["latest_published_at"] = max(entry["latest_published_at"], filing.published_at)
            entry["highlights"].append(
                {
                    "id": filing.id,
                    "ticker": filing.ticker,
                    "form_type": filing.form_type,
                    "tier": filing.tier,
                    "impact": filing.analysis.impact_label if filing.analysis else "待分析",
                    "published_at": filing.published_at.isoformat(),
                    "summary": clean_text(filing.analysis.summary_text if filing.analysis else filing.raw_feed_summary or filing.title)[:180],
                }
            )

    clusters: list[dict] = []
    for cluster in themed.values():
        tickers = sorted(cluster["tickers"])
        highlights = sorted(
            cluster["highlights"],
            key=lambda item: (item["tier"], item["published_at"]),
            reverse=True,
        )[:3]
        clusters.append(
            {
                "id": cluster["id"],
                "label": cluster["label"],
                "description": cluster["description"],
                "score": min(100, int(cluster["score"])),
                "count": cluster["count"],
                "tier1_count": cluster["tier1_count"],
                "tickers": tickers,
                "latest_published_at": cluster["latest_published_at"].isoformat(),
                "summary": (
                    f"{cluster['label']} 主题近 21 天出现 {cluster['count']} 次，覆盖 "
                    f"{', '.join(tickers[:4])}{' 等' if len(tickers) > 4 else ''}。"
                ),
                "highlights": highlights,
            }
        )

    clusters.sort(key=lambda item: (item["score"], item["tier1_count"], item["latest_published_at"]), reverse=True)
    return clusters[:limit]


def build_sec_brief(filings: list[Filing]) -> dict:
    if not filings:
        return {
            "available": False,
            "title": "SEC Brief",
            "generated_at": utc_now().isoformat(),
            "summary": "数据库里还没有真实 SEC 披露，因此暂时无法生成简报。",
            "action_plan": "先运行 ingestion worker，让真实披露进入数据库。",
            "risk_watch": "暂无可判断的风险主题。",
            "highlights": [],
            "source_filing_ids": [],
            "mode": "rules-based",
        }

    sorted_filings = sorted(filings, key=lambda item: compute_filing_signal_score(item), reverse=True)
    fresh_cutoff = utc_now() - timedelta(days=3)
    fresh = [filing for filing in filings if normalize_timestamp(filing.published_at) >= fresh_cutoff] or filings[: min(10, len(filings))]
    highlights = sorted(sorted_filings[:5], key=lambda item: item.published_at, reverse=True)
    tier1_count = sum(1 for filing in fresh if filing.tier == 1)
    negative_count = sum(1 for filing in fresh if filing.analysis and filing.analysis.impact_label == "利空")
    positive_count = sum(1 for filing in fresh if filing.analysis and filing.analysis.impact_label == "利好")
    unique_tickers = sorted({filing.ticker for filing in fresh})
    dominant_themes = _top_theme_labels(sorted_filings[:10])

    headline = (
        f"过去 {max(1, math.ceil((utc_now() - min(normalize_timestamp(filing.published_at) for filing in fresh)).total_seconds() / 86400))} 天内，"
        f"数据库新增或保留在高关注区间的真实披露共 {len(fresh)} 条，涉及 {len(unique_tickers)} 个 ticker。"
    )

    if tier1_count:
        headline += f" 其中 Tier 1 有 {tier1_count} 条。"

    if dominant_themes:
        headline += f" 主导主题集中在 {' / '.join(dominant_themes[:3])}。"

    if negative_count > positive_count:
        action_plan = "优先复核融资、合规与治理类文件，确认是否需要调低风险暴露或更新投资假设。"
        risk_watch = "近期利空型披露偏多，重点关注融资摊薄、重述、退市风险和管理层变动。"
    elif positive_count > negative_count:
        action_plan = "优先跟进利好型经营更新，确认订单、指引或战略交易是否足以提升预期。"
        risk_watch = "虽然整体偏建设性，仍需警惕高估值标的在融资或指引回撤上的反身性。"
    else:
        action_plan = "保持事件驱动筛选，优先阅读 Tier 1 和主题聚集度最高的文件。"
        risk_watch = "当前市场信号偏混合，避免只依据单条 filing 下判断。"

    return {
        "available": True,
        "title": "SEC Brief",
        "generated_at": utc_now().isoformat(),
        "summary": headline,
        "action_plan": action_plan,
        "risk_watch": risk_watch,
        "highlights": [
            {
                "id": filing.id,
                "ticker": filing.ticker,
                "company_name": filing.company_name,
                "form_type": filing.form_type,
                "tier": filing.tier,
                "impact": filing.analysis.impact_label if filing.analysis else "待分析",
                "summary": clean_text(filing.analysis.summary_text if filing.analysis else filing.raw_feed_summary or filing.title),
                "published_at": filing.published_at.isoformat(),
            }
            for filing in highlights
        ],
        "source_filing_ids": [filing.id for filing in highlights],
        "mode": "rules-based",
    }


def build_feed_facets(filings: list[Filing]) -> dict:
    ticker_counts: dict[str, int] = defaultdict(int)
    form_counts: dict[str, int] = defaultdict(int)
    impact_counts: dict[str, int] = defaultdict(int)
    theme_counts: dict[str, int] = defaultdict(int)

    for filing in filings:
        ticker_counts[filing.ticker] += 1
        form_counts[filing.form_type] += 1
        impact = filing.analysis.impact_label if filing.analysis else "待分析"
        impact_counts[impact] += 1
        for theme in classify_filing_themes(filing):
            theme_counts[str(theme["id"])] += 1

    return {
        "tickers": [
            {"value": ticker, "count": count}
            for ticker, count in sorted(ticker_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
        "forms": [
            {"value": form_type, "count": count}
            for form_type, count in sorted(form_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
        "impacts": [
            {"value": impact, "count": count}
            for impact, count in sorted(impact_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
        "themes": [
            {"value": theme_id, "count": count}
            for theme_id, count in sorted(theme_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
    }


def filter_feed_filings(
    filings: list[Filing],
    *,
    query: str = "",
    tier: str = "all",
    ticker: str = "",
    form_type: str = "",
    impact: str = "all",
    theme: str = "all",
    tickers: tuple[str, ...] = (),
    limit: int = 80,
) -> list[Filing]:
    query_lower = query.strip().lower()
    ticker_upper = ticker.strip().upper()
    selected_tickers = {value.upper() for value in tickers if value.strip()}

    def matches(filing: Filing) -> bool:
        if tier != "all" and str(filing.tier) != str(tier):
            return False
        if ticker_upper and filing.ticker != ticker_upper:
            return False
        if selected_tickers and filing.ticker not in selected_tickers:
            return False
        if form_type and filing.form_type != form_type:
            return False
        filing_impact = filing.analysis.impact_label if filing.analysis else "待分析"
        if impact != "all" and filing_impact != impact:
            return False

        filing_themes = classify_filing_themes(filing)
        if theme != "all" and theme not in {item["id"] for item in filing_themes}:
            return False

        if query_lower:
            haystack = " ".join(
                filter(
                    None,
                    [
                        filing.ticker,
                        filing.company_name,
                        filing.title,
                        filing.form_type,
                        clean_text(filing.raw_feed_summary),
                        filing.analysis.summary_text if filing.analysis else "",
                        " ".join(filing.analysis.key_takeaways) if filing.analysis else "",
                        " ".join(item["label"] for item in filing_themes),
                    ],
                )
            ).lower()
            if query_lower not in haystack:
                tokens = [token for token in re.findall(r"[a-z0-9_.-]+", query_lower) if token]
                if not tokens or not all(token in haystack for token in tokens):
                    return False
        return True

    matched = [filing for filing in filings if matches(filing)]
    matched.sort(key=lambda item: (compute_filing_signal_score(item), item.published_at), reverse=True)
    return matched[:limit]
