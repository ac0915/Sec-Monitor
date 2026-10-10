from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from html import escape
from typing import TYPE_CHECKING

import requests
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..config import Settings
from ..database import SessionLocal, init_db
from ..models import (
    AnalysisResult,
    Filing,
    IngestionRun,
    TelegramChat,
    TelegramMessage,
    TelegramState,
    utc_now,
)
from ..watchlist import SEC_ITEM_NOTES_YUE, TICKER_NAMES, WATCHLIST
from .analysis import BaseAnalysisService, build_analysis_service

if TYPE_CHECKING:
    from .llm_usage import LlmUsageService


logger = logging.getLogger(__name__)

MAX_TELEGRAM_MESSAGE_LENGTH = 3500
TELEGRAM_OFFSET_STATE_KEY = "telegram_updates_offset"
SUPPORTED_UPDATE_TYPES = [
    "message",
    "edited_message",
    "channel_post",
    "edited_channel_post",
]
SUPPORTED_CONTROL_COMMANDS = {
    "start",
    "help",
    "status",
    "subscribe",
    "unsubscribe",
    "stop",
}
WATCHLIST_SET = set(WATCHLIST) | set(TICKER_NAMES.keys())

ASSISTANT_SYSTEM_PROMPT = """
You are the SEC Monitor Telegram assistant.

Answer in Traditional Chinese using only the provided database-backed SEC filing context.
Requirements:
- Never invent filings, dates, numbers, prices, or sources.
- If the database context is insufficient, say so explicitly.
- Distinguish facts from inference when you infer.
- Keep the answer concise and useful for an investor or operator.
- End with a short "資料來源" section that cites the provided filing IDs.
- Do not claim to have searched the internet. Your source is the local SEC Monitor database context only.
""".strip()


@dataclass(slots=True)
class TelegramInboundEnvelope:
    update_id: int
    update_kind: str
    chat_payload: dict
    from_payload: dict
    message_payload: dict
    text: str
    message_id: str | None
    command_name: str | None
    command_args: str


class TelegramBotClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.available = bool(settings.telegram_bot_token)
        self.base_url = (
            f"https://api.telegram.org/bot{settings.telegram_bot_token}"
            if settings.telegram_bot_token
            else None
        )
        self.session = requests.Session()
        self._bot_profile: dict | None = None

    def get_updates(self, *, offset: int | None = None, limit: int | None = None, timeout: int | None = None) -> list[dict]:
        params: dict[str, str | int] = {
            "limit": limit or self.settings.telegram_updates_limit,
            "timeout": timeout if timeout is not None else self.settings.telegram_updates_timeout_seconds,
            "allowed_updates": json.dumps(SUPPORTED_UPDATE_TYPES),
        }
        if offset is not None:
            params["offset"] = offset
        return self._request(
            "GET",
            "getUpdates",
            params=params,
            timeout=max(
                self.settings.request_timeout_seconds,
                (timeout if timeout is not None else self.settings.telegram_updates_timeout_seconds) + 5,
            ),
        )

    def get_me(self) -> dict:
        if self._bot_profile is None:
            self._bot_profile = self._request("GET", "getMe", timeout=self.settings.request_timeout_seconds)
        return self._bot_profile

    def get_bot_username(self) -> str | None:
        profile = self.get_me()
        username = profile.get("username")
        if not username:
            return None
        return str(username)

    def send_message(
        self,
        *,
        chat_id: str,
        text: str,
        parse_mode: str | None = None,
        disable_web_page_preview: bool = True,
        reply_to_message_id: str | None = None,
    ) -> dict:
        payload: dict[str, str | bool] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_to_message_id:
            payload["reply_to_message_id"] = reply_to_message_id
        return self._request(
            "POST",
            "sendMessage",
            data=payload,
            timeout=self.settings.request_timeout_seconds,
        )

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict | None = None,
        data: dict | None = None,
        timeout: int,
    ) -> dict | list[dict]:
        if not self.available or not self.base_url:
            raise RuntimeError("Telegram bot token is not configured.")

        response = self.session.request(
            method,
            f"{self.base_url}/{endpoint}",
            params=params,
            data=data,
            timeout=timeout,
        )

        if response.status_code == 409 and endpoint == "getUpdates":
            raise RuntimeError(
                "Telegram getUpdates is unavailable because this bot is using a webhook. "
                "Use the webhook endpoint or remove the webhook before using long polling."
            )

        if not response.ok:
            raise RuntimeError(f"Telegram API error {response.status_code}: {response.text[:500]}")

        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram API returned ok=false for {endpoint}: {payload}")
        return payload.get("result", {})


