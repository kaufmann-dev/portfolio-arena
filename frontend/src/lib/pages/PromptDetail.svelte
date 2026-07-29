<script lang="ts">
  import { apiJson } from "../api/client";
  import type { MarketDataStatus, PortfolioSummary, PromptOut } from "../api/types";
  import MarketDataWarning from "../components/MarketDataWarning.svelte";
  import PortfolioTable from "../components/PortfolioTable.svelte";
  import { link } from "../stores/router.svelte";

  interface Props {
    slug: string;
  }

  const { slug }: Props = $props();

  interface Payload {
    as_of: string | null;
    market_data_status: MarketDataStatus;
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
    <article class="detail-page">
      <header class="detail-head">
        <nav class="crumbs" aria-label="Breadcrumb">
          <a href="/" onclick={(event) => link(event, "/")}>Leaderboard</a>
          <span aria-hidden="true">/</span>
          <span>Prompt</span>
        </nav>
        <h1>{data.prompt.name}</h1>
        <p class="muted detail-meta">
          <span class="num">{data.prompt.slug}</span>
          <span aria-hidden="true">·</span>
          updated <span class="num">{data.prompt.updated_at.slice(0, 10)}</span>
        </p>
      </header>

      <MarketDataWarning status={data.market_data_status} asOf={data.as_of} />

      <section class="detail-section prompt-card">
        <h2>Strategy</h2>
        <pre>{data.prompt.text}</pre>
        {#if data.prompt.notes}
          <p class="muted notes">{data.prompt.notes}</p>
        {/if}
      </section>

      <section class="detail-section policy-card">
        <h2>Allocation policy</h2>
        <div class="policy-stats">
          <div>
            <span>Position count</span>
            <strong class="num">
              {data.prompt.allocation_policy.derived_min_positions}–{data.prompt.allocation_policy
                .derived_max_positions}
            </strong>
          </div>
          <div>
            <span>Weight per position</span>
            <strong class="num">
              {data.prompt.allocation_policy.min_position_weight_pct}%–{data.prompt.allocation_policy
                .max_position_weight_pct}%
            </strong>
          </div>
        </div>
        <p>Fully invested in USD-denominated equities and ETFs.</p>
      </section>

      <section class="portfolios-section">
        <h2>
          Portfolios using this prompt
          <span class="muted">— does it work across models?</span>
        </h2>
        <PortfolioTable rows={data.portfolios} />
      </section>
    </article>
  {:catch error}
    <div class="error-box" role="alert">{requestErrorMessage(error)}</div>
  {/await}
{/key}

<style>
  .detail-page {
    min-width: 0;
  }

  .detail-head {
    margin-bottom: 28px;
    padding-bottom: 22px;
    border-bottom: 1px solid var(--border-subtle);
  }

  .crumbs {
    margin-bottom: 10px;
    display: flex;
    align-items: center;
    gap: 6px;
    color: var(--text-tertiary);
    font-size: 12px;
  }

  h1 {
    margin: 0 0 8px;
    font-size: clamp(28px, 8vw, 44px);
    line-height: 1.05;
    letter-spacing: -0.04em;
  }

  h2 {
    margin: 0 0 14px;
    font-size: 17px;
    line-height: 1.2;
    letter-spacing: -0.015em;
  }

  .detail-meta {
    display: flex;
    align-items: center;
    gap: 7px;
    flex-wrap: wrap;
  }

  .detail-section {
    margin-top: 0;
    padding: 22px 0;
    border: 0;
    border-bottom: 1px solid var(--border-subtle);
    border-radius: 0;
    background: transparent;
  }

  .portfolios-section {
    margin-top: 32px;
  }

  .prompt-card pre {
    max-height: min(62vh, 560px);
    padding: 14px;
    overflow: auto;
    border: 1px solid var(--border-subtle);
    border-radius: 0;
    background: var(--bg-inset);
    font-family: var(--font-mono);
    font-size: 12px;
    line-height: 1.65;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }

  .notes {
    margin-top: 14px;
    font-size: 13px;
    line-height: 1.6;
  }

  .policy-card p {
    margin-top: 14px;
    color: var(--text-secondary);
    line-height: 1.65;
  }

  .policy-stats {
    display: grid;
    grid-template-columns: 1fr;
    border: 1px solid var(--border-subtle);
    background: var(--border-subtle);
    gap: 1px;
  }

  .policy-stats > div {
    min-height: 78px;
    padding: 12px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    background: var(--bg-base);
  }

  .policy-stats span {
    color: var(--text-secondary);
    font-size: 10.5px;
    font-weight: 650;
    letter-spacing: 0.07em;
    text-transform: uppercase;
  }

  .policy-stats strong {
    font-size: 19px;
  }

  @media (min-width: 560px) {
    .policy-stats {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .prompt-card pre {
      padding: 18px;
      font-size: 13px;
    }
  }
</style>
