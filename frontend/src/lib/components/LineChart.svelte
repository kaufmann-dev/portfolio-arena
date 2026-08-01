<script lang="ts">
  import { SvelteSet } from "svelte/reactivity";

  import type { SeriesPoint } from "../api/types";

  export interface ChartSeries {
    name: string;
    points: SeriesPoint[];
    color?: string;
    dashed?: boolean;
  }

  interface Props {
    series: ChartSeries[];
    /** vertical marker dates (e.g. allocation effective dates) */
    markers?: string[];
    height?: number;
    ariaLabel?: string;
  }

  const { series, markers = [], height = 320, ariaLabel = "Performance chart" }: Props = $props();

  const PALETTE = ["var(--accent)", "var(--warn)", "var(--pos)", "#c4a7ff", "#67e8f9", "var(--neg)"];

  const PAD = { top: 14, right: 12, bottom: 30, left: 48 };
  let width = $state(720);

  const dates = $derived.by(() => {
    const all = new SvelteSet<string>();
    for (const entry of series) for (const point of entry.points) all.add(point.date);
    return [...all].sort();
  });

  const domain = $derived.by(() => {
    let min = Infinity;
    let max = -Infinity;
    for (const entry of series)
      for (const point of entry.points) {
        if (point.nav < min) min = point.nav;
        if (point.nav > max) max = point.nav;
      }
    if (!isFinite(min)) return { min: 0, max: 1 };
    const pad = (max - min || max * 0.05 || 1) * 0.06;
    return { min: min - pad, max: max + pad };
  });

  const dateIndex = $derived(new Map(dates.map((date, i) => [date, i])));

  function x(i: number): number {
    const n = Math.max(dates.length - 1, 1);
    return PAD.left + (i / n) * (width - PAD.left - PAD.right);
  }

  function y(value: number): number {
    const { min, max } = domain;
    const t = (value - min) / (max - min || 1);
    return PAD.top + (1 - t) * (height - PAD.top - PAD.bottom);
  }

  function path(points: SeriesPoint[]): string {
    let d = "";
    for (const point of points) {
      const i = dateIndex.get(point.date);
      if (i === undefined) continue;
      d += `${d ? "L" : "M"}${x(i).toFixed(1)},${y(point.nav).toFixed(1)}`;
    }
    return d;
  }

  const yTicks = $derived.by(() => {
    const { min, max } = domain;
    const span = max - min;
    const rawStep = span / 4;
    const magnitude = Math.pow(10, Math.floor(Math.log10(rawStep || 1)));
    const step =
      [1, 2, 2.5, 5, 10].map((mult) => mult * magnitude).find((s) => span / s <= 5.5) ?? magnitude * 10;
    const ticks: number[] = [];
    for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) ticks.push(v);
    return ticks;
  });

  const xTicks = $derived.by(() => {
    if (dates.length < 2) return [];
    const count = Math.max(2, Math.min(6, Math.floor((width - PAD.left - PAD.right) / 90), dates.length));
    const ticks: { i: number; label: string }[] = [];
    for (let k = 0; k < count; k++) {
      const i = Math.round((k / (count - 1)) * (dates.length - 1));
      ticks.push({ i, label: dates[i].slice(0, 7) });
    }
    return ticks.filter((tick, idx, arr) => idx === 0 || tick.label !== arr[idx - 1].label);
  });

  let hoverIndex = $state<number | null>(null);

  function onPointerMove(event: PointerEvent) {
    const rect = (event.currentTarget as SVGElement).getBoundingClientRect();
    const px = ((event.clientX - rect.left) / rect.width) * width;
    const n = Math.max(dates.length - 1, 1);
    const i = Math.round(((px - PAD.left) / (width - PAD.left - PAD.right)) * n);
    hoverIndex = Math.max(0, Math.min(dates.length - 1, i));
  }

  function valueAt(entry: ChartSeries, date: string): number | null {
    const point = entry.points.find((p) => p.date === date);
    return point ? point.nav : null;
  }

  function color(idx: number, entry: ChartSeries): string {
    return entry.color ?? PALETTE[idx % PALETTE.length];
  }

  const hoverDate = $derived(hoverIndex === null ? null : dates[hoverIndex]);
</script>

