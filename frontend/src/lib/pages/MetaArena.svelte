<script lang="ts">
  import { onMount } from "svelte";

  import { apiJson } from "../api/client";
  import type {
    ArenaTrack,
    CostBasis,
    Direction,
    ManagedArenaPortfolio,
    ManagedMetaResponse,
    MetaCompareResponse,
    RebuiltArenaPortfolio,
    RebuiltMetaResponse,
    RebuiltObjective,
    RebuiltView,
  } from "../api/types";
  import { parseDirection, rebuiltContext, rebuiltContextParams } from "../arena";
  import LineChart, { type ChartSeries } from "../components/LineChart.svelte";
  import ManagedArenaTable from "../components/ManagedArenaTable.svelte";
  import MarketDataWarning from "../components/MarketDataWarning.svelte";
  import RebuiltArenaTable from "../components/RebuiltArenaTable.svelte";
  import SignalMatrix from "../components/SignalMatrix.svelte";
  import SelectField from "../components/ui/SelectField.svelte";
  import ToggleSwitch from "../components/ui/ToggleSwitch.svelte";
  import { pctPoints } from "../format";
  import { combineMarketData } from "../marketData";
  import { metaBatchStatusCopy } from "../meta";

  type MetaPortfolio = ManagedArenaPortfolio | RebuiltArenaPortfolio;

  const DIRECTIONS: { value: Direction; label: string; description: string }[] = [
    {
      value: "long",
      label: "Long",
      description: "Synthesizes the strongest long ideas after reconciling the normal Arena's evidence.",
    },
    {
      value: "short",
      label: "Short",
      description: "Synthesizes actionable underperformance theses while testing timing and squeeze risk.",
    },
  ];
  const TRACKS: { value: ArenaTrack; label: string; description: string }[] = [
    {
      value: "rebuilt",
      label: "Rebuilt",
      description: "Fresh synthesis signals built from the frozen daily source packet.",
    },
    {
      value: "managed",
      label: "Managed",
      description: "Stateful synthesis portfolios that re-underwrite their existing holdings.",
    },
  ];
  const VIEW_OPTIONS: { value: RebuiltView; label: string }[] = [
    { value: "common", label: "Common policy" },
    { value: "tuned", label: "Portfolio tuned" },
    { value: "signal", label: "Signal Alpha" },
  ];
  const OBJECTIVE_OPTIONS: { value: RebuiltObjective; label: string }[] = [
    { value: "canonical", label: "Adjusted lower 95%" },
    { value: "max_alpha", label: "Mean alpha" },
    { value: "max_information_ratio", label: "Information ratio" },
    { value: "max_sharpe", label: "Sharpe (rf=0)" },
  ];
  const COST_OPTIONS: { value: CostBasis; label: string }[] = [
    { value: "net", label: "Net of costs" },
    { value: "gross", label: "Gross" },
  ];
  const HORIZON_OPTIONS = Array.from({ length: 20 }, (_, index) => ({
    value: String(index + 1),
    label: `H${index + 1} · ${index + 1} session${index ? "s" : ""}`,
  }));

  let direction = $state<Direction>(
    parseDirection(new URLSearchParams(window.location.search).get("direction")),
  );
  let track = $state<ArenaTrack>("rebuilt");
  let rebuiltView = $state<RebuiltView>("common");
  let objective = $state<RebuiltObjective>("canonical");
  let costBasis = $state<CostBasis>("net");
  let horizon = $state(5);
  let showArchived = $state(false);
  let agentFilter = $state("all");
  let promptFilter = $state("all");
  let managedData = $state.raw<ManagedMetaResponse | null>(null);
  let rebuiltData = $state.raw<RebuiltMetaResponse | null>(null);
  let loading = $state(false);
  let error = $state("");
  let selected = $state<string[]>([]);
  let compareData = $state.raw<MetaCompareResponse | null>(null);
  let compareLoading = $state(false);
  let comparisonError = $state("");
  let requestSequence = 0;
  let compareSequence = 0;

  const currentData = $derived(track === "rebuilt" ? rebuiltData : managedData);
  const batch = $derived(currentData?.batch ?? null);
  const allMetaRows = $derived.by((): MetaPortfolio[] => {
    if (!currentData) return [];
    return currentData.portfolios.filter(
      (row): row is MetaPortfolio => row.kind === "managed" || row.kind === "rebuilt",
    );
  });
  const agents = $derived.by(() => {
    const entries: { value: string; label: string }[] = [];
    for (const row of allMetaRows) {
      if (!entries.some((entry) => entry.value === row.agent.slug)) {
        entries.push({ value: row.agent.slug, label: row.agent.name });
      }
    }
    return entries;
  });
  const prompts = $derived.by(() => {
    const entries: { value: string; label: string }[] = [];
    for (const row of allMetaRows) {
      if (!entries.some((entry) => entry.value === row.prompt.slug)) {
        entries.push({ value: row.prompt.slug, label: row.prompt.name });
      }
    }
    return entries;
  });
  const agentOptions = $derived([{ value: "all", label: "All agents" }, ...agents]);
  const promptOptions = $derived([{ value: "all", label: "All synthesis prompts" }, ...prompts]);
  const filteredMetaRows = $derived(
    allMetaRows.filter(
      (row) =>
        (showArchived || row.status === "active") &&
        (agentFilter === "all" || row.agent.slug === agentFilter) &&
        (promptFilter === "all" || row.prompt.slug === promptFilter),
    ),
  );
  const managedRows = $derived.by((): ManagedMetaResponse["portfolios"] => {
    if (!managedData) return [];
    const references = managedData.portfolios.filter(
      (row) => row.kind === "benchmark" || row.kind === "control",
    );
    const controlAlreadyIncluded = references.some((row) => row.kind === "control");
    if (!controlAlreadyIncluded && managedData.control) references.push(managedData.control);
    return [
      ...references,
      ...filteredMetaRows.filter((row): row is ManagedArenaPortfolio => row.kind === "managed"),
    ];
  });
  const rebuiltRows = $derived.by((): RebuiltMetaResponse["portfolios"] => {
    if (!rebuiltData) return [];
    const references = rebuiltData.portfolios.filter(
      (row) => row.kind === "benchmark" || row.kind === "control",
    );
    const controlAlreadyIncluded = references.some((row) => row.kind === "control");
    if (!controlAlreadyIncluded && rebuiltData.control) references.push(rebuiltData.control);
    return [
      ...references,
      ...filteredMetaRows.filter((row): row is RebuiltArenaPortfolio => row.kind === "rebuilt"),
    ];
  });
  const signalRows = $derived(
    filteredMetaRows.filter((row): row is RebuiltArenaPortfolio => row.kind === "rebuilt"),
  );
  const rebuiltBenchmarkName = $derived(
    rebuiltData?.portfolios.find((row) => row.kind === "benchmark")?.name ??
      (direction === "short" ? "Short SPY" : "SPY"),
  );
  const compareSeries = $derived.by((): ChartSeries[] => {
    if (!compareData) return [];
    const series: ChartSeries[] = compareData.series.map((entry) => ({
      name: entry.name,
      points: entry.series,
    }));
    if (compareData.control_series?.series.length) {
      series.push({
        name: compareData.control_series.name,
        points: compareData.control_series.series,
        dashed: true,
        color: "var(--warn)",
      });
    }
    if (compareData.spy_series.length) {
      series.push({
        name: compareData.direction === "short" ? "Short SPY" : "SPY",
        points: compareData.spy_series,
        dashed: true,
        color: "var(--spark)",
      });
    }
    return series;
  });
  const displayedMarketData = $derived(combineMarketData(currentData, compareData));
  const activeDirectionDescription = $derived(
    DIRECTIONS.find((item) => item.value === direction)?.description ?? "",
  );
  const activeTrackDescription = $derived(TRACKS.find((item) => item.value === track)?.description ?? "");

  onMount(() => {
    writeDirectionUrl(direction);
    void loadMetaArena();
  });

  function rebuiltQuery(): URLSearchParams {
    const query = rebuiltContextParams(rebuiltContext(rebuiltView, objective, costBasis, horizon));
    query.set("direction", direction);
    return query;
  }

  function writeDirectionUrl(next: Direction): void {
    const url = new URL(window.location.href);
    url.searchParams.set("direction", next);
    window.history.replaceState(window.history.state, "", url);
  }

  async function loadMetaArena(): Promise<void> {
    const sequence = ++requestSequence;
    error = "";
    loading = true;
    try {
      if (track === "managed") {
        const payload = await apiJson<ManagedMetaResponse>(`/api/meta/managed?direction=${direction}`);
        if (sequence === requestSequence) managedData = payload;
      } else {
        const payload = await apiJson<RebuiltMetaResponse>(`/api/meta/rebuilt?${rebuiltQuery().toString()}`);
        if (sequence === requestSequence) rebuiltData = payload;
      }
    } catch (caught) {
      if (sequence === requestSequence) {
        error = caught instanceof Error ? caught.message : "Could not load the Meta Arena.";
      }
    } finally {
      if (sequence === requestSequence) loading = false;
    }
  }

  function resetFilters(): void {
    agentFilter = "all";
    promptFilter = "all";
  }

  function changeDirection(next: Direction): void {
    if (direction === next) return;
    direction = next;
    writeDirectionUrl(next);
    managedData = null;
    rebuiltData = null;
    resetFilters();
    clearComparison();
    void loadMetaArena();
  }

  function changeTrack(next: ArenaTrack): void {
    if (track === next) return;
    track = next;
    resetFilters();
    clearComparison();
    void loadMetaArena();
  }

  function changeView(value: string): void {
    const next = VIEW_OPTIONS.find((option) => option.value === value)?.value;
    if (!next || next === rebuiltView) return;
    rebuiltView = next;
    if (next === "signal") {
      objective = "canonical";
      costBasis = "gross";
    } else if (costBasis === "gross") {
      costBasis = "net";
    }
    rebuiltData = null;
    clearComparison();
    void loadMetaArena();
  }

  function changeObjective(value: string): void {
    const next = OBJECTIVE_OPTIONS.find((option) => option.value === value)?.value;
    if (!next || next === objective) return;
    objective = next;
    rebuiltData = null;
    clearComparison();
    void loadMetaArena();
  }

  function changeCostBasis(value: string): void {
    const next = COST_OPTIONS.find((option) => option.value === value)?.value;
    if (!next || next === costBasis) return;
    costBasis = next;
    rebuiltData = null;
    clearComparison();
    void loadMetaArena();
  }

  function changeHorizon(value: string): void {
    const next = Number(value);
    if (!Number.isInteger(next) || next < 1 || next > 20 || next === horizon) return;
    horizon = next;
    rebuiltData = null;
    clearComparison();
    void loadMetaArena();
  }

  function toggleCompare(slug: string): void {
    if (!selected.includes(slug) && selected.length >= 8) {
      comparisonError = "Compare up to eight meta portfolios at a time.";
      return;
    }
    selected = selected.includes(slug)
      ? selected.filter((candidate) => candidate !== slug)
      : [...selected, slug];
    void loadComparison();
  }

  function clearComparison(): void {
    compareSequence += 1;
    selected = [];
    compareData = null;
    compareLoading = false;
    comparisonError = "";
  }

  async function loadComparison(): Promise<void> {
    const slugs = selected;
    const sequence = ++compareSequence;
    comparisonError = "";
    if (slugs.length === 0) {
      compareData = null;
      compareLoading = false;
      return;
    }

    const query = new URLSearchParams({ slugs: slugs.join(","), track, direction });
    if (track === "rebuilt") {
      for (const [key, value] of rebuiltQuery()) query.set(key, value);
    }

    compareLoading = true;
    try {
      const payload = await apiJson<MetaCompareResponse>(`/api/meta/compare?${query.toString()}`);
      if (sequence === compareSequence) compareData = payload;
    } catch (caught) {
      if (sequence === compareSequence) {
        compareData = null;
        comparisonError = caught instanceof Error ? caught.message : "Could not compare meta portfolios.";
      }
    } finally {
      if (sequence === compareSequence) compareLoading = false;
    }
  }
