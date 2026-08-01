<script lang="ts">
  import { apiJson } from "../api/client";
  import type { Direction, SignalOut, SignalsPage } from "../api/types";
  import { fmtDate, fmtDateTime, pctPoints } from "../format";

  interface Props {
    slug: string;
    direction: Direction;
    initialSignals: SignalOut[];
    initialNextCursor: number | null;
  }

  const { slug, direction, initialSignals, initialNextCursor }: Props = $props();
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
  <header class="section-head">
    <div>
      <h2 id="signal-history-title">Recent signals</h2>
      <p>Independent {direction} target portfolios, newest first.</p>
    </div>
    <span class="num">{signals.length} loaded</span>
  </header>

  {#if error}
    <div class="error-box" role="alert">{error}</div>
  {/if}

  <div class="disclosure-list">
    {#each signals as signal (signal.id)}
      <details>
        <summary>
          <span class="disclosure-primary">
            <strong class="num">{fmtDate(signal.effective_date)}</strong>
            <span>{signal.positions.length} positions</span>
          </span>
          <span class="disclosure-meta">
            {signal.locked ? "Complete" : "Pending"}
          </span>
        </summary>
        <div class="disclosure-body">
          <p class="muted num">Entered {fmtDateTime(signal.entered_at)}</p>
          {#if signal.note}<p>{signal.note}</p>{/if}
          <div class="table-scroll">
            <table class="data-table">
              <caption class="visually-hidden">
                Positions for the signal effective {fmtDate(signal.effective_date)}
              </caption>
              <thead><tr><th scope="col">Symbol</th><th scope="col" class="right">Weight</th></tr></thead>
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
      {#if !loading}<div class="empty-state compact">No signals have been entered yet.</div>{/if}
    {/each}
  </div>

  {#if loading}
    <div class="loading-block compact" aria-live="polite">
      <span class="spinner" aria-hidden="true"></span>
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
</style>
