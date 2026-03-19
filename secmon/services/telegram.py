from __future__ import annotations

import logging
from html import escape

import requests

from ..config import Settings
from ..models import AnalysisResult, Filing
from ..watchlist import SEC_ITEM_NOTES_YUE


logger = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.available = bool(settings.telegram_bot_token)
        self._resolved_chat_id = settings.telegram_chat_id

    def send_filing_alert(self, filing: Filing, analysis: AnalysisResult | None) -> None:
        if not self.available:
            raise RuntimeError("Telegram notifier is not configured.")

        emoji = {1: "RED", 2: "AMBER", 3: "GREEN"}.get(filing.tier, "INFO")
        notes = self._build_item_notes(filing.sec_items, filing.form_type)
        impact = analysis.impact_label if analysis else "待分析"
        summary = analysis.summary_text if analysis else "文件已入库，等待后续处理。"
        takeaways = analysis.key_takeaways if analysis else []

        takeaway_block = "\n".join(f"• {escape(item)}" for item in takeaways[:3]) or "• 暂无"
        items_display = ", ".join(filing.sec_items) if filing.sec_items else f"Form {filing.form_type}"

        message = (
            f"<b>{escape(filing.ticker)} | {escape(filing.company_name)}</b>\n"
            f"<b>Tier:</b> {filing.tier} / {escape(emoji)}\n"
            f"<b>Form:</b> {escape(filing.form_type)}\n"
            f"<b>Items:</b> {escape(items_display)}\n"
            f"<b>Impact:</b> {escape(impact)}\n"
            f"<b>Notes:</b>\n{escape(notes)}\n\n"
            f"<b>Summary:</b>\n{escape(summary)}\n\n"
            f"<b>Takeaways:</b>\n{takeaway_block}\n\n"
            f"<a href=\"{escape(filing.entry_link)}\">Open SEC Filing</a>"
        )

        chat_id = self._resolve_chat_id()
        url = f"https://api.telegram.org/bot{self.settings.telegram_bot_token}/sendMessage"
        response = requests.post(
            url,
            data={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=self.settings.request_timeout_seconds,
        )
        response.raise_for_status()

    def _resolve_chat_id(self) -> str:
        if self._resolved_chat_id:
            return self._resolved_chat_id

        updates_url = f"https://api.telegram.org/bot{self.settings.telegram_bot_token}/getUpdates"
        response = requests.get(
            updates_url,
            params={"limit": 100},
            timeout=self.settings.request_timeout_seconds,
        )

        if response.status_code == 409:
            raise RuntimeError(
                "Telegram getUpdates is unavailable because this bot is using a webhook. "
                "Set TELEGRAM_CHAT_ID manually or remove the webhook before using auto-discovery."
            )

        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram getUpdates failed: {payload}")

        candidates: list[tuple[int, str, str]] = []
        for update in payload.get("result", []):
            update_id = int(update.get("update_id", 0))
            chat = self._extract_chat(update)
            if not chat:
                continue

            chat_id = chat.get("id")
            if chat_id is None:
                continue

            label_parts = [
                chat.get("type", "unknown"),
                chat.get("title"),
                chat.get("username"),
                " ".join(part for part in [chat.get("first_name"), chat.get("last_name")] if part),
            ]
            label = " | ".join(part for part in label_parts if part)
            candidates.append((update_id, str(chat_id), label))

        if not candidates:
            raise RuntimeError(
                "Unable to auto-discover TELEGRAM_CHAT_ID. "
                "Send at least one message to the bot from the target chat, then retry."
            )

        candidates.sort(key=lambda item: item[0], reverse=True)
        update_id, chat_id, label = candidates[0]
        self._resolved_chat_id = chat_id
        logger.info(
            "Auto-discovered Telegram chat_id=%s from update_id=%s (%s). "
            "Set TELEGRAM_CHAT_ID to pin a specific chat.",
            chat_id,
            update_id,
            label or "unlabeled chat",
        )
        return chat_id

    def _extract_chat(self, update: dict) -> dict | None:
        direct_message_fields = (
            "message",
            "edited_message",
            "channel_post",
            "edited_channel_post",
            "my_chat_member",
            "chat_member",
            "chat_join_request",
        )
        for field in direct_message_fields:
            container = update.get(field)
            if isinstance(container, dict):
                chat = container.get("chat")
                if isinstance(chat, dict):
                    return chat

        callback_query = update.get("callback_query")
        if isinstance(callback_query, dict):
            message = callback_query.get("message")
            if isinstance(message, dict):
                chat = message.get("chat")
                if isinstance(chat, dict):
                    return chat

        return None

    def _build_item_notes(self, items: list[str], form_type: str) -> str:
        if items:
            lines = [f"{item}: {SEC_ITEM_NOTES_YUE.get(item, '需阅读原文确认')}" for item in items]
            return "\n".join(lines)

        if form_type == "10-K":
            return "年度报告"
        if form_type == "10-Q":
            return "季度报告"
        if form_type == "4":
            return "高管 / 内部人交易"
        if form_type in {"S-1", "S-3"}:
            return "证券发行 / 融资文件"
        return f"Form {form_type}"
