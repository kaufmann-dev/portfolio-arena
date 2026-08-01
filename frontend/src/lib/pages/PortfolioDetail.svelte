<script lang="ts">
  import { apiJson } from "../api/client";
  import type {
    ManagedPortfolioDetail,
    PortfolioAnalysisResponse,
    RebuiltPortfolioDetail,
  } from "../api/types";
  import EvidenceBadge from "../components/EvidenceBadge.svelte";
  import LineChart, { type ChartSeries } from "../components/LineChart.svelte";
  import MarketDataWarning from "../components/MarketDataWarning.svelte";
  import PolicyMatrix from "../components/PolicyMatrix.svelte";
  import SignalHistory from "../components/SignalHistory.svelte";
  import SignalMatrix from "../components/SignalMatrix.svelte";
  import { parseDirection } from "../arena";
  import {
    ageLabel,
    fmtDate,
    fmtDateTime,
    num,
    pct,
    pctPoints,
    pctPointsSignClass,
    pctSignClass,
  } from "../format";
  import { link } from "../stores/router.svelte";

  interface Props {
    slug: string;
  }

  const { slug }: Props = $props();
  let copyResult = $state<"" | "copied" | "error">("");

  function requestUrl(portfolioSlug: string): string {
    const source = new URLSearchParams(window.location.search);
    const track = source.get("track");
    const direction = parseDirection(source.get("direction"));
    const query = new URLSearchParams({ direction });
    if (track !== "managed" && track !== "rebuilt") {
      return `/api/portfolios/${portfolioSlug}?${query.toString()}`;
    }

    query.set("track", track);
    if (track === "rebuilt") {
      for (const key of ["view", "objective", "cost_basis", "horizon"]) {
        const value = source.get(key);
        if (value) query.set(key, value);
      }
    }
    return `/api/portfolios/${portfolioSlug}?${query.toString()}`;
  }

  function chartSeries(portfolio: ManagedPortfolioDetail | RebuiltPortfolioDetail): ChartSeries[] {
    if (!portfolio.series.length) return [];
    return [
      { name: portfolio.name, points: portfolio.series },
      {
        name: portfolio.direction === "short" ? "Short SPY" : "SPY",
        points: portfolio.spy_series,
        dashed: true,
        color: "var(--spark)",
      },
    ];
  }

  function allocationTitle(index: number, total: number): string {
    return index === total - 1 ? "Initial allocation" : "Rebalance";
  }

  function markersFor(data: PortfolioAnalysisResponse): string[] {
    if (data.track === "managed") {
      return data.portfolio.allocations
        .map((allocation) => allocation.applied_date)
        .filter((date): date is string => date !== null);
    }
    return data.portfolio.signals.map((signal) => signal.effective_date);
  }

  async function copyPrompt(executionPrompt: string | null): Promise<void> {
    if (!executionPrompt) return;
    try {
      await navigator.clipboard.writeText(executionPrompt);
      copyResult = "copied";
    } catch {
      copyResult = "error";
    }
  }

  function requestErrorMessage(error: unknown): string {
    return error instanceof Error ? error.message : "Could not load this portfolio.";
  }
</script>

