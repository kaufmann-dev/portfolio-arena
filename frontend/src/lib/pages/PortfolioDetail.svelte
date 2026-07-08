<script lang="ts">
  import { apiJson } from "../api/client";
  import type { AllocationOut, PortfolioDetail } from "../api/types";
  import { ChevronDown, ChevronRight } from "@lucide/svelte";
  import LineChart, { type ChartSeries } from "../components/LineChart.svelte";
  import { ageLabel, fmtDate, fmtDateTime, num, pct, pctPoints, signClass } from "../format";
  import { link } from "../stores/router.svelte";

  interface Props {
    slug: string;
  }

  const { slug }: Props = $props();

  let data = $state<{ as_of: string | null; portfolio: PortfolioDetail } | null>(null);
  let error = $state("");
  let expanded = $state<number[]>([]);

  $effect(() => {
    data = null;
    error = "";
    apiJson<{ as_of: string | null; portfolio: PortfolioDetail }>(`/api/portfolios/${slug}`, {
      auth: false,
    })
      .then((payload) => (data = payload))
      .catch((e) => (error = e.message));
  });

  const portfolio = $derived(data?.portfolio ?? null);

  const chartSeries = $derived.by((): ChartSeries[] => {
    if (!portfolio || !portfolio.series.length) return [];
    const out: ChartSeries[] = [{ name: portfolio.name, points: portfolio.series }];
    if (!portfolio.is_benchmark || portfolio.slug !== "spy-buy-and-hold") {
      out.push({ name: "SPY", points: portfolio.spy_series, dashed: true, color: "var(--spark)" });
    }
    return out;
  });

  const markers = $derived(
    (portfolio?.allocations ?? [])
      .map((allocation) => allocation.applied_date)
      .filter((date): date is string => date !== null),
  );

  function toggle(id: number) {
    expanded = expanded.includes(id) ? expanded.filter((e) => e !== id) : [...expanded, id];
  }

  function allocationTitle(allocation: AllocationOut, index: number, total: number): string {
    if (index === total - 1) return "Initial allocation";
    return "Rebalance";
  }

  const staleSymbols = $derived(Object.keys(portfolio?.stale_days ?? {}));
</script>

