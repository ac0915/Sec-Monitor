from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
    usage: UsageMetrics | None
    raw_payload: dict


@dataclass(slots=True)
class CompletionPayload:
    provider: str
    model: str
    text: str
    usage: UsageMetrics | None
    raw_payload: dict


@dataclass(slots=True)
class ModelCatalogEntry:
    id: str
    label: str
    description: str | None
    capabilities: list[str]
    raw_payload: dict[str, Any]


@dataclass(slots=True)
class UsageMetrics:
    requests: int = 1
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_tokens: int | None = None
    cost_usd: float | None = None
    extra_metrics: dict[str, Any] | None = None


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
        return self._build_analysis_payload(completion.text, usage=completion.usage, raw_payload=completion.raw_payload)

    def complete_text(self, *, system_prompt: str, user_prompt: str, json_mode: bool = False) -> CompletionPayload:
        raise NotImplementedError

    def _build_analysis_payload(self, response_text: str, usage: UsageMetrics | None, raw_payload: dict) -> AnalysisPayload:
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
            usage=usage,
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
        self.client = None
        self.types = None

        if not settings.gemini_api_key:
            self.unavailable_reason = "GEMINI_API_KEY is not set."
            return

        try:
            from google import genai
            from google.genai import types
        except ImportError:
            logger.warning("google-genai is not installed; Gemini analysis disabled.")
            self.unavailable_reason = "google-genai is not installed."
            return

        self.client = genai.Client(api_key=settings.gemini_api_key)
        self.types = types
        self.available = True

    def complete_text(self, *, system_prompt: str, user_prompt: str, json_mode: bool = False) -> CompletionPayload:
        self._require_available()
        if self.client is None or self.types is None:
            raise RuntimeError("Gemini client is not initialized.")

        config = self.types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=self.settings.analysis_temperature,
            max_output_tokens=self.settings.analysis_max_tokens,
            response_mime_type="application/json" if json_mode else None,
        )
        response = self.client.models.generate_content(
            model=self.selected_model,
            contents=user_prompt,
            config=config,
        )
        response_text = getattr(response, "text", "") or ""
        usage = _extract_gemini_usage(response)
        return CompletionPayload(
            provider=self.provider_name,
            model=self.selected_model,
            text=response_text,
            usage=usage,
            raw_payload={
                "response_id": getattr(response, "response_id", None),
                "model_version": getattr(response, "model_version", None),
                "usage": _usage_metrics_dict(usage),
            },
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
            usage=_extract_openai_usage(data.get("usage")),
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
            usage=_extract_copilot_usage(data.get("usage")),
            raw_payload={"copilot_response": data},
        )


def fetch_provider_model_catalog(settings: Settings, provider_name: str) -> dict[str, Any]:
    provider = normalize_provider_name(provider_name)
    try:
        if provider == "gemini":
            source = "google-genai"
            models = _fetch_gemini_model_catalog(settings)
        elif provider == "deepseek":
            source = "deepseek-api"
            models = _fetch_deepseek_model_catalog(settings)
        elif provider == "grok":
            source = "xai-api"
            models = _fetch_grok_model_catalog(settings)
        elif provider == "github":
            source = "github-models-catalog"
            models = _fetch_github_model_catalog(settings)
        elif provider == "copilot":
            source = "copilot-sdk"
            models = _fetch_copilot_model_catalog(settings)
        else:
            raise RuntimeError(f"Unsupported provider '{provider_name}'.")
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(str(exc) or f"Failed to fetch model catalog for {provider}.") from exc

    selected_model = _selected_model_for_provider(settings, provider)
    serialized_models = [serialize_model_catalog_entry(entry) for entry in _sort_model_catalog(models)]
    return {
        "provider": provider,
        "source": source,
        "selected_model": selected_model,
        "models": serialized_models,
        "count": len(serialized_models),
    }


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


def serialize_model_catalog_entry(entry: ModelCatalogEntry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "label": entry.label,
        "description": entry.description,
        "capabilities": entry.capabilities,
    }


def _selected_model_for_provider(settings: Settings, provider: str) -> str:
    if provider == "gemini":
        return settings.analysis_model or settings.gemini_model
    if provider == "deepseek":
        return settings.analysis_model or settings.deepseek_model
    if provider == "grok":
        return settings.analysis_model or settings.xai_model
    if provider == "github":
        return settings.analysis_model or settings.github_models_model
    if provider == "copilot":
        return settings.analysis_model or settings.copilot_model
    return settings.analysis_model or ""