<div class="chart" bind:clientWidth={width}>
  {#if dates.length < 2}
    <div class="empty-state"><p>Not enough data to chart yet.</p></div>
  {:else}
    <svg
      viewBox="0 0 {width} {height}"
      role="img"
      aria-label={ariaLabel}
      onpointermove={onPointerMove}
      onpointerdown={onPointerMove}
      onpointerleave={() => (hoverIndex = null)}
    >
      <!-- grid + y labels -->
      {#each yTicks as tick (tick)}
        <line x1={PAD.left} x2={width - PAD.right} y1={y(tick)} y2={y(tick)} stroke="var(--chart-grid)" />
        <text x={PAD.left - 8} y={y(tick) + 4} text-anchor="end" class="tick">
          {tick.toFixed(0)}
        </text>
      {/each}
      {#each xTicks as tick (tick.i)}
        <text x={x(tick.i)} y={height - 8} text-anchor="middle" class="tick">{tick.label}</text>
      {/each}

      <!-- base-100 reference -->
      {#if domain.min < 100 && domain.max > 100}
        <line
          x1={PAD.left}
          x2={width - PAD.right}
          y1={y(100)}
          y2={y(100)}
          stroke="var(--border-strong)"
          stroke-dasharray="2 3"
        />
      {/if}

      <!-- allocation markers -->
      {#each markers as marker (marker)}
        {#if dateIndex.has(marker)}
          <line
            x1={x(dateIndex.get(marker)!)}
            x2={x(dateIndex.get(marker)!)}
            y1={PAD.top}
            y2={height - PAD.bottom}
            stroke="var(--text-tertiary)"
            stroke-dasharray="3 4"
            opacity="0.6"
          />
        {/if}
      {/each}

      <!-- series -->
      {#each series as entry, idx (entry.name)}
        <path
          d={path(entry.points)}
          fill="none"
          stroke={color(idx, entry)}
          stroke-width="2"
          stroke-dasharray={entry.dashed ? "5 4" : undefined}
          vector-effect="non-scaling-stroke"
        />
      {/each}

      <!-- hover crosshair -->
      {#if hoverIndex !== null && hoverDate}
        <line
          x1={x(hoverIndex)}
          x2={x(hoverIndex)}
          y1={PAD.top}
          y2={height - PAD.bottom}
          stroke="var(--text-secondary)"
          opacity="0.5"
        />
        {#each series as entry, idx (entry.name)}
          {@const value = valueAt(entry, hoverDate)}
          {#if value !== null}
            <circle cx={x(hoverIndex)} cy={y(value)} r="3.5" fill={color(idx, entry)} />
          {/if}
        {/each}
      {/if}
    </svg>

    <div class="legend" aria-live="polite" aria-atomic="true">
      {#if hoverDate}
        <span class="num muted">{hoverDate}</span>
      {/if}
      {#each series as entry, idx (entry.name)}
        <span class="legend-item">
          <span
            class={["swatch", entry.dashed && "dashed"]}
            style:--swatch-color={color(idx, entry)}
            aria-hidden="true"
          ></span>
          <span class="legend-name">{entry.name}</span>
          {#if hoverDate}
            {@const value = valueAt(entry, hoverDate)}
            <span class="num">{value === null ? "—" : value.toFixed(1)}</span>
          {/if}
        </span>
      {/each}
    </div>

    <details class="chart-summary">
      <summary>Chart summary</summary>
      <dl>
        {#each series as entry (entry.name)}
          {@const latest = entry.points.at(-1)}
          <div>
            <dt>{entry.name}</dt>
            <dd class="num">{latest ? `${latest.nav.toFixed(1)} on ${latest.date}` : "No data"}</dd>
          </div>
        {/each}
      </dl>
    </details>
  {/if}
</div>

<style>
  .chart {
    width: 100%;
    min-width: 0;
  }

  svg {
    display: block;
    width: 100%;
    max-height: min(360px, 56vw);
    touch-action: pan-y;
  }

  .tick {
    fill: var(--text-tertiary);
    font-size: 10.5px;
    font-family: var(--font-mono);
  }

  .legend {
    display: flex;
    flex-wrap: wrap;
    gap: 8px 18px;
    padding: 10px 0 0;
    font-size: 12.5px;
    color: var(--text-secondary);
    min-height: 30px;
    align-items: center;
  }

  .legend-item {
    min-width: 0;
    max-width: 100%;
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }

  .legend-name {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .swatch {
    flex: 0 0 auto;
    width: 14px;
    height: 2px;
    display: inline-block;
    background: var(--swatch-color);
  }

  .swatch.dashed {
    background: repeating-linear-gradient(90deg, var(--swatch-color) 0 5px, transparent 5px 8px);
  }

  .chart-summary {
    margin-top: 10px;
    color: var(--text-secondary);
    font-size: 12px;
  }

  .chart-summary summary {
    width: max-content;
    color: var(--text-secondary);
    cursor: pointer;
  }

  .chart-summary dl {
    display: grid;
    gap: 6px;
    margin-top: 10px;
    padding: 10px 0;
  }

  .chart-summary dl div {
    display: flex;
    justify-content: space-between;
    gap: 16px;
  }

  .chart-summary dd {
    color: var(--text-primary);
  }

  @media (max-width: 600px) {
    svg {
      max-height: 260px;
    }

    .legend {
      gap: 8px 12px;
      font-size: 11px;
    }
  }
</style>
