<script lang="ts">
  import { ChevronDown, ChevronUp } from "@lucide/svelte";

  import type { BenchmarkArenaPortfolio, ManagedArenaPortfolio, ManagedArenaResponse } from "../api/types";
  import { portfolioAnalysisHref } from "../arena";
  import { num, pct, pctPoints, signClass } from "../format";
  import { link } from "../stores/router.svelte";
  import EvidenceBadge from "./EvidenceBadge.svelte";
  import SelectField from "./ui/SelectField.svelte";

  type Row = ManagedArenaResponse["portfolios"][number];
  type SortKey =
    | "rank_score"
    | "mean_daily_alpha"
    | "cumulative_excess"
    | "information_ratio"
    | "sharpe"
    | "max_drawdown"
    | "turnover_pct";

  interface Props {
    rows: Row[];
    selected?: string[];
    onToggle?: (slug: string) => void;
  }

  const SORT_OPTIONS: { value: SortKey; label: string }[] = [
    { value: "rank_score", label: "Adjusted lower 95%" },
    { value: "mean_daily_alpha", label: "Mean daily alpha" },
    { value: "cumulative_excess", label: "Cumulative excess" },
    { value: "information_ratio", label: "Information ratio" },
    { value: "sharpe", label: "Sharpe" },
    { value: "max_drawdown", label: "Max drawdown" },
    { value: "turnover_pct", label: "Turnover" },
  ];

  const uid = $props.id();
  const { rows, selected = [], onToggle }: Props = $props();
  let sortKey = $state<SortKey>("rank_score");
  let sortDesc = $state(true);

  function metric(row: ManagedArenaPortfolio, key: SortKey): number | null {
    const value =
      key === "rank_score"
        ? row.rank_score
        : key === "mean_daily_alpha"
          ? row.metrics.mean_daily_alpha
          : row.metrics[key];
    return typeof value === "number" && isFinite(value) ? value : null;
  }

  function compareRows(a: ManagedArenaPortfolio, b: ManagedArenaPortfolio): number {
    const aValue = metric(a, sortKey);
    const bValue = metric(b, sortKey);
    if (aValue === null && bValue === null) return a.name.localeCompare(b.name);
    if (aValue === null) return 1;
    if (bValue === null) return -1;
    const difference = sortDesc ? bValue - aValue : aValue - bValue;
    return difference || a.name.localeCompare(b.name);
  }

  const benchmark = $derived(rows.find((row): row is BenchmarkArenaPortfolio => row.kind === "benchmark"));
  const contestants = $derived(
    [...rows.filter((row): row is ManagedArenaPortfolio => row.kind === "managed")].sort(compareRows),
  );
  const currentSortLabel = $derived(
    SORT_OPTIONS.find((option) => option.value === sortKey)?.label ?? "Adjusted lower 95%",
  );

  function setSort(key: SortKey): void {
    if (sortKey === key) {
      sortDesc = !sortDesc;
      return;
    }
    sortKey = key;
    sortDesc = key !== "max_drawdown";
  }

  function selectMobileSort(value: string): void {
    const next = SORT_OPTIONS.find((option) => option.value === value)?.value;
    if (!next || next === sortKey) return;
    sortKey = next;
    sortDesc = next !== "max_drawdown";
  }

  function ariaSort(key: SortKey): "ascending" | "descending" | undefined {
    if (sortKey !== key) return undefined;
    return sortDesc ? "descending" : "ascending";
  }
</script>

