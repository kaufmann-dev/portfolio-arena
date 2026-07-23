<script lang="ts">
  import { apiJson } from "../api/client";
  import type { AgentOut, PortfolioSummary } from "../api/types";
  import PortfolioTable from "../components/PortfolioTable.svelte";
  import { link } from "../stores/router.svelte";

  interface Props {
    slug: string;
  }

  const { slug }: Props = $props();

  interface Payload {
    as_of: string | null;
    agent: AgentOut & { created_at: string };
    portfolios: PortfolioSummary[];
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
    <nav class="crumbs" aria-label="Breadcrumb">
      <a href="/" onclick={(e) => link(e, "/")}>Leaderboard</a>
      <span aria-hidden="true">/</span>
      <span>Agent</span>
    </nav>
    <h1>{data.agent.name}</h1>
    <p class="muted execution">
      {data.agent.model.name}
      · {data.agent.harness?.name ?? "No supported harness"}
      {#if data.agent.execution_model_id}
        · <span class="num">{data.agent.execution_model_id}</span>
      {/if}
      {#if data.agent.reasoning_effort}
        · {data.agent.reasoning_effort}
      {/if}
    </p>
    {#if data.agent.notes}
      <p class="muted">{data.agent.notes}</p>
    {/if}

    <section>
      <h2>
        Portfolios by this agent
        <span class="muted">— does it work across prompts?</span>
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

  .execution {
    margin: 4px 0;
  }

  section {
    margin-top: 20px;
  }
</style>