class TelegramNotifier:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = TelegramBotClient(settings)
        self.available = self.client.available

    def send_filing_alert(self, db: Session, filing: Filing, analysis: AnalysisResult | None) -> int:
        if not self.available:
            raise RuntimeError("Telegram notifier is not configured.")

        recipients = self._resolve_recipients(db)
        if not recipients:
            raise RuntimeError(
                "No Telegram recipients are configured. "
                "Use /subscribe from Telegram or set TELEGRAM_CHAT_ID."
            )

        emoji = {1: "🔴", 2: "🟡", 3: "🟢"}.get(filing.tier, "INFO")
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
            f'<a href="{escape(filing.entry_link)}">Open SEC Filing</a>'
        )

        delivered = 0
        failures: list[str] = []
        for chat in recipients:
            try:
                response = self.client.send_message(
                    chat_id=chat.telegram_chat_id,
                    text=message,
                    parse_mode="HTML",
                )
            except Exception as exc:
                failures.append(f"{chat.telegram_chat_id}:{exc}")
                if "403" in str(exc):
                    chat.is_blocked = True
                continue

            chat.last_outbound_at = utc_now()
            db.add(
                TelegramMessage(
                    chat_id=chat.id,
                    telegram_message_id=str(response.get("message_id", "")) or None,
                    direction="outbound",
                    role="assistant",
                    command_name="alert",
                    content=message,
                    provider="sec_monitor",
                    model="filing_alert",
                    message_metadata={
                        "kind": "filing_alert",
                        "filing_id": filing.id,
                        "entry_link": filing.entry_link,
                    },
                )
            )
            delivered += 1

        db.flush()
        if delivered:
            return delivered

        detail = "; ".join(failures) if failures else "No recipient accepted the message."
        raise RuntimeError(detail)

    def send_system_notice(
        self,
        db: Session,
        *,
        title: str,
        body_lines: list[str],
        metadata: dict | None = None,
    ) -> int:
        if not self.available:
            raise RuntimeError("Telegram notifier is not configured.")

        recipients = self._resolve_recipients(db)
        if not recipients:
            raise RuntimeError("No Telegram recipients are configured.")

        message = (
            f"<b>{escape(title)}</b>\n"
            + "\n".join(escape(line) for line in body_lines if str(line).strip())
        ).strip()

        delivered = 0
        failures: list[str] = []
        for chat in recipients:
            try:
                response = self.client.send_message(
                    chat_id=chat.telegram_chat_id,
                    text=message,
                    parse_mode="HTML",
                )
            except Exception as exc:
                failures.append(f"{chat.telegram_chat_id}:{exc}")
                if "403" in str(exc):
                    chat.is_blocked = True
                continue

            chat.last_outbound_at = utc_now()
            db.add(
                TelegramMessage(
                    chat_id=chat.id,
                    telegram_message_id=str(response.get("message_id", "")) or None,
                    direction="outbound",
                    role="assistant",
                    command_name="system_notice",
                    content=message,
                    provider="sec_monitor",
                    model="system_notice",
                    message_metadata={
                        "kind": "system_notice",
                        **(metadata or {}),
                    },
                )
            )
            delivered += 1

        db.flush()
        if delivered:
            return delivered

        detail = "; ".join(failures) if failures else "No recipient accepted the message."
        raise RuntimeError(detail)

    def _resolve_recipients(self, db: Session) -> list[TelegramChat]:
        recipients = db.execute(
            select(TelegramChat)
            .where(TelegramChat.alerts_enabled.is_(True), TelegramChat.is_blocked.is_(False))
            .order_by(TelegramChat.updated_at.desc())
        ).scalars().all()
        
        if recipients:
            unique_chats = []
            seen_ids = set()
            for chat in recipients:
                if chat.telegram_chat_id not in seen_ids:
                    unique_chats.append(chat)
                    seen_ids.add(chat.telegram_chat_id)
            return unique_chats

        fallback = self._discover_default_chat()
        if fallback is None:
            return []

        chat_id, chat_payload = fallback
        existing = db.execute(
            select(TelegramChat).where(TelegramChat.telegram_chat_id == chat_id)
        ).scalar_one_or_none()
        if existing is not None:
            existing.alerts_enabled = True
            return [existing]

        chat = TelegramChat(
            telegram_chat_id=chat_id,
            chat_type=str(chat_payload.get("type", "unknown")),
            title=_string_or_none(chat_payload.get("title")),
            username=_string_or_none(chat_payload.get("username")),
            first_name=_string_or_none(chat_payload.get("first_name")),
            last_name=_string_or_none(chat_payload.get("last_name")),
            alerts_enabled=True,
            assistant_enabled=self.settings.telegram_assistant_enabled,
        )
        db.add(chat)
        db.flush()
        return [chat]

    def _discover_default_chat(self) -> tuple[str, dict] | None:
        if self.settings.telegram_chat_id:
            return self.settings.telegram_chat_id, {"id": self.settings.telegram_chat_id, "type": "unknown"}

        updates = self.client.get_updates(limit=100, timeout=0)
        candidates: list[tuple[int, str, dict]] = []
        for update in updates:
            update_id = int(update.get("update_id", 0))
            chat = extract_chat(update)
            if not chat or chat.get("id") is None:
                continue
            candidates.append((update_id, str(chat["id"]), chat))

        if not candidates:
            return None

        candidates.sort(key=lambda item: item[0], reverse=True)
        _, chat_id, chat_payload = candidates[0]
        logger.info("Auto-discovered Telegram chat_id=%s for alert delivery.", chat_id)
        return chat_id, chat_payload

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


