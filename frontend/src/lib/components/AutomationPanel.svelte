<script lang="ts">
  import { apiJson } from "../api/client";
  import type {
    EvaluationRun,
    EvaluationRunsResponse,
    EvaluationRunStatus,
    PortfolioSummary,
  } from "../api/types";
  import { fmtDateTime } from "../format";

  interface Props {
    portfolios: PortfolioSummary[];
  }

  const { portfolios }: Props = $props();

  let runs = $state.raw<EvaluationRun[]>([]);
  let portfolioFilter = $state("");
  let statusFilter = $state<"" | EvaluationRunStatus>("");
  let nextCursor = $state<string | null>(null);
  let loading = $state(false);
  let error = $state("");

  function query(cursor?: string): string {
    const params = new URLSearchParams({ limit: "25" });
    if (portfolioFilter) params.set("portfolio_id", portfolioFilter);
    if (statusFilter) params.set("status", statusFilter);
    if (cursor) params.set("cursor", cursor);
    return `/api/evaluation-runs?${params.toString()}`;
  }

  async function loadRuns(reset: boolean) {
    loading = true;
    error = "";
    try {
      const cursor = reset ? undefined : (nextCursor ?? undefined);
      const payload = await apiJson<EvaluationRunsResponse>(query(cursor));
      runs = reset ? payload.items : [...runs, ...payload.items];
      nextCursor = payload.next_cursor;
    } catch (e) {
      error = e instanceof Error ? e.message : "Could not load evaluation history.";
    } finally {
      loading = false;
    }
  }

  function statusClass(status: EvaluationRunStatus): string {
    if (status === "succeeded") return "success";
    if (status === "failed") return "neg";
    if (status === "running") return "warn";
    return "";
  }

  void loadRuns(true);
</script>

<section class="card">
  <div class="panel-head">
    <div>
      <h2>Automated evaluation history</h2>
      <p class="muted">
        Runs are scheduled 90 minutes before each NYSE close. Submission closes 10 minutes before the close.
      </p>
    </div>
    <button class="btn small" type="button" onclick={() => loadRuns(true)} disabled={loading}>Refresh</button>
  </div>

  <div class="filters" aria-label="Evaluation run filters">
    <div class="field">
      <label for="run-portfolio">Portfolio</label>
      <select id="run-portfolio" bind:value={portfolioFilter} onchange={() => loadRuns(true)}>
        <option value="">All portfolios</option>
        {#each portfolios as portfolio (portfolio.id)}
          <option value={String(portfolio.id)}>{portfolio.name}</option>
        {/each}
      </select>
    </div>
    <div class="field">
      <label for="run-status">Status</label>
      <select id="run-status" bind:value={statusFilter} onchange={() => loadRuns(true)}>
        <option value="">All statuses</option>
        <option value="running">Running</option>
        <option value="succeeded">Succeeded</option>
        <option value="failed">Failed</option>
        <option value="skipped">Skipped</option>
      </select>
    </div>
  </div>

  {#if error}
    <div class="error-box" role="alert">{error}</div>
  {/if}

  <div class="table-scroll">
    <table>
      <caption>Automated portfolio evaluation runs, newest first</caption>
      <thead>
        <tr>
          <th>Session</th>
          <th>Portfolio</th>
          <th>Agent / model</th>
          <th>Status</th>
          <th class="right">Attempts</th>
          <th>Finished</th>
          <th>Result</th>
        </tr>
      </thead>
      <tbody aria-live="polite">
        {#each runs as run (run.id)}
          <tr>
            <td class="num">{run.scheduled_for}</td>
            <td>
              <strong>{run.portfolio.name}</strong>
              <div class="muted num">{run.portfolio.slug}</div>
            </td>
            <td>
              {run.agent.name}
              <div class="muted num">{run.model} · {run.codex_version}</div>
            </td>
            <td><span class={`badge ${statusClass(run.status)}`}>{run.status}</span></td>
            <td class="right num">{run.attempt_count}/2</td>
            <td class="num">{fmtDateTime(run.finished_at)}</td>
            <td>
              {#if run.report || run.error}
                <details>
                  <summary>{run.error ? "Error" : "Report"}</summary>
                  <pre class:error-report={Boolean(run.error)}>{run.error ?? run.report}</pre>
                </details>
              {:else if run.allocation_id}
                <span class="muted">Allocation #{run.allocation_id}</span>
              {:else}
                <span class="muted">—</span>
              {/if}
            </td>
          </tr>
        {:else}
          {#if !loading}
            <tr><td colspan="7" class="muted empty">No evaluation runs match these filters.</td></tr>
          {/if}
        {/each}
        {#if loading && runs.length === 0}
          <tr><td colspan="7" class="muted empty">Loading evaluation runs…</td></tr>
        {/if}
      </tbody>
    </table>
  </div>

  {#if nextCursor}
    <div class="more">
      <button class="btn" type="button" onclick={() => loadRuns(false)} disabled={loading}>
        {loading ? "Loading…" : "Load more"}
      </button>
    </div>
  {/if}
</section>

<style>
  .panel-head {
    display: flex;
    justify-content: space-between;
    align-items: start;
    gap: 16px;
    margin-bottom: 18px;
  }

  h2 {
    font-size: 15px;
    margin-bottom: 4px;
  }

  .filters {
    display: grid;
    grid-template-columns: repeat(2, minmax(180px, 280px));
    gap: 12px;
  }

  .field {
    margin-bottom: 14px;
  }

  caption {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
  }

  td {
    vertical-align: top;
  }

  .badge.success {
    color: var(--pos);
    border-color: var(--pos);
  }

  summary {
    color: var(--accent);
    cursor: pointer;
  }

  pre {
    max-width: 520px;
    max-height: 240px;
    overflow: auto;
    white-space: pre-wrap;
    margin-top: 8px;
    padding: 10px;
    background: var(--bg-inset);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-sm);
    font-size: 12px;
    font-family: var(--font-mono);
  }

  pre.error-report {
    color: var(--neg);
  }

  .empty {
    text-align: center;
    padding: 24px;
  }

  .more {
    display: flex;
    justify-content: center;
    margin-top: 14px;
  }

  @media (max-width: 640px) {
    .filters {
      grid-template-columns: 1fr;
    }
  }
</style>
