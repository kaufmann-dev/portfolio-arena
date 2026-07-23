<script lang="ts">
  import { onMount } from "svelte";

  import { apiJson } from "../api/client";
  import type { CompareResponse, LeaderboardResponse, PortfolioSummary } from "../api/types";
  import LineChart, { type ChartSeries } from "../components/LineChart.svelte";
  import PortfolioTable from "../components/PortfolioTable.svelte";
  import SelectField from "../components/ui/SelectField.svelte";
  import ToggleSwitch from "../components/ui/ToggleSwitch.svelte";

  let data = $state<LeaderboardResponse | null>(null);
  let error = $state("");
  let showArchived = $state(false);
  let agentFilter = $state("all");
  let promptFilter = $state("all");
  let selected = $state<string[]>([]);
  let compareData = $state.raw<CompareResponse | null>(null);
  let compareLoading = $state(false);
  let comparisonError = $state("");

  onMount(() => {
    void loadLeaderboard();
  });

  async function loadLeaderboard() {
    error = "";
    try {
      data = await apiJson<LeaderboardResponse>("/api/leaderboard");
    } catch (e) {
      error = e instanceof Error ? e.message : "Could not load the leaderboard.";
    }
  }

  const agents = $derived.by(() => {
    const entries: [string, string][] = [];
    for (const row of data?.portfolios ?? []) {
      if (!row.is_benchmark && !entries.some(([slug]) => slug === row.agent.slug)) {
        entries.push([row.agent.slug, row.agent.name]);
      }
    }
    return entries;
  });

  const prompts = $derived.by(() => {
    const entries: [string, string][] = [];
    for (const row of data?.portfolios ?? []) {
      if (row.prompt && !row.is_benchmark && !entries.some(([slug]) => slug === row.prompt?.slug)) {
        entries.push([row.prompt.slug, row.prompt.name]);
      }
    }
    return entries;
  });

  const agentOptions = $derived([
    { value: "all", label: "All agents" },
    ...agents.map(([value, label]) => ({ value, label })),
  ]);

  const promptOptions = $derived([
    { value: "all", label: "All prompts" },
    ...prompts.map(([value, label]) => ({ value, label })),
  ]);

  const rows = $derived.by(() => {
    let out: PortfolioSummary[] = data?.portfolios ?? [];
    if (!showArchived) out = out.filter((row) => row.status === "active");
    if (agentFilter !== "all") out = out.filter((row) => row.is_benchmark || row.agent.slug === agentFilter);
    if (promptFilter !== "all") {
      out = out.filter((row) => row.is_benchmark || row.prompt?.slug === promptFilter);
    }
    return out;
  });

  function toggleCompare(slug: string) {
    selected = selected.includes(slug) ? selected.filter((item) => item !== slug) : [...selected, slug];
    void loadComparison();
  }

  function clearComparison() {
    selected = [];
    compareData = null;
    compareLoading = false;
    comparisonError = "";
  }

  async function loadComparison() {
    const slugs = selected;
    comparisonError = "";
    if (slugs.length < 2) {
      compareData = null;
      compareLoading = false;
      return;
    }

    compareLoading = true;
    try {
      const payload = await apiJson<CompareResponse>(`/api/compare?slugs=${slugs.join(",")}`);
      if (selected === slugs) compareData = payload;
    } catch (e) {
      if (selected === slugs) {
        compareData = null;
        comparisonError = e instanceof Error ? e.message : "Could not compare portfolios.";
      }
    } finally {
      if (selected === slugs) compareLoading = false;
    }
  }

  const compareSeries = $derived.by((): ChartSeries[] => {
    if (!compareData) return [];
    return compareData.series.map((entry) => ({
      name: entry.name,
      points: entry.series,
      dashed: entry.is_benchmark,
    }));
  });
</script>

<svelte:head>
  <title>Leaderboard · Portfolio Arena</title>
</svelte:head>