class TelegramAssistantService:
    def __init__(
        self,
        settings: Settings,
        analysis_service: BaseAnalysisService | None = None,
        usage_service: LlmUsageService | None = None,
    ) -> None:
        self.settings = settings
        self.client = TelegramBotClient(settings)
        self.analysis_service = analysis_service or build_analysis_service(settings)
        self.usage_service = usage_service
        self.available = self.client.available and settings.telegram_assistant_enabled

    def sync_updates_once(self) -> dict:
        self._require_available()
        init_db()

        stats = {
            "updates_seen": 0,
            "processed": 0,
            "replied": 0,
            "ignored": 0,
            "duplicates": 0,
            "errors": 0,
        }
        offset = self._get_offset()
        updates = self.client.get_updates(offset=offset)

        for update in updates:
            stats["updates_seen"] += 1
            update_id = int(update.get("update_id", 0))
            result = self.process_update_payload(update)
            status = result.get("status", "processed")
            if status == "duplicate":
                stats["duplicates"] += 1
            elif status == "ignored":
                stats["ignored"] += 1
            elif status == "error":
                stats["errors"] += 1
            else:
                stats["processed"] += 1
                if result.get("replied"):
                    stats["replied"] += 1
            if update_id:
                self._set_offset(update_id + 1)

        return stats

    def process_update_payload(self, update: dict) -> dict:
        self._require_available()
        init_db()
        try:
            with SessionLocal() as db:
                result = self._process_update(db, update)
                db.commit()
                return result
        except Exception:
            logger.exception("Telegram update processing failed")
            return {"status": "error", "replied": False}

    def _process_update(self, db: Session, update: dict) -> dict:
        envelope = self._build_envelope(update)
        if envelope is None:
            return {"status": "ignored", "replied": False}

        if envelope.from_payload.get("is_bot"):
            return {"status": "ignored", "replied": False}

        existing = db.execute(
            select(TelegramMessage).where(TelegramMessage.telegram_update_id == str(envelope.update_id))
        ).scalar_one_or_none()
        if existing is not None:
            return {"status": "duplicate", "replied": False}

        chat = self._upsert_chat(db, envelope.chat_payload, envelope.from_payload)
        self._store_inbound_message(db, chat, envelope, update)

        if self.settings.telegram_allowed_chat_ids and chat.telegram_chat_id not in self.settings.telegram_allowed_chat_ids:
            self._send_reply(
                db,
                chat=chat,
                text="当前会话未被授权使用此机器人。请在服务端配置 TELEGRAM_ALLOWED_CHAT_IDS 后重试。",
                reply_to_message_id=envelope.message_id,
                command_name=envelope.command_name,
            )
            return {"status": "processed", "replied": True}

        if not envelope.text.strip():
            self._send_reply(
                db,
                chat=chat,
                text="目前只支持文本消息与指令。可发送 /help 查看可用命令。",
                reply_to_message_id=envelope.message_id,
                command_name=envelope.command_name,
            )
            return {"status": "processed", "replied": True}

        if chat.chat_type != "private" and not self._is_addressed_to_bot(envelope, chat):
            return {"status": "ignored", "replied": False}

        if not chat.assistant_enabled and (envelope.command_name or "") not in SUPPORTED_CONTROL_COMMANDS:
            self._send_reply(
                db,
                chat=chat,
                text="当前会话的助手已暂停。发送 /start 恢复，或发送 /help 查看控制命令。",
                reply_to_message_id=envelope.message_id,
                command_name=envelope.command_name,
            )
            return {"status": "processed", "replied": True}

        if envelope.command_name:
            response = self._handle_command(db, chat, envelope)
        else:
            response = self._answer_freeform_question(db, chat, envelope.text)

        self._send_reply(
            db,
            chat=chat,
            text=response["text"],
            reply_to_message_id=envelope.message_id,
            command_name=envelope.command_name,
            provider=response.get("provider"),
            model=response.get("model"),
            metadata=response.get("metadata"),
        )
        return {"status": "processed", "replied": True}

    def _handle_command(self, db: Session, chat: TelegramChat, envelope: TelegramInboundEnvelope) -> dict:
        command = envelope.command_name or ""
        args = envelope.command_args.strip()
        
        if command == "start":
            chat.assistant_enabled = True
            text = (
                "SEC Monitor 助手已启用。\n\n"
                "可用命令：\n"
                "/help 查看帮助\n"
                "/status 查看系统状态\n"
                "/subscribe 订阅高优先级提醒\n"
                "/unsubscribe 取消提醒\n"
                "/latest [数量] 查看最新真实披露\n"
                "/ticker <代码> [数量] 查看某 ticker 最近披露\n"
                "/search <关键词> 搜索真实披露\n"
                "/ask <问题> 基于数据库里的真实披露提问"
            )
            return {"text": text}

        if command == "stop":
            chat.assistant_enabled = False
            return {"text": "SEC Monitor 助手已暂停。发送 /start 可重新启用。"}

        if command == "help":
            return {
                "text": (
                    "可用命令：\n"
                    "/status\n"
                    "/subscribe\n"
                    "/unsubscribe\n"
                    "/latest [数量]\n"
                    "/ticker <代码> [数量]\n"
                    "/search <关键词>\n"
                    "/ask <问题>\n\n"
                    "也可以直接发送自然语言问题，例如：\n"
                    "“总结一下 NVDA 最近的 SEC 披露”\n"
                    "“找出最近涉及融资或稀释风险的文件”\n\n"
                    "所有回答只基于本地数据库里的真实 SEC 数据，不使用 demo 数据。"
                )
            }

        if command == "status":
            return {"text": self._build_status_text(db, chat)}

        if command == "subscribe":
            chat.alerts_enabled = True
            return {"text": "已开启该会话的高优先级 SEC 披露提醒。"}

        if command == "unsubscribe":
            chat.alerts_enabled = False
            return {"text": "已关闭该会话的高优先级 SEC 披露提醒。"}

        if command == "latest":
            limit = self._parse_optional_limit(args, default=5)
            filings = db.execute(
                select(Filing)
                .options(selectinload(Filing.analysis))
                .order_by(Filing.published_at.desc())
                .limit(limit)
            ).scalars().all()
            return {"text": self._format_filing_list("最新真实披露", filings)}

        if command == "ticker":
            parts = args.split()
            if not parts:
                return {"text": "用法：/ticker <代码> [数量]"}
            ticker = parts[0].upper()
            limit = self._parse_optional_limit(parts[1] if len(parts) > 1 else "", default=5)
            filings = db.execute(
                select(Filing)
                .options(selectinload(Filing.analysis))
                .where(Filing.ticker == ticker)
                .order_by(Filing.published_at.desc())
                .limit(limit)
            ).scalars().all()
            return {"text": self._format_filing_list(f"{ticker} 最近真实披露", filings)}

        if command == "search":
            if not args:
                return {"text": "用法：/search <关键词>"}
            filings = self._retrieve_context_filings(db, args, limit=self.settings.telegram_assistant_context_filings)
            return {"text": self._format_filing_list(f"搜索结果：{args}", filings)}

        if command == "ask":
            if not args:
                return {"text": "用法：/ask <问题>"}
            return self._answer_freeform_question(db, chat, args)

        return {"text": "未知命令。发送 /help 查看可用命令。"}

    def _answer_freeform_question(self, db: Session, chat: TelegramChat, question: str) -> dict:
        filings = self._retrieve_context_filings(
            db,
            question,
            limit=self.settings.telegram_assistant_context_filings,
        )
        if not filings:
            return {
                "text": (
                    "目前数据库里没有找到足够相关的真实 SEC 披露，无法可靠回答。\n\n"
                    "你可以尝试：\n"
                    "/latest 5\n"
                    "/ticker NVDA\n"
                    "/search 融资"
                )
            }

        if not self.analysis_service.available:
            return {
                "text": self._build_non_llm_answer(question, filings),
                "provider": self.analysis_service.provider_name,
                "model": self.analysis_service.selected_model,
                "metadata": {"llm_available": False, "filing_ids": [filing.id for filing in filings]},
            }
            
        history = self._load_recent_history(db, chat)
        prompt = self._build_assistant_prompt(question=question, history=history, filings=filings)

        try:
            completion = self.analysis_service.complete_text(
                system_prompt=ASSISTANT_SYSTEM_PROMPT,
                user_prompt=prompt,
            )
            answer = completion.text.strip()
            if self.usage_service is not None:
                self.usage_service.record_usage(
                    db,
                    provider=completion.provider,
                    model=completion.model,
                    feature="telegram_assistant",
                    usage=completion.usage,
                    raw_payload=completion.raw_payload,
                    metadata={"chat_id": chat.id, "filing_ids": [filing.id for filing in filings]},
                )
        except Exception as exc:
            logger.exception("Telegram assistant LLM request failed")
            answer = (
                f"LLM 处理失败：{exc}\n\n"
                "下面先返回数据库里最相关的真实披露：\n\n"
                f"{self._format_filing_list('相关披露', filings)}"
            )
            return {
                "text": answer,
                "provider": self.analysis_service.provider_name,
                "model": self.analysis_service.selected_model,
                "metadata": {"error": str(exc), "filing_ids": [filing.id for filing in filings]},
            }

        sources = self._format_sources(filings)
        if "資料來源" not in answer:
            answer = f"{answer}\n\n資料來源\n{sources}"
        return {
            "text": answer,
            "provider": completion.provider,
            "model": completion.model,
            "metadata": {"filing_ids": [filing.id for filing in filings]},
        }

    def _build_status_text(self, db: Session, chat: TelegramChat) -> str:
        total_filings = db.scalar(select(func.count(Filing.id))) or 0
        analyzed = db.scalar(select(func.count(AnalysisResult.id))) or 0
        alert_subscribers = db.scalar(
            select(func.count(TelegramChat.id)).where(TelegramChat.alerts_enabled.is_(True))
        ) or 0
        assistant_chats = db.scalar(
            select(func.count(TelegramChat.id)).where(TelegramChat.assistant_enabled.is_(True))
        ) or 0
        last_run = db.execute(select(IngestionRun).order_by(IngestionRun.started_at.desc()).limit(1)).scalar_one_or_none()

        last_run_line = "最近一次采集：暂无记录"
        if last_run is not None:
            last_run_line = (
                f"最近一次采集：{last_run.status} | "
                f"started={last_run.started_at.isoformat()} | new={last_run.new_filings}"
            )

        provider_status = (
            f"{self.analysis_service.provider_name}/{self.analysis_service.selected_model}"
            if self.analysis_service.available
            else f"{self.analysis_service.provider_name} 未就绪"
        )

        return (
            "SEC Monitor 状态\n"
            f"真实披露总数：{total_filings}\n"
            f"已分析文件：{analyzed}\n"
            f"提醒订阅会话：{alert_subscribers}\n"
            f"助手启用会话：{assistant_chats}\n"
            f"当前会话提醒：{'开启' if chat.alerts_enabled else '关闭'}\n"
            f"当前会话助手：{'开启' if chat.assistant_enabled else '关闭'}\n"
            f"LLM Provider：{provider_status}\n"
            f"{last_run_line}"
        )

    def _build_non_llm_answer(self, question: str, filings: list[Filing]) -> str:
        return (
            f"当前未配置可用 LLM，先返回与问题最相关的真实披露。\n"
            f"问题：{question}\n\n"
            f"{self._format_filing_list('相关披露', filings)}"
        )

    def _build_assistant_prompt(
        self,
        *,
        question: str,
        history: list[TelegramMessage],
        filings: list[Filing],
    ) -> str:
        history_block = "无"
        if history:
            lines: list[str] = []
            for message in history[-self.settings.telegram_assistant_history_messages :]:
                role = "用户" if message.direction == "inbound" else "助手"
                lines.append(f"{role}: {message.content}")
            history_block = "\n".join(lines)

        filing_blocks: list[str] = []
        for filing in filings:
            analysis_summary = filing.analysis.summary_text if filing.analysis else ""
            takeaways = " | ".join(filing.analysis.key_takeaways) if filing.analysis else ""
            filing_blocks.append(
                "\n".join(
                    [
                        f"filing_id: {filing.id}",
                        f"ticker: {filing.ticker}",
                        f"company_name: {filing.company_name}",
                        f"published_at: {filing.published_at.isoformat()}",
                        f"form_type: {filing.form_type}",
                        f"tier: {filing.tier}",
                        f"sec_items: {', '.join(filing.sec_items) if filing.sec_items else 'N/A'}",
                        f"title: {filing.title}",
                        f"feed_summary: {filing.raw_feed_summary or ''}",
                        f"analysis_summary: {analysis_summary}",
                        f"analysis_takeaways: {takeaways}",
                        f"entry_link: {filing.entry_link}",
                    ]
                )
            )

        return (
            f"用户问题:\n{question}\n\n"
            f"最近对话:\n{history_block}\n\n"
            "来自本地数据库的真实 SEC 披露上下文:\n"
            f"{chr(10).join(filing_blocks)}\n\n"
            "请直接回答用户问题。如果证据不足，明确说数据库里没有足够信息。"
        )

    def _retrieve_context_filings(self, db: Session, query: str, limit: int) -> list[Filing]:
        candidates = db.execute(
            select(Filing)
            .options(selectinload(Filing.analysis))
            .order_by(Filing.published_at.desc())
            .limit(250)
        ).scalars().all()

        query_lower = query.lower().strip()
        if not query_lower:
            return candidates[:limit]

        tokens = [token for token in re.findall(r"[A-Za-z0-9_.-]+", query_lower) if len(token) >= 2]
        direct_tickers = {token.upper() for token in tokens if token.upper() in WATCHLIST_SET}
        for ticker, company_name in TICKER_NAMES.items():
            if company_name.lower() in query_lower:
                direct_tickers.add(ticker)

        scored: list[tuple[int, Filing]] = []
        for filing in candidates:
            haystack_parts = [
                filing.ticker,
                filing.company_name,
                filing.title,
                filing.form_type,
                filing.raw_feed_summary or "",
                " ".join(filing.sec_items),
                filing.analysis.summary_text if filing.analysis else "",
                " ".join(filing.analysis.key_takeaways) if filing.analysis else "",
            ]
            haystack = " ".join(haystack_parts).lower()
            score = 0

            if filing.ticker in direct_tickers:
                score += 30
            if query_lower in haystack:
                score += 20
            for token in tokens:
                if token == filing.ticker.lower():
                    score += 12
                elif token in haystack:
                    score += 4

            if score > 0:
                scored.append((score, filing))

        if not scored and direct_tickers:
            filtered = [filing for filing in candidates if filing.ticker in direct_tickers]
            return filtered[:limit]

        scored.sort(key=lambda item: (item[0], item[1].published_at), reverse=True)
        unique: list[Filing] = []
        seen_ids: set[int] = set()
        for _, filing in scored:
            if filing.id in seen_ids:
                continue
            seen_ids.add(filing.id)
            unique.append(filing)
            if len(unique) >= limit:
                break
        return unique

    def _load_recent_history(self, db: Session, chat: TelegramChat) -> list[TelegramMessage]:
        messages = db.execute(
            select(TelegramMessage)
            .where(
                TelegramMessage.chat_id == chat.id,
                TelegramMessage.command_name.is_distinct_from("alert"),
            )
            .order_by(TelegramMessage.created_at.desc())
            .limit(self.settings.telegram_assistant_history_messages + 1)
        ).scalars().all()
        return list(reversed(messages))

    def _format_filing_list(self, title: str, filings: list[Filing]) -> str:
        if not filings:
            return f"{title}\n暂无符合条件的真实披露。"

        lines = [title]
        for index, filing in enumerate(filings, start=1):
            summary = filing.analysis.summary_text if filing.analysis else filing.raw_feed_summary or "暂无摘要"
            summary = normalize_space(summary)
            if len(summary) > 180:
                summary = f"{summary[:177]}..."
            lines.append(
                (
                    f"{index}. {filing.ticker} | {filing.form_type} | Tier {filing.tier} | "
                    f"{filing.published_at.date().isoformat()} | filing_id={filing.id}\n"
                    f"{summary}\n"
                    f"{filing.entry_link}"
                )
            )
        return "\n\n".join(lines)

    def _format_sources(self, filings: list[Filing]) -> str:
        return "\n".join(
            f"- filing_id={filing.id} | {filing.ticker} | {filing.form_type} | {filing.published_at.date().isoformat()}"
            for filing in filings
        )

    def _send_reply(
        self,
        db: Session,
        *,
        chat: TelegramChat,
        text: str,
        reply_to_message_id: str | None,
        command_name: str | None,
        provider: str | None = None,
        model: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        parts = split_telegram_message(text, MAX_TELEGRAM_MESSAGE_LENGTH)
        total_parts = len(parts)
        for index, part in enumerate(parts, start=1):
            response = self.client.send_message(
                chat_id=chat.telegram_chat_id,
                text=part,
                reply_to_message_id=reply_to_message_id if index == 1 else None,
            )
            chat.last_outbound_at = utc_now()
            db.add(
                TelegramMessage(
                    chat_id=chat.id,
                    telegram_message_id=str(response.get("message_id", "")) or None,
                    direction="outbound",
                    role="assistant",
                    command_name=command_name,
                    content=part,
                    provider=provider,
                    model=model,
                    message_metadata={
                        **(metadata or {}),
                        "part_index": index,
                        "part_count": total_parts,
                    },
                )
            )
        db.flush()

    def _store_inbound_message(
        self,
        db: Session,
        chat: TelegramChat,
        envelope: TelegramInboundEnvelope,
        raw_update: dict,
    ) -> None:
        chat.last_inbound_at = utc_now()
        db.add(
            TelegramMessage(
                chat_id=chat.id,
                telegram_update_id=str(envelope.update_id),
                telegram_message_id=envelope.message_id,
                direction="inbound",
                role="user",
                command_name=envelope.command_name,
                content=envelope.text,
                message_metadata={
                    "update_kind": envelope.update_kind,
                    "chat_id": chat.telegram_chat_id,
                    "raw_update": raw_update,
                },
            )
        )
        db.flush()

    def _upsert_chat(self, db: Session, chat_payload: dict, from_payload: dict) -> TelegramChat:
        telegram_chat_id = str(chat_payload.get("id"))
        chat = db.execute(
            select(TelegramChat).where(TelegramChat.telegram_chat_id == telegram_chat_id)
        ).scalar_one_or_none()

        if chat is None:
            chat = TelegramChat(
                telegram_chat_id=telegram_chat_id,
                chat_type=str(chat_payload.get("type", "unknown")),
                alerts_enabled=telegram_chat_id == (self.settings.telegram_chat_id or ""),
                assistant_enabled=self.settings.telegram_assistant_enabled,
            )
            db.add(chat)

        chat.chat_type = str(chat_payload.get("type", chat.chat_type or "unknown"))
        chat.title = _string_or_none(chat_payload.get("title"))
        chat.username = _string_or_none(chat_payload.get("username"))
        chat.first_name = _string_or_none(chat_payload.get("first_name") or from_payload.get("first_name"))
        chat.last_name = _string_or_none(chat_payload.get("last_name") or from_payload.get("last_name"))
        chat.language_code = _string_or_none(from_payload.get("language_code"))
        db.flush()
        return chat

    def _build_envelope(self, update: dict) -> TelegramInboundEnvelope | None:
        update_id = int(update.get("update_id", 0))
        if update_id <= 0:
            return None

        for kind in SUPPORTED_UPDATE_TYPES:
            message = update.get(kind)
            if not isinstance(message, dict):
                continue

            chat_payload = message.get("chat")
            if not isinstance(chat_payload, dict):
                continue

            text = str(message.get("text") or message.get("caption") or "").strip()
            command_name, command_args = parse_command(text)
            return TelegramInboundEnvelope(
                update_id=update_id,
                update_kind=kind,
                chat_payload=chat_payload,
                from_payload=message.get("from", {}) if isinstance(message.get("from"), dict) else {},
                message_payload=message,
                text=text,
                message_id=str(message.get("message_id", "")) or None,
                command_name=command_name,
                command_args=command_args,
            )
        return None

    def _is_addressed_to_bot(self, envelope: TelegramInboundEnvelope, chat: TelegramChat) -> bool:
        if envelope.command_name:
            return True

        username = self.client.get_bot_username()
        text_lower = envelope.text.lower()
        if username and f"@{username.lower()}" in text_lower:
            return True

        reply_to = envelope.message_payload.get("reply_to_message")
        if isinstance(reply_to, dict):
            from_payload = reply_to.get("from")
            if isinstance(from_payload, dict) and from_payload.get("is_bot"):
                return True

        return chat.chat_type == "private"

    def _get_offset(self) -> int | None:
        with SessionLocal() as db:
            state = db.execute(
                select(TelegramState).where(TelegramState.key == TELEGRAM_OFFSET_STATE_KEY)
            ).scalar_one_or_none()
            if state is None:
                return None
            try:
                return int(state.value)
            except ValueError:
                return None

    def _set_offset(self, value: int) -> None:
        with SessionLocal() as db:
            state = db.execute(
                select(TelegramState).where(TelegramState.key == TELEGRAM_OFFSET_STATE_KEY)
            ).scalar_one_or_none()
            if state is None:
                db.add(TelegramState(key=TELEGRAM_OFFSET_STATE_KEY, value=str(value)))
            else:
                state.value = str(value)
            db.commit()

    def _parse_optional_limit(self, raw: str, *, default: int) -> int:
        try:
            value = int(raw.strip()) if raw.strip() else default
        except ValueError:
            value = default
        return max(1, min(value, 10))

    def _require_available(self) -> None:
        if self.available:
            return
        if not self.client.available:
            raise RuntimeError("Telegram bot token is not configured.")
        raise RuntimeError("Telegram assistant is disabled.")


def extract_chat(update: dict) -> dict | None:
    for kind in SUPPORTED_UPDATE_TYPES:
        message = update.get(kind)
        if isinstance(message, dict):
            chat = message.get("chat")
            if isinstance(chat, dict):
                return chat
    return None


def parse_command(text: str) -> tuple[str | None, str]:
    match = re.match(r"^/([A-Za-z0-9_]+)(?:@[\\w_]+)?(?:\\s+(.*))?$", text.strip())
    if not match:
        return None, ""
    return match.group(1).lower(), (match.group(2) or "").strip()


def split_telegram_message(text: str, limit: int) -> list[str]:
    normalized = text.strip()
    if not normalized:
        return [""]
    if len(normalized) <= limit:
        return [normalized]

    chunks: list[str] = []
    remaining = normalized
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit)
        if split_at < limit // 2:
            split_at = remaining.rfind(" ", 0, limit)
        if split_at < limit // 2:
            split_at = limit
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None