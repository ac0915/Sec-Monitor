(function () {
  const metaApiBase = document
    .querySelector('meta[name="sec-monitor-api-base"]')
    ?.getAttribute("content")
    ?.trim();
  const apiBase = metaApiBase || "";
  const refreshMs = 45000;
  const prefsKey = "sec-monitor-ui-v3";
  const commandHistoryKey = "sec-monitor-command-history-v1";
  const guiSessionKey = "sec-monitor-gui-session-v1";

  const providerFormSpecs = {
    gemini: {
      label: "Google Gemini",
      hint: "适合直接填入 Google AI Studio API key。",
      fields: [
        { key: "model", label: "Model", type: "text", placeholder: "gemini-2.5-flash" },
        { key: "api_key", label: "API Key", type: "password", secret: true, placeholder: "留空表示保留已保存密钥" },
      ],
    },
    deepseek: {
      label: "DeepSeek",
      hint: "走标准 chat completions 接口，可自定义 base URL。",
      fields: [
        { key: "model", label: "Model", type: "text", placeholder: "deepseek-chat" },
        { key: "base_url", label: "Base URL", type: "text", placeholder: "https://api.deepseek.com" },
        { key: "api_key", label: "API Key", type: "password", secret: true, placeholder: "留空表示保留已保存密钥" },
      ],
    },
    grok: {
      label: "xAI Grok",
      hint: "使用 xAI REST chat API。",
      fields: [
        { key: "model", label: "Model", type: "text", placeholder: "grok-4" },
        { key: "base_url", label: "Base URL", type: "text", placeholder: "https://api.x.ai/v1" },
        { key: "api_key", label: "API Key", type: "password", secret: true, placeholder: "留空表示保留已保存密钥" },
      ],
    },
    github: {
      label: "GitHub Models",
      hint: "通过 GitHub 官方 models inference API 调用。",
      fields: [
        { key: "model", label: "Model", type: "text", placeholder: "openai/gpt-4.1" },
        { key: "base_url", label: "Base URL", type: "text", placeholder: "https://models.github.ai" },
        { key: "api_version", label: "API Version", type: "text", placeholder: "2026-03-10" },
        { key: "org", label: "Organization", type: "text", placeholder: "可选，不填则走通用 endpoint" },
        { key: "token", label: "Token", type: "password", secret: true, placeholder: "留空表示保留已保存 token" },
      ],
    },
    copilot: {
      label: "GitHub Copilot",
      hint: "这里管理模型与本机 Node 路径。真正授权来自当前机器上的 Copilot CLI 登录态。",
      fields: [
        { key: "model", label: "Model", type: "text", placeholder: "gpt-4.1", envKey: "COPILOT_MODEL" },
        { key: "node_binary", label: "Node Binary", type: "text", placeholder: "node", envKey: "COPILOT_NODE_BINARY" },
      ],
    },
  };

  const usageDefaults = {
    gemini: {
      enabled: false,
      alert_enabled: true,
      prefer_provider_api: false,
      metric_name: "total_tokens",
      metric_unit: "tokens",
      limit_value: 0,
      threshold_percentages: [50, 80, 90, 100],
      billing_actor_type: "user",
      billing_actor: "",
      billing_token: "",
      has_billing_token: false,
      billing_token_masked: "",
    },
    deepseek: {
      enabled: false,
      alert_enabled: true,
      prefer_provider_api: false,
      metric_name: "total_tokens",
      metric_unit: "tokens",
      limit_value: 0,
      threshold_percentages: [50, 80, 90, 100],
      billing_actor_type: "user",
      billing_actor: "",
      billing_token: "",
      has_billing_token: false,
      billing_token_masked: "",
    },
    grok: {
      enabled: false,
      alert_enabled: true,
      prefer_provider_api: false,
      metric_name: "total_tokens",
      metric_unit: "tokens",
      limit_value: 0,
      threshold_percentages: [50, 80, 90, 100],
      billing_actor_type: "user",
      billing_actor: "",
      billing_token: "",
      has_billing_token: false,
      billing_token_masked: "",
    },
    github: {
      enabled: false,
      alert_enabled: true,
      prefer_provider_api: false,
      metric_name: "total_tokens",
      metric_unit: "tokens",
      limit_value: 0,
      threshold_percentages: [50, 80, 90, 100],
      billing_actor_type: "user",
      billing_actor: "",
      billing_token: "",
      has_billing_token: false,
      billing_token_masked: "",
    },
    copilot: {
      enabled: false,
      alert_enabled: true,
      prefer_provider_api: false,
      metric_name: "requests",
      metric_unit: "requests",
      limit_value: 0,
      threshold_percentages: [50, 80, 90, 100],
      billing_actor_type: "user",
      billing_actor: "",
      billing_token: "",
      has_billing_token: false,
      billing_token_masked: "",
    },
  };

  const defaultPrefs = {
    query: "",
    tier: "all",
    impact: "all",
    formType: "",
    theme: "all",
    autoRefresh: true,
    denseMode: false,
    focusOnly: false,
    focusTickers: [],
  };

  const state = {
    dashboard: null,
    feed: null,
    prefs: loadPrefs(),
    commandQuery: "",
    commandHistory: loadCommandHistory(),
    gui: {
      bootstrap: null,
      settings: null,
      draft: null,
      modelCatalogs: {},
      usageSummary: null,
      pendingPassword: "",
      clearSecretFields: {},
      token: loadGuiToken(),
      feedback: "",
      feedbackTone: "",
      testResult: null,
    },
  };

  const els = {
    healthPill: document.getElementById("health-pill"),
    statusMeta: document.getElementById("status-meta"),
    heroMetrics: document.getElementById("hero-metrics"),
    opsStrip: document.getElementById("ops-strip"),
    briefPanel: document.getElementById("brief-panel"),
    signalIndex: document.getElementById("signal-index"),
    themeClusters: document.getElementById("theme-clusters"),
    corpusGrid: document.getElementById("corpus-grid"),
    opsHealth: document.getElementById("ops-health"),
    focusInput: document.getElementById("focus-input"),
    focusAdd: document.getElementById("focus-add"),
    focusChips: document.getElementById("focus-chips"),
    autoRefreshToggle: document.getElementById("auto-refresh-toggle"),
    denseModeToggle: document.getElementById("dense-mode-toggle"),
    focusOnlyToggle: document.getElementById("focus-only-toggle"),
    trendChart: document.getElementById("trend-chart"),
    sentimentStack: document.getElementById("sentiment-stack"),
    filingsFeed: document.getElementById("filings-feed"),
    resultMeta: document.getElementById("result-meta"),
    runsTable: document.getElementById("runs-table"),
    feedSearch: document.getElementById("feed-search"),
    tierFilters: document.getElementById("tier-filters"),
    impactFilter: document.getElementById("impact-filter"),
    formFilter: document.getElementById("form-filter"),
    themeFilter: document.getElementById("theme-filter"),
    themeShortcuts: document.getElementById("theme-shortcuts"),
    clearFilters: document.getElementById("clear-filters"),
    overlay: document.getElementById("overlay"),
    drawer: document.getElementById("drawer"),
    drawerContent: document.getElementById("drawer-content"),
    drawerClose: document.getElementById("drawer-close"),
    refreshButton: document.getElementById("refresh-button"),
    commandOpen: document.getElementById("command-open"),
    commandOverlay: document.getElementById("command-overlay"),
    commandInput: document.getElementById("command-input"),
    commandResults: document.getElementById("command-results"),
    commandClose: document.getElementById("command-close"),
    llmSettingsOpen: document.getElementById("llm-settings-open"),
    llmOverlay: document.getElementById("llm-overlay"),
    llmBody: document.getElementById("llm-body"),
    llmClose: document.getElementById("llm-close"),
    llmRuntimeMeta: document.getElementById("llm-runtime-meta"),
  };

  function loadPrefs() {
    try {
      const raw = localStorage.getItem(prefsKey);
      if (!raw) return { ...defaultPrefs };
      const parsed = JSON.parse(raw);
      return {
        ...defaultPrefs,
        ...parsed,
        focusTickers: Array.isArray(parsed?.focusTickers)
          ? parsed.focusTickers
              .map((value) => String(value || "").trim().toUpperCase())
              .filter(Boolean)
              .slice(0, 24)
          : [],
      };
    } catch {
      return { ...defaultPrefs };
    }
  }

  function savePrefs() {
    try {
      localStorage.setItem(prefsKey, JSON.stringify(state.prefs));
    } catch {
      // ignore localStorage failures
    }
  }

  function loadCommandHistory() {
    try {
      const raw = localStorage.getItem(commandHistoryKey);
      const parsed = raw ? JSON.parse(raw) : [];
      return Array.isArray(parsed) ? parsed.map((value) => String(value)).slice(0, 8) : [];
    } catch {
      return [];
    }
  }

  function loadGuiToken() {
    try {
      return sessionStorage.getItem(guiSessionKey) || "";
    } catch {
      return "";
    }
  }

  function saveGuiToken(token) {
    try {
      if (token) sessionStorage.setItem(guiSessionKey, token);
      else sessionStorage.removeItem(guiSessionKey);
    } catch {
      // ignore sessionStorage failures
    }
  }

  function saveCommandHistory() {
    try {
      localStorage.setItem(commandHistoryKey, JSON.stringify(state.commandHistory.slice(0, 8)));
    } catch {
      // ignore localStorage failures
    }
  }

  function rememberCommand(query) {
    const normalized = String(query || "").trim();
    if (!normalized) return;
    state.commandHistory = [normalized, ...state.commandHistory.filter((item) => item !== normalized)].slice(0, 8);
    saveCommandHistory();
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function numberFormat(value) {
    return new Intl.NumberFormat("en-US").format(Number(value || 0));
  }

  function formatDate(value) {
    if (!value) return "N/A";
    return new Intl.DateTimeFormat("zh-Hant-HK", {
      year: "numeric",
      month: "short",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(value));
  }

  function formatShortDate(value) {
    if (!value) return "N/A";
    return new Intl.DateTimeFormat("zh-Hant-HK", {
      month: "short",
      day: "2-digit",
    }).format(new Date(value));
  }

  function impactTone(impact) {
    if (impact === "利好") return "impact-positive";
    if (impact === "利空") return "impact-negative";
    return "impact-neutral";
  }

  function scoreTone(score) {
    if (score >= 85) return "critical";
    if (score >= 68) return "high";
    if (score >= 45) return "elevated";
    return "monitoring";
  }

  async function requestJson(path, options) {
    const config = options || {};
    const headers = { ...(config.headers || {}) };
    if (config.token) {
      headers.Authorization = `Bearer ${config.token}`;
    }
    const fetchOptions = {
      method: config.method || "GET",
      headers,
    };
    if (config.body !== undefined) {
      headers["Content-Type"] = "application/json";
      fetchOptions.body = JSON.stringify(config.body);
    }

    const response = await fetch(`${apiBase}${path}`, fetchOptions);
    const text = await response.text();
    let payload = null;
    try {
      payload = text ? JSON.parse(text) : null;
    } catch {
      payload = null;
    }
    if (!response.ok) {
      const message = payload?.detail || payload?.message || text || `${response.status} ${response.statusText}`;
      const error = new Error(message);
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  function fetchJson(path) {
    return requestJson(path);
  }

  function buildFeedUrl() {
    const params = new URLSearchParams();
    if (state.prefs.query) params.set("q", state.prefs.query);
    if (state.prefs.tier) params.set("tier", state.prefs.tier);
    if (state.prefs.impact) params.set("impact", state.prefs.impact);
    if (state.prefs.formType) params.set("form_type", state.prefs.formType);
    if (state.prefs.theme) params.set("theme", state.prefs.theme);
    if (state.prefs.focusOnly && state.prefs.focusTickers.length) {
      params.set("tickers", state.prefs.focusTickers.join(","));
    }
    params.set("limit", "120");
    return `/api/feed?${params.toString()}`;
  }

  function setStatusError(message) {
    els.healthPill.textContent = "API unavailable";
    els.healthPill.className = "status-pill is-danger";
    els.statusMeta.textContent = message;
    els.filingsFeed.innerHTML = `<div class="empty-state">${escapeHtml(message)}</div>`;
  }

  function syncPreferenceControls() {
    els.feedSearch.value = state.prefs.query;
    els.autoRefreshToggle.checked = Boolean(state.prefs.autoRefresh);
    els.denseModeToggle.checked = Boolean(state.prefs.denseMode);
    els.focusOnlyToggle.checked = Boolean(state.prefs.focusOnly);

    Array.from(els.tierFilters.querySelectorAll(".filter-chip")).forEach((chip) => {
      chip.classList.toggle("is-active", chip.getAttribute("data-tier") === String(state.prefs.tier));
    });
  }

  function renderSelectOptions(select, items, value, defaultValue, defaultLabel) {
    const options = [`<option value="${escapeHtml(defaultValue)}">${escapeHtml(defaultLabel)}</option>`];
    (items || []).forEach((item) => {
      options.push(
        `<option value="${escapeHtml(item.value)}"${item.value === value ? " selected" : ""}>${escapeHtml(
          `${item.value} (${item.count})`
        )}</option>`
      );
    });
    select.innerHTML = options.join("");
    select.value = value;
  }

  function renderStatus() {
    const dashboard = state.dashboard;
    const lastRun = dashboard?.last_run;
    const analysis = dashboard?.analysis;
    const telegram = dashboard?.telegram;
    const dataPolicy = dashboard?.data_policy;
    const analysisText = analysis
      ? `analysis ${analysis.provider}/${analysis.model || "unconfigured"}${analysis.available ? "" : " disabled"} via ${
          analysis.runtime_source || "env"
        }`
      : "analysis unavailable";
    const telegramText = telegram?.assistant_available
      ? `Telegram assistant ready · ${telegram.subscribed_chats} subscribed`
      : "Telegram assistant disabled";
    const realText = dataPolicy?.synthetic_data === false ? "real-data only" : "data policy unknown";
    const warningText = analysis?.runtime_error ? ` · config warning: ${analysis.runtime_error}` : "";

    if (!lastRun) {
      els.healthPill.textContent = "Waiting for first real ingestion";
      els.healthPill.className = "status-pill is-warn";
      els.statusMeta.textContent = `${analysisText} · ${telegramText} · ${realText}${warningText}`;
      return;
    }

    const healthy = lastRun.status === "completed";
    els.healthPill.className = `status-pill ${healthy ? "" : "is-danger"}`.trim();
    els.healthPill.textContent = healthy ? "Pipeline healthy" : "Pipeline needs attention";
    els.statusMeta.textContent =
      `${formatDate(lastRun.completed_at || lastRun.started_at)} · seen ${numberFormat(lastRun.entries_seen)} · matched ${numberFormat(
        lastRun.matched_entries
      )} · ${analysisText} · ${telegramText} · ${realText}${warningText}`;
  }

  function renderHeroMetrics() {
    const dashboard = state.dashboard;
    const signalIndex = dashboard.signal_index || [];
    const highSignal = signalIndex.filter((item) => item.score >= 68).length;
    const cards = [
      {
        label: "Total Filings",
        value: numberFormat(dashboard.totals.filings),
        meta: `Watchlist ${numberFormat(dashboard.watchlist_size)}`,
        tone: "tier3",
      },
      {
        label: "Tier 1 Events",
        value: numberFormat(dashboard.tier_counts.tier1),
        meta: "最高优先级披露池",
        tone: "tier1",
      },
      {
        label: "High-Signal Names",
        value: numberFormat(highSignal),
        meta: "Signal Index >= 68",
        tone: "tier2",
      },
      {
        label: "Corpus Chunks",
        value: numberFormat(dashboard.totals.chunks),
        meta: "RAG / eval ready",
        tone: "tier3",
      },
    ];

    els.heroMetrics.innerHTML = cards
      .map(
        (card) => `
          <article class="metric-card ${card.tone}">
            <p class="metric-label">${escapeHtml(card.label)}</p>
            <div class="metric-value">${escapeHtml(card.value)}</div>
            <div class="metric-meta">${escapeHtml(card.meta)}</div>
          </article>
        `
      )
      .join("");
  }

  function renderOpsStrip() {
    const analysis = state.dashboard.analysis;
    const telegram = state.dashboard.telegram;
    const cards = [
      {
        label: "LLM",
        value: analysis.available ? "Ready" : "Fallback",
        meta: `${analysis.provider}/${analysis.model || "n/a"} · ${analysis.runtime_source || "env"}`,
      },
      {
        label: "Telegram",
        value: telegram.assistant_available ? "Live" : "Off",
        meta: `${numberFormat(telegram.subscribed_chats)} subscribed`,
      },
      {
        label: "Brief Mode",
        value: escapeHtml(state.dashboard.sec_brief?.mode || "rules-based"),
        meta: "worldmonitor-style control brief",
      },
      {
        label: "Focus List",
        value: numberFormat(state.prefs.focusTickers.length),
        meta: state.prefs.focusOnly ? "focus-only on" : "focus-only off",
      },
    ];

    els.opsStrip.innerHTML = cards
      .map(
        (card) => `
          <article class="ops-card">
            <div class="metric-label">${escapeHtml(card.label)}</div>
            <div class="ops-value">${escapeHtml(card.value)}</div>
            <div class="metric-meta">${escapeHtml(card.meta)}</div>
          </article>
        `
      )
      .join("");
  }

  function renderBrief() {
    const brief = state.dashboard.sec_brief;
    if (!brief?.available) {
      els.briefPanel.innerHTML = '<div class="empty-state">暂无真实数据，无法生成 SEC Brief。</div>';
      return;
    }

    els.briefPanel.innerHTML = `
      <div class="brief-card">
        <div class="brief-headline">${escapeHtml(brief.summary)}</div>
        <div class="brief-meta">Generated ${escapeHtml(formatDate(brief.generated_at))} · ${escapeHtml(brief.mode)}</div>
      </div>
      <div class="brief-grid">
        <section class="brief-block">
          <div class="brief-block-title">Action Plan</div>
          <div class="brief-block-text">${escapeHtml(brief.action_plan)}</div>
        </section>
        <section class="brief-block">
          <div class="brief-block-title">Risk Watch</div>
          <div class="brief-block-text">${escapeHtml(brief.risk_watch)}</div>
        </section>
      </div>
      <div class="brief-highlight-list">
        ${(brief.highlights || [])
          .map(
            (item) => `
              <button class="brief-highlight" type="button" data-filing-id="${escapeHtml(String(item.id))}">
                <div class="badge-row">
                  <span class="badge tier-${escapeHtml(String(item.tier))}">Tier ${escapeHtml(String(item.tier))}</span>
                  <span class="badge">${escapeHtml(item.form_type)}</span>
                  <span class="badge ${impactTone(item.impact)}">${escapeHtml(item.impact)}</span>
                </div>
                <strong>${escapeHtml(item.ticker)} · ${escapeHtml(item.company_name)}</strong>
                <div class="brief-block-text">${escapeHtml(item.summary)}</div>
              </button>
            `
          )
          .join("")}
      </div>
    `;
  }

  function renderSignalIndex() {
    const rows = state.dashboard.signal_index || [];
    if (!rows.length) {
      els.signalIndex.innerHTML = '<div class="empty-state">暂无真实数据，无法计算 Signal Index。</div>';
      return;
    }

    els.signalIndex.innerHTML = rows
      .map(
        (row) => `
          <button class="signal-row" type="button" data-search-ticker="${escapeHtml(row.ticker)}">
            <div class="signal-main">
              <div>
                <div class="signal-title">${escapeHtml(row.ticker)} · ${escapeHtml(row.company_name)}</div>
                <div class="metric-meta">${escapeHtml(row.rationale)}</div>
                <div class="signal-tags">
                  ${(row.themes || [])
                    .map((theme) => `<span class="badge">${escapeHtml(theme)}</span>`)
                    .join("")}
                </div>
              </div>
              <div class="signal-score ${scoreTone(row.score)}">
                <strong>${escapeHtml(String(row.score))}</strong>
                <span>${escapeHtml(row.level)}</span>
              </div>
            </div>
            <div class="signal-meter">
              <div class="signal-meter-fill ${scoreTone(row.score)}" style="width:${Math.min(100, row.score)}%"></div>
            </div>
            <div class="signal-foot">
              <span>${escapeHtml(row.sentiment)}</span>
              <span>${numberFormat(row.filings_count)} filings · ${numberFormat(row.tier1_count)} tier1</span>
            </div>
          </button>
        `
      )
      .join("");
  }

  function renderThemeClusters() {
    const clusters = state.dashboard.theme_clusters || [];
    if (!clusters.length) {
      els.themeClusters.innerHTML = '<div class="empty-state">暂无可聚合的主题簇。</div>';
      return;
    }

    els.themeClusters.innerHTML = clusters
      .map(
        (cluster) => `
          <article class="cluster-card">
            <div class="cluster-topline">
              <div>
                <h3>${escapeHtml(cluster.label)}</h3>
                <div class="metric-meta">${escapeHtml(cluster.description)}</div>
              </div>
              <button class="badge cluster-apply" type="button" data-theme-apply="${escapeHtml(cluster.id)}">Filter</button>
            </div>
            <div class="cluster-score-row">
              <div class="signal-meter compact-meter">
                <div class="signal-meter-fill ${scoreTone(cluster.score)}" style="width:${Math.min(100, cluster.score)}%"></div>
              </div>
              <strong>${escapeHtml(String(cluster.score))}</strong>
            </div>
            <div class="cluster-summary">${escapeHtml(cluster.summary)}</div>
            <div class="cluster-tags">
              ${(cluster.tickers || [])
                .slice(0, 6)
                .map((ticker) => `<button class="badge" type="button" data-search-ticker="${escapeHtml(ticker)}">${escapeHtml(ticker)}</button>`)
                .join("")}
            </div>
          </article>
        `
      )
      .join("");
  }

  function renderFocusChips() {
    if (!state.prefs.focusTickers.length) {
      els.focusChips.innerHTML = '<div class="detail-meta">还没有本地 focus ticker。</div>';
      return;
    }
    els.focusChips.innerHTML = state.prefs.focusTickers
      .map(
        (ticker) => `
          <button class="focus-chip" type="button" data-search-ticker="${escapeHtml(ticker)}">
            <span>${escapeHtml(ticker)}</span>
            <span class="focus-chip-remove" data-remove-focus="${escapeHtml(ticker)}">×</span>
          </button>
        `
      )
      .join("");
  }

  function renderCorpusAndOps() {
    const dashboard = state.dashboard;
    const totals = dashboard.totals;
    const cards = [
      {
        label: "Raw Text Ready",
        value: numberFormat(totals.raw_text_ready),
        meta: "已抓正文",
      },
      {
        label: "Analysis Coverage",
        value: `${Math.round((totals.analyzed / Math.max(totals.filings, 1)) * 100)}%`,
        meta: "已分析比例",
      },
      {
        label: "Telegram Chats",
        value: numberFormat(dashboard.telegram.assistant_chats),
        meta: dashboard.telegram.webhook_enabled ? "webhook mode" : "polling mode",
      },
      {
        label: "Focus Coverage",
        value: state.prefs.focusTickers.length
          ? `${Math.min(100, Math.round((state.prefs.focusTickers.length / Math.max(dashboard.watchlist_size, 1)) * 100))}%`
          : "0%",
        meta: "本地关注池 / 主 watchlist",
      },
    ];

    els.corpusGrid.innerHTML = cards
      .map(
        (card) => `
          <article class="corpus-card">
            <p class="corpus-label">${escapeHtml(card.label)}</p>
            <div class="corpus-value">${escapeHtml(card.value)}</div>
            <div class="metric-meta">${escapeHtml(card.meta)}</div>
          </article>
        `
      )
      .join("");

    els.opsHealth.innerHTML = `
      <div class="metric-meta">LLM: ${escapeHtml(
        dashboard.analysis.available ? `${dashboard.analysis.provider}/${dashboard.analysis.model}` : "fallback mode"
      )}</div>
      <div class="metric-meta">LLM source: ${escapeHtml(dashboard.analysis.runtime_source || "env")}</div>
      <div class="metric-meta">Telegram assistant: ${dashboard.telegram.assistant_available ? "enabled" : "disabled"}</div>
      <div class="metric-meta">Real-data policy: synthetic_data = false</div>
    `;
  }

  function renderTrendChart() {
    const rows = state.dashboard.daily_counts || [];
    if (!rows.length) {
      els.trendChart.innerHTML = '<div class="empty-state">暂无真实 SEC 历史数据。</div>';
      return;
    }

    const maxValue = Math.max(
      ...rows.map((row) => Number(row.tier1 || 0) + Number(row.tier2 || 0) + Number(row.tier3 || 0)),
      1
    );

    els.trendChart.innerHTML = rows
      .map((row) => {
        const t1 = Number(row.tier1 || 0);
        const t2 = Number(row.tier2 || 0);
        const t3 = Number(row.tier3 || 0);
        const total = t1 + t2 + t3;
        const scale = 164 / maxValue;

        return `
          <div class="bar-day">
            <div class="bar-stack" title="${escapeHtml(row.date)} | T1 ${t1} / T2 ${t2} / T3 ${t3}">
              <div class="bar-segment tier3" style="height:${Math.max(6, t3 * scale)}px"></div>
              <div class="bar-segment tier2" style="height:${Math.max(6, t2 * scale)}px"></div>
              <div class="bar-segment tier1" style="height:${Math.max(6, t1 * scale)}px"></div>
            </div>
            <div class="bar-label">${escapeHtml(row.date.slice(5).replace("-", "/"))}</div>
            <div class="bar-label">${escapeHtml(String(total))}</div>
          </div>
        `;
      })
      .join("");
  }

  function renderSentiment() {
    const sentiments = state.dashboard.sentiment_counts || {};
    const total = Object.values(sentiments).reduce((sum, value) => sum + Number(value || 0), 0) || 1;
    const statusRows = [
      {
        label: "LLM",
        count: state.dashboard.analysis.available ? "ready" : "fallback",
        pct: state.dashboard.analysis.available ? 100 : 35,
      },
      {
        label: "Telegram",
        count: state.dashboard.telegram.assistant_available ? "live" : "off",
        pct: state.dashboard.telegram.assistant_available ? 100 : 0,
      },
    ];
    const sentimentRows = Object.entries(sentiments).map(([label, count]) => ({
      label,
      count: `${count} filings`,
      pct: Math.round((Number(count || 0) / total) * 100),
    }));

    els.sentimentStack.innerHTML = [...statusRows, ...sentimentRows]
      .map(
        (row) => `
          <div class="sentiment-row">
            <div>
              <strong>${escapeHtml(row.label)}</strong>
              <div class="metric-meta">${escapeHtml(String(row.count))}</div>
              <div class="sentiment-bar-track">
                <div class="sentiment-bar-fill" style="width:${Math.max(6, row.pct)}%"></div>
              </div>
            </div>
            <strong>${escapeHtml(`${row.pct}%`)}</strong>
          </div>
        `
      )
      .join("");
  }

  function renderThemeShortcuts() {
    const clusters = state.dashboard.theme_clusters || [];
    const shortcuts = [
      { id: "all", label: "All" },
      ...clusters.slice(0, 5).map((cluster) => ({ id: cluster.id, label: cluster.label })),
    ];

    els.themeShortcuts.innerHTML = shortcuts
      .map(
        (shortcut) => `
          <button class="quick-action${state.prefs.theme === shortcut.id ? " is-active" : ""}" type="button" data-theme-apply="${escapeHtml(
            shortcut.id
          )}">
            ${escapeHtml(shortcut.label)}
          </button>
        `
      )
      .join("");
  }

  function renderFeedControls() {
    const facets = state.feed?.facets || state.dashboard?.feed_facets || {};
    renderSelectOptions(els.impactFilter, facets.impacts || [], state.prefs.impact, "all", "All Impact");
    renderSelectOptions(els.formFilter, facets.forms || [], state.prefs.formType, "", "All Forms");
    renderSelectOptions(els.themeFilter, facets.themes || [], state.prefs.theme, "all", "All Themes");
    syncPreferenceControls();
  }

  function renderFeed() {
    const results = state.feed?.results || [];
    const totalMatches = Number(state.feed?.total_matches || 0);
    els.resultMeta.textContent = `${numberFormat(totalMatches)} real dossiers matched`;
    els.filingsFeed.classList.toggle("is-dense", Boolean(state.prefs.denseMode));

    if (!results.length) {
      els.filingsFeed.innerHTML = '<div class="empty-state">当前筛选条件下没有真实 filing 数据。</div>';
      return;
    }

    els.filingsFeed.innerHTML = results
      .map((filing) => {
        const items = filing.sec_items?.length ? filing.sec_items.join(", ") : `Form ${filing.form_type}`;
        const analysis = filing.analysis;
        const summary = analysis?.summary || filing.raw_feed_summary || "暂无真实摘要。";
        const themes = filing.themes || [];
        const focusMatch = state.prefs.focusTickers.includes(filing.ticker);

        return `
          <article class="filing-card ${focusMatch ? "is-focus" : ""}" data-tier="${escapeHtml(String(filing.tier))}">
            <div class="filing-card-header">
              <div>
                <div class="badge-row">
                  <span class="badge tier-${escapeHtml(String(filing.tier))}">Tier ${escapeHtml(String(filing.tier))}</span>
                  <span class="badge">${escapeHtml(filing.form_type)}</span>
                  <span class="badge ${impactTone(analysis?.impact || "中性")}">${escapeHtml(analysis?.impact || "待分析")}</span>
                  <span class="badge score-${scoreTone(filing.signal_score)}">Signal ${escapeHtml(String(filing.signal_score))}</span>
                </div>
                <h3>${escapeHtml(filing.ticker)} · ${escapeHtml(filing.company_name)}</h3>
              </div>
              <div class="feed-subline">${escapeHtml(formatDate(filing.published_at))}</div>
            </div>

            <div class="feed-subline">${escapeHtml(items)}</div>
            <div class="theme-pill-row">
              ${themes
                .map(
                  (theme) => `
                    <button class="badge" type="button" data-theme-apply="${escapeHtml(theme.id)}">${escapeHtml(
                      theme.label
                    )}</button>
                  `
                )
                .join("")}
            </div>
            <div class="filing-summary">${escapeHtml(summary)}</div>

            <div class="filing-footer">
              <div class="feed-subline">${numberFormat(filing.document_char_count)} chars · ${numberFormat(
                filing.chunk_count
              )} chunks</div>
              <div class="filing-actions">
                <button class="ghost-button compact" type="button" data-search-ticker="${escapeHtml(filing.ticker)}">Focus</button>
                <button class="detail-trigger" type="button" data-filing-id="${escapeHtml(String(filing.id))}">Open dossier</button>
              </div>
            </div>
          </article>
        `;
      })
      .join("");
  }

  function renderRuns() {
    const runs = state.dashboard.recent_runs || [];
    if (!runs.length) {
      els.runsTable.innerHTML = '<div class="empty-state">暂无真实 ingestion 运行记录。</div>';
      return;
    }

    els.runsTable.innerHTML = runs
      .map(
        (run) => `
          <div class="run-row">
            <div class="run-cell">
              <div class="run-status ${run.status === "completed" ? "" : "failed"}">${escapeHtml(run.status)}</div>
              <div class="metric-meta">${escapeHtml(formatDate(run.started_at))}</div>
            </div>
            <div class="run-cell"><strong>${numberFormat(run.entries_seen)}</strong><div class="metric-meta">seen</div></div>
            <div class="run-cell"><strong>${numberFormat(run.matched_entries)}</strong><div class="metric-meta">matched</div></div>
            <div class="run-cell"><strong>${numberFormat(run.new_filings)}</strong><div class="metric-meta">new</div></div>
            <div class="run-cell"><strong>${numberFormat(run.updated_filings)}</strong><div class="metric-meta">updated</div></div>
            <div class="run-cell"><strong>${numberFormat(run.analyzed_filings)}</strong><div class="metric-meta">analyzed</div></div>
          </div>
        `
      )
      .join("");
  }

  function renderDashboard() {
    renderStatus();
    renderHeroMetrics();
    renderOpsStrip();
    renderBrief();
    renderSignalIndex();
    renderThemeClusters();
    renderCorpusAndOps();
    renderTrendChart();
    renderSentiment();
    renderThemeShortcuts();
    renderFocusChips();
    renderRuns();
  }

  function renderCommandResults() {
    const query = String(state.commandQuery || "").trim().toLowerCase();
    const baseActions = [
      { type: "action", id: "reset", label: "Reset feed filters", subtitle: "清空 query / tier / impact / form / theme" },
      { type: "action", id: "tier1", label: "Focus Tier 1", subtitle: "只显示 Tier 1 披露" },
      { type: "action", id: "negative", label: "Show negative filings", subtitle: "只显示利空分析结果" },
      { type: "action", id: "financing", label: "Show financing theme", subtitle: "聚焦 Financing / Dilution 主题" },
      { type: "action", id: "focus-toggle", label: "Toggle focus watchlist", subtitle: "切换 focus-only 模式" },
    ];

    const dynamicItems = [];
    (state.dashboard?.signal_index || []).forEach((item) => {
      dynamicItems.push({
        type: "ticker",
        id: item.ticker,
        label: `${item.ticker} · ${item.company_name}`,
        subtitle: `${item.level} · score ${item.score}`,
      });
    });
    (state.dashboard?.theme_clusters || []).forEach((item) => {
      dynamicItems.push({
        type: "theme",
        id: item.id,
        label: item.label,
        subtitle: item.summary,
      });
    });
    (state.feed?.results || []).slice(0, 16).forEach((item) => {
      dynamicItems.push({
        type: "filing",
        id: String(item.id),
        label: `${item.ticker} · ${item.form_type} · Tier ${item.tier}`,
        subtitle: item.analysis?.summary || item.raw_feed_summary || item.title,
      });
    });

    const scored = [...baseActions, ...dynamicItems].map((item) => {
      const haystack = `${item.label} ${item.subtitle || ""}`.toLowerCase();
      let score = 0;
      if (!query) {
        score = item.type === "action" ? 40 : 20;
      } else if (haystack.includes(query)) {
        score = haystack.startsWith(query) ? 100 : 70;
      } else {
        const tokens = query.split(/\s+/).filter(Boolean);
        if (tokens.length && tokens.every((token) => haystack.includes(token))) {
          score = 55;
        }
      }
      return { ...item, score };
    });

    const recent = !query
      ? state.commandHistory.map((item) => ({
          type: "recent",
          id: item,
          label: item,
          subtitle: "Recent command / search",
          score: 90,
        }))
      : [];

    const results = [...recent, ...scored.filter((item) => item.score > 0)]
      .sort((a, b) => b.score - a.score)
      .slice(0, 14);

    if (!results.length) {
      els.commandResults.innerHTML = '<div class="empty-state">没有匹配项。试试输入 ticker、theme 或命令。</div>';
      return;
    }

    els.commandResults.innerHTML = results
      .map(
        (item) => `
          <button
            class="command-result"
            type="button"
            data-command-type="${escapeHtml(item.type)}"
            data-command-id="${escapeHtml(item.id)}"
          >
            <div>
              <strong>${escapeHtml(item.label)}</strong>
              <div class="metric-meta">${escapeHtml(item.subtitle || "")}</div>
            </div>
            <span class="badge">${escapeHtml(item.type)}</span>
          </button>
        `
      )
      .join("");
  }

  function openCommandModal() {
    els.commandOverlay.classList.remove("hidden");
    els.commandInput.value = state.commandQuery;
    renderCommandResults();
    els.commandInput.focus();
  }

  function closeCommandModal() {
    els.commandOverlay.classList.add("hidden");
  }

  function executeCommand(type, id) {
    if (type === "recent") {
      state.commandQuery = id;
      els.commandInput.value = id;
      renderCommandResults();
      return;
    }
    if (type === "action") {
      if (id === "reset") {
        state.prefs = { ...state.prefs, query: "", tier: "all", impact: "all", formType: "", theme: "all", focusOnly: false };
      } else if (id === "tier1") {
        state.prefs = { ...state.prefs, tier: "1" };
      } else if (id === "negative") {
        state.prefs = { ...state.prefs, impact: "利空" };
      } else if (id === "financing") {
        state.prefs = { ...state.prefs, theme: "financing" };
      } else if (id === "focus-toggle") {
        state.prefs = { ...state.prefs, focusOnly: !state.prefs.focusOnly };
      }
      savePrefs();
      syncPreferenceControls();
      loadFeed();
      rememberCommand(id);
      closeCommandModal();
      return;
    }
    if (type === "ticker") {
      applySearchTicker(id);
      rememberCommand(id);
      closeCommandModal();
      return;
    }
    if (type === "theme") {
      applyTheme(id);
      rememberCommand(id);
      closeCommandModal();
      return;
    }
    if (type === "filing") {
      openDetail(id);
      rememberCommand(id);
      closeCommandModal();
    }
  }

  function clearGuiSession() {
    state.gui.token = "";
    state.gui.settings = null;
    state.gui.draft = null;
    state.gui.modelCatalogs = {};
    state.gui.usageSummary = null;
    state.gui.pendingPassword = "";
    state.gui.clearSecretFields = {};
    state.gui.testResult = null;
    saveGuiToken("");
  }

  function setGuiFeedback(message, tone) {
    state.gui.feedback = String(message || "").trim();
    state.gui.feedbackTone = tone || "";
  }

  function buildGuiDraft(maskedConfig) {
    const config = maskedConfig || {};
    const providers = config.providers || {};
    const usageMonitoring = config.usage_monitoring?.providers || {};
    return {
      analysis_provider: config.analysis_provider || "gemini",
      analysis_temperature: config.analysis_temperature ?? 0.1,
      analysis_max_tokens: config.analysis_max_tokens ?? 900,
      analysis_timeout_seconds: config.analysis_timeout_seconds ?? 120,
      providers: {
        gemini: {
          model: providers.gemini?.model || "gemini-2.5-flash",
          api_key: "",
        },
        deepseek: {
          model: providers.deepseek?.model || "deepseek-chat",
          base_url: providers.deepseek?.base_url || "https://api.deepseek.com",
          api_key: "",
        },
        grok: {
          model: providers.grok?.model || "grok-4",
          base_url: providers.grok?.base_url || "https://api.x.ai/v1",
          api_key: "",
        },
        github: {
          model: providers.github?.model || "openai/gpt-4.1",
          base_url: providers.github?.base_url || "https://models.github.ai",
          api_version: providers.github?.api_version || "2026-03-10",
          org: providers.github?.org || "",
          token: "",
        },
        copilot: {
          model: providers.copilot?.model || "gpt-4.1",
          node_binary: providers.copilot?.node_binary || "node",
        },
      },
      usage_monitoring: {
        providers: {
          gemini: {
            ...usageDefaults.gemini,
            ...(usageMonitoring.gemini || {}),
            billing_token: "",
            metric_unit: metricUnitForName(usageMonitoring.gemini?.metric_name || usageDefaults.gemini.metric_name),
          },
          deepseek: {
            ...usageDefaults.deepseek,
            ...(usageMonitoring.deepseek || {}),
            billing_token: "",
            metric_unit: metricUnitForName(usageMonitoring.deepseek?.metric_name || usageDefaults.deepseek.metric_name),
          },
          grok: {
            ...usageDefaults.grok,
            ...(usageMonitoring.grok || {}),
            billing_token: "",
            metric_unit: metricUnitForName(usageMonitoring.grok?.metric_name || usageDefaults.grok.metric_name),
          },
          github: {
            ...usageDefaults.github,
            ...(usageMonitoring.github || {}),
            billing_token: "",
            metric_unit: metricUnitForName(usageMonitoring.github?.metric_name || usageDefaults.github.metric_name),
          },
          copilot: {
            ...usageDefaults.copilot,
            ...(usageMonitoring.copilot || {}),
            billing_token: "",
            metric_unit: metricUnitForName(usageMonitoring.copilot?.metric_name || usageDefaults.copilot.metric_name),
          },
        },
      },
    };
  }

  function currentGuiSnapshot() {
    return state.gui.settings || state.gui.bootstrap;
  }

  function updateGuiRuntimeMeta() {
    const snapshot = currentGuiSnapshot();
    if (!snapshot) {
      els.llmRuntimeMeta.textContent = "在页面里管理 provider、密钥和授权状态。";
      return;
    }
    const current = snapshot.current || {};
    const source = snapshot.runtime_source || "env";
    const provider = current.provider || "n/a";
    const model = current.model || "unconfigured";
    const availability = current.available ? "ready" : current.reason || "unavailable";
    els.llmRuntimeMeta.textContent = `Runtime ${source} · ${provider}/${model} · ${availability}`;
  }

  function getMaskedConfig() {
    return state.gui.settings?.masked_config || { providers: {} };
  }

  function getProviderMasked(providerId) {
    return getMaskedConfig().providers?.[providerId] || {};
  }

  function getProviderCatalog(providerId) {
    return (
      state.gui.modelCatalogs?.[providerId] || {
        loading: false,
        error: "",
        source: "",
        models: [],
        count: 0,
      }
    );
  }

  function findCatalogModel(providerId, modelId) {
    const catalog = getProviderCatalog(providerId);
    return (catalog.models || []).find((item) => item.id === modelId) || null;
  }

  function isSecretClearing(providerId, field) {
    return Boolean(state.gui.clearSecretFields?.[providerId]?.[field]);
  }

  function getSecretStatus(providerId, field) {
    const provider = getProviderMasked(providerId);
    if (providerId === "github" && field === "token") {
      return {
        hasSecret: Boolean(provider.has_token),
        masked: provider.token_masked || "",
      };
    }
    return {
      hasSecret: Boolean(provider.has_api_key),
      masked: provider.api_key_masked || "",
    };
  }

  function buildGuiPayloadFromDraft() {
    const draft = state.gui.draft;
    const clearSecretFields = {};
    Object.entries(state.gui.clearSecretFields || {}).forEach(([providerId, fields]) => {
      const activeFields = Object.entries(fields || {})
        .filter(([, enabled]) => enabled)
        .map(([field]) => field);
      if (activeFields.length) {
        clearSecretFields[providerId] = activeFields;
      }
    });
    return {
      analysis_provider: draft.analysis_provider,
      analysis_temperature: Number(draft.analysis_temperature || 0),
      analysis_max_tokens: Number(draft.analysis_max_tokens || 0),
      analysis_timeout_seconds: Number(draft.analysis_timeout_seconds || 0),
      providers: draft.providers,
      usage_monitoring: draft.usage_monitoring,
      clear_secret_fields: clearSecretFields,
    };
  }

  function getUsageDraft(providerId) {
    return state.gui.draft?.usage_monitoring?.providers?.[providerId] || { ...(usageDefaults[providerId] || {}) };
  }

  function getUsageSummary(providerId) {
    const providers = state.gui.usageSummary?.providers || [];
    return providers.find((item) => item.provider === providerId) || null;
  }

  function formatMetricValue(value, unit) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "N/A";
    const numeric = Number(value);
    if (unit === "usd") return `$${numeric.toFixed(2)}`;
    if (unit === "tokens" || unit === "requests") return `${numberFormat(numeric)} ${unit}`;
    return `${numeric.toFixed(2)} ${unit}`;
  }

  function metricOptionsForProvider(providerId) {
    if (providerId === "copilot") {
      return [
        { value: "requests", label: "Requests" },
        { value: "total_tokens", label: "Total Tokens" },
        { value: "cost_usd", label: "Cost USD" },
      ];
    }
    return [
      { value: "total_tokens", label: "Total Tokens" },
      { value: "cost_usd", label: "Cost USD" },
      { value: "requests", label: "Requests" },
    ];
  }

  function metricUnitForName(metricName) {
    if (metricName === "cost_usd") return "usd";
    if (metricName === "requests") return "requests";
    return "tokens";
  }

  function renderModelCatalogField(providerId, draftProvider) {
    const catalog = getProviderCatalog(providerId);
    const options = Array.isArray(catalog.models) ? catalog.models : [];
    const currentModel = String(draftProvider.model || "").trim();
    const hasSelectedOption = options.some((item) => item.id === currentModel);
    const selectedMeta = findCatalogModel(providerId, currentModel);
    let statusText = "使用当前输入或已保存的授权信息拉取 provider 可用模型。";
    if (catalog.loading) {
      statusText = "正在拉取模型列表…";
    } else if (catalog.error) {
      statusText = catalog.error;
    } else if (options.length) {
      statusText = `已载入 ${options.length} 个模型，来源 ${catalog.source || "provider API"}。`;
    }

    return `
      <div class="llm-stack">
        <div class="provider-card-meta">
          <div class="secret-meta">${escapeHtml(statusText)}</div>
          <button
            class="ghost-button compact"
            type="button"
            data-gui-refresh-models="${escapeHtml(providerId)}"
            ${catalog.loading ? "disabled" : ""}
          >
            ${catalog.loading ? "Refreshing..." : "Refresh Models"}
          </button>
        </div>
        <label class="field-shell">
          <span>Model Catalog</span>
          <select data-gui-provider="${escapeHtml(providerId)}" data-gui-model-select="1">
            <option value="__custom__"${hasSelectedOption ? "" : " selected"}>Custom / Manual Input</option>
            ${options
              .map(
                (item) => `
                  <option value="${escapeHtml(item.id)}"${item.id === currentModel ? " selected" : ""}>
                    ${escapeHtml(item.label || item.id)}${item.label && item.label !== item.id ? ` · ${escapeHtml(item.id)}` : ""}
                  </option>
                `
              )
              .join("")}
          </select>
        </label>
        <label class="field-shell">
          <span>Selected Model</span>
          <input
            type="text"
            value="${escapeHtml(currentModel)}"
            placeholder="输入自定义 model，或从上方目录选择"
            data-gui-provider="${escapeHtml(providerId)}"
            data-gui-field="model"
          />
        </label>
        ${
          selectedMeta?.description
            ? `<div class="secret-meta">${escapeHtml(selectedMeta.description)}</div>`
            : ""
        }
      </div>
    `;
  }

  async function loadGuiBootstrap(options) {
    try {
      state.gui.bootstrap = await fetchJson("/api/gui/llm/bootstrap");
      updateGuiRuntimeMeta();
      if (!options?.quiet) renderGuiModal();
      return state.gui.bootstrap;
    } catch (error) {
      setGuiFeedback(error.message, "warn");
      updateGuiRuntimeMeta();
      if (!options?.quiet) renderGuiModal();
      throw error;
    }
  }

  async function loadGuiSettings() {
    if (!state.gui.token) {
      return null;
    }
    try {
      state.gui.settings = await requestJson("/api/gui/llm/settings", { token: state.gui.token });
      state.gui.draft = buildGuiDraft(state.gui.settings.masked_config);
      state.gui.modelCatalogs = {};
      state.gui.clearSecretFields = {};
      state.gui.testResult = null;
      state.gui.usageSummary = null;
      setGuiFeedback("", "");
      updateGuiRuntimeMeta();
      renderGuiModal();
      ensureActiveProviderModelCatalog();
      loadGuiUsageSummary({ quiet: true });
      return state.gui.settings;
    } catch (error) {
      if (error.status === 401) {
        clearGuiSession();
      }
      setGuiFeedback(error.message, "warn");
      updateGuiRuntimeMeta();
      renderGuiModal();
      throw error;
    }
  }

  async function openGuiModal() {
    els.llmOverlay.classList.remove("hidden");
    renderGuiModal();
    if (!state.gui.bootstrap) {
      await loadGuiBootstrap({ quiet: true });
    }
    if (state.gui.token && state.gui.bootstrap?.auth_enabled) {
      await loadGuiSettings();
    } else {
      renderGuiModal();
    }
  }

  async function refreshProviderModels(providerId, options) {
    if (!state.gui.token || !providerId || !state.gui.draft) return null;
    const previous = getProviderCatalog(providerId);
    state.gui.modelCatalogs[providerId] = {
      ...previous,
      loading: true,
      error: "",
    };
    if (!options?.quiet) {
      renderGuiModal();
    }
    try {
      const payload = await requestJson(`/api/gui/llm/providers/${encodeURIComponent(providerId)}/models`, {
        method: "POST",
        token: state.gui.token,
        body: buildGuiPayloadFromDraft(),
      });
      state.gui.modelCatalogs[providerId] = {
        loading: false,
        error: "",
        source: payload.source || "",
        models: Array.isArray(payload.models) ? payload.models : [],
        count: Number(payload.count || 0),
      };
      if (!options?.quiet) {
        setGuiFeedback(`${providerId} model catalog 已刷新。`, "ok");
      }
      renderGuiModal();
      return payload;
    } catch (error) {
      if (error.status === 401) {
        clearGuiSession();
        setGuiFeedback(error.message, "warn");
        renderGuiModal();
        return null;
      }
      state.gui.modelCatalogs[providerId] = {
        ...previous,
        loading: false,
        error: error.message,
      };
      if (!options?.quiet) {
        setGuiFeedback(error.message, "warn");
      }
      renderGuiModal();
      return null;
    }
  }

  function ensureActiveProviderModelCatalog() {
    const providerId = state.gui.draft?.analysis_provider;
    if (!providerId || !state.gui.token) return;
    const catalog = getProviderCatalog(providerId);
    if (catalog.loading || (Array.isArray(catalog.models) && catalog.models.length) || catalog.error) {
      return;
    }
    refreshProviderModels(providerId, { quiet: true }).catch(() => {
      renderGuiModal();
    });
  }

  async function loadGuiUsageSummary(options) {
    if (!state.gui.token) return null;
    try {
      state.gui.usageSummary = await requestJson("/api/gui/llm/usage", { token: state.gui.token });
      if (!options?.quiet) {
        renderGuiModal();
      }
      return state.gui.usageSummary;
    } catch (error) {
      if (error.status === 401) {
        clearGuiSession();
      }
      if (!options?.quiet) {
        setGuiFeedback(error.message, "warn");
        renderGuiModal();
      }
      return null;
    }
  }

  async function syncGuiUsage(providerId) {
    try {
      await requestJson("/api/gui/llm/usage/sync", {
        method: "POST",
        token: state.gui.token,
        body: providerId ? { provider: providerId } : {},
      });
      await loadGuiUsageSummary({ quiet: true });
      setGuiFeedback(providerId ? `${providerId} usage sync completed.` : "Usage sync completed.", "ok");
      renderGuiModal();
    } catch (error) {
      if (error.status === 401) {
        clearGuiSession();
      }
      setGuiFeedback(error.message, "warn");
      renderGuiModal();
    }
  }

  function closeGuiModal() {
    els.llmOverlay.classList.add("hidden");
  }

  function renderGuiMessageBlocks() {
    const blocks = [];
    const snapshot = currentGuiSnapshot();
    if (snapshot?.config_error) {
      blocks.push(`
        <div class="llm-notice warn">
          <div class="llm-notice-title">Config Warning</div>
          <div class="llm-notice-text">${escapeHtml(snapshot.config_error)}</div>
        </div>
      `);
    }
    if (state.gui.feedback) {
      blocks.push(`
        <div class="llm-notice ${escapeHtml(state.gui.feedbackTone)}">
          <div class="llm-notice-title">${state.gui.feedbackTone === "ok" ? "Saved" : "Notice"}</div>
          <div class="llm-notice-text">${escapeHtml(state.gui.feedback)}</div>
        </div>
      `);
    }
    if (state.gui.testResult) {
      blocks.push(`
        <div class="llm-result ${state.gui.testResult.ok ? "ok" : "error"}">
          <div class="llm-notice-title">Connectivity Test</div>
          <div class="llm-result-text">${
            state.gui.testResult.ok
              ? `${escapeHtml(state.gui.testResult.provider)}/${escapeHtml(state.gui.testResult.model)} · ${escapeHtml(
                  state.gui.testResult.response_preview || ""
                )}`
              : escapeHtml(state.gui.testResult.reason || "Provider test failed.")
          }</div>
        </div>
      `);
    }
    return blocks.join("");
  }

  function renderProviderCard(providerId, providerMeta) {
    const draftProvider = state.gui.draft?.providers?.[providerId] || {};
    const providerSpec = providerFormSpecs[providerId];
    const active = state.gui.draft?.analysis_provider === providerId;
    const fields = providerSpec.fields
      .map((field) => {
        if (field.key === "model") {
          return renderModelCatalogField(providerId, draftProvider);
        }
        if (!field.secret) {
          return `
            <label class="field-shell">
              <span>${escapeHtml(field.label)}</span>
              <input
                type="${escapeHtml(field.type || "text")}"
                value="${escapeHtml(draftProvider[field.key] || "")}"
                placeholder="${escapeHtml(field.placeholder || "")}"
                data-gui-provider="${escapeHtml(providerId)}"
                data-gui-field="${escapeHtml(field.key)}"
              />
              ${field.envKey ? `<div class="field-meta">Maps to ${escapeHtml(field.envKey)}</div>` : ""}
            </label>
          `;
        }

        const secretStatus = getSecretStatus(providerId, field.key);
        const clearing = isSecretClearing(providerId, field.key);
        return `
          <div class="secret-row">
            <label class="field-shell">
              <span>${escapeHtml(field.label)}</span>
              <input
                type="password"
                value=""
                placeholder="${escapeHtml(field.placeholder || "")}"
                data-gui-provider="${escapeHtml(providerId)}"
                data-gui-field="${escapeHtml(field.key)}"
              />
            </label>
            <button
              class="ghost-button compact"
              type="button"
              data-gui-clear-secret="${escapeHtml(providerId)}:${escapeHtml(field.key)}"
            >
              ${clearing ? "Undo Clear" : "Clear Saved"}
            </button>
          </div>
          <div class="secret-meta">${
            clearing
              ? "将在保存时清除此已保存密钥，并回退到环境变量。"
              : secretStatus.hasSecret
              ? `已保存：${escapeHtml(secretStatus.masked || "已存在")}`
              : "当前未检测到已保存密钥。"
          }</div>
        `;
      })
      .join("");
    const usageDraft = getUsageDraft(providerId);
    const usageSummary = getUsageSummary(providerId);
    const usageMetricOptions = metricOptionsForProvider(providerId);
    const usageMetric = usageDraft.metric_name || usageDefaults[providerId]?.metric_name || "total_tokens";
    const usageMetricUnit = metricUnitForName(usageMetric);
    const usageObserved = usageSummary?.observed || {};
    const usageStatusText = usageSummary
      ? usageObserved.value !== null && usageObserved.value !== undefined
        ? `${formatMetricValue(usageObserved.value, usageObserved.metric_unit || usageDraft.metric_unit)} via ${
            usageObserved.source || "local_events"
          }`
        : "No observed usage yet."
      : "Usage summary not loaded.";
    const remoteSyncSupported = providerId === "github" || providerId === "copilot";

    return `
      <section class="provider-card ${active ? "is-active" : ""}">
        <div class="provider-card-head">
          <div>
            <div class="provider-card-label">${escapeHtml(providerMeta.label || providerSpec.label)}</div>
            <h3>${escapeHtml(providerSpec.label)}</h3>
          </div>
          <div class="provider-card-head-actions">
            ${active ? '<span class="provider-badge">Active</span>' : ""}
          </div>
        </div>
        <div class="provider-card-hint">${escapeHtml(providerSpec.hint)}</div>
        ${
          providerId === "copilot"
            ? `
              <div class="provider-setup-note">
                <div class="provider-setup-title">Copilot Config In GUI</div>
                <div class="provider-setup-text">
                  这里保存的就是 <code>COPILOT_MODEL</code> 和 <code>COPILOT_NODE_BINARY</code> 对应配置。
                  真正授权仍来自当前机器上的 Copilot CLI 登录态，不是网页 OAuth。
                </div>
                <div class="provider-setup-list">
                  <div><strong>1.</strong> Model 设为你想用的 Copilot 模型，例如 <code>gpt-4.1</code></div>
                  <div><strong>2.</strong> Node Binary 默认保持 <code>node</code>，只有本机命令名不同才改</div>
                  <div><strong>3.</strong> 本机先执行 <code>npm install -g @github/copilot</code></div>
                  <div><strong>4.</strong> 然后运行 <code>copilot</code> 并按提示完成 <code>/login</code></div>
                  <div><strong>5.</strong> 保存后把 Active Provider 切到 <code>copilot</code>，再点 <code>Test Active Provider</code></div>
                </div>
              </div>
            `
            : ""
        }
        <div class="llm-stack">
          ${fields}
          <div class="usage-shell">
            <div class="provider-card-meta">
              <div>
                <div class="provider-card-label">Usage Monitoring</div>
                <div class="secret-meta">${escapeHtml(usageStatusText)}</div>
              </div>
              ${remoteSyncSupported ? `<button class="ghost-button compact" type="button" data-gui-usage-sync="${escapeHtml(providerId)}">Sync Usage</button>` : ""}
            </div>
            <div class="usage-status-grid">
              <div class="usage-status-card">
                <span>Current</span>
                <strong>${escapeHtml(
                  usageObserved.value !== null && usageObserved.value !== undefined
                    ? formatMetricValue(usageObserved.value, usageObserved.metric_unit || usageMetricUnit)
                    : "N/A"
                )}</strong>
              </div>
              <div class="usage-status-card">
                <span>Limit</span>
                <strong>${escapeHtml(formatMetricValue(usageDraft.limit_value || 0, usageMetricUnit))}</strong>
              </div>
              <div class="usage-status-card">
                <span>Used</span>
                <strong>${escapeHtml(
                  usageSummary?.percent_used !== null && usageSummary?.percent_used !== undefined
                    ? `${usageSummary.percent_used}%`
                    : "N/A"
                )}</strong>
              </div>
              <div class="usage-status-card">
                <span>Last Alert</span>
                <strong>${escapeHtml(
                  usageSummary?.latest_alert?.threshold_percent !== undefined
                    ? `${usageSummary.latest_alert.threshold_percent}%`
                    : "None"
                )}</strong>
              </div>
            </div>
            <div class="llm-inline-grid">
              <label class="field-shell">
                <span>Tracking</span>
                <select data-gui-usage-provider="${escapeHtml(providerId)}" data-gui-usage-field="enabled">
                  <option value="false"${usageDraft.enabled ? "" : " selected"}>Off</option>
                  <option value="true"${usageDraft.enabled ? " selected" : ""}>On</option>
                </select>
              </label>
              <label class="field-shell">
                <span>Alerts</span>
                <select data-gui-usage-provider="${escapeHtml(providerId)}" data-gui-usage-field="alert_enabled">
                  <option value="false"${usageDraft.alert_enabled ? "" : " selected"}>Off</option>
                  <option value="true"${usageDraft.alert_enabled ? " selected" : ""}>On</option>
                </select>
              </label>
              <label class="field-shell">
                <span>Metric</span>
                <select data-gui-usage-provider="${escapeHtml(providerId)}" data-gui-usage-field="metric_name">
                  ${usageMetricOptions
                    .map(
                      (option) => `
                        <option value="${escapeHtml(option.value)}"${option.value === usageMetric ? " selected" : ""}>${escapeHtml(option.label)}</option>
                      `
                    )
                    .join("")}
                </select>
              </label>
              <label class="field-shell">
                <span>Total Limit</span>
                <input
                  type="number"
                  min="0"
                  step="1"
                  value="${escapeHtml(String(usageDraft.limit_value || 0))}"
                  data-gui-usage-provider="${escapeHtml(providerId)}"
                  data-gui-usage-field="limit_value"
                />
              </label>
            </div>
            <div class="llm-inline-grid">
              <label class="field-shell">
                <span>Threshold %</span>
                <input
                  type="text"
                  value="${escapeHtml((usageDraft.threshold_percentages || []).join(","))}"
                  placeholder="50,80,90,100"
                  data-gui-usage-provider="${escapeHtml(providerId)}"
                  data-gui-usage-field="threshold_percentages"
                />
              </label>
              <label class="field-shell">
                <span>Usage Source</span>
                <select data-gui-usage-provider="${escapeHtml(providerId)}" data-gui-usage-field="prefer_provider_api">
                  <option value="false"${usageDraft.prefer_provider_api ? "" : " selected"}>Local Events</option>
                  <option value="true"${usageDraft.prefer_provider_api ? " selected" : ""}>Provider API First</option>
                </select>
              </label>
              ${
                remoteSyncSupported
                  ? `
                    <label class="field-shell">
                      <span>Billing Actor Type</span>
                      <select data-gui-usage-provider="${escapeHtml(providerId)}" data-gui-usage-field="billing_actor_type">
                        <option value="user"${usageDraft.billing_actor_type === "user" ? " selected" : ""}>User</option>
                        <option value="org"${usageDraft.billing_actor_type === "org" ? " selected" : ""}>Organization</option>
                      </select>
                    </label>
                    <label class="field-shell">
                      <span>Billing Actor</span>
                      <input
                        type="text"
                        value="${escapeHtml(usageDraft.billing_actor || "")}"
                        placeholder="GitHub username or org"
                        data-gui-usage-provider="${escapeHtml(providerId)}"
                        data-gui-usage-field="billing_actor"
                      />
                    </label>
                  `
                  : ""
              }
            </div>
            ${
              remoteSyncSupported
                ? `
                  <label class="field-shell">
                    <span>Billing Token</span>
                    <input
                      type="password"
                      value=""
                      placeholder="Optional; blank keeps saved token or falls back when supported"
                      data-gui-usage-provider="${escapeHtml(providerId)}"
                      data-gui-usage-field="billing_token"
                    />
                  </label>
                  <div class="secret-meta">${
                    usageDraft.has_billing_token
                      ? `Saved billing token: ${escapeHtml(usageDraft.billing_token_masked || "present")}`
                      : "No saved billing token."
                  }</div>
                `
                : `<div class="secret-meta">This provider currently uses real response usage captured by SEC Monitor. No official account-level sync is wired here.</div>`
            }
          </div>
        </div>
      </section>
    `;
  }

  function renderGuiLocked() {
    return `
      <div class="llm-stack">
        ${renderGuiMessageBlocks()}
        <section class="pref-card">
          <div class="pref-title-row">
            <h3>Unlock Settings</h3>
            <div class="detail-meta">所有 GUI provider 配置都会加密后写入数据库。</div>
          </div>
          <div class="llm-auth-row">
            <label class="field-shell">
              <span>GUI / Admin Password</span>
              <input
                id="gui-admin-password"
                type="password"
                value="${escapeHtml(state.gui.pendingPassword || "")}"
                placeholder="输入 GUI_ADMIN_PASSWORD 或 bootstrap admin 密码"
              />
            </label>
            <button class="detail-trigger" data-gui-login type="button">Unlock</button>
          </div>
          <div class="llm-muted">先在服务端设置 <code>GUI_ADMIN_PASSWORD</code>，然后用它或当前 bootstrap admin 密码解锁控制面板。</div>
        </section>
      </div>
    `;
  }

  function renderGuiDisabled() {
    return `
      <div class="llm-stack">
        ${renderGuiMessageBlocks()}
        <div class="llm-notice warn">
          <div class="llm-notice-title">GUI Auth Disabled</div>
          <div class="llm-notice-text">
            当前服务端还没有设置 GUI 管理密码。先在 <code>.env</code> 里配置 <code>GUI_ADMIN_PASSWORD</code>，
            然后重启 API，页面里的 LLM 控制面板才会启用。
          </div>
        </div>
      </div>
    `;
  }

  function renderGuiUnlocked() {
    const bootstrap = state.gui.bootstrap || { providers: [] };
    const current = state.gui.settings?.current || bootstrap.current || {};
    return `
      <div class="llm-stack">
        ${renderGuiMessageBlocks()}
        <section class="pref-card">
          <div class="pref-title-row">
            <h3>Runtime</h3>
            <div class="detail-meta">当前生效 provider 与 GUI 存储状态</div>
          </div>
          <div class="llm-runtime-grid">
            <label class="field-shell">
              <span>Active Provider</span>
              <select data-gui-top="analysis_provider">
                ${bootstrap.providers
                  .map(
                    (provider) => `
                      <option value="${escapeHtml(provider.id)}"${
                        provider.id === state.gui.draft.analysis_provider ? " selected" : ""
                      }>${escapeHtml(provider.label)}</option>
                    `
                  )
                  .join("")}
              </select>
            </label>
            <label class="field-shell">
              <span>Temperature</span>
              <input type="number" step="0.1" min="0" max="2" value="${escapeHtml(
                String(state.gui.draft.analysis_temperature)
              )}" data-gui-top="analysis_temperature" />
            </label>
            <label class="field-shell">
              <span>Max Tokens</span>
              <input type="number" min="1" step="1" value="${escapeHtml(
                String(state.gui.draft.analysis_max_tokens)
              )}" data-gui-top="analysis_max_tokens" />
            </label>
            <label class="field-shell">
              <span>Timeout Seconds</span>
              <input type="number" min="1" step="1" value="${escapeHtml(
                String(state.gui.draft.analysis_timeout_seconds)
              )}" data-gui-top="analysis_timeout_seconds" />
            </label>
          </div>
          <div class="provider-card-hint">
            当前运行中：${escapeHtml(current.provider || "n/a")}/${escapeHtml(current.model || "unconfigured")} · ${
              current.available ? "provider ready" : escapeHtml(current.reason || "provider unavailable")
            }
          </div>
        </section>

        <section class="pref-card">
          <div class="pref-title-row">
            <h3>Provider Credentials</h3>
            <div class="detail-meta">可以预先保存多个 provider，只切换 active provider 即可生效。</div>
          </div>
          <div class="llm-provider-grid">
            ${bootstrap.providers.map((provider) => renderProviderCard(provider.id, provider)).join("")}
          </div>
        </section>

        <section class="pref-card">
          <div class="llm-actions">
            <button class="ghost-button" data-gui-test type="button">Test Active Provider</button>
            <button class="detail-trigger" data-gui-save type="button">Save Settings</button>
            <button class="ghost-button compact" data-gui-logout type="button">Lock Panel</button>
          </div>
        </section>
      </div>
    `;
  }

  function renderGuiModal() {
    updateGuiRuntimeMeta();
    if (!state.gui.bootstrap) {
      els.llmBody.innerHTML = '<div class="empty-state">正在加载 LLM 控制台配置…</div>';
      return;
    }
    if (!state.gui.bootstrap.auth_enabled) {
      els.llmBody.innerHTML = renderGuiDisabled();
      return;
    }
    if (!state.gui.token) {
      els.llmBody.innerHTML = renderGuiLocked();
      return;
    }
    if (!state.gui.settings || !state.gui.draft) {
      els.llmBody.innerHTML = '<div class="empty-state">正在解密并读取已保存的 GUI 设置…</div>';
      return;
    }
    els.llmBody.innerHTML = renderGuiUnlocked();
  }

  async function loginGui() {
    const password = String(state.gui.pendingPassword || "").trim();
    if (!password) {
      setGuiFeedback("请输入 GUI_ADMIN_PASSWORD。", "warn");
      renderGuiModal();
      return;
    }
    try {
      const payload = await requestJson("/api/gui/auth/login", {
        method: "POST",
        body: { password },
      });
      state.gui.token = payload.token;
      saveGuiToken(payload.token);
      state.gui.pendingPassword = "";
      setGuiFeedback("GUI 管理会话已解锁。", "ok");
      await loadGuiSettings();
    } catch (error) {
      setGuiFeedback(error.message, "warn");
      renderGuiModal();
    }
  }

  async function saveGuiSettings() {
    try {
      const payload = await requestJson("/api/gui/llm/settings", {
        method: "PUT",
        token: state.gui.token,
        body: buildGuiPayloadFromDraft(),
      });
      state.gui.settings = {
        ...(state.gui.settings || {}),
        ...payload,
        masked_config: payload.masked_config,
      };
      state.gui.bootstrap = {
        ...(state.gui.bootstrap || {}),
        current: payload.current,
        runtime_source: payload.runtime_source,
        config_error: payload.config_error || null,
      };
      state.gui.draft = buildGuiDraft(payload.masked_config);
      state.gui.clearSecretFields = {};
      state.gui.testResult = null;
      await loadGuiUsageSummary({ quiet: true });
      setGuiFeedback(`已保存 GUI LLM 设置，当前运行源为 ${payload.runtime_source}。`, "ok");
      renderGuiModal();
      await loadAll();
    } catch (error) {
      if (error.status === 401) {
        clearGuiSession();
      }
      setGuiFeedback(error.message, "warn");
      renderGuiModal();
    }
  }

  async function testGuiSettings() {
    try {
      state.gui.testResult = await requestJson("/api/gui/llm/settings/test", {
        method: "POST",
        token: state.gui.token,
        body: buildGuiPayloadFromDraft(),
      });
      renderGuiModal();
    } catch (error) {
      if (error.status === 401) {
        clearGuiSession();
      }
      setGuiFeedback(error.message, "warn");
      renderGuiModal();
    }
  }

  function applyTheme(themeId) {
    state.prefs.theme = themeId || "all";
    savePrefs();
    renderThemeShortcuts();
    renderFeedControls();
    loadFeed();
  }

  function applySearchTicker(ticker) {
    state.prefs.query = String(ticker || "").toUpperCase();
    state.prefs.focusOnly = false;
    savePrefs();
    renderFeedControls();
    loadFeed();
  }

  function addFocusTicker(rawValue) {
    const ticker = String(rawValue || "").trim().toUpperCase().replace(/\s+/g, "");
    if (!ticker) return;
    if (state.prefs.focusTickers.includes(ticker)) return;
    state.prefs.focusTickers = [...state.prefs.focusTickers, ticker].slice(0, 24);
    savePrefs();
    renderFocusChips();
    renderOpsStrip();
    renderCorpusAndOps();
    if (state.prefs.focusOnly) {
      loadFeed();
    }
  }

  function removeFocusTicker(ticker) {
    state.prefs.focusTickers = state.prefs.focusTickers.filter((item) => item !== ticker);
    savePrefs();
    renderFocusChips();
    renderOpsStrip();
    renderCorpusAndOps();
    if (state.prefs.focusOnly) {
      loadFeed();
    }
  }

  function debounce(fn, wait) {
    let timer = null;
    return function (...args) {
      clearTimeout(timer);
      timer = setTimeout(() => fn.apply(this, args), wait);
    };
  }

  async function openDetail(filingId) {
    const payload = await fetchJson(`/api/filings/${filingId}`);
    const analysis = payload.analysis;
    const chunks = payload.chunks || [];
    const themes = payload.themes || [];
    const items = payload.sec_items?.length ? payload.sec_items.join(", ") : `Form ${payload.form_type}`;

    els.drawerContent.innerHTML = `
      <section class="detail-hero">
        <div class="badge-row">
          <span class="badge tier-${escapeHtml(String(payload.tier))}">Tier ${escapeHtml(String(payload.tier))}</span>
          <span class="badge">${escapeHtml(payload.form_type)}</span>
          <span class="badge ${impactTone(analysis?.impact || "中性")}">${escapeHtml(analysis?.impact || "待分析")}</span>
          <span class="badge score-${scoreTone(payload.signal_score)}">Signal ${escapeHtml(String(payload.signal_score))}</span>
        </div>
        <div>
          <h3>${escapeHtml(payload.ticker)} · ${escapeHtml(payload.company_name)}</h3>
          <div class="detail-meta">${escapeHtml(formatDate(payload.published_at))} · ${escapeHtml(items)}</div>
        </div>
        <div class="badge-row">
          <a class="external-link" href="${escapeHtml(payload.entry_link)}" target="_blank" rel="noreferrer">SEC Index</a>
          ${
            payload.primary_document_url
              ? `<a class="external-link" href="${escapeHtml(payload.primary_document_url)}" target="_blank" rel="noreferrer">Primary Document</a>`
              : ""
          }
        </div>
      </section>

      <div class="theme-pill-row">
        ${themes.map((theme) => `<span class="badge">${escapeHtml(theme.label)}</span>`).join("")}
      </div>

      <div class="detail-grid">
        <section class="detail-section">
          <h3>AI Brief</h3>
          <p>${escapeHtml(analysis?.summary || payload.raw_feed_summary || "还没有分析结果。")}</p>
          <div class="takeaway-list">
            ${(analysis?.key_takeaways || []).map((item) => `<div class="takeaway-item">${escapeHtml(item)}</div>`).join("")}
          </div>
        </section>

        <section class="detail-section">
          <h3>Corpus Chunks</h3>
          <div class="chunk-grid">
            ${chunks
              .slice(0, 8)
              .map(
                (chunk) => `
                  <article class="chunk-card">
                    <div class="chunk-meta">Chunk ${escapeHtml(String(chunk.index))} · ${numberFormat(
                      chunk.char_count
                    )} chars · ~${numberFormat(chunk.token_estimate)} tokens</div>
                    <div>${escapeHtml(chunk.content)}</div>
                  </article>
                `
              )
              .join("")}
          </div>
        </section>

        <section class="detail-section">
          <h3>Raw Document Preview</h3>
          <div class="raw-text">${escapeHtml((payload.raw_document_text || "暂无真实文档文本。").slice(0, 6000))}</div>
        </section>
      </div>
    `;

    els.overlay.classList.remove("hidden");
    els.drawer.classList.remove("hidden");
    els.drawer.setAttribute("aria-hidden", "false");
  }

  function closeDetail() {
    els.overlay.classList.add("hidden");
    els.drawer.classList.add("hidden");
    els.drawer.setAttribute("aria-hidden", "true");
  }

  async function loadDashboard() {
    state.dashboard = await fetchJson("/api/dashboard");
    renderDashboard();
  }

  async function loadFeed() {
    savePrefs();
    renderFeedControls();
    state.feed = await fetchJson(buildFeedUrl());
    renderFeedControls();
    renderFeed();
    renderCommandResults();
  }

  async function loadAll() {
    try {
      await Promise.all([loadDashboard(), loadFeed()]);
    } catch (error) {
      setStatusError(error.message);
    }
  }

  const debouncedFeedReload = debounce(() => {
    loadFeed().catch((error) => {
      els.resultMeta.textContent = error.message;
      els.filingsFeed.innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
    });
  }, 260);

  function bindEvents() {
    els.refreshButton.addEventListener("click", () => {
      // show loading state while refresh runs
      try {
        els.refreshButton.classList.add('is-loading');
        els.refreshButton.disabled = true;
        const p = Promise.resolve(loadAll());
        p.then(() => {
          showToast('刷新完成');
        }).catch((err) => {
          console.error('loadAll failed', err);
          showToast('刷新失败: ' + (err && err.message ? err.message : '未知错误'));
        }).finally(() => {
          els.refreshButton.classList.remove('is-loading');
          els.refreshButton.disabled = false;
        });
      } catch (e) {
        els.refreshButton.classList.remove('is-loading');
        els.refreshButton.disabled = false;
        console.error(e);
      }
    });

    els.feedSearch.addEventListener("input", (event) => {
      state.prefs.query = event.target.value.trim();
      savePrefs();
      debouncedFeedReload();
    });

    els.tierFilters.addEventListener("click", (event) => {
      const button = event.target.closest("[data-tier]");
      if (!button) return;
      state.prefs.tier = button.getAttribute("data-tier") || "all";
      savePrefs();
      renderFeedControls();
      loadFeed();
    });

    [els.impactFilter, els.formFilter, els.themeFilter].forEach((element) => {
      element.addEventListener("change", () => {
        state.prefs.impact = els.impactFilter.value;
        state.prefs.formType = els.formFilter.value;
        state.prefs.theme = els.themeFilter.value;
        savePrefs();
        renderThemeShortcuts();
        loadFeed();
      });
    });

    els.clearFilters.addEventListener("click", () => {
      state.prefs = {
        ...state.prefs,
        query: "",
        tier: "all",
        impact: "all",
        formType: "",
        theme: "all",
        focusOnly: false,
      };
      savePrefs();
      renderFeedControls();
      renderThemeShortcuts();
      loadFeed();
    });

    els.focusAdd.addEventListener("click", () => {
      addFocusTicker(els.focusInput.value);
      els.focusInput.value = "";
    });

    els.focusInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        addFocusTicker(els.focusInput.value);
        els.focusInput.value = "";
      }
    });

    els.autoRefreshToggle.addEventListener("change", () => {
      state.prefs.autoRefresh = els.autoRefreshToggle.checked;
      savePrefs();
      renderOpsStrip();
    });

    els.denseModeToggle.addEventListener("change", () => {
      state.prefs.denseMode = els.denseModeToggle.checked;
      savePrefs();
      renderFeed();
    });

    els.focusOnlyToggle.addEventListener("change", () => {
      state.prefs.focusOnly = els.focusOnlyToggle.checked;
      savePrefs();
      renderOpsStrip();
      loadFeed();
    });

    document.body.addEventListener("click", (event) => {
      const filingButton = event.target.closest("[data-filing-id]");
      if (filingButton) {
        openDetail(filingButton.getAttribute("data-filing-id"));
        return;
      }

      const themeButton = event.target.closest("[data-theme-apply]");
      if (themeButton) {
        applyTheme(themeButton.getAttribute("data-theme-apply"));
        return;
      }

      const tickerButton = event.target.closest("[data-search-ticker]");
      if (tickerButton) {
        applySearchTicker(tickerButton.getAttribute("data-search-ticker"));
        return;
      }

      const focusRemove = event.target.closest("[data-remove-focus]");
      if (focusRemove) {
        removeFocusTicker(focusRemove.getAttribute("data-remove-focus"));
      }
    });

    els.overlay.addEventListener("click", closeDetail);
    els.drawerClose.addEventListener("click", closeDetail);

    els.commandOpen.addEventListener("click", openCommandModal);
    els.commandClose.addEventListener("click", closeCommandModal);
    els.commandOverlay.addEventListener("click", (event) => {
      if (event.target === els.commandOverlay) {
        closeCommandModal();
      }
    });
    els.commandInput.addEventListener("input", (event) => {
      state.commandQuery = event.target.value;
      renderCommandResults();
    });
    els.commandInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        const first = els.commandResults.querySelector(".command-result");
        if (first) {
          executeCommand(first.getAttribute("data-command-type"), first.getAttribute("data-command-id"));
        }
      }
    });
    els.commandResults.addEventListener("click", (event) => {
      const button = event.target.closest(".command-result");
      if (!button) return;
      executeCommand(button.getAttribute("data-command-type"), button.getAttribute("data-command-id"));
    });

    els.llmSettingsOpen.addEventListener("click", () => {
      openGuiModal().catch((error) => {
        setGuiFeedback(error.message, "warn");
        renderGuiModal();
      });
    });
    els.llmClose.addEventListener("click", closeGuiModal);
    els.llmOverlay.addEventListener("click", (event) => {
      if (event.target === els.llmOverlay) {
        closeGuiModal();
      }
    });
    els.llmBody.addEventListener("input", (event) => {
      const target = event.target;
      if (target.id === "gui-admin-password") {
        state.gui.pendingPassword = target.value;
        return;
      }
      const topField = target.getAttribute("data-gui-top");
      if (topField && state.gui.draft) {
        state.gui.draft[topField] = target.value;
        if (topField === "analysis_provider") {
          renderGuiModal();
          ensureActiveProviderModelCatalog();
        }
        return;
      }
      const providerModelSelect = target.getAttribute("data-gui-model-select");
      if (providerModelSelect) {
        const providerId = target.getAttribute("data-gui-provider");
        if (providerId && state.gui.draft?.providers?.[providerId] && target.value !== "__custom__") {
          state.gui.draft.providers[providerId].model = target.value;
          renderGuiModal();
        }
        return;
      }
      const usageProviderId = target.getAttribute("data-gui-usage-provider");
      const usageField = target.getAttribute("data-gui-usage-field");
      if (usageProviderId && usageField && state.gui.draft?.usage_monitoring?.providers?.[usageProviderId]) {
        let nextValue = target.value;
        if (["enabled", "alert_enabled", "prefer_provider_api"].includes(usageField)) {
          nextValue = String(target.value) === "true";
        } else if (usageField === "limit_value") {
          nextValue = Number(target.value || 0);
        } else if (usageField === "threshold_percentages") {
          nextValue = String(target.value || "")
            .split(",")
            .map((item) => item.trim())
            .filter(Boolean)
            .map((item) => Number(item))
            .filter((item) => Number.isFinite(item) && item > 0);
        }
        state.gui.draft.usage_monitoring.providers[usageProviderId][usageField] = nextValue;
        if (usageField === "metric_name") {
          state.gui.draft.usage_monitoring.providers[usageProviderId].metric_unit = metricUnitForName(String(nextValue || ""));
          renderGuiModal();
        }
        return;
      }
      const providerId = target.getAttribute("data-gui-provider");
      const field = target.getAttribute("data-gui-field");
      if (providerId && field && state.gui.draft?.providers?.[providerId]) {
        state.gui.draft.providers[providerId][field] = target.value;
        if (target.value.trim()) {
          state.gui.clearSecretFields[providerId] = state.gui.clearSecretFields[providerId] || {};
          state.gui.clearSecretFields[providerId][field] = false;
        }
        if (["api_key", "base_url", "api_version", "org", "token", "node_binary"].includes(field)) {
          delete state.gui.modelCatalogs[providerId];
        }
      }
    });
    els.llmBody.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && event.target.id === "gui-admin-password") {
        event.preventDefault();
        loginGui();
      }
    });
    els.llmBody.addEventListener("change", (event) => {
      const target = event.target;
      const providerId = target.getAttribute("data-gui-provider");
      if (!providerId || !target.getAttribute("data-gui-model-select")) {
        return;
      }
      if (state.gui.draft?.providers?.[providerId] && target.value !== "__custom__") {
        state.gui.draft.providers[providerId].model = target.value;
        renderGuiModal();
      }
    });
    els.llmBody.addEventListener("click", (event) => {
      const loginButton = event.target.closest("[data-gui-login]");
      if (loginButton) {
        loginGui();
        return;
      }
      const saveButton = event.target.closest("[data-gui-save]");
      if (saveButton) {
        saveGuiSettings();
        return;
      }
      const testButton = event.target.closest("[data-gui-test]");
      if (testButton) {
        testGuiSettings();
        return;
      }
      const logoutButton = event.target.closest("[data-gui-logout]");
      if (logoutButton) {
        clearGuiSession();
        setGuiFeedback("GUI 管理会话已锁定。", "ok");
        renderGuiModal();
        return;
      }
      const clearSecretButton = event.target.closest("[data-gui-clear-secret]");
      if (clearSecretButton) {
        const [providerId, field] = String(clearSecretButton.getAttribute("data-gui-clear-secret") || "").split(":");
        if (!providerId || !field) return;
        state.gui.clearSecretFields[providerId] = state.gui.clearSecretFields[providerId] || {};
        state.gui.clearSecretFields[providerId][field] = !state.gui.clearSecretFields[providerId][field];
        if (state.gui.draft?.providers?.[providerId]) {
          state.gui.draft.providers[providerId][field] = "";
        }
        delete state.gui.modelCatalogs[providerId];
        renderGuiModal();
        return;
      }
      const refreshModelsButton = event.target.closest("[data-gui-refresh-models]");
      if (refreshModelsButton) {
        const providerId = String(refreshModelsButton.getAttribute("data-gui-refresh-models") || "").trim();
        if (!providerId) return;
        refreshProviderModels(providerId);
        renderGuiModal();
        return;
      }
      const syncUsageButton = event.target.closest("[data-gui-usage-sync]");
      if (syncUsageButton) {
        const providerId = String(syncUsageButton.getAttribute("data-gui-usage-sync") || "").trim();
        syncGuiUsage(providerId || null);
        return;
      }
    });

    document.addEventListener("keydown", (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        if (els.commandOverlay.classList.contains("hidden")) openCommandModal();
        else closeCommandModal();
        return;
      }

      if (event.key === "/" && !isTypingTarget(event.target)) {
        event.preventDefault();
        els.feedSearch.focus();
        els.feedSearch.select();
        return;
      }

      if (event.key === "Escape") {
        closeDetail();
        closeCommandModal();
        closeGuiModal();
      }
    });
  }

  function isTypingTarget(target) {
    if (!target) return false;
    const tagName = target.tagName;
    return tagName === "INPUT" || tagName === "TEXTAREA" || target.isContentEditable;
  }

  syncPreferenceControls();
  bindEvents();
  loadAll();

  window.setInterval(() => {
    if (state.prefs.autoRefresh) {
      loadAll();
    }
  }, refreshMs);
})();