</script>

<svelte:head>
  <title>Meta Arena</title>
</svelte:head>

<section class="meta-page" aria-labelledby="meta-title">
  <header class="page-head">
    <div>
      <h1 id="meta-title">Meta Arena</h1>
      <p class="lede">
        Compare agents that reconcile the normal Arena's latest independent reasoning into one portfolio. The
        consensus control tests whether synthesis adds value beyond simply averaging the same-cell sources.
      </p>
    </div>
    <div class="valuation-stamp">
      <span>Valuation</span>
      <strong class="num">
        {displayedMarketData.asOf ? `${displayedMarketData.asOf} close` : "Pending"}
      </strong>
    </div>
  </header>

  <nav class="direction-selector" aria-label="Investment direction">
    {#each DIRECTIONS as item (item.value)}
      <button
        type="button"
        class={{ active: direction === item.value }}
        aria-pressed={direction === item.value}
        onclick={() => changeDirection(item.value)}
      >
        {item.label}
      </button>
    {/each}
  </nav>
  <p class="selector-description">{activeDirectionDescription}</p>

  <nav class="track-selector" aria-label="Meta Arena track">
    {#each TRACKS as item (item.value)}
      <button
        type="button"
        class={{ active: track === item.value }}
        aria-current={track === item.value ? "page" : undefined}
        onclick={() => changeTrack(item.value)}
      >
        <strong>{item.label}</strong>
        <span>{item.value === "rebuilt" ? "Fresh daily synthesis" : "Stateful synthesis"}</span>
      </button>
    {/each}
  </nav>
  <p class="selector-description">{activeTrackDescription}</p>

  {#if currentData}
    <MarketDataWarning status={displayedMarketData.status} asOf={displayedMarketData.asOf} />
  {/if}

  {#if error}
    <div class="error-box load-error" role="alert">
      <span>{error}</span>
      <button class="btn small" type="button" onclick={loadMetaArena}>Retry</button>
    </div>
  {/if}

  {#if currentData}
    <section
      class={["batch-panel", (batch?.status === "insufficient" || batch?.status === "failed") && "failed"]}
      aria-labelledby="batch-title"
    >
      <header>
        <div>
          <h2 id="batch-title">Source batch</h2>
          {#if batch}
            <p>{metaBatchStatusCopy(batch.status)}</p>
            {#if batch.status === "failed"}
              <p class="batch-error" role="alert">
                {batch.error ?? "Packet construction failed without details."}
              </p>
            {/if}
          {:else}
            <p>
              No synthesis batch has opened yet. The first scheduled normal-Arena session will create one.
            </p>
          {/if}
        </div>
        <span
          class={[
            "badge",
            batch?.status === "waiting" && "warn",
            batch?.status === "ready" && "success",
            (batch?.status === "insufficient" || batch?.status === "failed") && "neg",
          ]}
        >
          {batch?.status ?? "not started"}
        </span>
      </header>
      {#if batch}
        <dl class="batch-counts">
          <div>
            <dt>Session</dt>
            <dd class="num">{batch.session_date}</dd>
          </div>
          <div>
            <dt>Sources</dt>
            <dd class="num">{batch.source_count}</dd>
          </div>
          <div>
            <dt>Due / terminal</dt>
            <dd class="num">{batch.due_count} / {batch.terminal_count}</dd>
          </div>
          <div>
            <dt>Succeeded</dt>
            <dd class="num">{batch.success_count}</dd>
          </div>
          <div>
            <dt>Fallbacks</dt>
            <dd class="num">{batch.fallback_count}</dd>
          </div>
          <div>
            <dt>Missing</dt>
            <dd class="num">{batch.missing_count}</dd>
          </div>
        </dl>
        {#if batch.snapshot_sha256}
          <p class="snapshot-line">
            Snapshot <code>{batch.snapshot_sha256.slice(0, 16)}…</code>
            {#if batch.sources_finished_at}
              · sources finished <span class="num"
                >{batch.sources_finished_at.slice(0, 16).replace("T", " ")} UTC</span
              >
            {/if}
          </p>
        {/if}
      {/if}
    </section>
  {/if}

  {#if track === "rebuilt"}
    <section class="analysis-controls" aria-label="Rebuilt analysis controls">
      <SelectField
        id="meta-rebuilt-view"
        label="Comparison mode"
        options={VIEW_OPTIONS}
        value={rebuiltView}
        compact
        onValueChange={changeView}
      />
      {#if rebuiltView !== "signal"}
        <SelectField
          id="meta-rebuilt-objective"
          label="Policy objective"
          options={OBJECTIVE_OPTIONS}
          value={objective}
          compact
          onValueChange={changeObjective}
        />
        <SelectField
          id="meta-rebuilt-cost-basis"
          label="Returns"
          options={COST_OPTIONS}
          value={costBasis}
          compact
          onValueChange={changeCostBasis}
        />
      {:else}
        <SelectField
          id="meta-rebuilt-horizon"
          label="Holding period"
          options={HORIZON_OPTIONS}
          value={String(horizon)}
          compact
          onValueChange={changeHorizon}
        />
        <div class="locked-context">
          <span>Signal Alpha</span>
          <strong>Gross · direct evidence</strong>
        </div>
      {/if}
    </section>
  {/if}

  {#if rebuiltData && track === "rebuilt" && rebuiltView === "common"}
    <section class="selection-summary" aria-label="Normal Arena common policy">
      <span>Normal Arena policy</span>
      {#if rebuiltData.common_policy}
        <strong class="num">
          H{rebuiltData.common_policy.horizon} · {pctPoints(rebuiltData.common_policy.exposure_pct, 0)} exposure
        </strong>
        <p>
          Selected exclusively from normal rebuilt portfolios, then applied to meta portfolios and the
          consensus control.
        </p>
      {:else}
        <strong>Pending evidence</strong>
        <p>No normal-Arena common horizon and exposure pair is eligible yet.</p>
      {/if}
    </section>
  {/if}

  {#if currentData}
    <section class="filter-panel" aria-label="Meta portfolio filters">
      <div class="filter-controls">
        <SelectField id="meta-agent" label="Agent" options={agentOptions} bind:value={agentFilter} compact />
        <SelectField
          id="meta-prompt"
          label="Prompt"
          options={promptOptions}
          bind:value={promptFilter}
          compact
        />
        <ToggleSwitch label="Show archived" bind:checked={showArchived} />
      </div>
      <div class="filter-context">
        <span class="result-count num">{filteredMetaRows.length} shown</span>
        <div class="compare-status" role="status" aria-live="polite">
          {#if selected.length}
            <span>
              Comparing {selected.length} meta portfolio{selected.length === 1 ? "" : "s"} with Consensus Control
              and SPY.
            </span>
            <button class="btn small" type="button" onclick={clearComparison}>Clear</button>
          {:else}
            <span>Select a portfolio to compare it with Consensus Control and SPY.</span>
          {/if}
        </div>
      </div>
    </section>
  {/if}

  {#if selected.length}
    <section class="comparison-panel" aria-labelledby="meta-comparison-title" aria-busy={compareLoading}>
      <header>
        <div>
          <h2 id="meta-comparison-title">
            {compareData?.start ? `Rebased to 100 at ${compareData.start}` : "Meta portfolio comparison"}
          </h2>
          <p>Meta selections are compared with the same-cell consensus control and direction-matched SPY.</p>
        </div>
        {#if compareLoading && compareData}<span role="status">Updating…</span>{/if}
      </header>
      {#if compareLoading && !compareData}
        <div class="loading-block compact" aria-live="polite">
          <span class="spinner" aria-hidden="true"></span>
          Loading comparison…
        </div>
      {:else if comparisonError}
        <div class="error-box" role="alert">
          <span>{comparisonError}</span>
          <button class="btn small" type="button" onclick={loadComparison}>Retry</button>
        </div>
      {:else if compareData}
        <LineChart series={compareSeries} ariaLabel="Meta portfolio comparison chart" height={300} />
      {/if}
    </section>
  {:else if comparisonError}
    <div class="error-box" role="alert">{comparisonError}</div>
  {/if}

  {#if loading && !currentData}
    <div class="loading-block" aria-live="polite" aria-busy="true">
      <span class="spinner" aria-hidden="true"></span>
      Building {direction}
      {track} meta rankings…
    </div>
  {:else if track === "managed" && managedData}
    <ManagedArenaTable rows={managedRows} {selected} onToggle={toggleCompare} />
  {:else if track === "rebuilt" && rebuiltData}
    <RebuiltArenaTable
      rows={rebuiltRows}
      view={rebuiltView}
      context={rebuiltData.context}
      {selected}
      onToggle={toggleCompare}
    />
    {#if rebuiltView === "signal"}
      <SignalMatrix
        rows={signalRows}
        selectedHorizon={horizon}
        context={rebuiltData.context}
        benchmarkName={rebuiltBenchmarkName}
      />
    {/if}
  {/if}
</section>

<style>
  .meta-page {
    min-width: 0;
    display: grid;
    gap: 20px;
  }

  .page-head {
    margin: 0;
    padding: 18px 0 24px;
  }

  h1 {
    margin: 0;
    font-size: clamp(32px, 5vw, 58px);
    font-weight: 650;
    letter-spacing: -0.045em;
    line-height: 0.95;
  }

  .lede {
    max-width: 900px;
    margin-top: 14px;
    color: var(--text-secondary);
    font-size: 15px;
    line-height: 1.6;
  }

  .valuation-stamp {
    flex: 0 0 auto;
    display: grid;
    gap: 3px;
    text-align: right;
  }

  .valuation-stamp span,
  .selection-summary > span,
  .locked-context span {
    color: var(--text-tertiary);
    font-size: 9px;
    font-weight: 750;
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }

  .valuation-stamp strong {
    font-size: 12px;
    font-weight: 550;
  }

  .direction-selector,
  .track-selector {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .direction-selector button,
  .track-selector button {
    min-height: 72px;
    display: grid;
    align-content: center;
    gap: 3px;
    padding: 12px 16px;
    color: var(--text-secondary);
    background: var(--bg-raised);
    text-align: left;
  }

  .direction-selector button:hover,
  .direction-selector button:focus-visible,
  .track-selector button:hover,
  .track-selector button:focus-visible {
    background: var(--bg-surface-hover);
    color: var(--text-primary);
  }

  .direction-selector button.active,
  .track-selector button.active {
    color: var(--text-inverse);
    background: var(--accent);
  }

  .direction-selector button {
    min-height: 46px;
    justify-content: center;
    font-size: 12px;
    font-weight: 760;
    letter-spacing: 0.08em;
    text-align: center;
    text-transform: uppercase;
  }

  .track-selector strong {
    font-size: 15px;
  }

  .track-selector span {
    font-size: 10px;
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }

  .selector-description {
    margin-top: -12px;
    color: var(--text-secondary);
    font-size: 12px;
  }

  .batch-panel {
    display: grid;
    gap: 14px;
    padding: 16px;
    border: 1px solid var(--border-subtle);
    background: var(--bg-raised);
  }

  .batch-panel.failed {
    border-color: var(--neg);
  }

  .batch-panel .batch-error {
    color: var(--neg);
  }

  .batch-panel header,
  .filter-context,
  .compare-status,
  .comparison-panel header,
  .load-error {
    min-width: 0;
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .batch-panel header,
  .comparison-panel header,
  .load-error {
    justify-content: space-between;
  }

  .batch-panel header {
    align-items: start;
  }

  .batch-panel h2,
  .comparison-panel h2 {
    margin: 0;
    font-size: 16px;
  }

  .batch-panel p,
  .comparison-panel p {
    margin-top: 5px;
    color: var(--text-secondary);
    font-size: 11px;
  }

  .batch-counts {
    display: grid;
    grid-template-columns: repeat(6, minmax(0, 1fr));
    gap: 1px;
    background: var(--border-subtle);
  }

  .batch-counts div {
    min-width: 0;
    padding: 10px;
    display: grid;
    gap: 3px;
    background: var(--bg-base);
  }

  .batch-counts dt {
    color: var(--text-tertiary);
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }

  .batch-counts dd {
    overflow: hidden;
    color: var(--text-primary);
    font-size: 12px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .snapshot-line {
    margin: 0;
  }

  .analysis-controls,
  .filter-controls {
    display: grid;
    align-items: end;
    gap: 12px;
  }

  .analysis-controls {
    grid-template-columns: repeat(3, minmax(160px, 230px));
    padding: 14px 0;
  }

  .locked-context {
    min-height: 38px;
    display: grid;
    align-content: center;
    gap: 2px;
    padding: 5px 10px;
    background: var(--bg-raised);
  }

  .locked-context strong {
    font-size: 11px;
  }

  .selection-summary {
    display: grid;
    grid-template-columns: auto auto minmax(0, 1fr);
    align-items: center;
    gap: 10px 18px;
    padding: 14px;
    border: 1px solid var(--accent);
    background: var(--accent-bg);
  }

  .selection-summary strong {
    color: var(--accent-strong);
    font-size: 14px;
  }

  .selection-summary p {
    color: var(--text-secondary);
    font-size: 11px;
  }

  .filter-panel {
    display: grid;
    gap: 12px;
    padding: 14px 0;
  }

  .filter-controls {
    grid-template-columns: repeat(2, minmax(160px, 220px)) auto;
  }

  .filter-context {
    justify-content: space-between;
    color: var(--text-tertiary);
    font-size: 11px;
  }

  .filter-context > *,
  .batch-panel header > *,
  .comparison-panel header > *,
  .load-error > * {
    min-width: 0;
  }

  .comparison-panel {
    min-width: 0;
    display: grid;
    gap: 14px;
    padding: 16px;
    background: var(--bg-raised);
  }

  .comparison-panel header {
    align-items: start;
  }

  @media (max-width: 900px) {
    .batch-counts {
      grid-template-columns: repeat(3, minmax(0, 1fr));
    }
  }

  @media (max-width: 760px) {
    .page-head {
      align-items: start;
    }

    .valuation-stamp {
      display: none;
    }

    .analysis-controls,
    .filter-controls {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .filter-controls :global(.toggle-row) {
      grid-column: 1 / -1;
    }

    .selection-summary {
      grid-template-columns: 1fr;
      gap: 5px;
    }
  }

  @media (max-width: 520px) {
    .track-selector,
    .batch-counts {
      grid-template-columns: 1fr;
    }

    .track-selector button {
      min-height: 60px;
    }

    .analysis-controls,
    .filter-controls {
      grid-template-columns: 1fr;
    }

    .filter-context,
    .batch-panel header {
      align-items: start;
      flex-direction: column;
    }
  }
</style>
