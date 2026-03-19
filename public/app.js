(function () {
  const metaApiBase = document
    .querySelector('meta[name="sec-monitor-api-base"]')
    ?.getAttribute("content")
    ?.trim();
  const apiBase = metaApiBase || "";
  const refreshMs = 45000;

  const state = {
    dashboard: null,
    activeTier: "all",
  };

  const els = {
    healthPill: document.getElementById("health-pill"),
    statusMeta: document.getElementById("status-meta"),
    heroMetrics: document.getElementById("hero-metrics"),
    trendChart: document.getElementById("trend-chart"),
    corpusGrid: document.getElementById("corpus-grid"),
    sentimentStack: document.getElementById("sentiment-stack"),
    filingsFeed: document.getElementById("filings-feed"),
    resultMeta: document.getElementById("result-meta"),
    runsTable: document.getElementById("runs-table"),
    overlay: document.getElementById("overlay"),
    drawer: document.getElementById("drawer"),
    drawerContent: document.getElementById("drawer-content"),
    drawerClose: document.getElementById("drawer-close"),
    tierFilters: document.getElementById("tier-filters"),
  };

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
    if (!value) {
      return "N/A";
    }
    return new Intl.DateTimeFormat("zh-Hant-HK", {
      year: "numeric",
      month: "short",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(value));
  }

  function tierLabel(tier) {
    return `Tier ${tier}`;
  }

  function impactTone(impact) {
    if (impact === "利好") return "impact-positive";
    if (impact === "利空") return "impact-negative";
    return "impact-neutral";
  }

  async function fetchJson(path) {
    const response = await fetch(`${apiBase}${path}`);
    if (!response.ok) {
      throw new Error(`${response.status} ${response.statusText}`);
    }
    return response.json();
  }

  function renderStatus() {
    const lastRun = state.dashboard?.last_run;
    const analysis = state.dashboard?.analysis;
    const dataPolicy = state.dashboard?.data_policy;
    const analysisText = analysis
      ? ` · analysis ${analysis.provider}/${analysis.model || "unconfigured"}${analysis.available ? "" : " (disabled)"}`
      : "";
    const realOnlyText = dataPolicy?.synthetic_data === false ? " · real-data only" : "";
    if (!lastRun) {
      els.healthPill.textContent = "Waiting for first real ingestion";
      els.healthPill.className = "status-pill is-warn";
      els.statusMeta.textContent = `Worker 尚未写入任何真实 ingestion run。${analysisText}${realOnlyText}`;
      return;
    }

    const healthy = lastRun.status === "completed";
    els.healthPill.className = `status-pill ${healthy ? "" : "is-danger"}`.trim();
    els.healthPill.textContent = healthy ? "Pipeline healthy" : "Pipeline needs attention";
    els.statusMeta.textContent = `${formatDate(lastRun.completed_at || lastRun.started_at)} · seen ${numberFormat(
      lastRun.entries_seen
    )} entries · matched ${numberFormat(lastRun.matched_entries)} watchlist filings${analysisText}${realOnlyText}`;
  }

  function renderHeroMetrics() {
    const dashboard = state.dashboard;
    const cards = [
      {
        label: "Total Filings",
        value: numberFormat(dashboard.totals.filings),
        meta: `Watchlist size ${numberFormat(dashboard.watchlist_size)}`,
        tone: "tier3",
      },
      {
        label: "Tier 1 Alerts",
        value: numberFormat(dashboard.tier_counts.tier1),
        meta: "高优先级事件池",
        tone: "tier1",
      },
      {
        label: "Analyses",
        value: numberFormat(dashboard.totals.analyzed),
        meta: "已完成 LLM 结构化摘要",
        tone: "tier2",
      },
      {
        label: "Corpus Chunks",
        value: numberFormat(dashboard.totals.chunks),
        meta: "可直接进入 RAG / eval pipeline",
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

  function renderTrendChart() {
    const rows = state.dashboard.daily_counts || [];
    if (!rows.length) {
      els.trendChart.innerHTML = '<div class="empty-state">暂无真实 SEC 历史数据。请先运行 worker 抓取并入库。</div>';
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
        const total = Math.max(t1 + t2 + t3, 1);
        const scale = 164 / maxValue;
        const label = row.date.slice(5).replace("-", "/");

        return `
          <div class="bar-day">
            <div class="bar-stack" title="${escapeHtml(row.date)} | T1 ${t1} / T2 ${t2} / T3 ${t3}">
              <div class="bar-segment tier3" style="height:${Math.max(6, t3 * scale)}px"></div>
              <div class="bar-segment tier2" style="height:${Math.max(6, t2 * scale)}px"></div>
              <div class="bar-segment tier1" style="height:${Math.max(6, t1 * scale)}px"></div>
            </div>
            <div class="bar-label">${escapeHtml(label)}</div>
            <div class="bar-label">${escapeHtml(total)}</div>
          </div>
        `;
      })
      .join("");
  }

  function renderCorpus() {
    const totals = state.dashboard.totals;
    const cards = [
      {
        label: "Raw Text Ready",
        value: numberFormat(totals.raw_text_ready),
        meta: "已抓取主文档正文",
      },
      {
        label: "Corpus Coverage",
        value: `${Math.round((totals.raw_text_ready / Math.max(totals.filings, 1)) * 100)}%`,
        meta: "进入语料池的 filing 占比",
      },
      {
        label: "Avg Chunks / Filing",
        value: (totals.chunks / Math.max(totals.raw_text_ready, 1)).toFixed(1),
        meta: "后续切 embedding 的密度",
      },
      {
        label: "Analysis Coverage",
        value: `${Math.round((totals.analyzed / Math.max(totals.filings, 1)) * 100)}%`,
        meta: "已有结构化解读的比例",
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

    const sentiments = state.dashboard.sentiment_counts || {};
    const total = Object.values(sentiments).reduce((sum, value) => sum + Number(value || 0), 0) || 1;
    els.sentimentStack.innerHTML = Object.entries(sentiments)
      .map(([label, count]) => {
        const pct = Math.round((Number(count || 0) / total) * 100);
        return `
          <div class="sentiment-row">
            <div>
              <strong>${escapeHtml(label)}</strong>
              <div class="metric-meta">${escapeHtml(String(count))} filings</div>
              <div class="sentiment-bar-track">
                <div class="sentiment-bar-fill" style="width:${pct}%"></div>
              </div>
            </div>
            <strong>${pct}%</strong>
          </div>
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

  function filteredFilings() {
    const filings = state.dashboard?.recent_filings || [];
    if (state.activeTier === "all") {
      return filings;
    }
    return filings.filter((filing) => String(filing.tier) === state.activeTier);
  }

  function renderFilings() {
    const filings = filteredFilings();
    els.resultMeta.textContent = `${numberFormat(filings.length)} real dossiers visible`;

    if (!filings.length) {
      els.filingsFeed.innerHTML = '<div class="empty-state">当前筛选条件下没有真实 filing 数据。</div>';
      return;
    }

    els.filingsFeed.innerHTML = filings
      .map((filing) => {
        const items = filing.sec_items?.length ? filing.sec_items.join(", ") : `Form ${filing.form_type}`;
        const analysis = filing.analysis;
        const takeaways = (analysis?.key_takeaways || []).slice(0, 2);
        const impact = analysis?.impact || "待分析";
        const summary = analysis?.summary || filing.raw_feed_summary || "暂无真实摘要，等待 SEC 文本或分析结果入库。";

        return `
          <article class="filing-card" data-tier="${escapeHtml(String(filing.tier))}">
            <div class="filing-card-header">
              <div>
                <div class="badge-row">
                  <span class="badge tier-${escapeHtml(String(filing.tier))}">${escapeHtml(tierLabel(filing.tier))}</span>
                  <span class="badge">${escapeHtml(filing.form_type)}</span>
                  <span class="badge ${impactTone(impact)}">${escapeHtml(impact)}</span>
                </div>
                <h3>${escapeHtml(filing.ticker)} · ${escapeHtml(filing.company_name)}</h3>
              </div>
              <div class="feed-subline">${escapeHtml(formatDate(filing.published_at))}</div>
            </div>

            <div class="feed-subline">${escapeHtml(items)}</div>
            <div class="filing-summary">${escapeHtml(summary)}</div>
          <div class="takeaway-list">
              ${takeaways
                .map((takeaway) => `<div class="takeaway-item">${escapeHtml(takeaway)}</div>`)
                .join("")}
            </div>

            <div class="filing-footer">
              <div class="feed-subline">
                ${numberFormat(filing.document_char_count)} chars · ${numberFormat(filing.chunk_count)} chunks
              </div>
              <button class="detail-trigger" data-filing-id="${escapeHtml(String(filing.id))}">Open dossier</button>
            </div>
          </article>
        `;
      })
      .join("");
  }

  async function openDetail(filingId) {
    const payload = await fetchJson(`/api/filings/${filingId}`);
    const analysis = payload.analysis;
    const chunks = payload.chunks || [];
    const chunkPreview = chunks.slice(0, 8);
    const items = payload.sec_items?.length ? payload.sec_items.join(", ") : `Form ${payload.form_type}`;

    els.drawerContent.innerHTML = `
      <section class="detail-hero">
        <div class="badge-row">
          <span class="badge tier-${escapeHtml(String(payload.tier))}">${escapeHtml(tierLabel(payload.tier))}</span>
          <span class="badge">${escapeHtml(payload.form_type)}</span>
          <span class="badge ${impactTone(analysis?.impact || "中性")}">${escapeHtml(analysis?.impact || "待分析")}</span>
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

      <div class="detail-grid">
        <section class="detail-section">
          <h3>AI Brief</h3>
          <p>${escapeHtml(analysis?.summary || payload.raw_feed_summary || "还没有分析结果。")}</p>
          <div class="takeaway-list">
            ${(analysis?.key_takeaways || [])
              .map((takeaway) => `<div class="takeaway-item">${escapeHtml(takeaway)}</div>`)
              .join("")}
          </div>
        </section>

        <section class="detail-section">
          <h3>Corpus Chunks</h3>
          <div class="chunk-grid">
            ${chunkPreview
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
          ${
            chunks.length > chunkPreview.length
              ? `<p class="detail-meta">Showing ${chunkPreview.length} of ${chunks.length} chunks.</p>`
              : ""
          }
        </section>

        <section class="detail-section">
          <h3>Raw Document Preview</h3>
          <div class="raw-text">${escapeHtml((payload.raw_document_text || "暂无真实文档文本，说明该 filing 还未成功抓取正文。").slice(0, 6000))}</div>
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

  function renderDashboard() {
    renderStatus();
    renderHeroMetrics();
    renderTrendChart();
    renderCorpus();
    renderFilings();
    renderRuns();
  }

  async function loadDashboard() {
    try {
      state.dashboard = await fetchJson("/api/dashboard");
      renderDashboard();
    } catch (error) {
      els.healthPill.textContent = "API unavailable";
      els.healthPill.className = "status-pill is-danger";
      els.statusMeta.textContent = error.message;
      els.filingsFeed.innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
    }
  }

  function bindEvents() {
    els.tierFilters.addEventListener("click", (event) => {
      const button = event.target.closest("[data-tier]");
      if (!button) return;
      state.activeTier = button.getAttribute("data-tier") || "all";
      Array.from(els.tierFilters.querySelectorAll(".filter-chip")).forEach((chip) => {
        chip.classList.toggle("is-active", chip === button);
      });
      renderFilings();
    });

    els.filingsFeed.addEventListener("click", (event) => {
      const button = event.target.closest("[data-filing-id]");
      if (!button) return;
      openDetail(button.getAttribute("data-filing-id"));
    });

    els.overlay.addEventListener("click", closeDetail);
    els.drawerClose.addEventListener("click", closeDetail);
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        closeDetail();
      }
    });
  }

  bindEvents();
  loadDashboard();
  window.setInterval(loadDashboard, refreshMs);
})();
