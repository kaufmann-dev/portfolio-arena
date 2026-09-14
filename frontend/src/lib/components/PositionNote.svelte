<script lang="ts">
  let { symbol, note }: { symbol: string; note?: string } = $props();
</script>

{#if note?.trim()}
  <details class="position-note">
    <summary aria-label={symbol + " handoff note"}>Handoff note</summary>
    <p>{note}</p>
  </details>
{:else}
  <span class="muted no-note">No handoff note</span>
{/if}

<style>
  .position-note {
    margin-top: 4px;
    border: 0;
    font-family: sans-serif;
    font-size: 12px;
    white-space: normal;
  }
  .position-note > summary {
    display: list-item;
    min-height: 24px;
    padding: 3px 0;
    cursor: pointer;
    color: var(--text-secondary);
  }
  .position-note > summary:focus-visible {
    outline: 2px solid var(--text-primary);
    outline-offset: 2px;
  }
  .position-note p {
    max-width: 60ch;
    padding: 8px 0;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    font-weight: 400;
  }
  .no-note {
    display: block;
    font-size: 11px;
    font-weight: 400;
  }
</style>