def _sort_model_catalog(entries: list[ModelCatalogEntry]) -> list[ModelCatalogEntry]:
    seen: set[str] = set()
    ordered: list[ModelCatalogEntry] = []
    for entry in sorted(entries, key=lambda item: (item.label.lower(), item.id.lower())):
        if entry.id in seen:
            continue
        seen.add(entry.id)
        ordered.append(entry)
    return ordered


def _fetch_gemini_model_catalog(settings: Settings) -> list[ModelCatalogEntry]:
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not set.")

    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError("google-genai is not installed.") from exc

    client = genai.Client(api_key=settings.gemini_api_key)
    entries: list[ModelCatalogEntry] = []
    for model in client.models.list():
        methods = [str(item) for item in getattr(model, "supported_actions", []) or []]
        normalized_methods = {item.strip().lower() for item in methods}
        if "generatecontent" not in {item.replace("_", "").replace("-", "") for item in normalized_methods}:
            continue

        model_name = _strip_model_prefix(str(getattr(model, "name", "")))
        if not model_name:
            continue

        label = str(getattr(model, "display_name", "")).strip() or model_name
        description = str(getattr(model, "description", "")).strip() or None
        entries.append(
            ModelCatalogEntry(
                id=model_name,
                label=label,
                description=description,
                capabilities=methods,
                raw_payload={"name": getattr(model, "name", ""), "supported_actions": methods},
            )
        )
    return entries


def _fetch_deepseek_model_catalog(settings: Settings) -> list[ModelCatalogEntry]:
    if not settings.deepseek_api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not configured.")

    data = _get_json_with_bearer(
        candidate_urls=[
            _join_url(settings.deepseek_base_url, "models"),
            _join_url(settings.deepseek_base_url, "v1/models"),
        ],
        bearer_token=settings.deepseek_api_key,
        timeout_seconds=settings.analysis_timeout_seconds,
    )
    models = data.get("data", [])
    if not isinstance(models, list):
        raise RuntimeError("DeepSeek model catalog returned an unexpected payload.")

    entries: list[ModelCatalogEntry] = []
    for item in models:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id", "")).strip()
        if not model_id:
            continue
        entries.append(
            ModelCatalogEntry(
                id=model_id,
                label=model_id,
                description=None,
                capabilities=[],
                raw_payload=item,
            )
        )
    return entries


def _fetch_grok_model_catalog(settings: Settings) -> list[ModelCatalogEntry]:
    if not settings.xai_api_key:
        raise RuntimeError("XAI_API_KEY is not configured.")

    data = _get_json_with_bearer(
        candidate_urls=[
            _join_url(settings.xai_base_url, "language-models"),
            _join_url(settings.xai_base_url, "models"),
        ],
        bearer_token=settings.xai_api_key,
        timeout_seconds=settings.analysis_timeout_seconds,
    )

    raw_models = data.get("models", data.get("data", []))
    if not isinstance(raw_models, list):
        raise RuntimeError("xAI model catalog returned an unexpected payload.")

    entries: list[ModelCatalogEntry] = []
    alias_entries: list[ModelCatalogEntry] = []
    for item in raw_models:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id", "")).strip()
        if not model_id:
            continue
        capabilities = _collect_capabilities(item)
        description = _build_grok_description(item)
        entries.append(
            ModelCatalogEntry(
                id=model_id,
                label=model_id,
                description=description,
                capabilities=capabilities,
                raw_payload=item,
            )
        )
        aliases = item.get("aliases", [])
        if isinstance(aliases, list):
            for alias in aliases:
                alias_id = str(alias).strip()
                if alias_id and alias_id != model_id:
                    alias_entries.append(
                        ModelCatalogEntry(
                            id=alias_id,
                            label=f"{alias_id} (alias)",
                            description=f"Alias of {model_id}",
                            capabilities=capabilities,
                            raw_payload=item,
                        )
                    )
    return [*entries, *alias_entries]


def _fetch_github_model_catalog(settings: Settings) -> list[ModelCatalogEntry]:
    if not settings.github_models_token:
        raise RuntimeError("GITHUB_MODELS_TOKEN is not configured.")

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {settings.github_models_token}",
        "X-GitHub-Api-Version": settings.github_models_api_version,
    }
    url = _join_url(settings.github_models_base_url, "catalog/models")
    response = requests.get(url, headers=headers, timeout=settings.analysis_timeout_seconds)
    if not response.ok:
        raise RuntimeError(f"GitHub Models catalog error {response.status_code}: {response.text[:500]}")

    payload = response.json()
    if not isinstance(payload, list):
        raise RuntimeError("GitHub Models catalog returned an unexpected payload.")

    entries: list[ModelCatalogEntry] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id", "")).strip()
        if not model_id or not _is_text_generation_model(item):
            continue
        label = str(item.get("name", "")).strip() or model_id
        publisher = str(item.get("publisher", "")).strip()
        summary = str(item.get("summary", "")).strip()
        description = " · ".join(part for part in [publisher, summary] if part) or None
        capabilities = [str(cap).strip() for cap in item.get("capabilities", []) if str(cap).strip()]
        entries.append(
            ModelCatalogEntry(
                id=model_id,
                label=label,
                description=description,
                capabilities=capabilities,
                raw_payload=item,
            )
        )
    return entries