{#snippet sortHeader(key: SortKey, label: string)}
  <th scope="col" class="right sortable" aria-sort={ariaSort(key)}>
    <button
      type="button"
      onclick={() => setSort(key)}
      aria-label={`Sort by ${label}${sortKey === key ? `, currently ${sortDesc ? "descending" : "ascending"}` : ""}`}
    >
      <span>{label}</span>
      {#if sortKey === key}
        {#if sortDesc}
          <ChevronDown size={13} aria-hidden="true" />
        {:else}
          <ChevronUp size={13} aria-hidden="true" />
        {/if}
      {/if}
    </button>
  </th>
{/snippet}

{#snippet portfolioIdentity(row: ManagedArenaPortfolio)}
  <a
    class="portfolio-link"
    href={portfolioAnalysisHref(row.slug, "managed")}
    onclick={(event) => link(event, portfolioAnalysisHref(row.slug, "managed"))}
  >
    {row.name}
  </a>
  <span class="badges">
    <EvidenceBadge state={row.evidence} compact />
    {#if row.status === "archived"}<span class="badge">archived</span>{/if}
    {#if row.stale_data}<span class="badge warn">stale data</span>{/if}
    {#if row.error}<span class="badge neg" title={row.error}>error</span>{/if}
  </span>
{/snippet}

{#snippet compareControl(row: ManagedArenaPortfolio, label = false)}
  <label class={["compare", label && "labelled"]}>
    <input
      type="checkbox"
      checked={selected.includes(row.slug)}
      onchange={() => onToggle?.(row.slug)}
      aria-label="Compare {row.name}"
    />
    {#if label}<span>Compare</span>{/if}
  </label>
{/snippet}

{#snippet metricTile(label: string, value: string, className = "")}
  <div>
    <dt>{label}</dt>
    <dd class={["num", className]}>{value}</dd>
  </div>
{/snippet}

<div class="arena-rankings">
  <div class="desktop-table">
    <table>
      <caption>
        Managed portfolio rankings. SPY is a pinned reference; portfolio rows are sorted by {currentSortLabel}
        {sortDesc ? " descending" : " ascending"}.
      </caption>
      <thead>
        <tr>
          <th class="compare-col" scope="col"><span class="visually-hidden">Compare</span></th>
          <th class="rank-col" scope="col">Rank</th>
          <th scope="col">Portfolio</th>
          <th scope="col">Agent</th>
          <th scope="col">Prompt</th>
          {@render sortHeader("rank_score", "Lower 95%")}
          {@render sortHeader("mean_daily_alpha", "Mean α/day")}
          {@render sortHeader("cumulative_excess", "Cum. excess")}
          {@render sortHeader("information_ratio", "Info ratio")}
          {@render sortHeader("sharpe", "Sharpe")}
          {@render sortHeader("max_drawdown", "Max DD")}
          {@render sortHeader("turnover_pct", "Turnover")}
        </tr>
      </thead>
      <tbody>
        {#if benchmark}
          <tr class="benchmark">
            <td></td>
            <td class="rank-col"><span class="badge">REF</span></td>
            <th scope="row">SPY</th>
            <td>—</td>
            <td>Buy and hold</td>
            <td class="right num">0.00%</td>
            <td class="right num">0.00%</td>
            <td class="right num">0.00%</td>
            <td class="right num">—</td>
            <td class="right num">{num(benchmark.metrics.sharpe)}</td>
            <td class="right num">{pct(benchmark.metrics.max_drawdown)}</td>
            <td class="right num">0%</td>
          </tr>
        {/if}
        {#each contestants as row (row.id)}
          <tr>
            <td class="compare-col">{@render compareControl(row)}</td>
            <td class="rank-col num">{row.rank ?? "—"}</td>
            <th class="portfolio-col" scope="row">{@render portfolioIdentity(row)}</th>
            <td>
              <a href="/agent/{row.agent.slug}" onclick={(event) => link(event, `/agent/${row.agent.slug}`)}>
                {row.agent.name}
              </a>
            </td>
            <td>
              <a
                href="/prompt/{row.prompt.slug}"
                onclick={(event) => link(event, `/prompt/${row.prompt.slug}`)}
              >
                {row.prompt.name}
              </a>
            </td>
            <td class="right num score {signClass(row.rank_score)}">
              {pct(row.rank_score, 2)}
            </td>
            <td class="right num {signClass(row.metrics.mean_daily_alpha)}">
              {pct(row.metrics.mean_daily_alpha, 2)}
            </td>
            <td class="right num {signClass(row.metrics.cumulative_excess)}">
              {pct(row.metrics.cumulative_excess)}
            </td>
            <td class="right num">{num(row.metrics.information_ratio)}</td>
            <td class="right num">{num(row.metrics.sharpe)}</td>
            <td class="right num">{pct(row.metrics.max_drawdown)}</td>
            <td class="right num">{pctPoints(row.metrics.turnover_pct, 0)}</td>
          </tr>
        {:else}
          <tr
            ><td colspan="12"><div class="empty-state">No managed portfolios match these filters.</div></td
            ></tr
          >
        {/each}
      </tbody>
    </table>
  </div>

  <section class="mobile-rankings" aria-label="Managed portfolio rankings">
    <div class="mobile-sort">
      <SelectField
        id={`${uid}-managed-sort`}
        label="Rank by"
        options={SORT_OPTIONS}
        value={sortKey}
        compact
        onValueChange={selectMobileSort}
      />
      <button
        class="direction"
        type="button"
        onclick={() => (sortDesc = !sortDesc)}
        aria-label={`Sort ${sortDesc ? "ascending" : "descending"} by ${currentSortLabel}`}
      >
        {sortDesc ? "Descending" : "Ascending"}
      </button>
    </div>

    {#if benchmark}
      <article class="mobile-card benchmark-card">
        <header><span class="rank">REF</span><strong>SPY</strong><span class="badge">reference</span></header>
        <p>Zero-alpha benchmark · buy and hold</p>
      </article>
    {/if}

    {#each contestants as row (row.id)}
      <article class="mobile-card">
        <header>
          <span class="rank">#{row.rank ?? "—"}</span>
          <div>{@render portfolioIdentity(row)}</div>
          {@render compareControl(row, true)}
        </header>
        <p class="context">{row.agent.name} · {row.prompt.name}</p>
        <dl>
          {@render metricTile("Lower 95%", pct(row.rank_score, 2), signClass(row.rank_score))}
          {@render metricTile(
            "Mean α/day",
            pct(row.metrics.mean_daily_alpha, 2),
            signClass(row.metrics.mean_daily_alpha),
          )}
          {@render metricTile(
            "Cum. excess",
            pct(row.metrics.cumulative_excess),
            signClass(row.metrics.cumulative_excess),
          )}
          {@render metricTile("Info ratio", num(row.metrics.information_ratio))}
          {@render metricTile("Sharpe", num(row.metrics.sharpe))}
          {@render metricTile("Max DD", pct(row.metrics.max_drawdown))}
        </dl>
      </article>
    {/each}
  </section>
</div>

<style>
  .arena-rankings {
    min-width: 0;
  }

  .desktop-table {
    overflow-x: auto;
    border: 1px solid var(--border-subtle);
  }

  table {
    min-width: 1220px;
  }

  caption {
    padding: 10px 12px;
    border-bottom: 1px solid var(--border-subtle);
    caption-side: top;
    color: var(--text-tertiary);
    font-size: 10px;
    text-align: left;
  }

  th[scope="row"] {
    text-align: left;
  }

  tbody tr:hover {
    background: var(--bg-surface-hover);
  }

  .benchmark {
    color: var(--text-secondary);
    background: var(--bg-inset);
  }

  .rank-col {
    width: 52px;
    text-align: center;
  }

  .compare-col {
    width: 42px;
    text-align: center;
  }

  .portfolio-col {
    min-width: 180px;
  }

  .portfolio-link {
    font-weight: 700;
  }

  .badges {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    margin-top: 5px;
  }

  .sortable {
    padding: 0;
  }

  .sortable button {
    display: inline-flex;
    width: 100%;
    min-height: 42px;
    align-items: center;
    justify-content: flex-end;
    gap: 3px;
    padding: 9px 12px;
    color: inherit;
    font: inherit;
    letter-spacing: inherit;
    text-transform: inherit;
    white-space: nowrap;
  }

  .sortable button:hover,
  .sortable button:focus-visible {
    color: var(--text-primary);
    background: var(--bg-surface-hover);
  }

  .score {
    font-weight: 760;
  }

  .compare {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    margin: 0;
  }

  .compare input {
    width: 17px;
    height: 17px;
    min-height: 0;
    accent-color: var(--accent);
  }

  .compare span {
    color: var(--text-tertiary);
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }

  .mobile-rankings {
    display: none;
  }

  @media (max-width: 960px) {
    .desktop-table {
      display: none;
    }

    .mobile-rankings {
      display: grid;
      gap: 10px;
    }

    .mobile-sort {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: end;
      gap: 8px;
      margin-bottom: 2px;
    }

    .direction {
      min-height: 38px;
      padding: 7px 10px;
      border: 1px solid var(--border-strong);
      color: var(--text-secondary);
      font-size: 10px;
      font-weight: 700;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }

    .mobile-card {
      padding: 14px;
      border: 1px solid var(--border-subtle);
      background: var(--bg-surface);
    }

    .mobile-card header {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr) auto;
      align-items: start;
      gap: 10px;
    }

    .rank {
      color: var(--accent);
      font-family: var(--font-mono);
      font-size: 11px;
      font-weight: 750;
    }

    .context,
    .benchmark-card p {
      margin: 10px 0;
      color: var(--text-secondary);
      font-size: 11px;
    }

    dl {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 1px;
      margin: 0;
      background: var(--border-subtle);
    }

    dl div {
      padding: 8px;
      background: var(--bg-surface);
    }

    dt {
      color: var(--text-tertiary);
      font-size: 8px;
      font-weight: 740;
      letter-spacing: 0.07em;
      text-transform: uppercase;
    }

    dd {
      margin: 3px 0 0;
      font-size: 12px;
      font-weight: 650;
    }
  }

  @media (max-width: 520px) {
    .mobile-card header {
      grid-template-columns: auto minmax(0, 1fr);
    }

    .mobile-card header .labelled {
      grid-column: 2;
      justify-self: start;
    }

    dl {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
  }
</style>
