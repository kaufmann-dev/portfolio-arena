<script lang="ts">
  import { apiJson } from "../api/client";
  import type { AllocationPolicy, Direction, ResolvedSymbol } from "../api/types";
  import { CircleCheck, ChevronUp, ChevronDown, X, Sigma } from "@lucide/svelte";

  export interface AllocationPayload {
    positions: { symbol: string; weight_pct: number; note: string }[];
    note: string;
  }

  interface Row {
    id: number;
    symbol: string;
    weight: string;
    note: string;
    status: "idle" | "checking" | "ok" | "error";
    validationRequestId: number;
    resolved?: ResolvedSymbol;
    error?: string;
  }

  interface Props {
    initialPositions?: { symbol: string; weight_pct: number; note?: string }[];
    initialNote?: string;
    /** When false (locked allocation edit) position rows are read-only. */
    positionsEditable?: boolean;
    submitLabel: string;
    onSubmit: (payload: AllocationPayload) => Promise<void>;
    policy?: AllocationPolicy;
    entryKind?: "allocation" | "signal";
    direction?: Direction;
  }

  const {
    initialPositions = [],
    initialNote = "",
    positionsEditable = true,
    submitLabel,
    onSubmit,
    policy,
    entryKind = "allocation",
    direction = "long",
  }: Props = $props();

  let nextRowId = 1;

  function emptyRow(): Row {
    return {
      id: nextRowId++,
      symbol: "",
      weight: "",
      note: "",
      status: "idle",
      validationRequestId: 0,
    };
  }

  function toRows(positions: { symbol: string; weight_pct: number; note?: string }[]): Row[] {
    const rows = positions.map((position): Row => ({
      id: nextRowId++,
      symbol: position.symbol,
      weight: String(position.weight_pct),
      note: position.note ?? "",
      status: "idle",
      validationRequestId: 0,
    }));
    return rows.length ? rows : [emptyRow()];
  }

  // Initial-value props intentionally seed local state; parents re-mount the
  // form with {#key} when a different allocation is loaded.
  // svelte-ignore state_referenced_locally
  let rows = $state<Row[]>(toRows(initialPositions));
  // svelte-ignore state_referenced_locally
  let note = $state(initialNote);
  let submitting = $state(false);
  let formError = $state("");

  let effectivePreview = $state<string | null>(null);
  async function loadEffectivePreview() {
    try {
      const payload = await apiJson<{ effective_date: string }>("/api/effective-date");
      effectivePreview = payload.effective_date;
    } catch {
      effectivePreview = null;
    }
  }
  void loadEffectivePreview();

  const weightSum = $derived(rows.reduce((sum, row) => sum + (parseFloat(row.weight) || 0), 0));
  const sumOk = $derived(Math.abs(weightSum - 100) < 1e-6);

  function addRow() {
    rows = [...rows, emptyRow()];
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

  function updateSymbol(row: Row, value: string): void {
    row.validationRequestId += 1;
    row.symbol = value;
    row.status = "idle";
    row.resolved = undefined;
    row.error = undefined;
  }

  async function checkSymbol(row: Row) {
    const symbol = row.symbol.trim().toUpperCase();
    const requestId = ++row.validationRequestId;
    row.symbol = symbol;
    row.resolved = undefined;
    row.error = undefined;

    if (!symbol) {
      row.status = "idle";
      return;
    }

    row.status = "checking";
    try {
      const resolved = await apiJson<ResolvedSymbol>(`/api/symbols/${encodeURIComponent(symbol)}`);
      if (row.validationRequestId !== requestId) return;

      row.resolved = resolved;
      row.status = "ok";
    } catch (e) {
      if (row.validationRequestId !== requestId) return;

      row.status = "error";
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
    if (
      policy &&
      positions.some(
        (position) =>
          position.weight_pct < policy.min_position_weight_pct ||
          position.weight_pct > policy.max_position_weight_pct,
      )
    ) {
      formError = `Every position must be between ${policy.min_position_weight_pct}% and ${policy.max_position_weight_pct}%.`;
      return;
    }
    submitting = true;
    try {
      await onSubmit({ positions, note });
    } catch (e) {
      formError = e instanceof Error ? e.message : "Submit failed";
    } finally {
      submitting = false;
    }
  }
</script>

<form onsubmit={submit} class="alloc-form" aria-busy={submitting}>
  {#if positionsEditable}
    <fieldset class="positions-fieldset">
      <legend class="positions-legend">Target {direction} positions</legend>
      <div class="positions-head">
        <div>
          <span class="positions-title" aria-hidden="true">Target {direction} positions</span>
          <p class="muted">
            USD-denominated equities and ETFs · fully invested {direction} book
          </p>
        </div>
        <span class={["weight-total", "num", sumOk ? "ok" : "neg"]} aria-live="polite">
          <Sigma size={15} strokeWidth={1.8} aria-hidden="true" />
          {weightSum.toFixed(4).replace(/\.?0+$/, "")}%
        </span>
      </div>
      {#if policy}
        <div class="policy-strip">
          <span><strong>{policy.derived_min_positions}–{policy.derived_max_positions}</strong> positions</span
          >
          <span
            ><strong>{policy.min_position_weight_pct}%–{policy.max_position_weight_pct}%</strong> each</span
          >
        </div>
      {/if}
      <div class="rows">
        {#each rows as row, index (row.id)}
          <article class="position-row">
            <div class="position-index num" aria-hidden="true">{String(index + 1).padStart(2, "0")}</div>
            <div class="symbol-cell">
              <label for="symbol-{row.id}">Symbol</label>
              <input
                id="symbol-{row.id}"
                type="text"
                placeholder="AAPL"
                value={row.symbol}
                oninput={(event) => updateSymbol(row, event.currentTarget.value)}
                onblur={() => checkSymbol(row)}
                aria-label="Symbol for row {index + 1}"
                class={row.status === "error" ? "invalid" : undefined}
              />
              <div class="resolution" aria-live="polite" aria-atomic="true">
                {#if row.status === "checking"}
                  <span class="muted">checking…</span>
                {:else if row.status === "ok" && row.resolved}
                  <span class="ok">
                    <CircleCheck size={14} aria-hidden="true" />
                    {row.resolved.name} · {row.resolved.security_type}
                  </span>
                {:else if row.status === "error"}
                  <span class="neg">{row.error}</span>
                {/if}
              </div>
            </div>
            <div class="weight-cell">
              <label for="weight-{row.id}">Weight %</label>
              <input
                id="weight-{row.id}"
                class="weight"
                type="number"
                step="0.0001"
                min="0"
                placeholder="0"
                bind:value={row.weight}
                aria-label="Weight percent for row {index + 1}"
              />
            </div>
            <div class="row-actions">
              <button
                type="button"
                class="btn small icon-btn"
                onclick={() => move(index, -1)}
                aria-label="Move row {index + 1} up"
                disabled={index === 0}><ChevronUp size={14} /></button
              >
              <button
                type="button"
                class="btn small icon-btn"
                onclick={() => move(index, 1)}
                aria-label="Move row {index + 1} down"
                disabled={index === rows.length - 1}><ChevronDown size={14} /></button
              >
              <button
                type="button"
                class="btn small danger icon-btn"
                onclick={() => removeRow(index)}
                aria-label="Remove row {index + 1}"><X size={14} /></button
              >
            </div>
            <div class="note-cell">
              <label for="position-note-{row.id}">Handoff note <span class="muted">optional</span></label>
              <input
                id="position-note-{row.id}"
                class="pos-note"
                type="text"
                placeholder={direction === "short"
                  ? "Why this security belongs in the short book"
                  : "Why this security belongs in the long portfolio"}
                bind:value={row.note}
                aria-label="Agent note for row {index + 1}"
              />
            </div>
          </article>
        {/each}
      </div>
      <div class="rows-footer">
        <button type="button" class="btn small" onclick={addRow}>+ Add position</button>
        {#if !sumOk}
          <span class="sum-message neg">Weights must total exactly 100%.</span>
        {/if}
        <button type="button" class="btn small" onclick={normalize} disabled={weightSum <= 0}>
          Normalize to 100
        </button>
      </div>
    </fieldset>
  {:else}
    <p class="muted locked-note">
      Positions are locked — the effective close has passed. Only the {entryKind} note can change.
    </p>
  {/if}

  <div class="field">
    <label for="alloc-note">
      {entryKind === "signal" ? "Signal rationale" : "Allocation handoff"}
      <span class="muted">optional</span>
    </label>
    <textarea
      id="alloc-note"
      bind:value={note}
      rows="4"
      placeholder={entryKind === "signal"
        ? direction === "short"
          ? "Summarize why these securities should underperform and the evidence behind the signal."
          : "Summarize why these securities should outperform and the evidence behind the signal."
        : "Summarize the thesis, changes, and what the next evaluation should watch."}></textarea>
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
    display: grid;
    gap: 22px;
  }

  .positions-fieldset {
    padding: 0;
    border: 0;
  }

  .positions-title {
    display: block;
    padding: 0;
    color: var(--text-primary);
    font-size: 16px;
    font-weight: 680;
  }

  .positions-legend {
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

  .positions-head {
    display: flex;
    align-items: start;
    justify-content: space-between;
    gap: 16px;
    padding-bottom: 14px;
  }

  .positions-head p {
    margin-top: 3px;
    font-size: 12px;
  }

  .weight-total {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    font-size: 15px;
    font-weight: 700;
  }

  .policy-strip {
    display: flex;
    gap: 18px;
    padding: 10px;
    color: var(--text-secondary);
    background: var(--bg-inset);
    font-size: 12px;
  }

  .policy-strip strong {
    color: var(--text-primary);
    font-family: var(--font-mono);
  }

  .rows {
    display: grid;
  }

  .position-row {
    display: grid;
    grid-template-columns: 40px minmax(180px, 0.8fr) 120px auto minmax(220px, 1.2fr);
    gap: 10px;
    align-items: start;
    padding: 14px 0;
    border-bottom: 1px solid var(--border-subtle);
  }

  .position-index {
    padding-top: 31px;
    color: var(--text-tertiary);
    font-size: 11px;
  }

  .symbol-cell,
  .weight-cell,
  .note-cell {
    min-width: 0;
  }

  .weight {
    font-family: var(--font-mono);
    text-align: right;
  }

  .pos-note {
    font-size: 12.5px;
  }

  input.invalid {
    border-color: var(--neg);
  }

  .resolution {
    min-height: 18px;
    margin-top: 4px;
    font-size: 12px;
  }

  .ok {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    color: var(--pos);
  }

  .row-actions {
    display: flex;
    gap: 4px;
    padding-top: 27px;
  }

  .row-actions .icon-btn {
    min-width: 34px;
  }

  .rows-footer {
    display: flex;
    align-items: center;
    gap: 12px;
    padding-top: 14px;
    flex-wrap: wrap;
  }

  .sum-message {
    margin-left: auto;
    font-size: 12px;
  }

  .locked-note {
    padding: 12px 14px;
    color: var(--warn);
    background: var(--warn-bg);
    border: 1px solid var(--warn);
  }

  .submit-line {
    display: flex;
    align-items: center;
    gap: 14px;
    flex-wrap: wrap;
  }

  @media (max-width: 900px) {
    .position-row {
      grid-template-columns: 32px minmax(0, 1fr) 100px auto;
    }

    .note-cell {
      grid-column: 2 / -1;
    }
  }

  @media (max-width: 620px) {
    .positions-head {
      align-items: center;
    }

    .policy-strip {
      display: grid;
      gap: 4px;
    }

    .position-row {
      grid-template-columns: minmax(0, 1fr) 92px;
      padding: 16px 0;
    }

    .position-index {
      display: none;
    }

    .row-actions {
      grid-column: 1 / -1;
      grid-row: 2;
      padding-top: 0;
    }

    .note-cell {
      grid-column: 1 / -1;
      grid-row: 3;
    }

    .row-actions .icon-btn {
      width: 44px;
      min-width: 44px;
    }

    .rows-footer {
      align-items: stretch;
    }

    .rows-footer .btn {
      flex: 1;
    }

    .sum-message {
      width: 100%;
      margin-left: 0;
      order: -1;
    }

    .submit-line {
      align-items: stretch;
      flex-direction: column;
    }
  }
</style>
