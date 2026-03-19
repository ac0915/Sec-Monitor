from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import requests
from ..config import Settings


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AnalysisPayload:
    provider: str
    model: str
    impact_label: str
    summary_text: str
    key_takeaways: list[str]
    raw_payload: dict


@dataclass(slots=True)
class CompletionPayload:
    provider: str
    model: str
    text: str
    raw_payload: dict


PROMPT_SYSTEM = """
You are a senior sell-side equity analyst.

Read SEC filings and produce a compact, decision-useful answer in Traditional Chinese.
Return exactly one JSON object and do not wrap it in markdown fences.
""".strip()


PROMPT_USER_TEMPLATE = """
Schema:
{{
  "impact": "利好 | 利空 | 中性",
  "summary": "1-2 sentence concise summary",
  "key_takeaways": ["bullet 1", "bullet 2", "bullet 3"]
}}

Rules:
- Focus on what changes for investors.
- Highlight financing, revenue, cash flow, debt, governance, leadership, risks, and opportunities.
- Ignore boilerplate.
- Keep the tone precise and compact.

Ticker: {ticker}
Company: {company_name}
Form Type: {form_type}
Document:
{document_text}
""".strip()


class BaseAnalysisService:
    provider_name = "disabled"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.available = False
        self.selected_model = settings.analysis_model or ""
        self.unavailable_reason = "Analysis provider is not configured."

    def analyze(self, *, ticker: str, company_name: str, form_type: str, document_text: str) -> AnalysisPayload:
        completion = self.complete_text(
            system_prompt=PROMPT_SYSTEM,
            user_prompt=build_prompt_user_message(
                ticker=ticker,
                company_name=company_name,
                form_type=form_type,
                document_text=document_text,
            ),
            json_mode=True,
        )
        return self._build_analysis_payload(completion.text, raw_payload=completion.raw_payload)

    def complete_text(self, *, system_prompt: str, user_prompt: str, json_mode: bool = False) -> CompletionPayload:
        raise NotImplementedError

    def _build_analysis_payload(self, response_text: str, raw_payload: dict) -> AnalysisPayload:
        parsed = extract_json_object(response_text)
        impact = str(parsed.get("impact", "中性")).strip() or "中性"
        summary = str(parsed.get("summary", "")).strip() or "模型未返回有效摘要。"
        takeaways = parsed.get("key_takeaways", [])
        if not isinstance(takeaways, list):
            takeaways = []
        clean_takeaways = [str(item).strip() for item in takeaways if str(item).strip()]

        return AnalysisPayload(
            provider=self.provider_name,
            model=self.selected_model,
            impact_label=impact,
            summary_text=summary,
            key_takeaways=clean_takeaways[:5],
            raw_payload={"raw_response": response_text, "parsed_response": parsed, **raw_payload},
        )

    def _require_available(self) -> None:
        if self.available:
            return
        raise RuntimeError(self.unavailable_reason)


class DisabledAnalysisService(BaseAnalysisService):
    def __init__(self, settings: Settings, provider_name: str, reason: str) -> None:
        super().__init__(settings)
        self.provider_name = provider_name
        self.unavailable_reason = reason

    def complete_text(self, *, system_prompt: str, user_prompt: str, json_mode: bool = False) -> CompletionPayload:
        self._require_available()
        raise RuntimeError(self.unavailable_reason)


class GeminiAnalysisService(BaseAnalysisService):
    provider_name = "gemini"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.selected_model = settings.analysis_model or settings.gemini_model
        self.model = None

        if not settings.gemini_api_key:
            self.unavailable_reason = "GEMINI_API_KEY is not set."
            return

        try:
            import google.generativeai as genai
        except ImportError:
            logger.warning("google-generativeai is not installed; Gemini analysis disabled.")
            self.unavailable_reason = "google-generativeai is not installed."
            return

        genai.configure(api_key=settings.gemini_api_key)
        self.model = genai.GenerativeModel(self.selected_model)
        self.available = True

    def complete_text(self, *, system_prompt: str, user_prompt: str, json_mode: bool = False) -> CompletionPayload:
        self._require_available()
        if self.model is None:
            raise RuntimeError("Gemini client is not initialized.")

        prompt = "\n\n".join([system_prompt, user_prompt])

        response = self.model.generate_content(prompt)
        response_text = getattr(response, "text", "") or ""
        return CompletionPayload(
            provider=self.provider_name,
            model=self.selected_model,
            text=response_text,
            raw_payload={},
        )


