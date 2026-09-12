<script lang="ts">
  import { onMount } from "svelte";

  import { router } from "../stores/router.svelte";
  import { getPublicQuery, PUBLIC_REFRESH_MS } from "../api/publicCache";
  import { arenaQueryUrl, ArenaPrefetchQueue } from "../api/arenaPrefetch";
  import type {
    ArenaTrack,
    ArenaVersion,
    VersionsResponse,
    CompareResponse,
    Direction,
    HorizonObjective,
    ManagedArenaPortfolio,
    ManagedArenaResponse,
    RebuiltArenaPortfolio,
    RebuiltArenaResponse,
  } from "../api/types";
  import {
    HORIZON_OBJECTIVES,
    horizonObjectiveLabel,
    parseDirection,
    parseHorizonObjective,
    selectedVersion,
  } from "../arena";
  import LineChart, { type ChartSeries } from "../components/LineChart.svelte";
  import ManagedArenaTable from "../components/ManagedArenaTable.svelte";
  import MarketDataWarning from "../components/MarketDataWarning.svelte";
  import RebuiltArenaTable from "../components/RebuiltArenaTable.svelte";
  import SignalMatrix from "../components/SignalMatrix.svelte";
  import SelectField from "../components/ui/SelectField.svelte";
  import { fmtDate } from "../format";
  import { combineMarketData } from "../marketData";

  type RealPortfolio = ManagedArenaPortfolio | RebuiltArenaPortfolio;

  const DIRECTIONS: { value: Direction; label: string; description: string }[] = [
    {
      value: "long",
      label: "Long",
      description: "Strategies select assets expected to outperform while capital remains fully invested.",
    },
    {
      value: "short",
      label: "Short",
      description: "Strategies select assets expected to underperform through fully invested short books.",
    },
  ];
  const TRACKS: { value: ArenaTrack; label: string; description: string }[] = [
    {
      value: "rebuilt",
      label: "Rebuilt",
      description:
        "Independent daily signals tested across H0.5–H20 holding periods at full target exposure.",
    },
    {
      value: "managed",
      label: "Managed",
      description: "Stateful portfolios whose agents receive their prior portfolio context.",
    },
  ];
  let direction = $state<Direction>(
    parseDirection(new URLSearchParams(window.location.search).get("direction")),
  );
  let track = $state<ArenaTrack>(
    new URLSearchParams(window.location.search).get("track") === "managed" ? "managed" : "rebuilt",
  );
  const versionsQuery = getPublicQuery<VersionsResponse>("/api/versions");
  const versions = $derived<ArenaVersion[]>(versionsQuery.data?.versions ?? []);
  let objective = $state<HorizonObjective>(
    parseHorizonObjective(new URLSearchParams(window.location.search).get("objective")),
  );
  function initialVersionId(): number | null {
    return (
      selectedVersion(
        versionsQuery.data?.versions ?? [],
        new URLSearchParams(window.location.search).get("version"),
      )?.id ?? null
    );
  }
  let versionId = $state<number | null>(initialVersionId());
  const version = $derived(versions.find((item) => item.id === versionId));
  let agentFilter = $state("all");
  let promptFilter = $state("all");
  const arenaQuery = $derived(
    versionId === null
      ? null
      : getPublicQuery<ManagedArenaResponse | RebuiltArenaResponse>(
          arenaQueryUrl({ versionId, track, direction, objective }),
        ),
  );
  const currentData = $derived(arenaQuery?.data ?? null);
  const managedData = $derived(currentData?.track === "managed" ? currentData : null);
  const rebuiltData = $derived(currentData?.track === "rebuilt" ? currentData : null);
  const loading = $derived(versionsQuery.loading || (arenaQuery?.loading ?? false));
  const error = $derived(arenaQuery?.error || versionsQuery.error);
  let selected = $state<string[]>([]);
  let comparisonSelectionError = $state("");
  const compareQuery = $derived.by(() => {
    if (selected.length < 2 || versionId === null) return null;
    const query = new URLSearchParams({
      slugs: selected.join(","),
      track,
      direction,
      version_id: String(versionId),
    });
    if (track === "rebuilt") query.set("objective", objective);
    return getPublicQuery<CompareResponse>(`/api/compare?${query.toString()}`);
  });
  const compareData = $derived(compareQuery?.data ?? null);
  const compareLoading = $derived(compareQuery?.loading ?? false);
  const comparisonError = $derived(comparisonSelectionError || compareQuery?.error || "");
  const allRealRows = $derived.by((): RealPortfolio[] => {
    if (!currentData) return [];
    return currentData.portfolios.filter((row): row is RealPortfolio => row.kind !== "benchmark");
  });
  const agents = $derived.by(() => {
    const entries: { value: string; label: string }[] = [];
    for (const row of allRealRows) {
      if (!entries.some((entry) => entry.value === row.agent.slug)) {
        entries.push({ value: row.agent.slug, label: row.agent.name });
      }
    }
    return entries;
  });
  const prompts = $derived.by(() => {
    const entries: { value: string; label: string }[] = [];
    for (const row of allRealRows) {
      if (!entries.some((entry) => entry.value === row.prompt.slug)) {
        entries.push({ value: row.prompt.slug, label: row.prompt.name });
      }
    }
    return entries;
  });
  const agentOptions = $derived([{ value: "all", label: "All agents" }, ...agents]);
  const promptOptions = $derived([{ value: "all", label: "All prompts" }, ...prompts]);
  const rebuiltBenchmarkName = $derived(
    rebuiltData?.portfolios.find((row) => row.kind === "benchmark")?.name ??
      (direction === "short" ? "Short SPY" : "SPY"),
  );
  const filteredRealRows = $derived(
    allRealRows.filter(
      (row) =>
        (agentFilter === "all" || row.agent.slug === agentFilter) &&
        (promptFilter === "all" || row.prompt.slug === promptFilter),
    ),
  );
  const managedRows = $derived.by((): ManagedArenaResponse["portfolios"] => {
    if (!managedData) return [];
    const benchmark = managedData.portfolios.filter((row) => row.kind === "benchmark");
    return [...benchmark, ...filteredRealRows.filter((row) => row.kind === "managed")];
  });
  const rebuiltRows = $derived.by((): RebuiltArenaResponse["portfolios"] => {
    if (!rebuiltData) return [];
    const benchmark = rebuiltData.portfolios.filter((row) => row.kind === "benchmark");
    return [...benchmark, ...filteredRealRows.filter((row) => row.kind === "rebuilt")];
  });
  const signalRows = $derived(
    filteredRealRows.filter((row): row is RebuiltArenaPortfolio => row.kind === "rebuilt"),
  );
  const compareSeries = $derived.by((): ChartSeries[] => {
    if (!compareData) return [];
    const portfolioSeries: ChartSeries[] = compareData.series.map((entry) => ({
      name: entry.name,
      points: entry.series,
      dashed: entry.kind === "benchmark",
    }));
    if (compareData.spy_series.length) {
      portfolioSeries.push({
        name: compareData.direction === "short" ? "Short SPY" : "SPY",
        points: compareData.spy_series,
        dashed: true,
        color: "var(--spark)",
      });
    }
    return portfolioSeries;
  });
  const displayedMarketData = $derived(combineMarketData(currentData, compareData));
  const activeDirectionDescription = $derived(
    DIRECTIONS.find((item) => item.value === direction)?.description ?? "",
  );
  const activeTrackDescription = $derived(TRACKS.find((item) => item.value === track)?.description ?? "");

  let mounted = false;
  const prefetch = new ArenaPrefetchQueue(
    (url) => getPublicQuery(url).load(),
    () => mounted && document.visibilityState === "visible" && !arenaQuery?.loading,
  );
  onMount(() => {
    mounted = true;
    writeDirectionUrl(direction);
    void initialize();
    const refreshTimer = window.setInterval(() => {
      if (document.visibilityState === "visible") void refreshVisible(true);
    }, PUBLIC_REFRESH_MS);
    return () => {
      mounted = false;
      prefetch.cancel();
      window.clearInterval(refreshTimer);
    };
  });

  function writeDirectionUrl(next: Direction): void {
    const url = new URL(window.location.href);
    url.searchParams.set("direction", next);
    url.searchParams.set("track", track);
    if (track === "rebuilt") url.searchParams.set("objective", objective);
    else url.searchParams.delete("objective");
    if (versionId !== null) url.searchParams.set("version", String(versionId));
    window.history.replaceState(window.history.state, "", url);
    router.syncVersion();
  }

  async function loadArena(force = false): Promise<void> {
    prefetch.cancel();
    const query = arenaQuery;
    if (!query || versionId === null) return;
    const view = { versionId, track, direction, objective };
    await query.load(force);
    if (mounted && query === arenaQuery && query.data && !query.error) prefetch.schedule(view);
  }

  function loadComparison(force = false): Promise<void> {
    return compareQuery?.load(force) ?? Promise.resolve();
  }

  async function refreshVisible(force = false): Promise<void> {
    await Promise.all([loadArena(force), loadComparison(force)]);
  }

  function resetFilters(): void {
    agentFilter = "all";
    promptFilter = "all";
  }

  function changeObjective(value: string): void {
    const next = parseHorizonObjective(value);
    if (next === objective) return;
    objective = next;
    writeDirectionUrl(direction);
    void loadArena();
    void loadComparison();
  }

  function changeDirection(next: Direction): void {
    if (direction === next) return;
    direction = next;
    writeDirectionUrl(next);
    resetFilters();
    clearComparison();
    void loadArena();
  }

  function changeTrack(next: ArenaTrack): void {
    if (track === next) return;
    track = next;
    writeDirectionUrl(direction);
    resetFilters();
    clearComparison();
    void loadArena();
  }

  function toggleCompare(slug: string): void {
    if (!selected.includes(slug) && selected.length >= 8) {
      comparisonSelectionError = "Compare up to eight portfolios at a time.";
      return;
    }
    comparisonSelectionError = "";
    selected = selected.includes(slug)
      ? selected.filter((candidate) => candidate !== slug)
      : [...selected, slug];
    void loadComparison();
  }

  function clearComparison(): void {
    selected = [];
    comparisonSelectionError = "";
  }

  async function initialize(): Promise<void> {
    // A returning visitor can render and refresh a cached version immediately.
    const initialArena = loadArena();
    await versionsQuery.load();
    if (!mounted) return;
    versionId =
      selectedVersion(
        versions,
        versionId === null ? new URLSearchParams(window.location.search).get("version") : String(versionId),
      )?.id ?? null;
    writeDirectionUrl(direction);
    await Promise.all([initialArena, loadArena()]);
  }
  function changeVersion(value: string): void {
    versionId = Number(value);
    resetFilters();
    clearComparison();
    writeDirectionUrl(direction);
    void loadArena();
  }
