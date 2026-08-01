<script lang="ts">
  import { ChevronDown, ChevronUp } from "@lucide/svelte";

  import type {
    BenchmarkArenaPortfolio,
    RebuiltAnalysisContext,
    RebuiltArenaPortfolio,
    RebuiltArenaResponse,
    RebuiltMetaControl,
    RebuiltView,
  } from "../api/types";
  import { portfolioAnalysisHref } from "../arena";
  import { num, pct, pctPoints, pctSignClass } from "../format";
  import { link } from "../stores/router.svelte";
  import EvidenceBadge from "./EvidenceBadge.svelte";
  import SelectField from "./ui/SelectField.svelte";

  type Row = RebuiltArenaResponse["portfolios"][number] | RebuiltMetaControl;
  type SortKey =
    "rank_score" | "mean_daily_alpha" | "information_ratio" | "sharpe" | "hit_rate" | "completion_ratio";

  interface Props {
    rows: Row[];
    view: RebuiltView;
    context: RebuiltAnalysisContext;
    selected?: string[];
    onToggle?: (slug: string) => void;
  }

  const SIGNAL_SORT_OPTIONS: { value: SortKey; label: string }[] = [
    { value: "rank_score", label: "Adjusted lower 95%" },
    { value: "mean_daily_alpha", label: "Mean daily alpha" },
    { value: "hit_rate", label: "Hit rate" },
    { value: "completion_ratio", label: "Completion" },
  ];
  const POLICY_SORT_OPTIONS: { value: SortKey; label: string }[] = [
    { value: "rank_score", label: "Adjusted lower 95%" },
    { value: "mean_daily_alpha", label: "Mean daily alpha" },
    { value: "information_ratio", label: "Information ratio" },
    { value: "sharpe", label: "Sharpe" },
    { value: "hit_rate", label: "Hit rate" },
    { value: "completion_ratio", label: "Completion" },
  ];

  const uid = $props.id();
  const { rows, view, context, selected = [], onToggle }: Props = $props();
  let sortKey = $state<SortKey>("rank_score");
  let sortDesc = $state(true);
  const sortOptions = $derived(view === "signal" ? SIGNAL_SORT_OPTIONS : POLICY_SORT_OPTIONS);
  const activeSortKey = $derived(
    sortOptions.some((option) => option.value === sortKey) ? sortKey : "rank_score",
  );

  function metric(row: RebuiltArenaPortfolio, key: SortKey): number | null {
    const value =
      key === "completion_ratio"
        ? row.completion.completion_ratio
        : key === "rank_score"
          ? row.rank_score
          : row.metrics[key];
    return typeof value === "number" && isFinite(value) ? value : null;
  }

  function compareRows(a: RebuiltArenaPortfolio, b: RebuiltArenaPortfolio): number {
    const aValue = metric(a, activeSortKey);
    const bValue = metric(b, activeSortKey);
    if (aValue === null && bValue === null) return a.name.localeCompare(b.name);
    if (aValue === null) return 1;
    if (bValue === null) return -1;
    const difference = sortDesc ? bValue - aValue : aValue - bValue;
    return difference || a.name.localeCompare(b.name);
  }

  const benchmark = $derived(rows.find((row): row is BenchmarkArenaPortfolio => row.kind === "benchmark"));
  const control = $derived(rows.find((row): row is RebuiltMetaControl => row.kind === "control"));
  const contestants = $derived(
    [...rows.filter((row): row is RebuiltArenaPortfolio => row.kind === "rebuilt")].sort(compareRows),
  );
  const currentSortLabel = $derived(
    sortOptions.find((option) => option.value === activeSortKey)?.label ?? "Adjusted lower 95%",
  );

  function setSort(key: SortKey): void {
    if (activeSortKey === key) {
      sortDesc = !sortDesc;
      return;
    }
    sortKey = key;
    sortDesc = true;
  }

  function selectMobileSort(value: string): void {
    const next = sortOptions.find((option) => option.value === value)?.value;
    if (!next || next === activeSortKey) return;
    sortKey = next;
    sortDesc = true;
  }

  function ariaSort(key: SortKey): "ascending" | "descending" | undefined {
    if (activeSortKey !== key) return undefined;
    return sortDesc ? "descending" : "ascending";
  }

  function detailHref(row: RebuiltArenaPortfolio): string {
    return portfolioAnalysisHref(row.slug, "rebuilt", row.direction, context);
  }
</script>

