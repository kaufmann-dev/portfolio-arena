<script lang="ts">
  import { apiJson } from "../api/client";
  import type { PromptOut, ResolvedSymbol } from "../api/types";
  import { CircleCheck, ChevronUp, ChevronDown, X, Sigma } from "@lucide/svelte";

  export interface AllocationPayload {
    prompt_id: number;
    positions: { symbol: string; weight_pct: number; note: string }[];
    note: string;
  }

  interface Row {
    symbol: string;
    weight: string;
    note: string;
    status: "idle" | "checking" | "ok" | "error";
    resolved?: ResolvedSymbol;
    error?: string;
  }

  interface Props {
    prompts: PromptOut[];
    initialPositions?: { symbol: string; weight_pct: number; note?: string }[];
    initialPromptId?: number | null;
    initialNote?: string;
    /** When false (locked allocation edit) position rows are read-only. */
    positionsEditable?: boolean;
    submitLabel: string;
    onSubmit: (payload: AllocationPayload) => Promise<void>;
  }

  const {
    prompts,
    initialPositions = [],
    initialPromptId = null,
    initialNote = "",
    positionsEditable = true,
    submitLabel,
    onSubmit,
  }: Props = $props();

  function toRows(positions: { symbol: string; weight_pct: number; note?: string }[]): Row[] {
    const rows = positions.map(
      (position): Row => ({
        symbol: position.symbol,
        weight: String(position.weight_pct),
        note: position.note ?? "",
        status: "idle",
      }),
    );
    return rows.length ? rows : [{ symbol: "", weight: "", note: "", status: "idle" }];
  }

  // Initial-value props intentionally seed local state; parents re-mount the
  // form with {#key} when a different allocation is loaded.
  // svelte-ignore state_referenced_locally
  let rows = $state<Row[]>(toRows(initialPositions));
  // svelte-ignore state_referenced_locally
  let promptId = $state<number | null>(initialPromptId);
  // svelte-ignore state_referenced_locally
  let note = $state(initialNote);
  let submitting = $state(false);
  let formError = $state("");

  let effectivePreview = $state<string | null>(null);
  $effect(() => {
    apiJson<{ effective_date: string }>("/api/effective-date")
      .then((payload) => (effectivePreview = payload.effective_date))
      .catch(() => (effectivePreview = null));
  });

  const weightSum = $derived(
    rows.reduce((sum, row) => sum + (parseFloat(row.weight) || 0), 0),
  );
  const sumOk = $derived(Math.abs(weightSum - 100) < 1e-6);

  function addRow() {
    rows = [...rows, { symbol: "", weight: "", note: "", status: "idle" }];
  }

  function removeRow(index: number) {
    rows = rows.filter((_, i) => i !== index);
    if (!rows.length) addRow();
  }

  function move(index: number, delta: number) {
    const target = index + delta;
    if (target < 0 || target >= rows.length) return;
    const next = [...rows];
    [next[index], next[target]] = [next[target], next[index]];
    rows = next;
  }

  async function checkSymbol(index: number) {
    const row = rows[index];
    const symbol = row.symbol.trim().toUpperCase();
    if (!symbol) {
      row.status = "idle";
      row.resolved = undefined;
      return;
    }
    row.symbol = symbol;
    row.status = "checking";
    try {
      row.resolved = await apiJson<ResolvedSymbol>(`/api/symbols/${encodeURIComponent(symbol)}`);
      row.status = "ok";
      row.error = undefined;
    } catch (e) {
      row.status = "error";
      row.resolved = undefined;
      row.error = e instanceof Error ? e.message : "Symbol validation failed";
    }
  }

  function normalize() {
    const total = weightSum;
    if (total <= 0) return;
    rows = rows.map((row) => {
      const weight = parseFloat(row.weight) || 0;
      return { ...row, weight: ((weight / total) * 100).toFixed(4).replace(/\.?0+$/, "") };
    });
    // rounding residue lands on the largest position so the sum is exactly 100
    const parsed = rows.map((row) => parseFloat(row.weight) || 0);
    const residue = 100 - parsed.reduce((a, b) => a + b, 0);
    if (Math.abs(residue) > 1e-9) {
      const largest = parsed.indexOf(Math.max(...parsed));
      rows[largest].weight = String(Math.round((parsed[largest] + residue) * 1e4) / 1e4);
    }
  }

  async function submit(event: SubmitEvent) {
    event.preventDefault();
    formError = "";
    if (promptId === null) {
      formError = "Pick the prompt that produced this decision.";
      return;
    }
    const positions = rows
      .filter((row) => row.symbol.trim())
      .map((row) => ({
        symbol: row.symbol.trim().toUpperCase(),
        weight_pct: parseFloat(row.weight) || 0,
        note: row.note.trim(),
      }));
    if (!positions.length) {
      formError = "Enter at least one position.";
      return;
    }
    if (!sumOk) {
      formError = `Weights sum to ${weightSum.toFixed(4)} — they must be exactly 100.`;
      return;
    }
    submitting = true;
    try {
      await onSubmit({ prompt_id: promptId, positions, note });
    } catch (e) {
      formError = e instanceof Error ? e.message : "Submit failed";
    } finally {
      submitting = false;
    }
  }
</script>

