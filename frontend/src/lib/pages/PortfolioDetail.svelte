<script lang="ts">
  import { Accordion } from "bits-ui";

  import { apiJson } from "../api/client";
  import type { AllocationOut, PortfolioDetail, PortfolioDetailResponse } from "../api/types";
  import { ChevronDown, ChevronRight } from "@lucide/svelte";
  import LineChart, { type ChartSeries } from "../components/LineChart.svelte";
  import MarketDataWarning from "../components/MarketDataWarning.svelte";
  import { ageLabel, fmtDate, fmtDateTime, num, pct, pctPoints, signClass } from "../format";
  import { link } from "../stores/router.svelte";

  interface Props {
    slug: string;
  }

  const { slug }: Props = $props();

  let expanded = $state<string[]>([]);
  let copyResult = $state.raw<{ slug: string; status: "" | "copied" | "error" }>({ slug: "", status: "" });

  function chartSeriesFor(portfolio: PortfolioDetail): ChartSeries[] {
    if (!portfolio.series.length) return [];
    const out: ChartSeries[] = [{ name: portfolio.name, points: portfolio.series }];
    if (!portfolio.is_benchmark || portfolio.slug !== "spy-buy-and-hold") {
      out.push({ name: "SPY", points: portfolio.spy_series, dashed: true, color: "var(--spark)" });
    }
    return out;
  }

  function markersFor(portfolio: PortfolioDetail): string[] {
    return portfolio.allocations
      .map((allocation) => allocation.applied_date)
      .filter((date): date is string => date !== null);
  }

  function allocationTitle(allocation: AllocationOut, index: number, total: number): string {
    if (index === total - 1) return "Initial allocation";
    return "Rebalance";
  }

  async function copyPrompt(portfolio: PortfolioDetail) {
    if (!portfolio.execution_prompt) return;

    try {
      await navigator.clipboard.writeText(portfolio.execution_prompt);
      copyResult = { slug: portfolio.slug, status: "copied" };
    } catch {
      copyResult = { slug: portfolio.slug, status: "error" };
    }
  }

  function requestErrorMessage(error: unknown): string {
    return error instanceof Error ? error.message : "Could not load this portfolio.";
  }
</script>

