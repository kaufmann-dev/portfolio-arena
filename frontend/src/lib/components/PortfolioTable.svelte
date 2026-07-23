<script lang="ts">
  import { ChevronDown, ChevronUp } from "@lucide/svelte";

  import type { PortfolioSummary } from "../api/types";
  import { ageLabel, num, pct, pctPoints, signClass } from "../format";
  import { link } from "../stores/router.svelte";
  import SelectField from "./ui/SelectField.svelte";
  import Sparkline from "./Sparkline.svelte";

  interface Props {
    rows: PortfolioSummary[];
    selectable?: boolean;
    selected?: string[];
    onToggle?: (slug: string) => void;
  }

  type SortKey = "vs_spy" | "itd_return" | "max_drawdown" | "sharpe" | "turnover_pct" | "age";

  interface RankedRow {
    portfolio: PortfolioSummary;
    rank: number | null;
  }

  const SORT_OPTIONS: { value: SortKey; label: string }[] = [
    { value: "vs_spy", label: "vs SPY" },
    { value: "itd_return", label: "ITD return" },
    { value: "max_drawdown", label: "Max drawdown" },
    { value: "sharpe", label: "Sharpe" },
    { value: "turnover_pct", label: "Turnover" },
    { value: "age", label: "Age" },
  ];

  const uid = $props.id();
  const { rows, selectable = false, selected = [], onToggle }: Props = $props();

  let sortKey = $state<SortKey>("vs_spy");
  let sortDesc = $state(true);

  function metric(row: PortfolioSummary, key: SortKey): number | null {
    if (key === "age") return row.age_days;
    const value = row.metrics[key];
    return typeof value === "number" && isFinite(value) ? value : null;
  }

  const rankedRows = $derived.by((): RankedRow[] => {
    const benchmarks = rows
      .filter((row) => row.is_benchmark)
      .sort((a, b) => a.name.localeCompare(b.name))
      .map((portfolio) => ({ portfolio, rank: null }));
    const contestants = rows.filter((row) => !row.is_benchmark);

    contestants.sort((a, b) => {
      const aValue = metric(a, sortKey);
      const bValue = metric(b, sortKey);
      if (aValue === null && bValue === null) return a.name.localeCompare(b.name);
      if (aValue === null) return 1;
      if (bValue === null) return -1;

      const difference = sortDesc ? bValue - aValue : aValue - bValue;
      return difference || a.name.localeCompare(b.name);
    });

    return [...benchmarks, ...contestants.map((portfolio, index) => ({ portfolio, rank: index + 1 }))];
  });

  const currentSortLabel = $derived(
    SORT_OPTIONS.find((option) => option.value === sortKey)?.label ?? "vs SPY",
  );

  function setSort(key: SortKey) {
    if (sortKey === key) {
      sortDesc = !sortDesc;
    } else {
      sortKey = key;
      sortDesc = true;
    }
  }

  function selectMobileSort(value: string) {
    const next = SORT_OPTIONS.find((option) => option.value === value)?.value;
    if (!next || next === sortKey) return;
    sortKey = next;
    sortDesc = true;
  }

  function ariaSort(key: SortKey): "ascending" | "descending" | undefined {
    if (sortKey !== key) return undefined;
    return sortDesc ? "descending" : "ascending";
  }
</script>

