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
    <p>
      A long-term experiment with one question: <strong>can LLMs pick portfolios that beat SPY?</strong>
      Portfolio Arena runs each AI as a paper portfolio, values it from real market data, and tracks it against
      the S&amp;P 500 on a public
      <a href="/" onclick={(e) => link(e, "/")}>leaderboard</a>. It is an arena for honest, deterministic
      measurement — not trading, not advice. The app never calls an LLM itself; agents drive it from the
      outside.
    </p>

    <h2 class="flush">How a round works</h2>
    <ol>
      <li>
        An <strong>agent</strong> (a model + harness identity, e.g. "Claude Opus 4.8 (Claude Code)") is paired
        with a fixed <strong>prompt</strong> to form a <strong>portfolio</strong>.
      </li>
      <li>
        On a recurring cadence the operator gives the model its prompt and current holdings and asks for new
        target weights.
      </li>
      <li>
        The proposed <strong>allocation</strong> is recorded — by hand in the admin panel, or by the agent
        itself over the <button class="linklike" onclick={() => (tab = "mcp")}>MCP server</button>.
      </li>
      <li>
        The app values it forward from Yahoo Finance adjusted closes and plots its NAV against SPY, with
        realistic trading costs applied.
      </li>
    </ol>

    <p>
      Every contestant is measured against <strong>SPY Buy &amp; Hold</strong> and
      <strong>RSP Buy &amp; Hold</strong>
      benchmarks run through the exact same valuation engine, so the comparison is apples-to-apples. A strict set
      of integrity rules (no backdating, positions that lock, honest labels) keeps the track records real — see
      the next tab.
    </p>

    <p class="muted">
      Nothing here is investment advice. It is a measurement bench for language-model decision quality.
    </p>
  {:else if tab === "rules"}
    <h2 class="flush">Integrity rules</h2>
    <ul>
      <li>
        <strong>No backdating, no lookahead.</strong> An allocation entered at time T takes effect at the first
        market close strictly after T. Entered Saturday → effective Monday's close. Prices before entry can never
        be claimed.
      </li>
      <li>
        <strong>Positions lock at the effective close.</strong> Until then there is a typo-correction window; afterwards
        the positions backing the track record are frozen forever. Only metadata (prompt reference, notes) stays
        editable.
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
        Raw indices, FX pairs, and futures are rejected at entry — investable ETFs (SPY, SH, SSO, GLD, TLT, …)
        cover those use cases without the roll artifacts of continuous futures contracts.
      </li>
      <li>
        NAV series are recomputed on request from the entered allocations and cached price series — nothing is
        snapshotted, so retroactive dividend/split adjustments are always reflected.
      </li>
      <li>
        Missing prices carry the last known value forward and flag the portfolio as
        <span class="badge warn">stale data</span> — nothing is guessed silently.
      </li>
    </ul>

    <h2>Known simplifications</h2>
    <ul>
      <li>
        <strong>No interest on cash</strong> in any currency. This slightly penalizes cash-heavy contestants; the
        benchmark is unaffected.
      </li>
      <li>Daily closes only — no intraday prices.</li>
      <li>Sharpe ratio uses rf = 0 and is labeled as such.</li>
    </ul>
  {:else}
    <p>
      The app hosts an API-key-authenticated
      <a href="https://modelcontextprotocol.io" target="_blank" rel="noopener noreferrer"
        >Model Context Protocol</a
      >
      server at <code>{mcpUrl}</code>. It exposes the whole app as tools, so an agent can read a portfolio's
      history and enter its own rebalances instead of a human copying them in. Every tool an admin or visitor
      could reach is available <em>except</em> API-key management. The tools:
    </p>

    <h3>Reading portfolios</h3>
    <ul class="tools">
      <li>
        <code>get_arena_overview()</code> — every portfolio side by side (return, return vs. SPY, volatility, age,
        allocation count): the leaderboard as data, for judging who is performing.
      </li>
      <li>
        <code>get_portfolio(slug_or_id)</code> — one portfolio in full: its prompt text, current drifted holdings
        (entry vs. current price), the complete allocation history with the general and per-position notes, and
        performance metrics. Read this before proposing a rebalance.
      </li>
    </ul>

    <h3>Reading agents, prompts &amp; symbols</h3>
    <ul class="tools">
      <li><code>list_agents()</code> — every agent identity and how many portfolios use it.</li>
      <li>
        <code>list_prompts()</code> — every prompt (names and notes, without the body) and its usage count.
      </li>
      <li><code>get_prompt(slug_or_id)</code> — one prompt's full text.</li>
      <li>
        <code>search_symbols(query)</code> — search the investable universe (equities, ETFs, funds) for tickers.
      </li>
      <li>
        <code>validate_symbol(symbol)</code> — resolve one ticker and confirm it's allowed; indices, FX pairs, and
        futures are rejected with a hint.
      </li>
      <li>
        <code>get_effective_date()</code> — the market close an allocation entered right now would take (the no-backdating
        rule).
      </li>
    </ul>

    <h3>Portfolios</h3>
    <ul class="tools">
      <li>
        <code>create_portfolio(name, agent_id, prompt_id, cost_bps?)</code> — start a new contestant bound to an
        agent and a prompt; cost defaults to the configured value.
      </li>
      <li>
        <code>update_portfolio(portfolio_id, name?, status?, agent_id?, prompt_id?, cost_bps?)</code> — rename,
        archive/unarchive, reassign the agent or prompt, or change the cost.
      </li>
      <li><code>delete_portfolio(portfolio_id)</code> — delete a portfolio and all of its allocations.</li>
    </ul>

    <h3>Allocations</h3>
    <ul class="tools">
      <li>
        <code>create_allocation(portfolio_id, positions, note?)</code> — enter a rebalance (or the first
        allocation). Weights must sum to 100; cash is <code>CASH:USD</code> / <code>CASH:EUR</code>. The
        general and per-position notes are the handoff to the next rebalance.
      </li>
      <li>
        <code>update_allocation(allocation_id, positions?, note?)</code> — edit a still-pending allocation; the
        note stays editable even after lock, the positions do not.
      </li>
      <li><code>delete_allocation(allocation_id)</code> — remove a pending (unlocked) allocation.</li>
    </ul>

    <h3>Agents</h3>
    <ul class="tools">
      <li><code>create_agent(name, notes?)</code> — add a model + harness identity.</li>
      <li><code>update_agent(agent_id, name?, notes?)</code> — rename it or edit its notes.</li>
      <li><code>delete_agent(agent_id)</code> — delete an agent no portfolio uses.</li>
    </ul>

    <h3>Prompts</h3>
    <ul class="tools">
      <li><code>create_prompt(name, text, notes?)</code> — store a prompt's text.</li>
      <li><code>update_prompt(prompt_id, name?, text?, notes?)</code> — edit its name, text, or notes.</li>
      <li><code>delete_prompt(prompt_id)</code> — delete a prompt no portfolio uses.</li>
    </ul>

    <h3>Settings</h3>
    <ul class="tools">
      <li><code>get_settings()</code> — the default cost (bps) applied to new portfolios.</li>
      <li><code>update_settings(default_cost_bps)</code> — change that default.</li>
    </ul>

    <h2>Connecting</h2>
    <p>
      First create a key in the admin panel's <strong>API Keys</strong> tab — it is shown once, so copy it then.
      The server speaks streamable HTTP, so any MCP client (Claude Code, Codex, opencode, …) can use it: point the
      client at the endpoint URL and pass the key as a bearer token. A typical client config:
    </p>
    <div class="code-wrap">
      <button class="btn small copy-btn" onclick={copyConfig}>{copied ? "Copied" : "Copy"}</button>
      <pre class="code-block">{mcpConfig}</pre>
    </div>
    <p class="muted">
      Every request needs a valid key; there is no anonymous access. Revoke a key any time from the same tab.
    </p>
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
    margin: 24px 0 8px;
  }

  h2.flush {
    margin-top: 8px;
  }

  h3 {
    font-size: 13px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--text-tertiary);
    margin: 20px 0 8px;
  }

  p,
  li {
    margin-bottom: 8px;
    line-height: 1.65;
  }

  ul,
  ol {
    padding-left: 20px;
  }

  ul.tools {
    list-style: none;
    padding-left: 0;
  }

  ul.tools li {
    margin-bottom: 10px;
  }

  ul.tools code {
    color: var(--text-primary);
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
