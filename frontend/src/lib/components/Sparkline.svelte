<script lang="ts">
  interface Props {
    values: number[];
    width?: number;
    height?: number;
  }

  const { values, width = 110, height = 28 }: Props = $props();

  const path = $derived.by(() => {
    if (values.length < 2) return "";
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min || 1;
    return values
      .map((value, i) => {
        const px = (i / (values.length - 1)) * (width - 2) + 1;
        const py = height - 2 - ((value - min) / span) * (height - 4);
        return `${i === 0 ? "M" : "L"}${px.toFixed(1)},${py.toFixed(1)}`;
      })
      .join("");
  });

  const trendClass = $derived(
    values.length < 2 ? "" : values[values.length - 1] >= values[0] ? "up" : "down",
  );
</script>

{#if values.length >= 2}
  <svg {width} {height} class={trendClass} aria-hidden="true">
    <path d={path} fill="none" stroke="currentColor" stroke-width="1.4" />
  </svg>
{:else}
  <span class="muted">—</span>
{/if}

<style>
  svg {
    color: var(--spark);
    display: block;
  }

  svg.up {
    color: var(--pos);
  }

  svg.down {
    color: var(--neg);
  }
</style>