{#snippet sortHeader(key: SortKey, label: string)}
  <th scope="col" class="right sortable-column" aria-sort={ariaSort(key)}>
    <button
      class="sort-button"
      type="button"
      onclick={() => setSort(key)}
      aria-label={`Sort by ${label}${sortKey === key ? `, currently ${sortDesc ? "descending" : "ascending"}` : ""}`}
    >
      <span>{label}</span>
      {#if sortKey === key}
        {#if sortDesc}
          <ChevronDown size={13} strokeWidth={2} aria-hidden="true" />
        {:else}
          <ChevronUp size={13} strokeWidth={2} aria-hidden="true" />
        {/if}
      {/if}
    </button>
  </th>
{/snippet}

{#snippet compareControl(row: PortfolioSummary, showLabel = false)}
  <label class={["compare-control", showLabel && "with-label"]}>
    <input
      type="checkbox"
      checked={selected.includes(row.slug)}
      onchange={() => onToggle?.(row.slug)}
      aria-label="Compare {row.name}"
    />
    {#if showLabel}<span>Compare</span>{/if}
  </label>
{/snippet}

{#snippet statusBadges(row: PortfolioSummary)}
  <span class="badges">
    {#if row.status === "archived"}<span class="badge">archived</span>{/if}
    {#if row.too_early && !row.is_benchmark}
      <span class="badge warn" title="Younger than 6 months — sample too small to judge">too early</span>
    {/if}
    {#if row.stale_data}
      <span class="badge warn" title="Some prices were carried forward">stale data</span>
    {/if}
    {#if row.frozen_symbols.length}
      <span class="badge neg" title="No recent prices: {row.frozen_symbols.join(', ')}">
        frozen: {row.frozen_symbols.join(", ")}
      </span>
    {/if}
    {#if row.error}
      <span class="badge neg" title={row.error}>error</span>
    {/if}
  </span>
{/snippet}

{#snippet agentLink(row: PortfolioSummary)}
  {#if row.agent.id !== null}
    <a href="/agent/{row.agent.slug}" onclick={(event) => link(event, `/agent/${row.agent.slug}`)}>
      {row.agent.name}
    </a>
  {:else}
    <span>{row.agent.name}</span>
  {/if}
{/snippet}

{#snippet promptLink(row: PortfolioSummary)}
  {#if row.prompt?.configurable}
    <a href="/prompt/{row.prompt.slug}" onclick={(event) => link(event, `/prompt/${row.prompt!.slug}`)}>
      {row.prompt.name}
    </a>
  {:else if row.prompt}
    <span>{row.prompt.name}</span>
  {:else}
    <span class="muted">—</span>
  {/if}
{/snippet}

{#snippet mobileMetric(label: string, value: string, valueClass = "")}
  <div class="mobile-metric">
    <dt>{label}</dt>
    <dd class={["num", valueClass]}>{value}</dd>
  </div>
{/snippet}

<div class="rankings">
  <div class="desktop-rankings">
    <table class="ranking-table">
      <caption>
        Portfolio rankings. Benchmarks remain pinned above contestants; contestants are sorted by
        {currentSortLabel}
        {sortDesc ? "descending" : "ascending"}.
      </caption>
      <thead>
        <tr>
          {#if selectable}
            <th scope="col" class="compare-column"><span class="visually-hidden">Compare</span></th>
          {/if}
          <th scope="col" class="rank-column">Rank</th>
          <th scope="col">Portfolio</th>
          <th scope="col">Agent</th>
          <th scope="col">Prompt</th>
          {@render sortHeader("age", "Age")}
          {@render sortHeader("itd_return", "ITD")}
          {@render sortHeader("vs_spy", "vs SPY")}
          {@render sortHeader("max_drawdown", "Max DD")}
          {@render sortHeader("sharpe", "Sharpe")}
          {@render sortHeader("turnover_pct", "Turnover")}
          <th scope="col">Trend</th>
        </tr>
      </thead>
      <tbody>
        {#each rankedRows as item (item.portfolio.slug)}
          {@const row = item.portfolio}
          <tr class={row.is_benchmark ? "benchmark" : undefined}>
            {#if selectable}
              <td class="compare-column">{@render compareControl(row)}</td>
            {/if}
            <td class="rank-column num">
              {#if row.is_benchmark}
                <span class="badge">BM</span>
              {:else}
                {item.rank}
              {/if}
            </td>
            <td class="portfolio-column">
              <a
                class="portfolio-link"
                href="/p/{row.slug}"
                onclick={(event) => link(event, `/p/${row.slug}`)}
              >
                {row.name}
              </a>
              {@render statusBadges(row)}
            </td>
            <td>{@render agentLink(row)}</td>
            <td>{@render promptLink(row)}</td>
            <td class="right num" title={row.inception ? `since ${row.inception}` : undefined}>
              {ageLabel(row.age_days)}
            </td>
            <td class="right num {signClass(row.metrics.itd_return)}">{pct(row.metrics.itd_return)}</td>
            <td class="right num vs-spy {signClass(row.metrics.vs_spy)}">{pct(row.metrics.vs_spy)}</td>
            <td class="right num">{pct(row.metrics.max_drawdown)}</td>
            <td class="right num">{num(row.metrics.sharpe)}</td>
            <td class="right num">{pctPoints(row.metrics.turnover_pct, 0)}</td>
            <td class="trend-cell"><Sparkline values={row.sparkline} /></td>
          </tr>
        {:else}
          <tr>
            <td colspan={selectable ? 12 : 11}>
              <div class="empty-state ranking-empty">
                <h3>No portfolios yet</h3>
                <p>Contestants appear here once the first allocation is entered.</p>
              </div>
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>

  <section class="mobile-rankings" aria-label="Portfolio rankings">
    <div class="mobile-sort">
      <SelectField
        id={`${uid}-ranking-sort`}
        label="Rank by"
        options={SORT_OPTIONS}
        value={sortKey}
        compact
        onValueChange={selectMobileSort}
      />
      <button
        class="direction-button"
        type="button"
        onclick={() => (sortDesc = !sortDesc)}
        aria-label={`Sort ${sortDesc ? "ascending" : "descending"} by ${currentSortLabel}`}
      >
        {#if sortDesc}
          <ChevronDown size={15} strokeWidth={2} aria-hidden="true" />
          Descending
        {:else}
          <ChevronUp size={15} strokeWidth={2} aria-hidden="true" />
          Ascending
        {/if}
      </button>
    </div>

    {#if rankedRows.length}
      <ol class="mobile-list" aria-label={`Sorted by ${currentSortLabel}`}>
        {#each rankedRows as item (item.portfolio.slug)}
          {@const row = item.portfolio}
          <li class={["rank-card", row.is_benchmark && "benchmark"]}>
            <header class="rank-card-head">
              <span class={["mobile-rank", row.is_benchmark && "benchmark-rank"]}>
                {row.is_benchmark ? "BM" : `#${item.rank}`}
              </span>
              <div class="mobile-title">
                <a href="/p/{row.slug}" onclick={(event) => link(event, `/p/${row.slug}`)}>
                  {row.name}
                </a>
                {@render statusBadges(row)}
              </div>
              {#if selectable}
                {@render compareControl(row, true)}
              {/if}
            </header>

            <dl class="mobile-context">
              <div>
                <dt>Agent</dt>
                <dd>{@render agentLink(row)}</dd>
              </div>
              <div>
                <dt>Prompt</dt>
                <dd>{@render promptLink(row)}</dd>
              </div>
            </dl>

            <dl class="mobile-metrics">
              {@render mobileMetric("vs SPY", pct(row.metrics.vs_spy), signClass(row.metrics.vs_spy))}
              {@render mobileMetric("ITD", pct(row.metrics.itd_return), signClass(row.metrics.itd_return))}
              {@render mobileMetric("Max DD", pct(row.metrics.max_drawdown))}
              {@render mobileMetric("Sharpe", num(row.metrics.sharpe))}
              {@render mobileMetric("Turnover", pctPoints(row.metrics.turnover_pct, 0))}
              {@render mobileMetric("Age", ageLabel(row.age_days))}
            </dl>

            <div class="mobile-trend">
              <span>Recent trend</span>
              <Sparkline values={row.sparkline} width={132} height={30} />
            </div>
          </li>
        {/each}
      </ol>
    {:else}
      <div class="empty-state ranking-empty">
        <h3>No portfolios yet</h3>
        <p>Contestants appear here once the first allocation is entered.</p>
      </div>
    {/if}
  </section>
</div>

<style>
  .rankings {
    min-width: 0;
  }

  .desktop-rankings {
    overflow-x: auto;
    border: 1px solid var(--border-subtle);
    border-radius: 0;
    background: var(--bg-surface);
  }

  .ranking-table {
    min-width: 1080px;
  }

  caption {
    padding: 11px 12px;
    border-bottom: 1px solid var(--border-subtle);
    caption-side: top;
    color: var(--text-tertiary);
    font-size: 11px;
    letter-spacing: 0.02em;
    text-align: left;
  }

  th {
    vertical-align: middle;
  }

  .ranking-table tbody tr {
    transition: background 120ms ease;
  }

  .ranking-table tbody tr:hover {
    background: var(--bg-surface-hover);
  }

  .ranking-table tr.benchmark {
    background: var(--bg-inset);
  }

  .ranking-table tr.benchmark:hover {
    background: var(--bg-surface-hover);
  }

  .rank-column {
    width: 54px;
    color: var(--text-tertiary);
    text-align: center;
  }

  .compare-column {
    width: 42px;
    text-align: center;
  }

  .portfolio-column {
    min-width: 190px;
  }

  .portfolio-link {
    font-weight: 650;
  }

  .badges {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    margin-top: 5px;
  }

  .badges:empty {
    display: none;
  }

  .rankings :global(.badge) {
    border-radius: 0;
  }

  .vs-spy {
    font-weight: 750;
  }

  .sortable-column {
    padding: 0;
  }

  .sort-button {
    display: inline-flex;
    width: 100%;
    min-height: 42px;
    align-items: center;
    justify-content: flex-end;
    gap: 3px;
    padding: 9px 12px;
    border-radius: 0;
    color: inherit;
    font: inherit;
    letter-spacing: inherit;
    text-transform: inherit;
    white-space: nowrap;
  }

  .sort-button:hover,
  .sort-button:focus-visible {
    color: var(--text-primary);
    background: var(--bg-surface-hover);
  }

  .compare-control {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    margin: 0;
    color: var(--text-secondary);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }

  .compare-control input {
    width: 17px;
    height: 17px;
    min-height: 0;
    padding: 0;
    border-radius: 0;
    accent-color: var(--accent);
  }

  .trend-cell {
    min-width: 120px;
  }

  .mobile-rankings {
    display: none;
  }

  .visually-hidden {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
    white-space: nowrap;
    border: 0;
  }

  .ranking-empty {
    border-radius: 0;
  }

  @media (max-width: 960px) {
    .desktop-rankings {
      display: none;
    }

    .mobile-rankings {
      display: grid;
      gap: 12px;
    }

    .mobile-sort {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: end;
      gap: 8px;
      padding-bottom: 12px;
      border-bottom: 1px solid var(--border-strong);
    }

    .mobile-sort :global(.select-trigger) {
      min-height: 38px;
      border-radius: 0;
    }

    .direction-button {
      display: inline-flex;
      min-height: 38px;
      align-items: center;
      justify-content: center;
      gap: 5px;
      padding: 7px 10px;
      border: 1px solid var(--border-strong);
      border-radius: 0;
      color: var(--text-secondary);
      font-size: 11px;
      font-weight: 650;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }

    .direction-button:hover,
    .direction-button:focus-visible {
      border-color: var(--accent);
      color: var(--text-primary);
    }

    .mobile-list {
      display: grid;
      gap: 10px;
      margin: 0;
      padding: 0;
      list-style: none;
    }

    .rank-card {
      min-width: 0;
      padding: 14px;
      border: 1px solid var(--border-subtle);
      border-radius: 0;
      background: var(--bg-surface);
    }

    .rank-card.benchmark {
      border-color: var(--border-strong);
      background: var(--bg-inset);
    }

    .rank-card-head {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr) auto;
      align-items: start;
      gap: 10px;
      padding-bottom: 12px;
      border-bottom: 1px solid var(--border-subtle);
    }

    .mobile-rank {
      min-width: 34px;
      color: var(--accent);
      font-family: var(--font-mono);
      font-size: 12px;
      font-weight: 700;
    }

    .benchmark-rank {
      color: var(--text-tertiary);
    }

    .mobile-title {
      min-width: 0;
    }

    .mobile-title > a {
      font-size: 15px;
      font-weight: 700;
    }

    .mobile-title .badges {
      margin-top: 7px;
    }

    .mobile-context {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
      margin: 0;
      padding: 12px 0;
      border-bottom: 1px solid var(--border-subtle);
    }

    .mobile-context > div {
      min-width: 0;
    }

    .mobile-context dt,
    .mobile-metric dt,
    .mobile-trend > span {
      margin-bottom: 3px;
      color: var(--text-tertiary);
      font-size: 9.5px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }

    .mobile-context dd {
      margin: 0;
      overflow-wrap: anywhere;
      font-size: 12px;
    }

    .mobile-metrics {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 1px;
      margin: 0;
      padding: 1px 0;
      background: var(--border-subtle);
    }

    .mobile-metric {
      min-width: 0;
      padding: 8px 9px;
      background: var(--bg-surface);
    }

    .rank-card.benchmark .mobile-metric {
      background: var(--bg-inset);
    }

    .mobile-metric dd {
      margin: 0;
      font-size: 13px;
      font-weight: 650;
    }

    .mobile-trend {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding-top: 12px;
    }

    .mobile-trend > span {
      margin: 0;
    }
  }

  @media (max-width: 520px) {
    .rank-card-head {
      grid-template-columns: auto minmax(0, 1fr);
    }

    .rank-card-head .with-label {
      grid-column: 2;
      justify-self: start;
    }

    .mobile-context {
      grid-template-columns: 1fr;
    }

    .mobile-metrics {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .ranking-table tbody tr {
      transition: none;
    }
  }
</style>