class ChatCompletionsAnalysisService(BaseAnalysisService):
    def __init__(
        self,
        settings: Settings,
        *,
        provider_name: str,
        api_key: str | None,
        model: str,
        endpoint: str,
        extra_headers: dict[str, str] | None = None,
        use_json_object_response: bool = False,
    ) -> None:
        super().__init__(settings)
        self.provider_name = provider_name
        self.selected_model = settings.analysis_model or model
        self.endpoint = endpoint
        self.use_json_object_response = use_json_object_response
        self.session = requests.Session()
        self.extra_headers = extra_headers or {}

        if not api_key:
            self.unavailable_reason = f"{provider_name} API key is not configured."
            return

        self.api_key = api_key
        self.available = True

    def complete_text(self, *, system_prompt: str, user_prompt: str, json_mode: bool = False) -> CompletionPayload:
        self._require_available()

        payload: dict = {
            "model": self.selected_model,
            "messages": build_messages(system_prompt=system_prompt, user_prompt=user_prompt),
            "stream": False,
            "temperature": self.settings.analysis_temperature,
            "max_tokens": self.settings.analysis_max_tokens,
        }
        if json_mode and self.use_json_object_response:
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            **self.extra_headers,
        }
        response = self.session.post(
            self.endpoint,
            headers=headers,
            json=payload,
            timeout=self.settings.analysis_timeout_seconds,
        )
        if not response.ok:
            raise RuntimeError(f"{self.provider_name} API error {response.status_code}: {response.text[:500]}")

        data = response.json()
        response_text = extract_chat_completion_text(data)
        return CompletionPayload(
            provider=self.provider_name,
            model=self.selected_model,
            text=response_text,
            raw_payload={"http_response": data},
        )


class DeepSeekAnalysisService(ChatCompletionsAnalysisService):
    def __init__(self, settings: Settings) -> None:
        super().__init__(
            settings,
            provider_name="deepseek",
            api_key=settings.deepseek_api_key,
            model=settings.deepseek_model,
            endpoint=f"{settings.deepseek_base_url.rstrip('/')}/chat/completions",
            use_json_object_response=True,
        )


class GrokAnalysisService(ChatCompletionsAnalysisService):
    def __init__(self, settings: Settings) -> None:
        super().__init__(
            settings,
            provider_name="grok",
            api_key=settings.xai_api_key,
            model=settings.xai_model,
            endpoint=f"{settings.xai_base_url.rstrip('/')}/chat/completions",
        )


class GitHubModelsAnalysisService(ChatCompletionsAnalysisService):
    def __init__(self, settings: Settings) -> None:
        base_url = settings.github_models_base_url.rstrip("/")
        if settings.github_models_org:
            endpoint = f"{base_url}/orgs/{settings.github_models_org}/inference/chat/completions"
        else:
            endpoint = f"{base_url}/inference/chat/completions"

        super().__init__(
            settings,
            provider_name="github",
            api_key=settings.github_models_token,
            model=settings.github_models_model,
            endpoint=endpoint,
            extra_headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": settings.github_models_api_version,
            },
            use_json_object_response=True,
        )