{#snippet sortHeader(key: SortKey, label: string)}
  <th scope="col" class="right sortable" aria-sort={ariaSort(key)}>
    <button
      type="button"
      onclick={() => setSort(key)}
      aria-label={`Sort by ${label}${activeSortKey === key ? `, currently ${sortDesc ? "descending" : "ascending"}` : ""}`}
    >
      <span>{label}</span>
      {#if activeSortKey === key}
        {#if sortDesc}
          <ChevronDown size={13} aria-hidden="true" />
        {:else}
          <ChevronUp size={13} aria-hidden="true" />
        {/if}
      {/if}
    </button>
  </th>
{/snippet}

{#snippet identity(row: RebuiltArenaPortfolio)}
  <a class="portfolio-link" href={detailHref(row)} onclick={(event) => link(event, detailHref(row))}>
    {row.name}
  </a>
  <span class="badges">
    <span class="badge">{row.direction}</span>
    <EvidenceBadge state={row.evidence} compact />
    {#if row.status === "archived"}<span class="badge">archived</span>{/if}
    {#if row.is_liquidated}
      <span class="badge neg" title={row.liquidated_at ? `Liquidated ${row.liquidated_at}` : "Liquidated"}>
        policy liquidated
      </span>
    {/if}
    {#if view === "common" && !row.common_admitted && row.status === "active" && !row.founding_v2 && !row.error}
      <span class="badge warn" title="Not yet admitted to the Common-policy meta-portfolio">
        H20 incubation
      </span>
    {:else if view !== "common" && !row.completion.eligible}
      <span class="badge">selected-horizon incubation</span>
    {/if}
    {#if row.stale_data}<span class="badge warn">stale data</span>{/if}
    {#if row.frozen_symbols.length}
      <span class="badge neg" title="Prices frozen at their last known values">
        frozen: {row.frozen_symbols.join(", ")}
      </span>
    {/if}
    {#if row.error}<span class="badge neg" title={row.error}>error</span>{/if}
  </span>
{/snippet}

{#snippet compareControl(row: RebuiltArenaPortfolio, label = false)}
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

<div class="arena-rankings rebuilt-rankings">
  <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
  <div
    class="desktop-table table-scroll"
    role="region"
    aria-label="Rebuilt portfolio rankings table"
    tabindex="0"
  >
    <table class="data-table">
      <caption>
        Rebuilt portfolio rankings in {view} view. {control
          ? "SPY and Consensus Control are pinned references"
          : "SPY is a pinned reference"}; portfolio rows are sorted by
        {currentSortLabel}
        {sortDesc ? " descending" : " ascending"}.
      </caption>
      <thead>
        <tr>
          <th class="compare-col" scope="col"><span class="visually-hidden">Compare</span></th>
          <th class="rank-col" scope="col">Rank</th>
          <th scope="col">Portfolio</th>
          <th scope="col">Agent</th>
          <th scope="col">Prompt</th>
          <th scope="col" class="right">Horizon</th>
          {#if view !== "signal"}<th scope="col" class="right">Exposure</th>{/if}
          {@render sortHeader("rank_score", "Lower 95%")}
          {@render sortHeader("mean_daily_alpha", "Mean α/day")}
          {#if view !== "signal"}
            {@render sortHeader("information_ratio", "Info ratio")}
            {@render sortHeader("sharpe", "Sharpe")}
          {/if}
          {@render sortHeader("hit_rate", "Hit rate")}
          {@render sortHeader("completion_ratio", "Completion")}
        </tr>
      </thead>
      <tbody>
        {#if benchmark}
          <tr class="benchmark">
            <td></td>
            <td class="rank-col"><span class="badge">REF</span></td>
            <th scope="row">
              {benchmark.name}
              {#if benchmark.is_liquidated}<span class="badge neg">liquidated</span>{/if}
            </th>
            <td>—</td>
            <td>{benchmark.direction === "short" ? "Daily −1× SPY" : "Buy and hold SPY"}</td>
            <td class="right num">—</td>
            {#if view !== "signal"}<td class="right num">100%</td>{/if}
            <td class="right num">0.00%</td>
            <td class="right num">0.00%</td>
            {#if view !== "signal"}
              <td class="right num">—</td>
              <td class="right num">{num(benchmark.metrics.sharpe)}</td>
            {/if}
            <td class="right num">—</td>
            <td class="right num">—</td>
          </tr>
        {/if}
        {#if control}
          <tr class="benchmark">
            <td></td>
            <td class="rank-col"><span class="badge">REF</span></td>
            <th scope="row">
              {control.name}
              <span class="badge">same-cell average · {control.contributor_count} sources</span>
              {#if control.stale_data}<span class="badge warn">stale data</span>{/if}
              {#if control.error}<span class="badge neg" title={control.error}>error</span>{/if}
            </th>
            <td>—</td>
            <td>Equal-weight normal signals</td>
            <td class="right num">{control.selected_policy ? `H${control.selected_policy.horizon}` : "—"}</td>
            {#if view !== "signal"}
              <td class="right num">{pctPoints(control.selected_policy?.exposure_pct, 0)}</td>
            {/if}
            <td class="right num">—</td>
            <td class="right num {pctSignClass(control.metrics.mean_daily_alpha, 2)}">
              {pct(control.metrics.mean_daily_alpha, 2)}
            </td>
            {#if view !== "signal"}
              <td class="right num">{num(control.metrics.information_ratio)}</td>
              <td class="right num">{num(control.metrics.sharpe)}</td>
            {/if}
            <td class="right num">{pct(control.metrics.hit_rate, 0)}</td>
            <td
              class="right num"
              title={`${control.completion.complete_count} complete, ${control.completion.open_count} open`}
            >
              {pct(control.completion.completion_ratio, 0)}
            </td>
          </tr>
        {/if}
        {#each contestants as row (row.id)}
          <tr>
            <td class="compare-col">{@render compareControl(row)}</td>
            <td class="rank-col num">{row.rank ?? "—"}</td>
            <th class="portfolio-col" scope="row">{@render identity(row)}</th>
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
            <td class="right num">{row.selected_policy ? `H${row.selected_policy.horizon}` : "—"}</td>
            {#if view !== "signal"}
              <td class="right num">{pctPoints(row.selected_policy?.exposure_pct, 0)}</td>
            {/if}
            <td class="right num score {pctSignClass(row.rank_score, 2)}">
              {pct(row.rank_score, 2)}
            </td>
            <td class="right num {pctSignClass(row.metrics.mean_daily_alpha, 2)}">
              {pct(row.metrics.mean_daily_alpha, 2)}
            </td>
            {#if view !== "signal"}
              <td class="right num">{num(row.metrics.information_ratio)}</td>
              <td class="right num">{num(row.metrics.sharpe)}</td>
            {/if}
            <td class="right num">{pct(row.metrics.hit_rate, 0)}</td>
            <td
              class="right num"
              title={`${row.completion.complete_count} complete, ${row.completion.open_count} open`}
            >
              {pct(row.completion.completion_ratio, 0)}
            </td>
          </tr>
        {:else}
          <tr>
            <td colspan={view === "signal" ? 10 : 13} class="table-empty">
              No rebuilt portfolios match these filters.
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>

  <section class="mobile-rankings" aria-label="Rebuilt portfolio rankings">
    <div class="mobile-sort">
      <SelectField
        id={`${uid}-rebuilt-sort`}
        label="Rank by"
        options={sortOptions}
        value={activeSortKey}
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
        <header>
          <span class="rank">REF</span><strong>{benchmark.name}</strong><span class="badge">reference</span>
          {#if benchmark.is_liquidated}<span class="badge neg">liquidated</span>{/if}
        </header>
        <p>
          Zero-alpha benchmark · {benchmark.direction === "short" ? "daily −1× SPY" : "buy and hold SPY"}
        </p>
      </article>
    {/if}

    {#if control}
      <article class="mobile-card benchmark-card">
        <header>
          <span class="rank">REF</span><strong>{control.name}</strong><span class="badge"
            >{control.contributor_count} sources</span
          >
        </header>
        <p>Equal-weight normal signals · unranked consensus reference</p>
      </article>
    {/if}

    {#each contestants as row (row.id)}
      <article class="mobile-card">
        <header>
          <span class="rank">#{row.rank ?? "—"}</span>
          <div>{@render identity(row)}</div>
          {@render compareControl(row, true)}
        </header>
        <p class="context">
          {row.agent.name} · {row.prompt.name}
          {#if row.selected_policy}
            · H{row.selected_policy.horizon}{view === "signal"
              ? ""
              : ` · ${pctPoints(row.selected_policy.exposure_pct, 0)} exposure`}
          {/if}
        </p>
        <dl>
          {@render metricTile("Lower 95%", pct(row.rank_score, 2), pctSignClass(row.rank_score, 2))}
          {@render metricTile(
            "Mean α/day",
            pct(row.metrics.mean_daily_alpha, 2),
            pctSignClass(row.metrics.mean_daily_alpha, 2),
          )}
          {#if view !== "signal"}
            {@render metricTile("Info ratio", num(row.metrics.information_ratio))}
            {@render metricTile("Sharpe", num(row.metrics.sharpe))}
          {/if}
          {@render metricTile("Hit rate", pct(row.metrics.hit_rate, 0))}
          {@render metricTile("Completion", pct(row.completion.completion_ratio, 0))}
        </dl>
      </article>
    {/each}
  </section>
</div>