</script>

<svelte:head>
  <title>Portfolio Arena</title>
</svelte:head>

<svelte:window onfocus={() => void refreshVisible()} />
<svelte:document
  onvisibilitychange={() => {
    if (document.visibilityState === "visible") void refreshVisible();
    else prefetch.cancel();
  }}
/>

<section class="leaderboard-page" aria-labelledby="arena-title">
  <header class="page-head">
    <div>
      <h1 id="arena-title">Portfolio Arena</h1>
      <p class="lede">
        Which AI investment strategy produces repeatable alpha? Compare long and short independent signals and
        stateful portfolios against their SPY reference on evidence, not a single lucky return.
      </p>
    </div>
    <div class="valuation-stamp">
      <span>Valuation</span>
      <strong class="num">
        {displayedMarketData.asOf ? fmtDate(displayedMarketData.asOf) : "Pending"}
      </strong>
      {#if loading && currentData}<span role="status">Updating…</span>{/if}
    </div>
  </header>

  {#if versionsQuery.data && !loading && !versions.length && !error}<div class="empty-state card">
      <p>No Arena versions have been created yet.</p>
    </div>{/if}
  <div class="filter-controls">
    <SelectField
      id="arena-version"
      label="Arena version"
      options={versions.map((item) => ({ value: String(item.id), label: item.name }))}
      value={versionId === null ? "" : String(versionId)}
      onValueChange={changeVersion}
    />
    {#if version}<span class="badge"
        >{version.evaluation_enabled ? "Evaluation enabled" : "Evaluation paused"}</span
      >{/if}
  </div>
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
  <p class="direction-description">{activeDirectionDescription}</p>

  <nav class="track-selector" aria-label="Arena track">
    {#each TRACKS as item (item.value)}
      <button
        type="button"
        class={{ active: track === item.value }}
        aria-current={track === item.value ? "page" : undefined}
        onclick={() => changeTrack(item.value)}
      >
        <strong>{item.label}</strong>
        <span>{item.value === "rebuilt" ? "Daily signal cohorts" : "Stateful portfolios"}</span>
      </button>
    {/each}
  </nav>
  <p class="track-description">{activeTrackDescription}</p>

  {#if track === "rebuilt"}
    <section class="horizon-control" aria-label="Horizon optimization">
      <SelectField
        id="arena-objective"
        label="Optimize horizon by"
        options={HORIZON_OBJECTIVES}
        value={objective}
        onValueChange={changeObjective}
      />
      <p class="muted">
        Selects each portfolio’s H and updates its metrics and charts. Column sorting only changes row order.
      </p>
      {#if loading && rebuiltData}
        <p role="status">
          Updating horizons… Showing {horizonObjectiveLabel(rebuiltData.objective)} results until ready.
        </p>
      {:else if rebuiltData && rebuiltData.objective !== objective}
        <p role="status">
          Showing {horizonObjectiveLabel(rebuiltData.objective)} results. Retry to apply the new selection.
        </p>
      {/if}
    </section>
  {/if}

  {#if currentData}
    {#key `${displayedMarketData.status}:${displayedMarketData.asOf}`}
      <MarketDataWarning
        versionId={versionId!}
        status={displayedMarketData.status}
        asOf={displayedMarketData.asOf}
        onReady={loadArena}
      />
    {/key}
  {/if}

  {#if error}
    <div class="error-box load-error" role="alert">
      <span>{currentData ? `Could not refresh; showing saved results. ${error}` : error}</span>
      <button class="btn small" type="button" onclick={() => void initialize()}>Retry</button>
    </div>
  {/if}

  {#if currentData}
    <section class="filter-panel" aria-label="Portfolio filters">
      <div class="filter-controls">
        <SelectField id="arena-agent" label="Agent" options={agentOptions} bind:value={agentFilter} compact />
        <SelectField
          id="arena-prompt"
          label="Prompt"
          options={promptOptions}
          bind:value={promptFilter}
          compact
        />
      </div>
      <div class="filter-context">
        <span class="result-count num">{filteredRealRows.length} shown</span>
        <div class="compare-status" role="status" aria-live="polite">
          {#if selected.length === 1}
            <span>Select one more portfolio to compare.</span>
          {:else if selected.length >= 2}
            <span>Comparing {selected.length} portfolios.</span>
            <button class="btn small" type="button" onclick={clearComparison}>Clear</button>
          {:else}
            <span>Select portfolios to compare their SPY-relative paths.</span>
          {/if}
        </div>
      </div>
    </section>
  {/if}

  {#if selected.length >= 2}
    <section class="comparison-panel" aria-labelledby="comparison-title" aria-busy={compareLoading}>
      <header>
        <div>
          <h2 id="comparison-title">
            {compareData?.start ? `Rebased to 100 at ${fmtDate(compareData.start)}` : "Portfolio comparison"}
          </h2>
          <p>Uses the same arena context and latest common inception for every selected line.</p>
        </div>
        {#if compareLoading && compareData}<span role="status">Updating…</span>{/if}
      </header>
      {#if compareLoading && !compareData}
        <div class="loading-block compact" aria-live="polite">
          <span class="spinner" aria-hidden="true"></span>
          Loading comparison…
        </div>
      {/if}
      {#if comparisonError}
        <div class="error-box" role="alert">
          <span>{comparisonError}</span>
          <button class="btn small" type="button" onclick={() => void loadComparison(true)}>Retry</button>
        </div>
      {/if}
      {#if compareData}
        <LineChart series={compareSeries} ariaLabel="Portfolio comparison chart" height={300} />
      {/if}
    </section>
  {:else if comparisonError}
    <div class="error-box" role="alert">{comparisonError}</div>
  {/if}

  {#if (loading || !versionsQuery.data) && !currentData && !error}
    <div class="loading-block" aria-live="polite" aria-busy="true">
      <span class="spinner" aria-hidden="true"></span>
      Loading {direction}
      {track} rankings…
    </div>
  {:else if track === "managed" && managedData}
    <ManagedArenaTable rows={managedRows} {selected} onToggle={toggleCompare} />
  {:else if track === "rebuilt" && rebuiltData}
    <RebuiltArenaTable rows={rebuiltRows} {selected} onToggle={toggleCompare} />
    <SignalMatrix rows={signalRows} benchmarkName={rebuiltBenchmarkName} />
  {/if}
</section>

<style>
  .horizon-control {
    display: grid;
    gap: 8px;
  }

  .horizon-control :global(.select-field) {
    max-width: 320px;
  }

  .leaderboard-page {
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
    max-width: 860px;
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

  .valuation-stamp span {
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

  .direction-description,
  .track-description {
    margin-top: -12px;
    color: var(--text-secondary);
    font-size: 12px;
  }

  .filter-controls {
    display: grid;
    align-items: end;
    gap: 12px;
  }

  .filter-panel {
    display: grid;
    gap: 12px;
    padding: 14px 0;
  }

  .filter-controls {
    grid-template-columns: repeat(2, minmax(160px, 220px)) auto;
  }

  .filter-context,
  .compare-status {
    min-width: 0;
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .filter-context > *,
  .comparison-panel header > *,
  .load-error > * {
    min-width: 0;
  }

  .filter-context {
    justify-content: space-between;
    color: var(--text-tertiary);
    font-size: 11px;
  }

  .comparison-panel {
    min-width: 0;
    display: grid;
    gap: 14px;
    padding: 16px;
    background: var(--bg-raised);
  }

  .comparison-panel header {
    display: flex;
    align-items: start;
    justify-content: space-between;
    gap: 16px;
  }

  .comparison-panel h2 {
    margin: 0;
    font-size: 16px;
  }

  .comparison-panel p {
    margin-top: 5px;
    color: var(--text-secondary);
    font-size: 11px;
  }

  .load-error {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
  }

  @media (max-width: 760px) {
    .page-head {
      align-items: start;
    }

    .valuation-stamp {
      display: none;
    }

    .filter-controls {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .filter-controls :global(.toggle-row) {
      grid-column: 1 / -1;
    }
  }

  @media (max-width: 520px) {
    .track-selector {
      grid-template-columns: 1fr;
    }

    .track-selector button {
      min-height: 60px;
    }

    .filter-controls {
      grid-template-columns: 1fr;
    }

    .filter-context {
      align-items: start;
      flex-direction: column;
    }
  }
</style>