{#snippet metricTile(label: string, value: string, className = "")}
  <div class="metric card">
    <span class="metric-label">{label}</span>
    <span class={["metric-value", "num", className]}>{value}</span>
  </div>
{/snippet}

{#snippet allocationHistory(portfolio: ManagedPortfolioDetail)}
  <section class="history-section" aria-labelledby="allocation-history-title">
    <header class="section-head">
      <div>
        <h2 id="allocation-history-title">Allocation history</h2>
        <p>Stateful target portfolios, newest first.</p>
      </div>
      <span class="num">{portfolio.allocations.length}</span>
    </header>
    <div class="disclosure-list">
      {#each portfolio.allocations as allocation, index (allocation.id)}
        <details>
          <summary>
            <span class="disclosure-primary">
              <strong class="num">{fmtDate(allocation.effective_date)}</strong>
              <span>{allocationTitle(index, portfolio.allocations.length)}</span>
            </span>
            <span class="disclosure-meta">
              {#if !allocation.applied_date}
                Pending
              {:else if allocation.turnover_pct !== null}
                {pctPoints(allocation.turnover_pct)} turnover
              {:else}
                Applied
              {/if}
            </span>
          </summary>
          <div class="disclosure-body">
            <p class="muted num">Entered {fmtDateTime(allocation.entered_at)}</p>
            {#if allocation.note}<p>{allocation.note}</p>{/if}
            <div class="table-scroll">
              <table class="data-table">
                <caption class="visually-hidden">
                  Positions for the allocation effective {fmtDate(allocation.effective_date)}
                </caption>
                <thead><tr><th scope="col">Symbol</th><th scope="col" class="right">Weight</th></tr></thead>
                <tbody>
                  {#each allocation.positions as position (position.symbol)}
                    <tr>
                      <td class="num">{position.symbol}</td>
                      <td class="right num">{pctPoints(position.weight_pct, 2)}</td>
                    </tr>
                  {/each}
                </tbody>
              </table>
            </div>
          </div>
        </details>
      {:else}
        <div class="empty-state compact">No allocations entered yet.</div>
      {/each}
    </div>
  </section>
{/snippet}

{#key slug}
  {@const request = apiJson<PortfolioAnalysisResponse>(requestUrl(slug))}
  {#await request}
    <div class="loading-block">
      <span class="spinner" aria-hidden="true"></span> Building portfolio analysis…
    </div>
  {:then data}
    {@const portfolio = data.portfolio}
    {@const managedPortfolio = data.track === "managed" ? (data.portfolio as ManagedPortfolioDetail) : null}
    {@const rebuiltPortfolio = data.track === "rebuilt" ? (data.portfolio as RebuiltPortfolioDetail) : null}
    {@const rebuiltContext = data.track === "rebuilt" ? data.context : null}
    {@const benchmarkName = portfolio.direction === "short" ? "Short SPY" : "SPY"}
    {@const arenaHref = `/?direction=${portfolio.direction}`}
    {@const series = chartSeries(portfolio)}
    {@const markers = markersFor(data)}
    <article class="portfolio-detail">
      <header class="detail-head split">
        <div>
          <nav class="crumbs" aria-label="Breadcrumb">
            <a href={arenaHref} onclick={(event) => link(event, arenaHref)}>Portfolio Arena</a>
            <span aria-hidden="true">/</span>
            <span>{data.track === "rebuilt" ? "Rebuilt" : "Managed"}</span>
          </nav>
          <h1>{portfolio.name}</h1>
          <p class="identity">
            <a
              href="/agent/{portfolio.agent.slug}"
              onclick={(event) => link(event, `/agent/${portfolio.agent.slug}`)}
            >
              {portfolio.agent.name}
            </a>
            · prompt
            <a
              href="/prompt/{portfolio.prompt.slug}"
              onclick={(event) => link(event, `/prompt/${portfolio.prompt.slug}`)}
            >
              {portfolio.prompt.name}
            </a>
            · {portfolio.direction} · {data.track}
            {#if data.as_of}· as of <span class="num">{data.as_of}</span>{/if}
          </p>
          {#if portfolio.execution_prompt}
            <div class="prompt-action">
              <button class="btn small" type="button" onclick={() => copyPrompt(portfolio.execution_prompt)}>
                Copy evaluation prompt
              </button>
              {#if copyResult}
                <span class={copyResult === "error" ? "neg" : "muted"} role="status">
                  {copyResult === "copied" ? "Copied." : "Copy failed."}
                </span>
              {/if}
            </div>
          {/if}
        </div>
        <div class="head-badges">
          <span class="badge">{portfolio.direction}</span>
          <span class="badge">{data.track}</span>
          <EvidenceBadge state={portfolio.evidence} />
          {#if portfolio.status === "archived"}<span class="badge">archived</span>{/if}
          {#if rebuiltPortfolio && rebuiltContext?.view === "common" && !rebuiltPortfolio.common_admitted && rebuiltPortfolio.status === "active" && !rebuiltPortfolio.founding_v2 && !rebuiltPortfolio.error}
            <span class="badge warn" title="Not yet admitted to the Common-policy meta-portfolio">
              H20 incubation
            </span>
          {/if}
          {#if portfolio.stale_data}<span class="badge warn">stale data</span>{/if}
          {#if portfolio.frozen_symbols.length}
            <span class="badge neg">{portfolio.frozen_symbols.length} frozen</span>
          {/if}
          {#if portfolio.is_liquidated}
            <span class="badge neg">{data.track === "rebuilt" ? "policy liquidated" : "liquidated"}</span>
          {/if}
        </div>
      </header>

      <MarketDataWarning status={data.market_data_status} asOf={data.as_of} />

      {#if portfolio.error}
        <div class="error-box" role="alert">Analysis failed: {portfolio.error}</div>
      {/if}

      {#if portfolio.is_liquidated}
        <div class="card warning-card" role="status">
          <strong
            >{data.track === "rebuilt" ? "Selected policy" : "Portfolio"} liquidated{portfolio.liquidated_at
              ? ` ${fmtDate(portfolio.liquidated_at)}`
              : ""}.</strong
          >
          {data.track === "rebuilt"
            ? "Independent signals continue to feed other policies and future cohorts."
            : "Its completed history remains visible, but it no longer accepts new allocations."}
        </div>
      {/if}

      {#if portfolio.frozen_symbols.length}
        <div class="error-box" role="alert">
          <strong>Frozen positions:</strong>
          {portfolio.frozen_symbols.join(", ")} stopped returning prices and remain at their last known values.
        </div>
      {/if}

      {#if rebuiltPortfolio && rebuiltContext}
        <section class="policy-context" aria-label="Selected rebuilt policy">
          <div>
            <span>Analysis mode</span>
            <strong
              >{rebuiltContext.view === "common"
                ? "Common policy"
                : rebuiltContext.view === "tuned"
                  ? "Portfolio tuned"
                  : "Signal Alpha"}</strong
            >
          </div>
          <div>
            <span>Aggregate book</span>
            <strong class="num">
              {rebuiltPortfolio.aggregate_policy
                ? `H${rebuiltPortfolio.aggregate_policy.horizon} · ${pctPoints(rebuiltPortfolio.aggregate_policy.exposure_pct, 0)} exposure${rebuiltPortfolio.aggregate_policy.provisional ? " · provisional" : ""}`
                : "Pending"}
            </strong>
          </div>
          <div>
            <span
              >{rebuiltPortfolio.aggregate_policy
                ? `H${rebuiltPortfolio.aggregate_policy.horizon}`
                : "Selected H"} completed / open</span
            >
            <strong class="num"
              >{rebuiltPortfolio.completion.complete_count} / {rebuiltPortfolio.completion.open_count}</strong
            >
          </div>
          <div>
            <span
              >{rebuiltPortfolio.aggregate_policy
                ? `H${rebuiltPortfolio.aggregate_policy.horizon}`
                : "Selected H"} completion</span
            >
            <strong class="num">{pct(rebuiltPortfolio.completion.completion_ratio, 0)}</strong>
          </div>
        </section>
      {/if}

      {#if portfolio.metrics.has_data}
        <section class="metric-grid" aria-label="Portfolio metrics">
          {@render metricTile(
            "Lower 95%",
            pct(portfolio.rank_score, 2),
            pctSignClass(portfolio.rank_score, 2),
          )}
          {@render metricTile(
            "Mean α/day",
            pct(portfolio.metrics.mean_daily_alpha, 2),
            pctSignClass(portfolio.metrics.mean_daily_alpha, 2),
          )}
          {@render metricTile(
            "Cumulative excess",
            pct(portfolio.metrics.cumulative_excess),
            pctSignClass(portfolio.metrics.cumulative_excess),
          )}
          {@render metricTile("Hit rate", pct(portfolio.metrics.hit_rate, 0))}
          {@render metricTile("Information ratio", num(portfolio.metrics.information_ratio))}
          {@render metricTile("Sharpe (rf=0)", num(portfolio.metrics.sharpe))}
          {@render metricTile("Max drawdown", pct(portfolio.metrics.max_drawdown))}
          {@render metricTile("Ann. volatility", pct(portfolio.metrics.ann_volatility))}
          {@render metricTile("Turnover", pctPoints(portfolio.metrics.turnover_pct, 0))}
          {@render metricTile("Cost drag", pctPoints(portfolio.metrics.cost_drag_pct, 2))}
          {#if managedPortfolio}
            {@render metricTile(
              "ITD return",
              pct(managedPortfolio.metrics.itd_return),
              pctSignClass(managedPortfolio.metrics.itd_return),
            )}
            {@render metricTile("Age", ageLabel(managedPortfolio.age_days))}
          {/if}
        </section>
      {:else}
        <div class="empty-state card">
          <h3>Evidence pending</h3>
          <p>
            {data.track === "rebuilt"
              ? "Daily signals will populate this policy as their holding periods complete."
              : "The first allocation has not produced a valued close yet."}
          </p>
        </div>
      {/if}

      {#if series.length}
        <section class="card chart-card">
          <header class="section-head">
            <div>
              <h2>NAV vs {benchmarkName}</h2>
              <p>Base 100, total return{rebuiltContext ? ` · ${rebuiltContext.cost_basis}` : ""}.</p>
            </div>
          </header>
          <LineChart {series} {markers} ariaLabel="{portfolio.name} NAV versus {benchmarkName}" />
          <p class="chart-note">
            Dotted vertical lines mark {data.track === "managed" ? "allocation" : "signal"} effective sessions.
          </p>
        </section>
      {/if}

      {#if managedPortfolio}
        {#if Object.keys(managedPortfolio.stale_days).length}
          <div class="card warning-card">
            <strong>Carried-forward prices:</strong>
            {Object.entries(managedPortfolio.stale_days)
              .map(([symbol, days]) => `${symbol} (${days.length} sessions)`)
              .join(", ")}.
          </div>
        {/if}
        <section class="data-section" aria-labelledby="managed-holdings-title">
          <header class="section-head">
            <div>
              <h2 id="managed-holdings-title">Current holdings</h2>
              <p>
                Drifted weights against the latest managed {managedPortfolio.direction === "short"
                  ? "short"
                  : "long"} target.
              </p>
            </div>
          </header>
          <div class="table-scroll">
            <table class="data-table">
              <caption class="visually-hidden">Current holdings for {managedPortfolio.name}</caption>
              <thead
                ><tr
                  ><th scope="col">Symbol</th><th scope="col" class="right">Weight</th><th
                    scope="col"
                    class="right">Target</th
                  ><th scope="col" class="right">Drift</th></tr
                ></thead
              >
              <tbody>
                {#each managedPortfolio.holdings as holding (holding.symbol)}
                  <tr>
                    <td class="num">{holding.symbol}</td>
                    <td class="right num">{pctPoints(holding.weight_pct)}</td>
                    <td class="right num">{pctPoints(holding.target_weight_pct)}</td>
                    <td
                      class="right num {pctPointsSignClass(holding.weight_pct - holding.target_weight_pct)}"
                    >
                      {pctPoints(holding.weight_pct - holding.target_weight_pct)}
                    </td>
                  </tr>
                {:else}
                  <tr><td colspan="4" class="table-empty">No current holdings.</td></tr>
                {/each}
              </tbody>
            </table>
          </div>
        </section>
        {@render allocationHistory(managedPortfolio)}
      {:else if rebuiltPortfolio && rebuiltContext}
        <section class="data-section" aria-labelledby="aggregate-holdings-title">
          <header class="section-head">
            <div>
              <h2 id="aggregate-holdings-title">Aggregate holdings</h2>
              {#if rebuiltPortfolio.aggregate_policy?.provisional}
                <p>
                  Live H20 incubation book. Each active signal contributes 1/20 of the exposure; the remainder
                  stays in {benchmarkName}.
                </p>
              {:else}
                <p>
                  Overlapping active {rebuiltPortfolio.direction} cohorts plus the unallocated
                  {benchmarkName} sleeve.
                </p>
              {/if}
            </div>
          </header>
          <div class="table-scroll">
            <table class="data-table">
              <caption class="visually-hidden">Aggregate holdings for {rebuiltPortfolio.name}</caption>
              <thead><tr><th scope="col">Symbol</th><th scope="col" class="right">Weight</th></tr></thead>
              <tbody>
                {#each rebuiltPortfolio.holdings as holding (holding.symbol)}
                  <tr>
                    <td class="num">{holding.symbol}</td>
                    <td class="right num">{pctPoints(holding.weight_pct, 2)}</td>
                  </tr>
                {:else}
                  <tr><td colspan="2" class="table-empty">No aggregate holdings available.</td></tr>
                {/each}
              </tbody>
            </table>
          </div>
        </section>

        <section class="data-section" aria-labelledby="active-cohorts-title">
          <header class="section-head">
            <div>
              <h2 id="active-cohorts-title">Active cohorts</h2>
              <p>
                Signals that are still contributing to the
                {rebuiltPortfolio.aggregate_policy?.provisional ? "incubation" : "selected"} aggregate policy.
              </p>
            </div>
            <span class="num">{rebuiltPortfolio.active_cohorts.length}</span>
          </header>
          <div class="disclosure-list">
            {#each rebuiltPortfolio.active_cohorts as cohort (cohort.signal_id)}
              <details>
                <summary>
                  <span class="disclosure-primary">
                    <strong class="num">{fmtDate(cohort.start_date)}</strong>
                    <span>Signal #{cohort.signal_id}</span>
                  </span>
                  <span class="disclosure-meta">
                    {cohort.age_sessions} sessions · ends {fmtDate(cohort.end_date)}
                  </span>
                </summary>
                <div class="disclosure-body">
                  <div class="table-scroll">
                    <table class="data-table">
                      <caption class="visually-hidden">
                        Positions for signal {cohort.signal_id}, effective {fmtDate(cohort.start_date)}
                      </caption>
                      <thead
                        ><tr><th scope="col">Symbol</th><th scope="col" class="right">Signal weight</th></tr
                        ></thead
                      >
                      <tbody>
                        {#each cohort.positions as position (position.symbol)}
                          <tr>
                            <td class="num">{position.symbol}</td>
                            <td class="right num">{pctPoints(position.weight_pct, 2)}</td>
                          </tr>
                        {/each}
                      </tbody>
                    </table>
                  </div>
                </div>
              </details>
            {:else}
              <div class="empty-state compact">No cohorts are active in this aggregate policy.</div>
            {/each}
          </div>
        </section>

        <SignalHistory
          slug={rebuiltPortfolio.slug}
          direction={rebuiltPortfolio.direction}
          initialSignals={rebuiltPortfolio.signals}
          initialNextCursor={rebuiltPortfolio.signals_next_cursor}
        />
        <SignalMatrix
          rows={[rebuiltPortfolio]}
          selectedHorizon={rebuiltContext.horizon ?? rebuiltPortfolio.selected_policy?.horizon ?? 1}
          context={rebuiltContext}
          {benchmarkName}
        />
        <PolicyMatrix cells={rebuiltPortfolio.policy_matrix} selected={rebuiltPortfolio.selected_policy} />
      {/if}
    </article>
  {:catch error}
    <div class="error-box" role="alert">{requestErrorMessage(error)}</div>
  {/await}
{/key}

<style>
  .portfolio-detail {
    min-width: 0;
    display: grid;
    gap: 22px;
  }

  .identity {
    margin-top: 9px;
    color: var(--text-secondary);
    font-size: 12px;
  }

  .prompt-action {
    display: flex;
    align-items: center;
    gap: 9px;
    margin-top: 12px;
    font-size: 11px;
  }

  .head-badges {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: 6px;
  }

  .policy-context {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 1px;
    background: var(--border-subtle);
  }

  .policy-context div {
    min-width: 0;
    display: grid;
    gap: 4px;
    padding: 12px;
    background: var(--bg-raised);
  }

  .policy-context span,
  .metric-label {
    color: var(--text-tertiary);
    font-size: 8.5px;
    font-weight: 750;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  .policy-context strong {
    font-size: 12px;
  }

  .metric-grid {
    display: grid;
    grid-template-columns: repeat(6, minmax(0, 1fr));
    gap: 1px;
    background: var(--border-subtle);
  }

  .metric {
    min-width: 0;
    min-height: 76px;
    display: grid;
    align-content: space-between;
    gap: 10px;
    padding: 12px;
    background: var(--bg-raised);
  }

  .metric-value {
    font-size: clamp(14px, 2vw, 18px);
    font-weight: 700;
  }

  .chart-card,
  .data-section,
  .history-section {
    min-width: 0;
    display: grid;
    gap: 12px;
  }

  .chart-note {
    color: var(--text-tertiary);
    font-size: 10px;
  }

  .warning-card {
    color: var(--warn);
    font-size: 12px;
  }

  @media (max-width: 960px) {
    .metric-grid {
      grid-template-columns: repeat(3, minmax(0, 1fr));
    }
  }

  @media (max-width: 680px) {
    .head-badges {
      justify-content: flex-start;
    }

    .policy-context {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .metric-grid {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
  }
</style>
