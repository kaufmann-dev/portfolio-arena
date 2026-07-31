<script lang="ts">
  import type { EvidenceState } from "../api/types";

  interface Props {
    state: EvidenceState;
    compact?: boolean;
  }

  const { state, compact = false }: Props = $props();

  const label = $derived(
    state === "pending"
      ? "Pending"
      : state === "positive"
        ? "Positive"
        : state === "negative"
          ? "Negative"
          : "Inconclusive",
  );
</script>

<span class={["evidence", state, compact && "compact"]}>{label}</span>

<style>
  .evidence {
    display: inline-flex;
    min-height: 22px;
    align-items: center;
    padding: 3px 7px;
    border: 1px solid var(--border-strong);
    color: var(--text-secondary);
    font-size: 9px;
    font-weight: 760;
    letter-spacing: 0.08em;
    line-height: 1;
    text-transform: uppercase;
    white-space: nowrap;
  }

  .evidence.compact {
    min-height: 19px;
    padding: 2px 5px;
    font-size: 8px;
  }

  .evidence.positive {
    border-color: color-mix(in srgb, var(--pos) 55%, var(--border-strong));
    color: var(--pos);
    background: color-mix(in srgb, var(--pos) 9%, transparent);
  }

  .evidence.negative {
    border-color: color-mix(in srgb, var(--neg) 55%, var(--border-strong));
    color: var(--neg);
    background: color-mix(in srgb, var(--neg) 9%, transparent);
  }

  .evidence.pending {
    border-style: dashed;
    color: var(--text-tertiary);
  }

  .evidence.inconclusive {
    color: var(--warn);
    background: var(--warn-bg);
  }
</style>
