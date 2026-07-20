<script lang="ts">
  import { apiJson } from "../api/client";
  import type { PortfolioSummary, PromptOut } from "../api/types";
  import PortfolioTable from "../components/PortfolioTable.svelte";
  import { link } from "../stores/router.svelte";

  interface Props {
    slug: string;
  }

  const { slug }: Props = $props();

  interface Payload {
    as_of: string | null;
    prompt: PromptOut & { created_at: string; updated_at: string };
    portfolios: PortfolioSummary[];
  }

  function requestErrorMessage(error: unknown): string {
    return error instanceof Error ? error.message : "Could not load this prompt.";
  }
</script>

{#key slug}
  {@const request = apiJson<Payload>(`/api/prompts/${slug}`)}
  {#await request}
    <div class="loading-block"><span class="spinner" aria-hidden="true"></span> Loading prompt…</div>
  {:then data}
    <nav class="crumbs" aria-label="Breadcrumb">
      <a href="/" onclick={(e) => link(e, "/")}>Leaderboard</a>
      <span aria-hidden="true">/</span>
      <span>Prompt</span>
    </nav>
    <h1>{data.prompt.name}</h1>
    <p class="muted">
      <span class="num">{data.prompt.slug}</span> · updated
      <span class="num">{data.prompt.updated_at.slice(0, 10)}</span>
    </p>

    <section class="card prompt-card">
      <h2>Strategy</h2>
      <pre>{data.prompt.text}</pre>
      {#if data.prompt.notes}
        <p class="muted notes">{data.prompt.notes}</p>
      {/if}
    </section>

    <section class="card policy-card">
      <h2>Allocation policy</h2>
      <p>
        Fully invested in USD-denominated equities and ETFs, with
        <strong>
          {data.prompt.allocation_policy.derived_min_positions}–{data.prompt.allocation_policy
            .derived_max_positions} positions
        </strong>
        at
        <strong>
          {data.prompt.allocation_policy.min_position_weight_pct}%–{data.prompt.allocation_policy
            .max_position_weight_pct}% each</strong
        >.
      </p>
    </section>

    <section>
      <h2>
        Portfolios using this prompt
        <span class="muted">— does it work across models?</span>
      </h2>
      <PortfolioTable rows={data.portfolios} />
    </section>
  {:catch error}
    <div class="error-box">{requestErrorMessage(error)}</div>
  {/await}
{/key}

<style>
  .crumbs {
    font-size: 12.5px;
    color: var(--text-tertiary);
    display: flex;
    gap: 6px;
    margin-bottom: 4px;
  }

  h1 {
    font-size: 22px;
    margin-bottom: 4px;
  }

  h2 {
    font-size: 15px;
    margin: 0 0 10px;
  }

  section {
    margin-top: 20px;
  }

  .prompt-card pre {
    white-space: pre-wrap;
    font-size: 13px;
    line-height: 1.6;
    font-family: var(--font-mono);
    background: var(--bg-inset);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-sm);
    padding: 14px;
    max-height: 480px;
    overflow-y: auto;
  }

  .notes {
    margin-top: 10px;
    font-size: 13px;
  }

  .policy-card p {
    line-height: 1.7;
  }
</style>
