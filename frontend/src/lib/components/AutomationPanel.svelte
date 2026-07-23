<script lang="ts">
  import { apiJson, postJson, putJson } from "../api/client";
  import type {
    EvaluationQueueResponse,
    EvaluationRun,
    EvaluationRunsResponse,
    EvaluationRunStatus,
    EvaluatorDashboard,
    EvaluatorSettings,
    PortfolioEvaluatorConfig,
  } from "../api/types";
  import { fmtDateTime } from "../format";

  const WEEKDAYS = [
    { value: 0, label: "Mon" },
    { value: 1, label: "Tue" },
    { value: 2, label: "Wed" },
    { value: 3, label: "Thu" },
    { value: 4, label: "Fri" },
  ];

  interface ConfigDraft {
    enabled: boolean;
    weekdays: number[];
  }

  let dashboard = $state.raw<EvaluatorDashboard | null>(null);
  let settingsDraft = $state<EvaluatorSettings | null>(null);
  let configDrafts = $state<Record<number, ConfigDraft>>({});
  let runs = $state.raw<EvaluationRun[]>([]);
  let portfolioFilter = $state("");
  let statusFilter = $state<"" | EvaluationRunStatus>("");
  let nextCursor = $state<string | null>(null);
  let loading = $state(false);
  let savingSettings = $state(false);
  let busyAction = $state("");
  let error = $state("");
  let notice = $state("");

  const enabledPortfolioIds = $derived(
    dashboard?.portfolios
      .filter((config) => config.enabled && config.portfolio.status === "active")
      .map((config) => config.portfolio.id) ?? [],
  );

  function settingsBody(settings: EvaluatorSettings) {
    return {
      enabled: settings.enabled,
      max_concurrency: settings.max_concurrency,
      poll_seconds: settings.poll_seconds,
      attempt_timeout_seconds: settings.attempt_timeout_seconds,
      max_attempts: settings.max_attempts,
      start_before_close_minutes: settings.start_before_close_minutes,
      cutoff_before_close_minutes: settings.cutoff_before_close_minutes,
    };
  }

  function setDashboard(payload: EvaluatorDashboard) {
    dashboard = payload;
    settingsDraft = { ...payload.settings };
    configDrafts = Object.fromEntries(
      payload.portfolios.map((config) => [
        config.portfolio.id,
        {
          enabled: config.enabled,
          weekdays: [...config.weekdays],
        },
      ]),
    );
  }

  async function loadDashboard() {
    const payload = await apiJson<EvaluatorDashboard>("/api/evaluator");
    setDashboard(payload);
  }

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

  async function refresh() {
    loading = true;
    error = "";
    try {
      await Promise.all([loadDashboard(), loadRuns(true)]);
    } catch (e) {
      error = e instanceof Error ? e.message : "Could not load evaluator controls.";
    } finally {
      loading = false;
    }
  }

  async function persistSettings(): Promise<boolean> {
    if (!settingsDraft) return false;
    savingSettings = true;
    error = "";
    try {
      const saved = await putJson<EvaluatorSettings>(
        "/api/evaluator/settings",
        settingsBody($state.snapshot(settingsDraft)),
      );
      settingsDraft = { ...saved };
      if (dashboard) dashboard = { ...dashboard, settings: saved };
      notice = "Evaluator settings saved.";
      return true;
    } catch (e) {
      error = e instanceof Error ? e.message : "Could not save evaluator settings.";
      return false;
    } finally {
      savingSettings = false;
    }
  }

  async function saveSettings(event: SubmitEvent) {
    event.preventDefault();
    await persistSettings();
  }

  async function togglePaused() {
    if (!settingsDraft) return;
    const previous = settingsDraft.enabled;
    settingsDraft.enabled = !settingsDraft.enabled;
    if (!(await persistSettings())) settingsDraft.enabled = previous;
  }

  function toggleWeekday(portfolioId: number, weekday: number) {
    const draft = configDrafts[portfolioId];
    if (!draft) return;
    draft.weekdays = draft.weekdays.includes(weekday)
      ? draft.weekdays.filter((day) => day !== weekday)
      : [...draft.weekdays, weekday].sort();
  }

  async function savePortfolio(config: PortfolioEvaluatorConfig) {
    const draft = configDrafts[config.portfolio.id];
    if (!draft) return;
    busyAction = `save-${config.portfolio.id}`;
    error = "";
    try {
      const saved = await putJson<PortfolioEvaluatorConfig>(
        `/api/evaluator/portfolios/${config.portfolio.id}`,
        $state.snapshot(draft),
      );
      if (dashboard) {
        dashboard = {
          ...dashboard,
          portfolios: dashboard.portfolios.map((item) =>
            item.portfolio.id === saved.portfolio.id ? saved : item,
          ),
        };
      }
      configDrafts[config.portfolio.id] = {
        enabled: saved.enabled,
        weekdays: [...saved.weekdays],
      };
      notice = `${config.portfolio.name} evaluator settings saved.`;
      await loadRuns(true);
    } catch (e) {
      error = e instanceof Error ? e.message : "Could not save portfolio evaluator settings.";
    } finally {
      busyAction = "";
    }
  }

  async function queueRuns(portfolioIds: number[], label: string) {
    if (!portfolioIds.length) return;
    busyAction = label;
    error = "";
    try {
      const response = await postJson<EvaluationQueueResponse>("/api/evaluator/runs", {
        portfolio_ids: portfolioIds,
      });
      const queued = response.items.filter((item) => item.action === "queued").length;
      const existing = response.items.filter((item) => item.action === "existing").length;
      const rejected = response.items.filter((item) => item.action === "rejected").length;
      notice = `${queued} queued${existing ? `, ${existing} already active` : ""}${rejected ? `, ${rejected} rejected` : ""}.`;
      await Promise.all([loadDashboard(), loadRuns(true)]);
    } catch (e) {
      error = e instanceof Error ? e.message : "Could not queue evaluations.";
    } finally {
      busyAction = "";
    }
  }

  async function cancelRun(run: EvaluationRun) {
    if (!confirm(`Cancel evaluation #${run.id} for ${run.portfolio.name}?`)) return;
    busyAction = `cancel-${run.id}`;
    error = "";
    try {
      await apiJson(`/api/evaluator/runs/${run.id}/cancel`, { method: "POST" });
      notice = `Cancellation requested for run #${run.id}.`;
      await Promise.all([loadDashboard(), loadRuns(true)]);
    } catch (e) {
      error = e instanceof Error ? e.message : "Could not cancel evaluation.";
    } finally {
      busyAction = "";
    }
  }

  async function retryRun(run: EvaluationRun) {
    busyAction = `retry-${run.id}`;
    error = "";
    try {
      await apiJson(`/api/evaluator/runs/${run.id}/retry`, { method: "POST" });
      notice = `Retry queued for run #${run.id}.`;
      await Promise.all([loadDashboard(), loadRuns(true)]);
    } catch (e) {
      error = e instanceof Error ? e.message : "Could not retry evaluation.";
    } finally {
      busyAction = "";
    }
  }

  function statusClass(status: EvaluationRunStatus): string {
    if (status === "succeeded") return "success";
    if (status === "failed" || status === "cancelled") return "neg";
    if (status === "running" || status === "cancel_requested") return "warn";
    if (status === "queued") return "accent";
    return "";
  }

  function cadenceLabel(config: PortfolioEvaluatorConfig): string {
    const weekdays = configDrafts[config.portfolio.id]?.weekdays ?? [];
    if (weekdays.length === 0) return "Manual only";
    if (weekdays.length === 5) return "Every trading day";
    return weekdays.map((day) => WEEKDAYS.find((item) => item.value === day)?.label).join(", ");
  }

  void refresh();
