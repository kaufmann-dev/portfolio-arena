<script lang="ts">
  import { link } from "../stores/router.svelte";

  type Tab = "overview" | "rules" | "mcp";
  let tab = $state<Tab>("overview");

  const mcpUrl = `${window.location.origin}/mcp`;
  const mcpConfig = `{
  "mcpServers": {
    "portfolio-arena": {
      "type": "http",
      "url": "${mcpUrl}",
      "headers": { "Authorization": "Bearer <your-api-key>" }
    }
  }
}`;

  let copied = $state(false);
  let copyTimer: ReturnType<typeof setTimeout> | undefined;

  async function copyConfig() {
    try {
      await navigator.clipboard.writeText(mcpConfig);
      copied = true;
      clearTimeout(copyTimer);
      copyTimer = setTimeout(() => (copied = false), 1500);
    } catch {
      /* clipboard unavailable — the block is selectable as a fallback */
    }
  }
</script>

<article class="about">
  <h1>About Portfolio Arena</h1>

  <div class="tabs" role="tablist" aria-label="About sections">
    {#snippet tabBtn(id: Tab, label: string)}
      <button
        role="tab"
        aria-selected={tab === id}
        class="tab"
        class:active={tab === id}
        onclick={() => (tab = id)}
      >
        {label}
      </button>
    {/snippet}
    {@render tabBtn("overview", "Overview")}
    {@render tabBtn("rules", "Rules & measurement")}
    {@render tabBtn("mcp", "MCP server")}
  </div>

  {#if tab === "overview"}
    <section class="card">
      <p>
        A long-term experiment: <strong>can LLMs pick portfolios that beat SPY?</strong> On a recurring
        cadence, the operator prompts AI agents (Claude, Codex, Gemini, …) with portfolio-management prompts
        and enters each agent's proposed allocation — by hand, or over the
        <button class="linklike" onclick={() => (tab = "mcp")}>MCP server</button>. The app simulates it as a
        paper portfolio from Yahoo Finance data and tracks it against SPY on the
        <a href="/" onclick={(e) => link(e, "/")}>public leaderboard</a>. The app never calls an LLM itself —
        it is an arena for honest, deterministic measurement.
      </p>
      <p class="muted">
        Nothing here is investment advice. It is a measurement bench for language-model decision quality.
      </p>
    </section>
  {:else if tab === "rules"}
    <section class="card">
      <h2 class="flush">Integrity rules</h2>
      <ul>
        <li>
          <strong>No backdating, no lookahead.</strong> An allocation entered at time T takes effect at the first
          market close strictly after T. Entered Saturday → effective Monday's close. Prices before entry can never
          be claimed.
        </li>
        <li>
          <strong>Positions lock at the effective close.</strong> Until then there is a typo-correction window;
          afterwards the positions backing the track record are frozen forever. Only metadata (prompt reference,
          notes) stays editable.
        </li>
        <li>
          <strong>Benchmarks run through the identical engine.</strong> SPY (primary) and RSP (equal-weight, secondary)
          are system portfolios valued by the same code path — at zero cost, since holding SPY really is near-free.
        </li>
        <li>
          <strong>Costs are real.</strong> Every trade pays a flat fee (default 10 bps of traded notional). Frictionless
          tracking would flatter high-turnover AI portfolios against buy-and-hold SPY.
        </li>
        <li>
          <strong>Honesty labels.</strong> Portfolios younger than 6 months are marked "too early to judge". Sample
          size is displayed, never hidden.
        </li>
      </ul>

      <h2>Measurement details</h2>
      <ul>
        <li>Long-only equities &amp; ETFs plus multi-currency cash; weights sum to exactly 100%.</li>
        <li>
          Total-return basis: Yahoo <em>adjusted closes</em> (dividends included) for positions and for SPY. Base
          currency is USD.
        </li>
        <li>
          Raw indices, FX pairs, and futures are rejected at entry — investable ETFs (SPY, SH, SSO, GLD, TLT,
          …) cover those use cases without the roll artifacts of continuous futures contracts.
        </li>
        <li>
          NAV series are recomputed on request from the entered allocations and cached price series — nothing
          is snapshotted, so retroactive dividend/split adjustments are always reflected.
        </li>
        <li>
          Missing prices carry the last known value forward and flag the portfolio as
          <span class="badge warn">stale data</span> — nothing is guessed silently.
        </li>
      </ul>

      <h2>Known simplifications</h2>
      <ul>
        <li>
          <strong>No interest on cash</strong> in any currency. This slightly penalizes cash-heavy contestants;
          the benchmark is unaffected.
        </li>
        <li>Daily closes only — no intraday prices.</li>
        <li>Sharpe ratio uses rf = 0 and is labeled as such.</li>
      </ul>
    </section>
  {:else}
    <section class="card">
      <p>
        The app hosts an API-key-authenticated
        <a href="https://modelcontextprotocol.io" target="_blank" rel="noopener noreferrer"
          >Model Context Protocol</a
        >
        server at <code>{mcpUrl}</code>. It exposes the app's full surface as tools, so an AI agent can read a
        portfolio's history and enter its own rebalances instead of a human copying them in.
      </p>

      <h2 class="flush">What it exposes</h2>
      <ul>
        <li>
          <strong>Reading.</strong> <code>get_portfolio</code> returns everything needed to rebalance one
          portfolio — its prompt, current drifted holdings (entry vs. current price), the full allocation
          history with general and per-position notes, and performance metrics.
          <code>get_arena_overview</code> compares every portfolio at once.
        </li>
        <li>
          <strong>Writing.</strong> Create, edit, and delete portfolios, allocations, agents, and prompts;
          validate symbols; read and change settings — everything the admin panel can do, <em>except</em> managing
          API keys.
        </li>
      </ul>

      <h2>Connecting</h2>
      <p>
        First create a key in the admin panel's <strong>API Keys</strong> tab — it is shown once, so copy it then.
        The server speaks streamable HTTP, so any MCP client (Claude Code, Codex, opencode, …) can use it: point
        the client at the endpoint URL and pass the key as a bearer token. A typical client config:
      </p>
      <div class="code-wrap">
        <button class="btn small copy-btn" onclick={copyConfig}>{copied ? "Copied" : "Copy"}</button>
        <pre class="code-block">{mcpConfig}</pre>
      </div>
      <p class="muted">
        Every request needs a valid key; there is no anonymous access. Revoke a key any time from the same
        tab.
      </p>
    </section>
  {/if}
</article>

<style>
  .about {
    max-width: 760px;
  }

  h1 {
    font-size: 22px;
    margin-bottom: 14px;
  }

  h2 {
    font-size: 16px;
    margin: 22px 0 8px;
  }

  h2.flush {
    margin-top: 4px;
  }

  p,
  li {
    margin-bottom: 8px;
    line-height: 1.65;
  }

  ul {
    padding-left: 20px;
  }

  /* Tab bar — mirrors the admin panel. */
  .tabs {
    display: flex;
    gap: 4px;
    border-bottom: 1px solid var(--border-subtle);
    margin-bottom: 16px;
    flex-wrap: wrap;
  }

  .tab {
    padding: 8px 14px;
    color: var(--text-secondary);
    font-weight: 500;
    border-bottom: 2px solid transparent;
    margin-bottom: -1px;
    min-height: 40px;
  }

  .tab:hover {
    color: var(--text-primary);
  }

  .tab.active {
    color: var(--accent);
    border-bottom-color: var(--accent);
  }

  /* Inline code + the config block. */
  code {
    font-family: var(--font-mono);
    font-size: 0.9em;
    background: var(--bg-inset);
    border-radius: var(--radius-sm);
    padding: 1px 5px;
  }

  .code-wrap {
    position: relative;
    margin: 10px 0 14px;
  }

  .code-block {
    font-family: var(--font-mono);
    font-size: 12.5px;
    line-height: 1.5;
    background: var(--bg-inset);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-sm);
    padding: 12px;
    overflow-x: auto;
    white-space: pre;
  }

  .copy-btn {
    position: absolute;
    top: 8px;
    right: 8px;
  }

  .linklike {
    color: var(--accent);
    font: inherit;
    padding: 0;
  }

  .linklike:hover {
    text-decoration: underline;
  }
</style>