class CopilotSdkAnalysisService(BaseAnalysisService):
    provider_name = "copilot"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.selected_model = settings.analysis_model or settings.copilot_model
        self.node_binary = settings.copilot_node_binary
        self.script_path = Path(__file__).resolve().parents[2] / "scripts" / "copilot_analyze.mjs"

        if shutil.which(self.node_binary) is None:
            self.unavailable_reason = f"Node.js binary '{self.node_binary}' was not found."
            return
        if shutil.which("copilot") is None:
            self.unavailable_reason = (
                "Copilot CLI binary 'copilot' was not found. "
                "Install it first, for example with `npm install -g @github/copilot`."
            )
            return
        if not self.script_path.exists():
            self.unavailable_reason = f"Copilot helper script not found: {self.script_path}"
            return

        self.available = True

    def complete_text(self, *, system_prompt: str, user_prompt: str, json_mode: bool = False) -> CompletionPayload:
        self._require_available()

        request_payload = {
            "model": self.selected_model,
            "prompt": "\n\n".join([system_prompt, user_prompt]),
        }

        result = subprocess.run(
            [self.node_binary, str(self.script_path)],
            input=json.dumps(request_payload),
            text=True,
            capture_output=True,
            timeout=self.settings.analysis_timeout_seconds,
            env=os.environ.copy(),
            check=False,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(f"Copilot SDK invocation failed: {stderr}")

        data = json.loads(result.stdout)
        response_text = str(data.get("content", "")).strip()
        return CompletionPayload(
            provider=self.provider_name,
            model=self.selected_model,
            text=response_text,
            raw_payload={"copilot_response": data},
        )


def build_prompt_user_message(*, ticker: str, company_name: str, form_type: str, document_text: str) -> str:
    return PROMPT_USER_TEMPLATE.format(
        ticker=ticker,
        company_name=company_name,
        form_type=form_type,
        document_text=document_text,
    )


def build_chat_messages(*, ticker: str, company_name: str, form_type: str, document_text: str) -> list[dict[str, str]]:
    return build_messages(
        system_prompt=PROMPT_SYSTEM,
        user_prompt=build_prompt_user_message(
            ticker=ticker,
            company_name=company_name,
            form_type=form_type,
            document_text=document_text,
        ),
    )


def build_messages(*, system_prompt: str, user_prompt: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def extract_chat_completion_text(data: dict) -> str:
    choices = data.get("choices", [])
    if not choices:
        return ""

    message = choices[0].get("message", {})
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if "text" in item:
                    texts.append(str(item.get("text", "")))
                elif item.get("type") == "output_text":
                    texts.append(str(item.get("text", "")))
            elif isinstance(item, str):
                texts.append(item)
        return "\n".join(text for text in texts if text).strip()
    return str(content).strip()


def extract_json_object(text: str) -> dict:
    if not text.strip():
        return {}

    direct = text.strip()
    try:
        return json.loads(direct)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return {"summary": text.strip()}

    snippet = match.group(0)
    try:
        return json.loads(snippet)
    except json.JSONDecodeError:
        logger.warning("Model returned non-JSON payload; storing fallback response.")
        return {"summary": text.strip()}


def build_analysis_service(settings: Settings) -> BaseAnalysisService:
    provider = normalize_provider_name(settings.analysis_provider)
    if provider == "gemini":
        return GeminiAnalysisService(settings)
    if provider == "deepseek":
        return DeepSeekAnalysisService(settings)
    if provider == "grok":
        return GrokAnalysisService(settings)
    if provider == "copilot":
        return CopilotSdkAnalysisService(settings)
    if provider == "github":
        return GitHubModelsAnalysisService(settings)

    return DisabledAnalysisService(
        settings,
        provider_name=settings.analysis_provider,
        reason=(
            "Unsupported ANALYSIS_PROVIDER. "
            "Use one of: gemini, deepseek, grok, copilot, github, github_models."
        ),
    )


def normalize_provider_name(provider_name: str) -> str:
    normalized = provider_name.strip().lower()
    aliases = {
        "xai": "grok",
        "grok": "grok",
        "deepseek": "deepseek",
        "gemini": "gemini",
        "copilot": "copilot",
        "github_copilot": "copilot",
        "github-copilot": "copilot",
        "github": "github",
        "github_models": "github",
        "github-models": "github",
    }
    return aliases.get(normalized, normalized)