</script>

<div class="automation-stack">
  <section class="card">
    <div class="panel-head">
      <div>
        <h2>Evaluator runtime</h2>
        <p class="muted">The website, scheduler, and Codex worker are deployed as one application.</p>
      </div>
      <button class="btn small" type="button" onclick={refresh} disabled={loading}>Refresh</button>
    </div>

    {#if dashboard}
      <div class="runtime-grid">
        <div>
          <span class="runtime-label">Worker</span>
          <span class={`badge ${dashboard.runtime.online ? "success" : "neg"}`}>
            {dashboard.runtime.online ? dashboard.runtime.status : "offline"}
          </span>
        </div>
        <div>
          <span class="runtime-label">Codex login</span>
          <span class={`badge ${dashboard.runtime.authenticated ? "success" : "warn"}`}>
            {dashboard.runtime.authenticated ? "ready" : "required"}
          </span>
        </div>
        <div>
          <span class="runtime-label">Version</span>
          <span class="num">{dashboard.runtime.harness_version ?? "—"}</span>
        </div>
        <div>
          <span class="runtime-label">Active</span>
          <span class="num">{dashboard.runtime.active_run_count}</span>
        </div>
        <div>
          <span class="runtime-label">Heartbeat</span>
          <span class="num">{fmtDateTime(dashboard.runtime.last_heartbeat_at)}</span>
        </div>
      </div>
      {#if dashboard.runtime.last_error}
        <div class="error-box runtime-error" role="alert">{dashboard.runtime.last_error}</div>
      {/if}
      <div class="runtime-actions">
        <button
          class={`btn ${settingsDraft?.enabled ? "danger" : "primary"}`}
          type="button"
          onclick={togglePaused}
          disabled={savingSettings}
        >
          {settingsDraft?.enabled ? "Pause evaluator" : "Resume evaluator"}
        </button>
        <button
          class="btn primary"
          type="button"
          onclick={() => queueRuns(enabledPortfolioIds, "run-all")}
          disabled={!settingsDraft?.enabled || enabledPortfolioIds.length === 0 || busyAction === "run-all"}
        >
          {busyAction === "run-all" ? "Queueing…" : "Run all now"}
        </button>
      </div>
    {:else if loading}
      <div class="loading-block"><span class="spinner" aria-hidden="true"></span></div>
    {/if}
  </section>

  {#if settingsDraft}
    <section class="card">
      <div class="panel-head">
        <div>
          <h2>Global evaluator settings</h2>
          <p class="muted">Changes apply to newly queued runs; active runs keep their captured settings.</p>
        </div>
      </div>
      <form onsubmit={saveSettings}>
        <div class="settings-grid">
          <div class="field">
            <label for="eval-concurrency">Concurrency</label>
            <input
              id="eval-concurrency"
              type="number"
              min="1"
              max="20"
              bind:value={settingsDraft.max_concurrency}
            />
          </div>
          <div class="field">
            <label for="eval-poll">Poll seconds</label>
            <input id="eval-poll" type="number" min="10" max="300" bind:value={settingsDraft.poll_seconds} />
          </div>
          <div class="field">
            <label for="eval-timeout">Attempt timeout</label>
            <input
              id="eval-timeout"
              type="number"
              min="60"
              max="1500"
              bind:value={settingsDraft.attempt_timeout_seconds}
            />
          </div>
          <div class="field">
            <label for="eval-attempts">Automatic attempts</label>
            <input id="eval-attempts" type="number" min="1" max="5" bind:value={settingsDraft.max_attempts} />
          </div>
          <div class="field">
            <label for="eval-opens">Start before close (min)</label>
            <input
              id="eval-opens"
              type="number"
              min="15"
              max="240"
              bind:value={settingsDraft.start_before_close_minutes}
            />
          </div>
          <div class="field">
            <label for="eval-cutoff">Cutoff before close (min)</label>
            <input
              id="eval-cutoff"
              type="number"
              min="0"
              max="60"
              bind:value={settingsDraft.cutoff_before_close_minutes}
            />
          </div>
        </div>
        <button class="btn primary" type="submit" disabled={savingSettings}>
          {savingSettings ? "Saving…" : "Save global settings"}
        </button>
      </form>
    </section>
  {/if}

  {#if dashboard}
    <section class="card">
      <div class="panel-head">
        <div>
          <h2>Portfolio automation</h2>
          <p class="muted">
            Integrated automation currently supports Codex agents. Selected weekdays shift to the next trading
            day on market holidays; no weekdays means manual-only.
          </p>
        </div>
      </div>
      <div class="portfolio-configs">
        {#each dashboard.portfolios as config (config.portfolio.id)}
          {@const draft = configDrafts[config.portfolio.id]}
          {#if draft}
            <article class="portfolio-config">
              <div class="config-head">
                <div>
                  <strong>{config.portfolio.name}</strong>
                  <div class="muted num">{config.portfolio.slug}</div>
                  <div class="muted">
                    {config.agent.name} · {config.agent.execution_model_id}
                    {#if config.agent.reasoning_effort}
                      · {config.agent.reasoning_effort}
                    {/if}
                  </div>
                </div>
                <label class="toggle-label">
                  <input
                    type="checkbox"
                    bind:checked={draft.enabled}
                    disabled={config.portfolio.status !== "active"}
                  />
                  Enabled
                </label>
              </div>
              <div class="config-fields">
                <div class="field">
                  <span class="field-label">Automatic days</span>
                  <div class="weekday-group" aria-label={`Automatic days for ${config.portfolio.name}`}>
                    {#each WEEKDAYS as weekday (weekday.value)}
                      <button
                        type="button"
                        class={["weekday", { selected: draft.weekdays.includes(weekday.value) }]}
                        aria-pressed={draft.weekdays.includes(weekday.value)}
                        onclick={() => toggleWeekday(config.portfolio.id, weekday.value)}
                      >
                        {weekday.label}
                      </button>
                    {/each}
                  </div>
                  <div class="muted cadence">{cadenceLabel(config)}</div>
                </div>
              </div>
              <div class="config-actions">
                <button
                  class="btn small"
                  type="button"
                  onclick={() => savePortfolio(config)}
                  disabled={busyAction === `save-${config.portfolio.id}`}
                >
                  {busyAction === `save-${config.portfolio.id}` ? "Saving…" : "Save"}
                </button>
                <button
                  class="btn small primary"
                  type="button"
                  onclick={() => queueRuns([config.portfolio.id], `run-${config.portfolio.id}`)}
                  disabled={!settingsDraft?.enabled || !draft.enabled || config.portfolio.status !== "active"}
                >
                  {busyAction === `run-${config.portfolio.id}` ? "Queueing…" : "Run now"}
                </button>
              </div>
            </article>
          {/if}
        {/each}
      </div>
    </section>
  {/if}

  <section class="card">
    <div class="panel-head">
      <div>
        <h2>Evaluation history</h2>
        <p class="muted">Scheduled, immediate, and retry runs share one auditable queue.</p>
      </div>
      <button class="btn small" type="button" onclick={() => loadRuns(true)} disabled={loading}
        >Refresh</button
      >
    </div>

    <div class="filters" aria-label="Evaluation run filters">
      <div class="field">
        <label for="run-portfolio">Portfolio</label>
        <select id="run-portfolio" bind:value={portfolioFilter} onchange={() => loadRuns(true)}>
          <option value="">All portfolios</option>
          {#each dashboard?.portfolios ?? [] as config (config.portfolio.id)}
            <option value={String(config.portfolio.id)}>{config.portfolio.name}</option>
          {/each}
        </select>
      </div>
      <div class="field">
        <label for="run-status">Status</label>
        <select id="run-status" bind:value={statusFilter} onchange={() => loadRuns(true)}>
          <option value="">All statuses</option>
          <option value="queued">Queued</option>
          <option value="running">Running</option>
          <option value="cancel_requested">Cancelling</option>
          <option value="succeeded">Succeeded</option>
          <option value="failed">Failed</option>
          <option value="cancelled">Cancelled</option>
          <option value="skipped">Skipped</option>
        </select>
      </div>
    </div>

    {#if notice}
      <div class="notice-box" role="status">{notice}</div>
    {/if}
    {#if error}
      <div class="error-box" role="alert">{error}</div>
    {/if}

    <div class="table-scroll">
      <table>
        <caption>Automated portfolio evaluation runs, newest first</caption>
        <thead>
          <tr>
            <th>Run</th>
            <th>Portfolio</th>
            <th>Model</th>
            <th>Status</th>
            <th class="right">Attempts</th>
            <th>Finished</th>
            <th>Result</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody aria-live="polite">
          {#each runs as run (run.id)}
            <tr>
              <td>
                <span class="badge">{run.trigger_kind}</span>
                <div class="muted num">{run.scheduled_for ?? `#${run.id}`}</div>
              </td>
              <td>
                <strong>{run.portfolio.name}</strong>
                <div class="muted num">{run.portfolio.slug}</div>
              </td>
              <td>
                {run.execution_model_id}
                <div class="muted num">
                  {run.harness}{run.reasoning_effort ? ` · ${run.reasoning_effort}` : ""}
                </div>
                <div class="muted num">{run.harness_version ?? "not claimed"}</div>
              </td>
              <td><span class={`badge ${statusClass(run.status)}`}>{run.status}</span></td>
              <td class="right num">{run.attempt_count}/{run.max_attempts}</td>
              <td class="num">{fmtDateTime(run.finished_at)}</td>
              <td>
                {#if run.report || run.error}
                  <details>
                    <summary>{run.error ? "Details" : "Report"}</summary>
                    <pre class:error-report={Boolean(run.error)}>{run.error ?? run.report}</pre>
                  </details>
                {:else if run.allocation_id}
                  <span class="muted">Allocation #{run.allocation_id}</span>
                {:else}
                  <span class="muted">—</span>
                {/if}
              </td>
              <td>
                <div class="row-actions">
                  {#if run.status === "queued" || run.status === "running"}
                    <button
                      class="btn small danger"
                      type="button"
                      onclick={() => cancelRun(run)}
                      disabled={busyAction === `cancel-${run.id}`}
                    >
                      Cancel
                    </button>
                  {:else if run.status === "failed"}
                    <button
                      class="btn small"
                      type="button"
                      onclick={() => retryRun(run)}
                      disabled={busyAction === `retry-${run.id}` || !settingsDraft?.enabled}
                    >
                      Retry
                    </button>
                  {:else}
                    <span class="muted">—</span>
                  {/if}
                </div>
              </td>
            </tr>
          {:else}
            {#if !loading}
              <tr><td colspan="8" class="muted empty">No evaluation runs match these filters.</td></tr>
            {/if}
          {/each}
          {#if loading && runs.length === 0}
            <tr><td colspan="8" class="muted empty">Loading evaluation runs…</td></tr>
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
</div>

<style>
  .automation-stack {
    display: grid;
    gap: 16px;
  }

  .automation-stack > .card {
    min-width: 0;
  }

  .panel-head,
  .config-head,
  .runtime-actions,
  .config-actions,
  .row-actions {
    display: flex;
    align-items: start;
    justify-content: space-between;
    gap: 12px;
  }

  .panel-head {
    margin-bottom: 18px;
  }

  h2 {
    font-size: 15px;
    margin-bottom: 4px;
  }

  .runtime-grid {
    display: grid;
    grid-template-columns: repeat(5, minmax(120px, 1fr));
    gap: 16px;
    margin-bottom: 18px;
  }

  .runtime-label,
  .field-label {
    display: block;
    margin-bottom: 5px;
    color: var(--text-secondary);
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }

  .runtime-actions {
    justify-content: flex-start;
  }

  .runtime-error {
    margin-bottom: 16px;
  }

  .settings-grid {
    display: grid;
    grid-template-columns: repeat(4, minmax(140px, 1fr));
    gap: 12px;
  }

  .field {
    margin-bottom: 14px;
  }

  .portfolio-configs {
    display: grid;
    gap: 12px;
  }

  .portfolio-config {
    padding: 14px;
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-sm);
    background: var(--bg-inset);
  }

  .config-head {
    margin-bottom: 12px;
  }

  .toggle-label {
    display: flex;
    align-items: center;
    gap: 7px;
    margin: 0;
    text-transform: none;
    letter-spacing: normal;
  }

  .toggle-label input {
    width: auto;
    min-height: auto;
  }

  .config-fields {
    display: grid;
    grid-template-columns: minmax(240px, 1fr) minmax(320px, 1.4fr);
    gap: 16px;
  }

  .weekday-group {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }

  .weekday {
    min-width: 48px;
    padding: 7px 9px;
    border: 1px solid var(--border-strong);
    border-radius: var(--radius-sm);
    background: var(--bg-surface);
    font-size: 12px;
  }

  .weekday.selected {
    color: var(--accent);
    border-color: var(--accent);
    background: var(--accent-bg);
  }

  .cadence {
    margin-top: 6px;
    font-size: 12px;
  }

  .config-actions {
    justify-content: flex-end;
  }

  .filters {
    display: grid;
    grid-template-columns: repeat(2, minmax(180px, 280px));
    gap: 12px;
  }

  .notice-box {
    margin-bottom: 12px;
    padding: 10px 12px;
    color: var(--pos);
    border: 1px solid var(--pos);
    border-radius: var(--radius-sm);
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
    max-width: 420px;
    max-height: 240px;
    overflow: auto;
    padding: 10px;
    margin-top: 8px;
    white-space: pre-wrap;
    background: var(--bg-inset);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-sm);
    font-family: var(--font-mono);
    font-size: 12px;
  }

  pre.error-report {
    color: var(--neg);
  }

  .row-actions {
    justify-content: flex-start;
  }

  .empty {
    padding: 24px;
    text-align: center;
  }

  .more {
    display: flex;
    justify-content: center;
    margin-top: 14px;
  }

  @media (max-width: 900px) {
    .runtime-grid,
    .settings-grid {
      grid-template-columns: repeat(2, minmax(140px, 1fr));
    }

    .config-fields {
      grid-template-columns: 1fr;
    }
  }

  @media (max-width: 640px) {
    .runtime-grid,
    .settings-grid,
    .filters {
      grid-template-columns: 1fr;
    }

    .panel-head,
    .config-head {
      align-items: stretch;
      flex-direction: column;
    }
  }
</style>
