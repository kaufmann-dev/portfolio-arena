<script lang="ts">
  import { apiJson } from "../api/client";
  import type { CompareResponse, LeaderboardResponse, PortfolioSummary } from "../api/types";
  import LineChart, { type ChartSeries } from "../components/LineChart.svelte";
  import PortfolioTable from "../components/PortfolioTable.svelte";

  let data = $state<LeaderboardResponse | null>(null);
  let error = $state("");
  let showArchived = $state(false);
  let agentFilter = $state("all");
  let promptFilter = $state("all");
  let selected = $state<string[]>([]);
  let compareData = $state<CompareResponse | null>(null);
  let compareLoading = $state(false);

  $effect(() => {
    apiJson<LeaderboardResponse>("/api/leaderboard", { auth: false })
      .then((payload) => (data = payload))
      .catch((e) => (error = e.message));
  });

  const agents = $derived.by(() => {
    const map = new Map<string, string>();
    for (const row of data?.portfolios ?? []) {
      if (!row.is_benchmark) map.set(row.agent.slug, row.agent.name);
    }
    return [...map.entries()];
  });

  const prompts = $derived.by(() => {
    const map = new Map<string, string>();
    for (const row of data?.portfolios ?? []) {
      if (row.prompt && !row.is_benchmark) map.set(row.prompt.slug, row.prompt.name);
    }
    return [...map.entries()];
  });

  const rows = $derived.by(() => {
    let out: PortfolioSummary[] = data?.portfolios ?? [];
    if (!showArchived) out = out.filter((row) => row.status === "active");
    if (agentFilter !== "all") out = out.filter((row) => row.is_benchmark || row.agent.slug === agentFilter);
    if (promptFilter !== "all")
      out = out.filter((row) => row.is_benchmark || row.prompt?.slug === promptFilter);
    return out;
  });

  function toggleCompare(slug: string) {
    selected = selected.includes(slug) ? selected.filter((s) => s !== slug) : [...selected, slug];
  }

  $effect(() => {
    if (selected.length < 2) {
      compareData = null;
      return;
    }
    compareLoading = true;
    apiJson<CompareResponse>(`/api/compare?slugs=${selected.join(",")}`, { auth: false })
      .then((payload) => (compareData = payload))
      .catch((e) => (error = e.message))
      .finally(() => (compareLoading = false));
  });

  const compareSeries = $derived.by((): ChartSeries[] => {
    if (!compareData) return [];
    return compareData.series.map((entry) => ({
      name: entry.name,
      points: entry.series,
      dashed: entry.is_benchmark,
    }));
  });
</script>

<div class="page-head">
  <div>
    <h1>Leaderboard</h1>
    <p class="muted">
      Can LLMs pick portfolios that beat SPY? Paper portfolios, total-return, net of transaction costs.
      {#if data?.as_of}<span class="num">As of {data.as_of} close.</span>{/if}
    </p>
  </div>
</div>

{#if error}
  <div class="error-box">{error}</div>
{/if}

{#if !data && !error}
  <div class="loading-block"><span class="spinner" aria-hidden="true"></span> Valuing portfolios…</div>
{:else if data}
  <div class="filters">
    <label class="filter">
      <span>Agent</span>
      <select bind:value={agentFilter}>
        <option value="all">All agents</option>
        {#each agents as [slug, name] (slug)}
          <option value={slug}>{name}</option>
        {/each}
      </select>
    </label>
    <label class="filter">
      <span>Prompt</span>
      <select bind:value={promptFilter}>
        <option value="all">All prompts</option>
        {#each prompts as [slug, name] (slug)}
          <option value={slug}>{name}</option>
        {/each}
      </select>
    </label>
    <label class="filter checkbox-filter">
      <input type="checkbox" bind:checked={showArchived} />
      <span>Show archived</span>
    </label>
    <span class="muted compare-hint">
      {#if selected.length === 1}
        Select one more portfolio to compare.
      {:else if selected.length >= 2}
        Comparing {selected.length} portfolios.
        <button class="btn small" onclick={() => (selected = [])}>Clear</button>
      {:else}
        Tick rows to overlay them in a chart.
      {/if}
    </span>
  </div>

  {#if selected.length >= 2}
    <section class="card compare-card" aria-label="Comparison chart">
      {#if compareLoading && !compareData}
        <div class="loading-block"><span class="spinner" aria-hidden="true"></span> Loading…</div>
      {:else if compareData}
        <h2>
          Rebased to 100 at {compareData.start}
          <span class="muted">(latest common inception)</span>
        </h2>
        <LineChart series={compareSeries} ariaLabel="Portfolio comparison chart" height={300} />
      {/if}
    </section>
  {/if}

  <PortfolioTable {rows} selectable {selected} onToggle={toggleCompare} />
{/if}

<style>
  .page-head {
    margin-bottom: 18px;
  }

  h1 {
    font-size: 22px;
    margin-bottom: 4px;
  }

  h2 {
    font-size: 14px;
    margin-bottom: 10px;
  }

  .filters {
    display: flex;
    align-items: end;
    flex-wrap: wrap;
    gap: 14px;
    margin-bottom: 14px;
  }

  .filter span {
    display: block;
    font-size: 11.5px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--text-secondary);
    margin-bottom: 4px;
  }

  .filter select {
    min-width: 170px;
    width: auto;
  }

  .checkbox-filter {
    display: flex;
    align-items: center;
    gap: 8px;
    min-height: 36px;
  }

  .checkbox-filter input {
    width: 16px;
    height: 16px;
    min-height: 0;
    accent-color: var(--accent);
  }

  .checkbox-filter span {
    margin-bottom: 0;
    text-transform: none;
    font-size: 13px;
    font-weight: 500;
    color: var(--text-primary);
  }

  .compare-hint {
    margin-left: auto;
    font-size: 12.5px;
    display: inline-flex;
    align-items: center;
    gap: 8px;
  }

  .compare-card {
    margin-bottom: 16px;
  }
</style>
