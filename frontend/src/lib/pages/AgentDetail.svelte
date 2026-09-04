<script lang="ts">
  import { apiJson } from "../api/client";
  import type { AgentOut, PortfolioRefOut } from "../api/types";
  import PortfolioRefTable from "../components/PortfolioRefTable.svelte";
  import { link } from "../stores/router.svelte";

  interface Props {
    slug: string;
  }

  const { slug }: Props = $props();

  interface Payload {
    agent: AgentOut & { created_at: string };
    portfolios: PortfolioRefOut[];
  }

  function requestErrorMessage(error: unknown): string {
    return error instanceof Error ? error.message : "Could not load this agent.";
  }
</script>

{#key slug}
  {@const request = apiJson<Payload>(`/api/agents/${slug}`)}
  {#await request}
    <div class="loading-block"><span class="spinner" aria-hidden="true"></span> Loading agent…</div>
  {:then data}
    <article class="detail-page">
      <header class="detail-head">
        <nav class="crumbs" aria-label="Breadcrumb">
          <a href="/" onclick={(event) => link(event, "/")}>Leaderboard</a>
          <span aria-hidden="true">/</span>
          <span>Agent</span>
        </nav>
        <h1>
          {data.agent.name}
          {#if data.agent.status === "archived"}<span class="badge warn">archived</span>{/if}
        </h1>
        {#if data.agent.notes}
          <p class="muted notes">{data.agent.notes}</p>
        {/if}
      </header>

      <dl class="execution-grid" aria-label="Agent execution profile">
        <div>
          <dt>Model</dt>
          <dd>{data.agent.model.name}</dd>
        </div>
        <div>
          <dt>Harness</dt>
          <dd>{data.agent.harness?.name ?? "No supported harness"}</dd>
        </div>
        {#if data.agent.execution_model_id}
          <div>
            <dt>Execution ID</dt>
            <dd class="num">{data.agent.execution_model_id}</dd>
          </div>
        {/if}
        {#if data.agent.reasoning_effort}
          <div>
            <dt>Reasoning</dt>
            <dd>{data.agent.reasoning_effort}</dd>
          </div>
        {/if}
      </dl>

      <section class="portfolios-section">
        <h2>
          Portfolios by this agent
          <span class="muted">— does it work across prompts?</span>
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
    margin-bottom: 24px;
  }

  h2 {
    margin: 0 0 14px;
    font-size: 17px;
    line-height: 1.25;
    letter-spacing: -0.015em;
  }

  h2 .muted {
    display: block;
    margin-top: 4px;
    font-size: 13px;
    font-weight: 400;
    letter-spacing: 0;
  }

  .notes {
    max-width: 720px;
    line-height: 1.6;
  }

  .execution-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 1px;
    background: var(--border-subtle);
  }

  .execution-grid > div {
    min-width: 0;
    min-height: 78px;
    padding: 12px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    background: var(--bg-raised);
  }

  dt {
    color: var(--text-secondary);
    font-size: 10.5px;
    font-weight: 650;
    letter-spacing: 0.07em;
    text-transform: uppercase;
  }

  dd {
    margin: 0;
    min-width: 0;
    overflow: hidden;
    color: var(--text-primary);
    font-size: 14px;
    font-weight: 600;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .portfolios-section {
    margin-top: 34px;
  }

  @media (min-width: 760px) {
    .execution-grid {
      grid-template-columns: repeat(4, minmax(0, 1fr));
    }

    h2 .muted {
      display: inline;
      margin: 0;
    }
  }
</style>