{#if error}
  <div class="error-box">{error}</div>
{:else if !portfolio}
  <div class="loading-block"><span class="spinner" aria-hidden="true"></span> Valuing portfolio…</div>
{:else}
  <div class="head">
    <div>
      <nav class="crumbs" aria-label="Breadcrumb">
        <a href="/" onclick={(e) => link(e, "/")}>Leaderboard</a>
        <span aria-hidden="true">/</span>
        <span>{portfolio.name}</span>
      </nav>
      <h1>{portfolio.name}</h1>
      <p class="muted">
        <a href="/agent/{portfolio.agent.slug}" onclick={(e) => link(e, `/agent/${portfolio.agent.slug}`)}
          >{portfolio.agent.name}</a
        >
        {#if portfolio.prompt}
          · prompt
          <a
            href="/prompt/{portfolio.prompt.slug}"
            onclick={(e) => link(e, `/prompt/${portfolio.prompt!.slug}`)}>{portfolio.prompt.name}</a
          >
        {/if}
        · costs {portfolio.cost_bps} bps on turnover
        {#if data?.as_of}· as of <span class="num">{data.as_of}</span>{/if}
      </p>
    </div>
    <div class="head-badges">
      {#if portfolio.is_benchmark}<span class="badge accent">benchmark</span>{/if}
      {#if portfolio.status === "archived"}<span class="badge">archived</span>{/if}
      {#if portfolio.too_early && !portfolio.is_benchmark}
        <span class="badge warn">too early to judge · {ageLabel(portfolio.age_days)}</span>
      {/if}
    </div>
  </div>

  {#if portfolio.error}
    <div class="error-box">Valuation failed: {portfolio.error}</div>
  {/if}

  {#if portfolio.frozen_symbols.length}
    <div class="error-box">
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
      {@render tile("ITD return", pct(portfolio.metrics.itd_return), signClass(portfolio.metrics.itd_return))}
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

    <section>
      <h2>Current holdings <span class="muted">(drifted)</span></h2>
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Type</th>
              <th class="right">Weight</th>
              <th class="right">Target</th>
              <th class="right">Drift</th>
            </tr>
          </thead>
          <tbody>
            {#each portfolio.holdings as holding (holding.symbol)}
              <tr>
                <td class="num">{holding.symbol}</td>
                <td class="muted">{holding.instrument}</td>
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

  <section>
    <h2>Allocation history</h2>
    <div class="timeline">
      {#each portfolio.allocations as allocation, index (allocation.id)}
        <article class="card allocation">
          <button
            class="allocation-head"
            onclick={() => toggle(allocation.id)}
            aria-expanded={expanded.includes(allocation.id)}
          >
            <span class="alloc-date num">{fmtDate(allocation.effective_date)}</span>
            <span class="alloc-kind">
              {allocationTitle(allocation, index, portfolio.allocations.length)}
            </span>
            {#if !allocation.applied_date}
              <span class="badge accent">pending — effective at the next close</span>
            {:else if allocation.turnover_pct !== null}
              <span class="muted">
                turnover {pctPoints(allocation.turnover_pct)} · cost {num(allocation.cost, 3)} pts
              </span>
            {:else if allocation.cost !== null}
              <span class="muted">entry cost {num(allocation.cost, 3)} pts</span>
            {/if}
            {#if allocation.prompt}
              <span class="badge">{allocation.prompt.name}</span>
            {/if}
            {#if !allocation.locked}
              <span class="badge warn">editable until close</span>
            {/if}
            <span class="chevron" aria-hidden="true">
              {#if expanded.includes(allocation.id)}
                <ChevronDown size={14} />
              {:else}
                <ChevronRight size={14} />
              {/if}
            </span>
          </button>
          {#if expanded.includes(allocation.id)}
            <div class="allocation-body">
              <p class="muted num entered">entered {fmtDateTime(allocation.entered_at)}</p>
              {#if allocation.note}
                <p class="note"><strong>Note:</strong> {allocation.note}</p>
              {/if}
              <div class="table-scroll">
                <table>
                  <thead>
                    <tr><th>Symbol</th><th>Type</th><th class="right">Weight</th></tr>
                  </thead>
                  <tbody>
                    {#each allocation.positions as position (position.symbol)}
                      <tr>
                        <td class="num">{position.symbol}</td>
                        <td class="muted">{position.instrument}</td>
                        <td class="right num">{pctPoints(position.weight_pct, 2)}</td>
                      </tr>
                    {/each}
                  </tbody>
                </table>
              </div>
            </div>
          {/if}
        </article>
      {:else}
        <div class="empty-state card"><p>No allocations entered yet.</p></div>
      {/each}
    </div>
  </section>
{/if}

<style>
  .head {
    display: flex;
    justify-content: space-between;
    align-items: start;
    gap: 16px;
    flex-wrap: wrap;
    margin-bottom: 16px;
  }

  .crumbs {
    font-size: 12.5px;
    color: var(--text-tertiary);
    display: flex;
    gap: 6px;
    margin-bottom: 4px;
  }

  h1 {
    font-size: 22px;
    margin-bottom: 4px;
  }

  h2 {
    font-size: 15px;
    margin: 22px 0 10px;
  }

  .head-badges {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
  }

  .metric-row {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
    gap: 10px;
    margin-bottom: 10px;
  }

  .metric {
    padding: 10px 14px;
  }

  .metric-label {
    display: block;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-secondary);
  }

  .metric-value {
    font-size: 17px;
    font-weight: 600;
  }

  .trailing {
    grid-template-columns: repeat(auto-fit, minmax(90px, 1fr));
    max-width: 480px;
  }

  .chart-card {
    margin-top: 16px;
  }

  .chart-card h2 {
    margin: 0 0 12px;
  }

  .chart-note {
    font-size: 12px;
    margin-top: 6px;
  }

  .warn-card {
    border-color: var(--warn);
    background: var(--warn-bg);
    margin-top: 12px;
    font-size: 13px;
  }

  .timeline {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .allocation {
    padding: 0;
  }

  .allocation-head {
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
    width: 100%;
    padding: 12px 16px;
    text-align: left;
    font-size: 13.5px;
  }

  .allocation-head:hover {
    background: var(--bg-surface-hover);
  }

  .alloc-date {
    font-weight: 600;
  }

  .alloc-kind {
    font-weight: 500;
  }

  .chevron {
    margin-left: auto;
    color: var(--text-tertiary);
  }

  .allocation-body {
    padding: 4px 16px 16px;
    border-top: 1px solid var(--border-subtle);
  }

  .entered {
    font-size: 12px;
    margin: 8px 0;
  }

  .note {
    margin-bottom: 10px;
  }
</style>
