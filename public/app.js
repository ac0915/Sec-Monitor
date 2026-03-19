(function () {
  const metaApiBase = document
    .querySelector('meta[name="sec-monitor-api-base"]')
    ?.getAttribute("content")
    ?.trim();
  const apiBase = metaApiBase || "";
  const refreshMs = 45000;
  const prefsKey = "sec-monitor-ui-v3";
  const commandHistoryKey = "sec-monitor-command-history-v1";

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

  function fetchJson(path) {
    return fetch(`${apiBase}${path}`).then((response) => {
      if (!response.ok) {
        throw new Error(`${response.status} ${response.statusText}`);
      }
      return response.json();
    });
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
      ? `analysis ${analysis.provider}/${analysis.model || "unconfigured"}${analysis.available ? "" : " disabled"}`
      : "analysis unavailable";
    const telegramText = telegram?.assistant_available
      ? `Telegram assistant ready · ${telegram.subscribed_chats} subscribed`
      : "Telegram assistant disabled";
    const realText = dataPolicy?.synthetic_data === false ? "real-data only" : "data policy unknown";

    if (!lastRun) {
      els.healthPill.textContent = "Waiting for first real ingestion";
      els.healthPill.className = "status-pill is-warn";
      els.statusMeta.textContent = `${analysisText} · ${telegramText} · ${realText}`;
      return;
    }

    const healthy = lastRun.status === "completed";
    els.healthPill.className = `status-pill ${healthy ? "" : "is-danger"}`.trim();
    els.healthPill.textContent = healthy ? "Pipeline healthy" : "Pipeline needs attention";
    els.statusMeta.textContent =
      `${formatDate(lastRun.completed_at || lastRun.started_at)} · seen ${numberFormat(lastRun.entries_seen)} · matched ${numberFormat(
        lastRun.matched_entries
      )} · ${analysisText} · ${telegramText} · ${realText}`;
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
        meta: `${analysis.provider}/${analysis.model || "n/a"}`,
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
      loadAll();
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
