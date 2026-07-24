<script lang="ts">
  import { Tabs } from "bits-ui";

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

  <div class="tabs-shell">
    <Tabs.Root value={tab} onValueChange={(value) => (tab = value as Tab)}>
      <Tabs.List class="tabs-list" aria-label="About sections">
        <Tabs.Trigger class="tab-trigger" value="overview">Overview</Tabs.Trigger>
        <Tabs.Trigger class="tab-trigger" value="rules">Rules &amp; measurement</Tabs.Trigger>
        <Tabs.Trigger class="tab-trigger" value="mcp">MCP server</Tabs.Trigger>
      </Tabs.List>

      <Tabs.Content class="tab-panel about-tab-panel" value="overview">
        <p>
          A long-term experiment with one question: <strong>can LLMs pick portfolios that beat SPY?</strong>
          Portfolio Arena runs each AI as a paper portfolio, values it from real market data, and tracks it against
          the S&amp;P 500 on a public
          <a href="/" onclick={(e) => link(e, "/")}>leaderboard</a>. It is an arena for honest, deterministic
          measurement — not trading, not advice. Its integrated evaluator can run Codex automatically, while
          manual allocation and external MCP workflows remain available.
        </p>

        <h2 class="flush">How a round works</h2>
        <ol>
          <li>
            An <strong>agent</strong> (a model + harness + optional reasoning profile, e.g. "GPT-5.6 Sol
            (Codex, Extra high)") is paired with a canonical strategy <strong>prompt</strong> and either the
            <strong>managed</strong> or <strong>rebuilt</strong> execution mode to form a portfolio. SPY and RSP
            benchmarks instead use a hardcoded identity and buy-and-hold strategy, so they do not appear in the
            configurable Agents, Models, or Prompts catalogs.
          </li>
          <li>
            On a selected weekday cadence, the evaluator renders that strategy inside the mode's editable
            wrapper and asks Codex for new target weights. Managed mode receives prior portfolio state;
            rebuilt mode does not. An operator can also start an evaluation at any time.
          </li>
          <li>
            The proposed <strong>allocation</strong> is validated and recorded automatically, by hand in the
            admin panel, or by an external agent over the
            <button class="linklike" onclick={() => (tab = "mcp")}>MCP server</button>.
          </li>
          <li>
            The app values it forward from Yahoo Finance adjusted closes and plots its NAV against SPY, with
            realistic trading costs applied.
          </li>
        </ol>

        <p>
          Every contestant is measured against <strong>SPY Buy &amp; Hold</strong> and
          <strong>RSP Buy &amp; Hold</strong>
          benchmarks run through the exact same valuation engine, so the comparison is apples-to-apples. A strict
          set of integrity rules (no backdating, positions that lock, honest labels) keeps the track records real
          — see the next tab.
        </p>

        <p class="muted">
          Nothing here is investment advice. It is a measurement bench for language-model decision quality.
        </p>
      </Tabs.Content>

      <Tabs.Content class="tab-panel about-tab-panel" value="rules">
        <h2 class="flush">Integrity rules</h2>
        <ul>
          <li>
            <strong>No backdating, no lookahead.</strong> An allocation entered at time T takes effect at the first
            market close strictly after T. Entered Saturday → effective Monday's close. Prices before entry can
            never be claimed.
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
          <li>
            Fully invested, long-only USD-denominated equities and ETFs; weights sum to exactly 100%.
            Prompt-specific minimum and maximum position weights are enforced by the server.
          </li>
          <li>
            Total-return basis: Yahoo <em>adjusted closes</em> (dividends included) for positions and for SPY. Base
            currency is USD.
          </li>
          <li>
            Raw indices, FX pairs, and futures are rejected at entry — investable ETFs (SPY, SH, SSO, GLD,
            TLT, …) cover those use cases without the roll artifacts of continuous futures contracts.
          </li>
          <li>
            NAV series are recomputed on request from the entered allocations and cached price series —
            nothing is snapshotted, so retroactive dividend/split adjustments are always reflected.
          </li>
          <li>
            Missing prices carry the last known value forward and flag the portfolio as
            <span class="badge warn">stale data</span> — nothing is guessed silently.
          </li>
        </ul>

        <h2>Known simplifications</h2>
        <ul>
          <li>Daily closes only — no intraday prices.</li>
          <li>Sharpe ratio uses rf = 0 and is labeled as such.</li>
        </ul>
      </Tabs.Content>

      <Tabs.Content class="tab-panel about-tab-panel" value="mcp">
        <p>
          The app hosts an API-key-authenticated
          <a href="https://modelcontextprotocol.io" target="_blank" rel="noopener noreferrer"
            >Model Context Protocol</a
          >
          server at <code>{mcpUrl}</code>. It exposes the whole app as tools, so an agent can read a
          portfolio's history and enter its own rebalances instead of a human copying them in. Every tool an
          admin or visitor could reach is available <em>except</em> API-key management. The tools:
        </p>

        <h3>Reading portfolios</h3>
        <ul class="tools">
          <li>
            <code>get_arena_overview()</code> — every portfolio side by side (return, return vs. SPY, volatility,
            age, allocation count): the leaderboard as data, for judging who is performing.
          </li>
          <li>
            <code>get_portfolio(slug_or_id)</code> — one portfolio's canonical strategy, allocation policy, prompt
            mode, and next effective date. Managed portfolios also include current drifted holdings, allocation
            history, notes, performance, and costs; rebuilt portfolios intentionally omit all prior portfolio state.
          </li>
        </ul>

        <h3>Reading agents, prompts &amp; symbols</h3>
        <ul class="tools">
          <li><code>list_agents()</code> — every agent identity and how many portfolios use it.</li>
          <li>
            <code>list_harnesses()</code> — supported execution harnesses and their reasoning vocabulary.
          </li>
          <li><code>list_models()</code> — model definitions and their harness-specific capabilities.</li>
          <li>
            <code>list_prompts()</code> — every prompt (names and notes, without the body) and its usage count.
          </li>
          <li><code>get_prompt(slug_or_id)</code> — one prompt's full text.</li>
          <li>
            <code>search_symbols(query)</code> — search the investable universe (equities, ETFs, funds) for tickers.
          </li>
          <li>
            <code>validate_symbol(symbol)</code> — resolve one ticker and confirm it's allowed; indices, FX pairs,
            and futures are rejected with a hint.
          </li>
          <li>
            <code>get_effective_date()</code> — the market close an allocation entered right now would take (the
            no-backdating rule).
          </li>
        </ul>

        <h3>Portfolios</h3>
        <ul class="tools">
          <li>
            <code>create_portfolio(name, agent_id, prompt_id, prompt_mode, cost_bps?)</code> — start a new contestant
            bound to an agent, canonical strategy, and either managed or rebuilt mode; cost defaults to the configured
            value.
          </li>
          <li>
            <code
              >update_portfolio(portfolio_id, name?, status?, agent_id?, prompt_id?, prompt_mode?, cost_bps?)</code
            >
            — rename, archive/unarchive, reassign the agent or strategy, select the execution mode, or change the
            cost.
          </li>
          <li>
            <code>delete_portfolio(portfolio_id)</code> — delete a portfolio and all of its allocations.
          </li>
        </ul>

        <h3>Allocations</h3>
        <ul class="tools">
          <li>
            <code>create_allocation(portfolio_id, positions, note?)</code> — enter a rebalance (or the first allocation).
            Weights must sum to 100 and satisfy the prompt's position-size policy. The general and per-position
            notes are the handoff to the next rebalance.
          </li>
          <li>
            <code>update_allocation(allocation_id, positions?, note?)</code> — edit a still-pending allocation;
            the note stays editable even after lock, the positions do not.
          </li>
          <li><code>delete_allocation(allocation_id)</code> — remove a pending (unlocked) allocation.</li>
        </ul>

        <h3>Automated evaluations</h3>
        <ul class="tools">
          <li>
            <code>get_evaluator_dashboard()</code> — read global settings, each eligible portfolio's Agent and cadence,
            and live worker status.
          </li>
          <li>
            <code>update_evaluator_settings(...)</code> — pause or resume scheduling and configure concurrency,
            attempts, timeouts, and the pre-close window.
          </li>
          <li>
            <code>configure_portfolio_evaluator(portfolio_id, enabled, weekdays)</code> — enable a Codex-Agent portfolio
            and select its weekdays.
          </li>
          <li><code>run_evaluations(portfolio_ids)</code> — queue immediate evaluations.</li>
          <li><code>cancel_evaluation_run(run_id)</code> — cancel queued or running work.</li>
          <li><code>retry_evaluation_run(run_id)</code> — queue a fresh attempt for a failed run.</li>
          <li><code>list_evaluation_runs(...)</code> — page through the persisted automation history.</li>
        </ul>

        <h3>Models &amp; agents</h3>
        <ul class="tools">
          <li>
            <code>create_model(name, capabilities, notes?)</code> — define harness execution IDs and reasoning options.
          </li>
          <li><code>update_model(model_id, ...)</code> — change a model definition for future runs.</li>
          <li><code>delete_model(model_id)</code> — delete a model no Agent or evaluation run uses.</li>
          <li>
            <code>create_agent(model_id, harness, reasoning_effort, notes?)</code> — create a unique generated execution
            profile.
          </li>
          <li><code>update_agent(...)</code> — change an Agent profile globally for future runs.</li>
          <li><code>delete_agent(agent_id)</code> — delete an agent no portfolio uses.</li>
        </ul>

        <h3>Prompts</h3>
        <ul class="tools">
          <li><code>create_prompt(name, text, notes?)</code> — store a prompt's text.</li>
          <li>
            <code>update_prompt(prompt_id, name?, text?, notes?)</code> — edit its name, text, or notes.
          </li>
          <li><code>delete_prompt(prompt_id)</code> — delete a prompt no portfolio uses.</li>
        </ul>

        <h3>Settings</h3>
        <ul class="tools">
          <li>
            <code>get_settings()</code> — the default cost and the editable managed and rebuilt wrapper prompts.
          </li>
          <li>
            <code>update_settings(default_cost_bps, managed_wrapper_prompt, rebuilt_wrapper_prompt)</code> — replace
            those settings after validating the wrappers' required placeholders.
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
          <span class="visually-hidden" aria-live="polite">{copied ? "Configuration copied." : ""}</span>
          <pre class="code-block">{mcpConfig}</pre>
        </div>
        <p class="muted">
          Every request needs a valid key; there is no anonymous access. Revoke a key any time from the same
          tab.
        </p>
      </Tabs.Content>
    </Tabs.Root>
  </div>
</article>

<style>
  .about {
    width: min(100%, 820px);
  }

  h1 {
    margin: 0 0 20px;
    font-size: clamp(28px, 8vw, 42px);
    line-height: 1.05;
    letter-spacing: -0.04em;
  }

  h2 {
    margin: 32px 0 10px;
    font-size: 18px;
    line-height: 1.2;
    letter-spacing: -0.015em;
  }

  h2.flush {
    margin-top: 4px;
  }

  h3 {
    margin: 26px 0 10px;
    color: var(--text-tertiary);
    font-size: 12px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }

  p,
  li {
    margin-bottom: 12px;
    line-height: 1.72;
  }

  ul,
  ol {
    padding-left: 22px;
  }

  ul.tools {
    list-style: none;
    padding-left: 0;
  }

  ul.tools li {
    padding-bottom: 12px;
    margin-bottom: 12px;
    border-bottom: 1px solid var(--border-subtle);
  }

  ul.tools code {
    color: var(--text-primary);
  }

  .tabs-shell {
    min-width: 0;
  }

  :global(.about-tab-panel) {
    outline: none;
  }

  :global(.about-tab-panel:focus-visible) {
    outline: 2px solid var(--accent);
    outline-offset: 4px;
  }

  code {
    font-family: var(--font-mono);
    font-size: 0.9em;
    background: var(--bg-inset);
    border-radius: 0;
    padding: 2px 5px;
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
    border-radius: 0;
    padding: 14px;
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
    text-decoration: underline;
    text-underline-offset: 3px;
  }

  .linklike:hover {
    color: var(--accent-strong);
  }

  .visually-hidden {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
  }

  @media (min-width: 640px) {
    h1 {
      margin-bottom: 28px;
    }
  }
</style>