{#key slug}
  {@const request = apiJson<PortfolioDetailResponse>(`/api/portfolios/${slug}`)}
  {#await request}
    <div class="loading-block"><span class="spinner" aria-hidden="true"></span> Valuing portfolio…</div>
  {:then data}
    {@const portfolio = data.portfolio}
    {@const chartSeries = chartSeriesFor(portfolio)}
    {@const markers = markersFor(portfolio)}
    {@const staleSymbols = Object.keys(portfolio.stale_days)}
    <div class="head">
      <div>
        <nav class="crumbs" aria-label="Breadcrumb">
          <a href="/" onclick={(e) => link(e, "/")}>Leaderboard</a>
          <span aria-hidden="true">/</span>
          <span>{portfolio.name}</span>
        </nav>
        <h1>{portfolio.name}</h1>
        <p class="muted">
          {#if portfolio.agent.id !== null && portfolio.agent.model}
            <a href="/agent/{portfolio.agent.slug}" onclick={(e) => link(e, `/agent/${portfolio.agent.slug}`)}
              >{portfolio.agent.name}</a
            >
            <span class="muted">
              · {portfolio.agent.model.name} · {portfolio.agent.harness?.name ?? "No supported harness"}
              {portfolio.agent.reasoning_effort ? ` · ${portfolio.agent.reasoning_effort}` : ""}
            </span>
          {:else}
            <span>{portfolio.agent.name}</span>
          {/if}
          {#if portfolio.prompt?.configurable}
            · prompt
            <a
              href="/prompt/{portfolio.prompt.slug}"
              onclick={(e) => link(e, `/prompt/${portfolio.prompt!.slug}`)}>{portfolio.prompt.name}</a
            >
            {#if !portfolio.is_benchmark}
              <button class="btn small prompt-copy" type="button" onclick={() => copyPrompt(portfolio)}>
                Copy prompt
              </button>
              {#if copyResult.slug === portfolio.slug && copyResult.status}
                <span
                  class={["copy-status", copyResult.status === "error" && "copy-status-error"]}
                  role="status"
                >
                  {copyResult.status === "copied"
                    ? "Copied with this portfolio's slug."
                    : "Copy failed — open the prompt and copy it manually."}
                </span>
              {/if}
            {/if}
          {:else if portfolio.prompt}
            · strategy {portfolio.prompt.name}
          {/if}
          {#if portfolio.prompt_mode}· {portfolio.prompt_mode}{/if}
          · costs {portfolio.cost_bps} bps on turnover
          {#if data.as_of}· as of <span class="num">{data.as_of}</span>{/if}
        </p>
      </div>
      <div class="head-badges">
        {#if portfolio.is_benchmark}<span class="badge accent">benchmark</span>{/if}
        {#if portfolio.prompt_mode}<span class="badge">{portfolio.prompt_mode}</span>{/if}
        {#if portfolio.status === "archived"}<span class="badge">archived</span>{/if}
        {#if portfolio.too_early && !portfolio.is_benchmark}
          <span class="badge warn">too early to judge · {ageLabel(portfolio.age_days)}</span>
        {/if}
      </div>
    </div>

    <MarketDataWarning status={data.market_data_status} asOf={data.as_of} />

    {#if portfolio.error}
      <div class="error-box" role="alert">Valuation failed: {portfolio.error}</div>
    {/if}

    {#if portfolio.frozen_symbols.length}
      <div class="error-box" role="alert">
        <strong>Frozen positions:</strong>
        {portfolio.frozen_symbols.join(", ")} stopped returning prices (possible delisting). The position is held
        at its last known price — resolve it with a corrective rebalance.
      </div>
    {/if}

    {#if portfolio.metrics.has_data}
      <div class="metric-row">
        {#snippet tile(label: string, value: string, cls = "")}
          <div class="metric card">
            <span class="metric-label">{label}</span>
            <span class="metric-value num {cls}">{value}</span>
          </div>
        {/snippet}
        {@render tile(
          "ITD return",
          pct(portfolio.metrics.itd_return),
          signClass(portfolio.metrics.itd_return),
        )}
        {@render tile("vs SPY", pct(portfolio.metrics.vs_spy), signClass(portfolio.metrics.vs_spy))}
        {@render tile("Max drawdown", pct(portfolio.metrics.max_drawdown))}
        {@render tile("Sharpe (rf=0)", num(portfolio.metrics.sharpe))}
        {@render tile("Ann. volatility", pct(portfolio.metrics.ann_volatility))}
        {@render tile("Cost drag", pctPoints(portfolio.metrics.cost_drag_pct, 2))}
        {@render tile("Turnover", pctPoints(portfolio.metrics.turnover_pct, 0))}
        {@render tile("Age", ageLabel(portfolio.age_days))}
      </div>

      <div class="metric-row trailing">
        {#snippet trail(label: string, value: number | null | undefined)}
          <div class="metric card">
            <span class="metric-label">{label}</span>
            <span class="metric-value num {signClass(value)}">{pct(value)}</span>
          </div>
        {/snippet}
        {@render trail("1M", portfolio.metrics.r1m)}
        {@render trail("3M", portfolio.metrics.r3m)}
        {@render trail("6M", portfolio.metrics.r6m)}
        {@render trail("1Y", portfolio.metrics.r1y)}
      </div>

      <section class="card chart-card">
        <h2>NAV vs SPY <span class="muted">(base 100 at inception, total return)</span></h2>
        <LineChart series={chartSeries} {markers} ariaLabel="{portfolio.name} NAV versus SPY" />
        <p class="muted chart-note">Dotted vertical lines mark allocation effective dates.</p>
      </section>

      {#if staleSymbols.length}
        <div class="card warn-card">
          <strong>Stale data:</strong>
          {#each staleSymbols as symbol, i (symbol)}
            {symbol} ({portfolio.stale_days[symbol].length} day{portfolio.stale_days[symbol].length === 1
              ? ""
              : "s"} carried forward){i < staleSymbols.length - 1 ? ", " : ""}
          {/each}
        </div>
      {/if}

      <section class="holdings-section">
        <h2>Current holdings <span class="muted">(drifted)</span></h2>
        <div class="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Symbol</th>
                <th class="right">Weight</th>
                <th class="right">Target</th>
                <th class="right">Drift</th>
              </tr>
            </thead>
            <tbody>
              {#each portfolio.holdings as holding (holding.symbol)}
                <tr>
                  <td class="num">{holding.symbol}</td>
                  <td class="right num">{pctPoints(holding.weight_pct)}</td>
                  <td class="right num">{pctPoints(holding.target_weight_pct)}</td>
                  <td class="right num {signClass(holding.weight_pct - holding.target_weight_pct)}">
                    {pctPoints(holding.weight_pct - holding.target_weight_pct)}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      </section>
    {:else}
      <div class="empty-state card">
        <h3>No track record yet</h3>
        <p>
          The first allocation takes effect at the next market close
          {#if portfolio.allocations.length}
            ({fmtDate(portfolio.allocations[portfolio.allocations.length - 1].effective_date)}).
          {:else}
            once it is entered.
          {/if}
        </p>
      </div>
    {/if}

    <section class="allocations-section">
      <h2>Allocation history</h2>
      <div class="timeline">
        <Accordion.Root type="multiple" bind:value={expanded}>
          {#each portfolio.allocations as allocation, index (allocation.id)}
            <Accordion.Item class="allocation-item" value={String(allocation.id)}>
              <Accordion.Header class="allocation-header" level={3}>
                <Accordion.Trigger class="allocation-trigger">
                  <span class="allocation-primary">
                    <span class="alloc-date num">{fmtDate(allocation.effective_date)}</span>
                    <span class="alloc-kind">
                      {allocationTitle(allocation, index, portfolio.allocations.length)}
                    </span>
                  </span>
                  <span class="allocation-meta">
                    {#if !allocation.applied_date}
                      <span class="badge accent">pending — effective at the next close</span>
                    {:else if allocation.turnover_pct !== null}
                      <span class="muted">
                        turnover {pctPoints(allocation.turnover_pct)} · cost {num(allocation.cost, 3)} pts
                      </span>
                    {:else if allocation.cost !== null}
                      <span class="muted">entry cost {num(allocation.cost, 3)} pts</span>
                    {/if}
                    {#if !allocation.locked}
                      <span class="badge warn">editable until close</span>
                    {/if}
                  </span>
                  <span class="chevron" aria-hidden="true">
                    {#if expanded.includes(String(allocation.id))}
                      <ChevronDown size={16} />
                    {:else}
                      <ChevronRight size={16} />
                    {/if}
                  </span>
                </Accordion.Trigger>
              </Accordion.Header>
              <Accordion.Content class="allocation-content">
                <div class="allocation-body">
                  <p class="muted num entered">entered {fmtDateTime(allocation.entered_at)}</p>
                  {#if allocation.note}
                    <p class="note"><strong>Note:</strong> {allocation.note}</p>
                  {/if}
                  <div class="table-scroll">
                    <table>
                      <thead>
                        <tr><th>Symbol</th><th class="right">Weight</th></tr>
                      </thead>
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
              </Accordion.Content>
            </Accordion.Item>
          {:else}
            <div class="empty-state card"><p>No allocations entered yet.</p></div>
          {/each}
        </Accordion.Root>
      </div>
    </section>
  {:catch error}
    <div class="error-box" role="alert">{requestErrorMessage(error)}</div>
  {/await}
{/key}

<style>
  .head {
    margin-bottom: 24px;
    padding-bottom: 22px;
    display: flex;
    align-items: start;
    justify-content: space-between;
    gap: 18px;
    flex-wrap: wrap;
    border-bottom: 1px solid var(--border-subtle);
  }

  .crumbs {
    max-width: 100%;
    margin-bottom: 10px;
    display: flex;
    align-items: center;
    gap: 6px;
    overflow: hidden;
    color: var(--text-tertiary);
    font-size: 12px;
    white-space: nowrap;
  }

  .crumbs span:last-child {
    overflow: hidden;
    text-overflow: ellipsis;
  }

  h1 {
    margin: 0 0 8px;
    font-size: clamp(28px, 8vw, 44px);
    line-height: 1.05;
    letter-spacing: -0.04em;
  }

  h2 {
    margin: 30px 0 12px;
    font-size: 17px;
    line-height: 1.2;
    letter-spacing: -0.015em;
  }

  .head-badges {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
  }

  .prompt-copy {
    min-height: 34px;
    margin: 6px 4px 2px;
    vertical-align: middle;
  }

  .copy-status {
    display: inline-block;
    margin: 4px 0;
    color: var(--text-secondary);
    font-size: 12px;
  }

  .copy-status-error {
    color: var(--neg);
  }

  .metric-row {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 1px;
    margin-bottom: 1px;
    border: 1px solid var(--border-subtle);
    background: var(--border-subtle);
  }

  .metric {
    min-width: 0;
    min-height: 82px;
    padding: 13px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    border: 0;
    border-radius: 0;
    background: var(--bg-base);
  }

  .metric-label {
    display: block;
    color: var(--text-secondary);
    font-size: 10.5px;
    font-weight: 650;
    text-transform: uppercase;
    letter-spacing: 0.07em;
  }

  .metric-value {
    overflow: hidden;
    font-size: clamp(16px, 5vw, 21px);
    font-weight: 650;
    text-overflow: ellipsis;
  }

  .trailing {
    max-width: none;
  }

  .chart-card {
    margin-top: 24px;
    padding: 14px 10px;
    border: 1px solid var(--border-subtle);
    border-radius: 0;
    background: var(--bg-base);
  }

  .chart-card h2 {
    margin: 0 0 12px;
  }

  .chart-note {
    font-size: 12px;
    margin-top: 6px;
  }

  .warn-card {
    margin-top: 14px;
    border-radius: 0;
    border-color: var(--warn);
    background: var(--warn-bg);
    font-size: 13px;
  }

  .holdings-section,
  .allocations-section {
    margin-top: 34px;
  }

  .holdings-section h2,
  .allocations-section h2 {
    margin-top: 0;
  }

  .timeline {
    border-bottom: 1px solid var(--border-subtle);
  }

  .timeline :global(.allocation-item) {
    border-top: 1px solid var(--border-subtle);
    border-radius: 0;
  }

  .timeline :global(.allocation-header) {
    margin: 0;
  }

  .timeline :global(.allocation-trigger) {
    width: 100%;
    min-height: 68px;
    padding: 12px 4px;
    display: grid;
    grid-template-columns: 1fr auto;
    align-items: center;
    gap: 8px 12px;
    border-radius: 0;
    text-align: left;
    font-size: 13px;
  }

  .timeline :global(.allocation-trigger:hover),
  .timeline :global(.allocation-trigger[data-state="open"]) {
    background: var(--bg-surface-hover);
  }

  .allocation-primary,
  .allocation-meta {
    min-width: 0;
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }

  .allocation-primary {
    font-size: 14px;
  }

  .allocation-meta {
    grid-column: 1 / -1;
  }

  .alloc-date {
    font-weight: 650;
  }

  .alloc-kind {
    font-weight: 500;
  }

  .chevron {
    grid-column: 2;
    grid-row: 1;
    color: var(--text-tertiary);
  }

  .timeline :global(.allocation-content) {
    overflow: hidden;
  }

  .allocation-body {
    padding: 4px 4px 18px;
    border-top: 1px solid var(--border-subtle);
  }

  .entered {
    font-size: 12px;
    margin: 8px 0;
  }

  .note {
    margin: 12px 0;
    line-height: 1.6;
  }

  @media (max-width: 460px) {
    .holdings-section :global(th),
    .holdings-section :global(td) {
      padding-inline: 6px;
      font-size: 11.5px;
    }
  }

  @media (min-width: 640px) {
    .metric-row {
      grid-template-columns: repeat(4, minmax(0, 1fr));
    }

    .chart-card {
      padding: 18px;
    }

    .timeline :global(.allocation-trigger) {
      min-height: 62px;
      padding: 12px 14px;
      grid-template-columns: auto minmax(0, 1fr) auto;
    }

    .allocation-primary {
      min-width: 230px;
    }

    .allocation-meta {
      grid-column: 2;
    }

    .chevron {
      grid-column: 3;
      grid-row: 1;
    }

    .allocation-body {
      padding: 8px 14px 20px;
    }
  }

  @media (min-width: 1120px) {
    .metric-row:not(.trailing) {
      grid-template-columns: repeat(8, minmax(0, 1fr));
    }

    .trailing {
      width: 50%;
    }
  }
</style>