def _fetch_copilot_model_catalog(settings: Settings) -> list[ModelCatalogEntry]:
    node_binary = settings.copilot_node_binary
    if shutil.which(node_binary) is None:
        raise RuntimeError(f"Node.js binary '{node_binary}' was not found.")
    if shutil.which("copilot") is None:
        raise RuntimeError("Copilot CLI binary 'copilot' was not found.")

    script_path = Path(__file__).resolve().parents[2] / "scripts" / "copilot_list_models.mjs"
    if not script_path.exists():
        raise RuntimeError(f"Copilot model helper script not found: {script_path}")

    result = subprocess.run(
        [node_binary, str(script_path)],
        text=True,
        capture_output=True,
        timeout=settings.analysis_timeout_seconds,
        env=os.environ.copy(),
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"Copilot model discovery failed: {stderr}")

    data = json.loads(result.stdout or "{}")
    models = data.get("models", [])
    if not isinstance(models, list):
        raise RuntimeError("Copilot model discovery returned an unexpected payload.")

    entries: list[ModelCatalogEntry] = []
    for item in models:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id", "")).strip()
        if not model_id:
            continue
        label = str(item.get("name", "")).strip() or model_id
        capabilities = [str(cap).strip() for cap in item.get("capabilities", []) if str(cap).strip()]
        reasoning = item.get("supportedReasoningEfforts", [])
        reasoning_text = ""
        if isinstance(reasoning, list) and reasoning:
            reasoning_text = f"Reasoning: {', '.join(str(level) for level in reasoning)}"
        description = reasoning_text or None
        entries.append(
            ModelCatalogEntry(
                id=model_id,
                label=label,
                description=description,
                capabilities=capabilities,
                raw_payload=item,
            )
        )
    return entries


def _get_json_with_bearer(*, candidate_urls: list[str], bearer_token: str, timeout_seconds: int) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Accept": "application/json",
    }
    last_error: str | None = None
    seen_urls: set[str] = set()
    for url in candidate_urls:
        clean_url = url.strip()
        if not clean_url or clean_url in seen_urls:
            continue
        seen_urls.add(clean_url)
        response = requests.get(clean_url, headers=headers, timeout=timeout_seconds)
        if response.ok:
            data = response.json()
            if isinstance(data, dict):
                return data
            raise RuntimeError(f"Unexpected JSON payload from {clean_url}.")
        if response.status_code == 404:
            last_error = f"{clean_url} returned 404."
            continue
        raise RuntimeError(f"Provider model catalog error {response.status_code}: {response.text[:500]}")
    raise RuntimeError(last_error or "No valid provider model catalog endpoint responded.")


def _join_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _strip_model_prefix(name: str) -> str:
    if name.startswith("models/"):
        return name.split("/", 1)[1]
    return name


def _collect_capabilities(item: dict[str, Any]) -> list[str]:
    capabilities: list[str] = []
    for key in ("input_modalities", "output_modalities"):
        values = item.get(key, [])
        if isinstance(values, list):
            capabilities.extend(str(value).strip() for value in values if str(value).strip())
    return sorted(set(capabilities))


def _build_grok_description(item: dict[str, Any]) -> str | None:
    parts: list[str] = []
    version = str(item.get("version", "")).strip()
    owned_by = str(item.get("owned_by", "")).strip()
    aliases = item.get("aliases", [])
    if owned_by:
        parts.append(owned_by)
    if version:
        parts.append(f"v{version}")
    if isinstance(aliases, list) and aliases:
        parts.append(f"Aliases: {', '.join(str(alias) for alias in aliases[:4])}")
    return " · ".join(parts) or None


def _is_text_generation_model(item: dict[str, Any]) -> bool:
    input_modalities = [str(value).strip().lower() for value in item.get("supported_input_modalities", []) if str(value).strip()]
    output_modalities = [str(value).strip().lower() for value in item.get("supported_output_modalities", []) if str(value).strip()]
    if input_modalities and "text" not in input_modalities:
        return False
    if output_modalities and "text" not in output_modalities:
        return False
    return True