<section class="leaderboard-page" aria-labelledby="leaderboard-title">
  <header class="page-head">
    <div>
      <h1 id="leaderboard-title">Leaderboard</h1>
      <p class="lede">
        Can LLMs pick portfolios that beat SPY? Paper portfolios measured on total return, net of transaction
        costs.
      </p>
    </div>
    <div class="valuation-stamp">
      <span>Valuation</span>
      <strong class="num">{data?.as_of ? `${data.as_of} close` : "Pending"}</strong>
    </div>
  </header>

  {#if error}
    <div class="error-box load-error" role="alert">
      <span>{error}</span>
      <button class="retry-button" type="button" onclick={loadLeaderboard}>Retry</button>
    </div>
  {/if}

  {#if !data && !error}
    <div class="loading-state" aria-live="polite" aria-busy="true">
      <span class="loading-mark" aria-hidden="true"></span>
      Valuing portfolios…
    </div>
  {:else if data}
    <section class="filter-panel" aria-label="Leaderboard filters">
      <div class="filter-controls">
        <SelectField
          id="leaderboard-agent"
          label="Agent"
          options={agentOptions}
          bind:value={agentFilter}
          compact
        />
        <SelectField
          id="leaderboard-prompt"
          label="Prompt"
          options={promptOptions}
          bind:value={promptFilter}
          compact
        />
        <ToggleSwitch label="Show archived" bind:checked={showArchived} />
      </div>

      <div class="filter-context">
        <span class="result-count num">{rows.length} shown</span>
        <div class="compare-status" role="status" aria-live="polite">
          {#if selected.length === 1}
            <span>Select one more portfolio to compare.</span>
          {:else if selected.length >= 2}
            <span>Comparing {selected.length} portfolios.</span>
            <button class="clear-button" type="button" onclick={clearComparison}>Clear</button>
          {:else}
            <span>Select portfolios to compare their track records.</span>
          {/if}
        </div>
      </div>
    </section>

    {#if selected.length >= 2}
      <section class="comparison-panel" aria-labelledby="comparison-title" aria-busy={compareLoading}>
        <header class="comparison-head">
          <div>
            <h2 id="comparison-title">
              {#if compareData}
                Rebased to 100 at {compareData.start}
              {:else}
                Portfolio comparison
              {/if}
            </h2>
            <p>Uses the latest common inception so every line starts from the same date.</p>
          </div>
          {#if compareLoading && compareData}
            <span class="updating-label" role="status">Updating…</span>
          {/if}
        </header>

        {#if compareLoading && !compareData}
          <div class="loading-state compact" aria-live="polite">
            <span class="loading-mark" aria-hidden="true"></span>
            Loading comparison…
          </div>
        {:else if comparisonError}
          <div class="error-box comparison-error" role="alert">
            <span>{comparisonError}</span>
            <button class="retry-button" type="button" onclick={loadComparison}>Retry</button>
          </div>
        {:else if compareData}
          <LineChart series={compareSeries} ariaLabel="Portfolio comparison chart" height={300} />
        {/if}
      </section>
    {/if}

    <PortfolioTable {rows} selectable {selected} onToggle={toggleCompare} />
  {/if}
</section>

<style>
  .leaderboard-page {
    display: grid;
    gap: 22px;
  }

  .page-head {
    display: flex;
    align-items: end;
    justify-content: space-between;
    gap: 32px;
    padding: 18px 0 24px;
    border-bottom: 1px solid var(--border-strong);
    margin-bottom: 0;
  }

  h1 {
    margin: 0;
    font-size: clamp(32px, 5vw, 58px);
    font-weight: 650;
    letter-spacing: -0.045em;
    line-height: 0.95;
  }

  .lede {
    max-width: 700px;
    margin-top: 14px;
    color: var(--text-secondary);
    font-size: 15px;
    line-height: 1.6;
  }

  .valuation-stamp {
    display: grid;
    flex: 0 0 auto;
    gap: 3px;
    padding-left: 18px;
    border-left: 1px solid var(--border-strong);
    text-align: right;
  }

  .valuation-stamp span {
    color: var(--text-tertiary);
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }

  .valuation-stamp strong {
    font-size: 12px;
    font-weight: 500;
  }

  .filter-panel {
    display: grid;
    gap: 14px;
    padding: 16px 0;
    border-bottom: 1px solid var(--border-subtle);
  }

  .filter-controls {
    display: grid;
    grid-template-columns: minmax(170px, 220px) minmax(170px, 220px) auto;
    align-items: end;
    gap: 12px;
  }

  .filter-controls :global(.select-trigger) {
    min-height: 38px;
    border-radius: 0;
  }

  .filter-controls :global(.switch-field) {
    min-height: 38px;
    margin: 0;
    border-radius: 0;
  }

  .filter-controls :global(.switch-control),
  .filter-controls :global(.switch-thumb) {
    border-radius: 0;
  }

  .filter-context {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    color: var(--text-secondary);
    font-size: 12px;
  }

  .result-count {
    color: var(--text-tertiary);
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }

  .compare-status {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 10px;
    min-height: 28px;
    text-align: right;
  }

  .clear-button,
  .retry-button {
    min-height: 30px;
    padding: 4px 10px;
    border: 1px solid var(--border-strong);
    border-radius: 0;
    color: var(--text-primary);
    font-size: 12px;
    font-weight: 600;
  }

  .clear-button:hover,
  .retry-button:hover {
    border-color: var(--accent);
    color: var(--accent);
  }

  .comparison-panel {
    min-width: 0;
    padding: 18px;
    border: 1px solid var(--border-strong);
    border-radius: 0;
    background: var(--bg-surface);
  }

  .comparison-head {
    display: flex;
    align-items: start;
    justify-content: space-between;
    gap: 20px;
    margin-bottom: 18px;
  }

  .comparison-head h2 {
    margin: 0;
    font-size: 17px;
    letter-spacing: -0.015em;
  }

  .comparison-head > div > p:last-child {
    margin-top: 4px;
    color: var(--text-secondary);
    font-size: 12px;
  }

  .updating-label {
    color: var(--accent);
    font-family: var(--font-mono);
    font-size: 11px;
    text-transform: uppercase;
  }

  .loading-state {
    display: flex;
    min-height: 180px;
    align-items: center;
    justify-content: center;
    gap: 10px;
    color: var(--text-secondary);
  }

  .loading-state.compact {
    min-height: 260px;
  }

  .loading-mark {
    width: 8px;
    height: 8px;
    background: var(--accent);
    animation: pulse 1s steps(2, end) infinite;
  }

  .load-error,
  .comparison-error {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    margin: 0;
    border-radius: 0;
  }

  .leaderboard-page :global(.error-box),
  .leaderboard-page :global(.badge) {
    border-radius: 0;
  }

  @keyframes pulse {
    50% {
      opacity: 0.35;
    }
  }

  @media (max-width: 720px) {
    .leaderboard-page {
      gap: 16px;
    }

    .page-head {
      display: grid;
      align-items: start;
      gap: 18px;
      padding-top: 8px;
    }

    .valuation-stamp {
      padding: 10px 0 0;
      border-top: 1px solid var(--border-subtle);
      border-left: 0;
      text-align: left;
    }

    .filter-controls {
      grid-template-columns: 1fr 1fr;
    }

    .filter-controls :global(.switch-field) {
      grid-column: 1 / -1;
    }

    .filter-context,
    .comparison-head {
      align-items: start;
      flex-direction: column;
    }

    .compare-status {
      justify-content: flex-start;
      text-align: left;
    }

    .comparison-panel {
      padding: 14px 0;
      border-inline: 0;
    }
  }

  @media (max-width: 460px) {
    .filter-controls {
      grid-template-columns: 1fr;
    }

    .filter-controls :global(.switch-field) {
      grid-column: auto;
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .loading-mark {
      animation: none;
    }
  }
</style>
