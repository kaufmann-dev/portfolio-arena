<script lang="ts">
  import { apiJson } from "../api/client";
  import type { PortfolioRefOut, PromptAvailability, PromptOut } from "../api/types";
  import PortfolioRefTable from "../components/PortfolioRefTable.svelte";
  import { link } from "../stores/router.svelte";

  interface Props {
    slug: string;
  }

  const { slug }: Props = $props();

  interface Payload {
    prompt: PromptOut & { created_at: string; updated_at: string };
    portfolios: PortfolioRefOut[];
  }

  function requestErrorMessage(error: unknown): string {
    return error instanceof Error ? error.message : "Could not load this prompt.";
  }

  function promptModeLabel(mode: PromptAvailability): string {
    if (mode === "both") return "Managed + Rebuilt";
    return mode === "managed" ? "Managed only" : "Rebuilt only";
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
          <span class="badge">{promptModeLabel(data.prompt.mode)}</span>
          <span aria-hidden="true">·</span>
          updated <span class="num">{data.prompt.updated_at.slice(0, 10)}</span>
        </p>
      </header>

      {#if data.prompt.managed_text}
        <section class="detail-section prompt-card">
          <h2>Managed strategy</h2>
          <p class="muted strategy-context">
            Used when a managed evaluator receives the portfolio's current state and prior decisions.
          </p>
          <pre>{data.prompt.managed_text}</pre>
        </section>
      {/if}

      {#if data.prompt.rebuilt_text}
        <section class="detail-section prompt-card">
          <h2>Rebuilt strategy</h2>
          <p class="muted strategy-context">
            Used when a rebuilt evaluator creates an independent signal without prior portfolio context.
          </p>
          <pre>{data.prompt.rebuilt_text}</pre>
        </section>
      {/if}

      {#if data.prompt.notes}
        <section class="detail-section notes-card">
          <h2>Shared notes</h2>
          <p class="muted notes">{data.prompt.notes}</p>
        </section>
      {/if}

      {#if data.prompt.allocation_policies.managed}
        {@const policy = data.prompt.allocation_policies.managed}
        <section class="detail-section policy-card">
          <h2>Managed allocation policy</h2>
          <div class="policy-stats">
            <div>
              <span>Position count</span>
              <strong class="num">
                {policy.derived_min_positions}–{policy.derived_max_positions}
              </strong>
            </div>
            <div>
              <span>Weight per position</span>
              <strong class="num">
                {policy.min_position_weight_pct}%–{policy.max_position_weight_pct}%
              </strong>
            </div>
          </div>
          <p>Global policy for every managed portfolio.</p>
        </section>
      {/if}

      {#if data.prompt.allocation_policies.rebuilt}
        {@const policy = data.prompt.allocation_policies.rebuilt}
        <section class="detail-section policy-card">
          <h2>Rebuilt allocation policy</h2>
          <div class="policy-stats">
            <div>
              <span>Position count</span>
              <strong class="num">
                {policy.derived_min_positions}–{policy.derived_max_positions}
              </strong>
            </div>
            <div>
              <span>Weight per position</span>
              <strong class="num">
                {policy.min_position_weight_pct}%–{policy.max_position_weight_pct}%
              </strong>
            </div>
          </div>
          <p>Global policy for every independent rebuilt signal.</p>
        </section>
      {/if}

      <section class="portfolios-section">
        <h2>
          Portfolios using this prompt
          <span class="muted">— does it work across models?</span>
        </h2>
        <PortfolioRefTable rows={data.portfolios} />
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
    background: transparent;
  }

  .portfolios-section {
    margin-top: 32px;
  }

  .prompt-card pre {
    max-height: min(62vh, 560px);
    padding: 14px;
    overflow: auto;
    background: var(--bg-raised);
    font-family: var(--font-mono);
    font-size: 12px;
    line-height: 1.65;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }

  .notes {
    margin: 0;
    font-size: 13px;
    line-height: 1.6;
  }

  .strategy-context {
    margin: -5px 0 12px;
    font-size: 12.5px;
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
    background: var(--border-subtle);
    gap: 1px;
  }

  .policy-stats > div {
    min-height: 78px;
    padding: 12px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    background: var(--bg-raised);
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