def _usage_metrics_dict(usage: UsageMetrics | None) -> dict[str, Any] | None:
    if usage is None:
        return None
    return {
        "requests": usage.requests,
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
        "reasoning_tokens": usage.reasoning_tokens,
        "cached_tokens": usage.cached_tokens,
        "cost_usd": usage.cost_usd,
        "extra_metrics": usage.extra_metrics or {},
    }


def _extract_gemini_usage(response: Any) -> UsageMetrics | None:
    metadata = getattr(response, "usage_metadata", None)
    if metadata is None:
        return None

    prompt_tokens = _coerce_int_value(
        getattr(metadata, "prompt_token_count", None),
        getattr(metadata, "cached_content_token_count", None),
    )
    completion_tokens = _coerce_int_value(
        getattr(metadata, "candidates_token_count", None),
        getattr(metadata, "response_token_count", None),
    )
    total_tokens = _coerce_int_value(getattr(metadata, "total_token_count", None))
    reasoning_tokens = _coerce_int_value(
        getattr(metadata, "thoughts_token_count", None),
        getattr(metadata, "reasoning_token_count", None),
    )
    cached_tokens = _coerce_int_value(getattr(metadata, "cached_content_token_count", None))
    return UsageMetrics(
        requests=1,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        reasoning_tokens=reasoning_tokens,
        cached_tokens=cached_tokens,
        extra_metrics={},
    )


def _extract_openai_usage(payload: Any) -> UsageMetrics | None:
    if not isinstance(payload, dict):
        return None

    prompt_tokens = _coerce_int_value(
        payload.get("prompt_tokens"),
        payload.get("input_tokens"),
    )
    completion_tokens = _coerce_int_value(
        payload.get("completion_tokens"),
        payload.get("output_tokens"),
    )
    total_tokens = _coerce_int_value(payload.get("total_tokens"))
    reasoning_tokens = _coerce_int_value(
        _nested_value(payload, "completion_tokens_details", "reasoning_tokens"),
        _nested_value(payload, "output_tokens_details", "reasoning_tokens"),
    )
    cached_tokens = _coerce_int_value(
        payload.get("prompt_cache_hit_tokens"),
        _nested_value(payload, "prompt_tokens_details", "cached_tokens"),
        _nested_value(payload, "input_tokens_details", "cached_tokens"),
    )
    cost_usd = _coerce_float_value(payload.get("cost"), payload.get("amount"))
    return UsageMetrics(
        requests=1,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        reasoning_tokens=reasoning_tokens,
        cached_tokens=cached_tokens,
        cost_usd=cost_usd,
        extra_metrics={},
    )


def _extract_copilot_usage(payload: Any) -> UsageMetrics | None:
    if not isinstance(payload, dict):
        return None

    input_tokens = _coerce_int_value(payload.get("inputTokens"))
    output_tokens = _coerce_int_value(payload.get("outputTokens"))
    cache_read_tokens = _coerce_int_value(payload.get("cacheReadTokens"))
    total_tokens = None
    if input_tokens is not None or output_tokens is not None:
        total_tokens = (input_tokens or 0) + (output_tokens or 0)

    extra_metrics: dict[str, Any] = {}
    quota_snapshots = payload.get("quotaSnapshots")
    if isinstance(quota_snapshots, dict):
        for quota_name, snapshot in quota_snapshots.items():
            if not isinstance(snapshot, dict):
                continue
            used = _coerce_int_value(snapshot.get("usedRequests"))
            entitlement = _coerce_int_value(snapshot.get("entitlementRequests"))
            if used is not None:
                extra_metrics[f"{quota_name}_used_requests"] = used
            if entitlement is not None:
                extra_metrics[f"{quota_name}_entitlement_requests"] = entitlement
            remaining = _coerce_float_value(snapshot.get("remainingPercentage"))
            if remaining is not None:
                extra_metrics[f"{quota_name}_remaining_percentage"] = remaining

    copilot_usage = payload.get("copilotUsage")
    if isinstance(copilot_usage, dict):
        total_nano_aiu = _coerce_int_value(copilot_usage.get("totalNanoAiu"))
        if total_nano_aiu is not None:
            extra_metrics["copilot_total_nano_aiu"] = total_nano_aiu

    return UsageMetrics(
        requests=1,
        prompt_tokens=input_tokens,
        completion_tokens=output_tokens,
        total_tokens=total_tokens,
        cached_tokens=cache_read_tokens,
        cost_usd=_coerce_float_value(payload.get("cost")),
        extra_metrics=extra_metrics,
    )


def _nested_value(payload: dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _coerce_int_value(*values: Any) -> int | None:
    for value in values:
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _coerce_float_value(*values: Any) -> float | None:
    for value in values:
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None
