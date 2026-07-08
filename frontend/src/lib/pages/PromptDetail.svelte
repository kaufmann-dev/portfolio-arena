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

  let data = $state<Payload | null>(null);
  let error = $state("");

  $effect(() => {
    data = null;
    error = "";
    apiJson<Payload>(`/api/prompts/${slug}`, { auth: false })
      .then((payload) => (data = payload))
      .catch((e) => (error = e.message));
  });
</script>

{#if error}
  <div class="error-box">{error}</div>
{:else if !data}
  <div class="loading-block"><span class="spinner" aria-hidden="true"></span> Loading prompt…</div>
{:else}
  <nav class="crumbs" aria-label="Breadcrumb">
    <a href="/" onclick={(e) => link(e, "/")}>Leaderboard</a>
    <span aria-hidden="true">/</span>
    <span>Prompt</span>
  </nav>
  <h1>{data.prompt.name}</h1>
  <p class="muted">
    <span class="num">{data.prompt.slug}</span> · updated <span class="num">{data.prompt.updated_at.slice(0, 10)}</span>
  </p>

  <section class="card prompt-card">
    <h2>Prompt text</h2>
    <pre>{data.prompt.text}</pre>
    {#if data.prompt.notes}
      <p class="muted notes">{data.prompt.notes}</p>
    {/if}
  </section>

  <section>
    <h2>
      Portfolios using this prompt
      <span class="muted">— does it work across models?</span>
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
</style>
