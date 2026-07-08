<script lang="ts">
  import type { PortfolioSummary } from "../api/types";
  import { ageLabel, num, pct, pctPoints, signClass } from "../format";
  import { link } from "../stores/router.svelte";
  import { ChevronDown, ChevronUp } from "@lucide/svelte";
  import Sparkline from "./Sparkline.svelte";

  interface Props {
    rows: PortfolioSummary[];
    selectable?: boolean;
    selected?: string[];
    onToggle?: (slug: string) => void;
  }

  const { rows, selectable = false, selected = [], onToggle }: Props = $props();

  type SortKey = "vs_spy" | "itd_return" | "max_drawdown" | "sharpe" | "turnover_pct" | "age";
  let sortKey = $state<SortKey>("vs_spy");
  let sortDesc = $state(true);

  function metric(row: PortfolioSummary, key: SortKey): number {
    if (key === "age") return row.age_days ?? -1;
    const value = row.metrics[key];
    return typeof value === "number" && isFinite(value) ? value : -Infinity;
  }

  const sorted = $derived.by(() => {
    const direction = sortDesc ? -1 : 1;
    const benchmarks = rows.filter((row) => row.is_benchmark);
    const contestants = rows.filter((row) => !row.is_benchmark);
    contestants.sort((a, b) => direction * (metric(a, sortKey) - metric(b, sortKey)));
    benchmarks.sort((a, b) => a.name.localeCompare(b.name));
    return [...benchmarks, ...contestants];
  });

  function setSort(key: SortKey) {
    if (sortKey === key) {
      sortDesc = !sortDesc;
    } else {
      sortKey = key;
      sortDesc = true;
    }
  }
</script>

{#snippet sortHeader(key: SortKey, label: string)}
  <th class="right sortable">
    <button class="sort-btn" onclick={() => setSort(key)}>
      {label}
      {#if sortKey === key}
        {#if sortDesc}
          <ChevronDown size={12} />
        {:else}
          <ChevronUp size={12} />
        {/if}
      {/if}
    </button>
  </th>
{/snippet}

<div class="table-scroll">
  <table>
    <thead>
      <tr>
        {#if selectable}
          <th><span class="visually-hidden">Compare</span></th>
        {/if}
        <th>#</th>
        <th>Portfolio</th>
        <th>Agent</th>
        <th>Prompt</th>
        {@render sortHeader("age", "Age")}
        {@render sortHeader("itd_return", "ITD")}
        {@render sortHeader("vs_spy", "vs SPY")}
        {@render sortHeader("max_drawdown", "Max DD")}
        {@render sortHeader("sharpe", "Sharpe")}
        {@render sortHeader("turnover_pct", "Turnover")}
        <th>Trend</th>
      </tr>
    </thead>
    <tbody>
      {#each sorted as row, index (row.slug)}
        <tr class:benchmark={row.is_benchmark}>
          {#if selectable}
            <td>
              <input
                type="checkbox"
                class="compare-check"
                checked={selected.includes(row.slug)}
                onchange={() => onToggle?.(row.slug)}
                aria-label="Compare {row.name}"
              />
            </td>
          {/if}
          <td class="num muted">
            {#if row.is_benchmark}
              <span class="badge">BM</span>
            {:else}
              {sorted.slice(0, index).filter((r) => !r.is_benchmark).length + 1}
            {/if}
          </td>
          <td>
            <a href="/p/{row.slug}" onclick={(e) => link(e, `/p/${row.slug}`)}>{row.name}</a>
            <span class="badges">
              {#if row.status === "archived"}<span class="badge">archived</span>{/if}
              {#if row.too_early && !row.is_benchmark}
                <span class="badge warn" title="Younger than 6 months — sample too small to judge"
                  >too early</span
                >
              {/if}
              {#if row.stale_data}
                <span class="badge warn" title="Some prices were carried forward">stale data</span>
              {/if}
              {#if row.frozen_symbols.length}
                <span class="badge neg" title="No recent prices: {row.frozen_symbols.join(', ')}"
                  >frozen: {row.frozen_symbols.join(", ")}</span
                >
              {/if}
              {#if row.error}
                <span class="badge neg" title={row.error}>error</span>
              {/if}
            </span>
          </td>
          <td>
            <a href="/agent/{row.agent.slug}" onclick={(e) => link(e, `/agent/${row.agent.slug}`)}>
              {row.agent.name}
            </a>
          </td>
          <td>
            {#if row.prompt}
              <a href="/prompt/{row.prompt.slug}" onclick={(e) => link(e, `/prompt/${row.prompt!.slug}`)}>
                {row.prompt.name}
              </a>
            {:else}
              <span class="muted">—</span>
            {/if}
          </td>
          <td class="right num" title={row.inception ? `since ${row.inception}` : undefined}>
            {ageLabel(row.age_days)}
          </td>
          <td class="right num {signClass(row.metrics.itd_return)}">{pct(row.metrics.itd_return)}</td>
          <td class="right num vs-spy {signClass(row.metrics.vs_spy)}">{pct(row.metrics.vs_spy)}</td>
          <td class="right num">{pct(row.metrics.max_drawdown)}</td>
          <td class="right num">{num(row.metrics.sharpe)}</td>
          <td class="right num">{pctPoints(row.metrics.turnover_pct, 0)}</td>
          <td><Sparkline values={row.sparkline} /></td>
        </tr>
      {:else}
        <tr>
          <td colspan={selectable ? 12 : 11}>
            <div class="empty-state">
              <h3>No portfolios yet</h3>
              <p>Contestants appear here once the first allocation is entered.</p>
            </div>
          </td>
        </tr>
      {/each}
    </tbody>
  </table>
</div>

<style>
  tr.benchmark {
    background: var(--bg-inset);
  }

  tr.benchmark td:nth-child(2 of td) {
    font-weight: 600;
  }

  .badges {
    margin-left: 6px;
    display: inline-flex;
    gap: 4px;
    flex-wrap: wrap;
  }

  .vs-spy {
    font-weight: 700;
  }

  .sort-btn {
    font: inherit;
    color: inherit;
    text-transform: inherit;
    letter-spacing: inherit;
    padding: 0;
    white-space: nowrap;
  }

  .sort-btn:hover {
    color: var(--text-primary);
  }

  .compare-check {
    width: 16px;
    height: 16px;
    min-height: 0;
    accent-color: var(--accent);
  }

  .visually-hidden {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
  }
</style>
