<script lang="ts">
  import { apiJson } from "../api/client";
  import type { SignalOut, SignalsPage } from "../api/types";
  import { fmtDate, fmtDateTime, pctPoints } from "../format";

  interface Props {
    slug: string;
    initialSignals: SignalOut[];
    initialNextCursor: number | null;
  }

  const { slug, initialSignals, initialNextCursor }: Props = $props();
  // Initial API data seeds local cursor state; the parent remounts this component per portfolio.
  // svelte-ignore state_referenced_locally
  let signals = $state.raw<SignalOut[]>(initialSignals);
  // svelte-ignore state_referenced_locally
  let nextCursor = $state<number | null>(initialNextCursor);
  let loading = $state(false);
  let error = $state("");

  async function loadPage(): Promise<void> {
    loading = true;
    error = "";
    const query = new URLSearchParams({ limit: "20" });
    if (nextCursor !== null) query.set("cursor", String(nextCursor));
    try {
      const payload = await apiJson<SignalsPage>(`/api/portfolios/${slug}/signals?${query.toString()}`);
      const existing = new Set(signals.map((signal) => signal.id));
      signals = [...signals, ...payload.signals.filter((signal) => !existing.has(signal.id))];
      nextCursor = payload.next_cursor;
    } catch (caught) {
      error = caught instanceof Error ? caught.message : "Could not load signal history.";
    } finally {
      loading = false;
    }
  }
</script>

<section class="signal-history" aria-labelledby="signal-history-title">
  <header>
    <div>
      <h2 id="signal-history-title">Recent signals</h2>
      <p>Independent target portfolios, newest first.</p>
    </div>
    <span class="num">{signals.length} loaded</span>
  </header>

  {#if error}
    <div class="error-box" role="alert">{error}</div>
  {/if}

  <div class="signals">
    {#each signals as signal (signal.id)}
      <details>
        <summary>
          <span>
            <strong class="num">{fmtDate(signal.effective_date)}</strong>
            <span>{signal.positions.length} positions</span>
          </span>
          <span class="signal-state">
            {signal.locked ? "Complete" : "Pending"}
          </span>
        </summary>
        <div class="signal-body">
          <p class="muted num">Entered {fmtDateTime(signal.entered_at)}</p>
          {#if signal.note}<p>{signal.note}</p>{/if}
          <div class="table-scroll">
            <table>
              <thead><tr><th>Symbol</th><th class="right">Weight</th></tr></thead>
              <tbody>
                {#each signal.positions as position (position.symbol)}
                  <tr>
                    <td class="num">{position.symbol}</td>
                    <td class="right num">{pctPoints(position.weight_pct, 2)}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
        </div>
      </details>
    {:else}
      {#if !loading}<div class="empty-state">No signals have been entered yet.</div>{/if}
    {/each}
  </div>

  {#if loading}
    <div class="loading-state compact" aria-live="polite">
      <span class="loading-mark" aria-hidden="true"></span>
      Loading signals…
    </div>
  {:else if nextCursor !== null}
    <button class="btn" type="button" onclick={loadPage}>Load older signals</button>
  {/if}
</section>

<style>
  .signal-history {
    display: grid;
    gap: 12px;
  }

  header {
    display: flex;
    align-items: end;
    justify-content: space-between;
    gap: 16px;
    padding-bottom: 10px;
    border-bottom: 1px solid var(--border-subtle);
  }

  h2 {
    margin: 0;
    font-size: 16px;
  }

  header p,
  header > span {
    margin-top: 5px;
    color: var(--text-secondary);
    font-size: 11px;
  }

  .signals {
    display: grid;
    border-top: 1px solid var(--border-subtle);
  }

  details {
    border-bottom: 1px solid var(--border-subtle);
  }

  summary {
    min-height: 52px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 14px;
    padding: 10px 4px;
    cursor: pointer;
  }

  summary > span:first-child {
    display: flex;
    align-items: baseline;
    gap: 10px;
  }

  summary span {
    color: var(--text-secondary);
    font-size: 11px;
  }

  summary strong {
    color: var(--text-primary);
    font-size: 12px;
  }

  .signal-state {
    color: var(--text-tertiary);
    font-size: 9px;
    font-weight: 750;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  .signal-body {
    display: grid;
    gap: 10px;
    padding: 4px 4px 16px;
  }

  .signal-body p {
    font-size: 12px;
  }

  .table-scroll {
    overflow-x: auto;
  }

  table {
    min-width: 360px;
  }
</style>
