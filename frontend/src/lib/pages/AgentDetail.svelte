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

  let data = $state<Payload | null>(null);
  let error = $state("");

  $effect(() => {
    data = null;
    error = "";
    apiJson<Payload>(`/api/agents/${slug}`, { auth: false })
      .then((payload) => (data = payload))
      .catch((e) => (error = e.message));
  });
</script>

{#if error}
  <div class="error-box">{error}</div>
{:else if !data}
  <div class="loading-block"><span class="spinner" aria-hidden="true"></span> Loading agent…</div>
{:else}
  <nav class="crumbs" aria-label="Breadcrumb">
    <a href="/" onclick={(e) => link(e, "/")}>Leaderboard</a>
    <span aria-hidden="true">/</span>
    <span>Agent</span>
  </nav>
  <h1>{data.agent.name}</h1>
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
{/if}

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
</style>