<form onsubmit={submit} class="alloc-form">
  {#if positionsEditable}
    <fieldset>
      <legend>Positions <span class="muted">(symbols + % of NAV; CASH:USD, CASH:EUR for cash)</span></legend>
      <div class="rows">
        {#each rows as row, index (index)}
          <div class="row">
            <div class="cell symbol-cell">
              <input
                type="text"
                placeholder="AAPL / SPY / CASH:USD"
                bind:value={row.symbol}
                onblur={() => checkSymbol(index)}
                aria-label="Symbol for row {index + 1}"
                class:invalid={row.status === "error"}
              />
              <div class="resolution" aria-live="polite">
                {#if row.status === "checking"}
                  <span class="muted">checking…</span>
                {:else if row.status === "ok" && row.resolved}
                  <span class="ok"><CircleCheck size={14} /> {row.resolved.name} · {row.resolved.instrument}</span>
                {:else if row.status === "error"}
                  <span class="neg">{row.error}</span>
                {/if}
              </div>
            </div>
            <input
              class="weight"
              type="number"
              step="0.0001"
              min="0"
              placeholder="%"
              bind:value={row.weight}
              aria-label="Weight percent for row {index + 1}"
            />
            <div class="row-actions">
              <button type="button" class="btn small" onclick={() => move(index, -1)} aria-label="Move row {index + 1} up" disabled={index === 0}><ChevronUp size={14} /></button>
              <button type="button" class="btn small" onclick={() => move(index, 1)} aria-label="Move row {index + 1} down" disabled={index === rows.length - 1}><ChevronDown size={14} /></button>
              <button type="button" class="btn small danger" onclick={() => removeRow(index)} aria-label="Remove row {index + 1}"><X size={14} /></button>
            </div>
            <input
              class="pos-note"
              type="text"
              placeholder="Agent note for this position (passed to the next cycle)"
              bind:value={row.note}
              aria-label="Agent note for row {index + 1}"
            />
          </div>
        {/each}
      </div>
      <div class="rows-footer">
        <button type="button" class="btn small" onclick={addRow}>+ Add position</button>
        <span class="sum num" class:ok={sumOk} class:neg={!sumOk} aria-live="polite">
          <Sigma size={14} /> {weightSum.toFixed(4).replace(/\.?0+$/, "")}%
          {#if !sumOk}(must be exactly 100){/if}
        </span>
        <button type="button" class="btn small" onclick={normalize} disabled={weightSum <= 0}>
          Normalize to 100
        </button>
      </div>
    </fieldset>
  {:else}
    <p class="muted locked-note">
      Positions are locked — the effective close has passed. Only prompt, note, and raw response
      can change.
    </p>
  {/if}

  <div class="field">
    <label for="prompt-select">Prompt that produced this decision</label>
    <select id="prompt-select" bind:value={promptId}>
      <option value={null} disabled>Select a prompt…</option>
      {#each prompts as prompt (prompt.id)}
        <option value={prompt.id}>{prompt.name}</option>
      {/each}
    </select>
    {#if prompts.length === 0}
      <p class="muted prompt-hint">No prompts yet — add one in the Prompts tab.</p>
    {/if}
  </div>

  <div class="field">
    <label for="alloc-note">Notes <span class="muted">(optional)</span></label>
    <textarea id="alloc-note" bind:value={note} rows="4"></textarea>
  </div>

  {#if formError}
    <div class="error-box" role="alert">{formError}</div>
  {/if}

  <div class="submit-line">
    <button type="submit" class="btn primary" disabled={submitting}>
      {submitting ? "Submitting…" : submitLabel}
    </button>
    {#if positionsEditable && effectivePreview}
      <span class="muted">
        Takes effect at the <strong>{effectivePreview}</strong> close — editable until then.
      </span>
    {/if}
  </div>
</form>

<style>
  .alloc-form {
    display: flex;
    flex-direction: column;
    gap: 16px;
  }

  fieldset {
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius);
    padding: 14px;
  }

  legend {
    padding: 0 6px;
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--text-secondary);
  }

  .rows {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .row {
    display: grid;
    grid-template-columns: minmax(200px, 1fr) 110px auto;
    gap: 8px;
    align-items: start;
  }

  .weight {
    text-align: right;
    font-family: var(--font-mono);
  }

  .pos-note {
    grid-column: 1 / -1;
    font-size: 12.5px;
  }

  input.invalid {
    border-color: var(--neg);
  }

  .resolution {
    font-size: 12px;
    min-height: 16px;
    margin-top: 3px;
  }

  .ok {
    color: var(--pos);
  }

  .row-actions {
    display: flex;
    gap: 4px;
    padding-top: 3px;
  }

  .rows-footer {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-top: 12px;
    flex-wrap: wrap;
  }

  .sum {
    font-weight: 600;
    margin-left: auto;
  }

  .prompt-hint {
    font-size: 12px;
    margin-top: 6px;
  }

  .locked-note {
    padding: 10px 12px;
    background: var(--warn-bg);
    border: 1px solid var(--warn);
    border-radius: var(--radius-sm);
    color: var(--warn);
  }

  .submit-line {
    display: flex;
    align-items: center;
    gap: 14px;
    flex-wrap: wrap;
  }

  @media (max-width: 560px) {
    .row {
      grid-template-columns: 1fr 90px;
    }

    .row-actions {
      grid-column: 1 / -1;
    }
  }
</style>
