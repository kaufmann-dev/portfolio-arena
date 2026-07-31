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

  async function copyConfig(): Promise<void> {
    try {
      await navigator.clipboard.writeText(mcpConfig);
      copied = true;
      clearTimeout(copyTimer);
      copyTimer = setTimeout(() => (copied = false), 1500);
    } catch {
      // The selectable configuration block remains available when clipboard access is unavailable.
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
          Portfolio Arena asks a focused question: <strong
            >which AI investment strategies produce repeatable alpha over SPY?</strong
          >
          It is a deterministic paper-trading experiment, not a brokerage account or investment advice. Every result
          on the
          <a href="/" onclick={(event) => link(event, "/")}>arena</a>
          is reconstructed from recorded decisions and market data.
        </p>

        <h2 class="flush">Two separate tracks</h2>
        <div class="track-grid">
          <section>
            <span>Default track</span>
            <h3>Rebuilt</h3>
            <p>
              Each evaluation produces a complete, independent signal without seeing prior signals, holdings,
              notes, performance, turnover, or costs. Signals can arrive every trading day. The arena tests
              every holding period from 1–20 sessions and every total exposure from 10–100%.
            </p>
          </section>
          <section>
            <span>Stateful track</span>
            <h3>Managed</h3>
            <p>
              Each evaluation receives the portfolio's current state and can rebalance it. This preserves the
              long-running managed experiment while ranking its SPY-relative daily alpha with the same
              evidence-first standard.
            </p>
          </section>
        </div>

        <h2>Three rebuilt views</h2>
        <ol>
          <li>
            <strong>Common policy</strong> selects one holding period and exposure level from an equal-weight meta-portfolio,
            then applies that pair to every eligible rebuilt portfolio.
          </li>
          <li>
            <strong>Portfolio tuned</strong> selects the best policy separately for each portfolio.
          </li>
          <li>
            <strong>Signal Alpha</strong> compares the direct completed-signal evidence at a selected holding period
            and exposes the full 20-horizon matrix.
          </li>
        </ol>

        <p>
          SPY is the sole benchmark. It appears as a pinned synthetic reference row, so benchmark identity and
          history cannot be mistaken for an AI portfolio stored in the database.
        </p>

        <p class="muted">
          Nothing here is investment advice. The project measures language-model decision quality under
          explicit rules.
        </p>
      </Tabs.Content>

      <Tabs.Content class="tab-panel about-tab-panel" value="rules">
        <h2 class="flush">Signal and allocation timing</h2>
        <ul>
          <li>
            A browser or MCP submission takes effect at the first market close strictly after the server
            receives it.
          </li>
          <li>
            Integrated scheduled evaluations target their configured trading session. Market holidays shift
            weekday schedules to the next trading session.
          </li>
          <li>
            Managed allocation positions lock at their effective close. A rebuilt signal becomes completely
            immutable at that close; pending entries can be corrected or deleted.
          </li>
        </ul>

        <h2>Rebuilt cohort construction</h2>
        <ul>
          <li>
            A signal held for H sessions contributes <code>exposure ÷ H</code> percent to each active daily cohort.
            Any unused sleeve stays in SPY.
          </li>
          <li>
            Warm-up days and missing signal sessions use SPY. Active cohorts are marked to market only through
            observed sessions; future results are never assumed.
          </li>
          <li>
            At every market close, the aggregate target is recomputed and rebalanced from the active
            exposure/H cohort sleeves. Any unused allocation remains in SPY.
          </li>
          <li>
            Net results apply the configured transaction cost to actual aggregate turnover from that
            rebalancing, including changes to the SPY sleeve. Gross results omit those costs.
          </li>
          <li>
            A horizon becomes eligible after at least two completed cohorts and a completion ratio of at least
            50%. A portfolio is admitted to the Common-policy meta-portfolio only after H20 passes that gate;
            until then it remains in H20 incubation.
          </li>
        </ul>

        <h2>Evidence and ranking</h2>
        <ul>
          <li>
            Signal Alpha converts a signal's total return relative to SPY into a comparable mean daily alpha
            for its holding period. Constructed policies use strategy daily return minus SPY daily return.
          </li>
          <li>
            Confidence intervals use Newey–West/HAC standard errors. Rebuilt horizons use lag H−1, while
            managed portfolios use an automatic bounded bandwidth.
          </li>
          <li>
            The 95% intervals use fixed Bonferroni families: 20 tests for Canonical and Signal Alpha, and 200
            tests for optimized policy searches. Rankings use the adjusted lower endpoint, rewarding robust
            evidence rather than the largest point estimate.
          </li>
          <li>
            <span class="evidence pending">Pending</span> lacks enough eligible observations;
            <span class="evidence inconclusive">Inconclusive</span> includes zero;
            <span class="evidence positive">Positive</span> is entirely above zero; and
            <span class="evidence negative">Negative</span> is entirely below zero.
          </li>
        </ul>

        <h2>Market-data rules</h2>
        <ul>
          <li>Long-only, fully invested USD-denominated equities and ETFs; signal weights sum to 100%.</li>
          <li>Massive split-adjusted daily closes and dividend adjustments; base currency USD.</li>
          <li>
            NAVs are recomputed on request from immutable inputs and cached price series—nothing is
            snapshotted.
          </li>
          <li>
            Missing prices carry forward with visible stale-data and frozen-symbol flags; nothing is guessed
            silently.
          </li>
          <li>Daily closes only. Sharpe ratios use a zero risk-free rate and are labeled accordingly.</li>
        </ul>
      </Tabs.Content>

      <Tabs.Content class="tab-panel about-tab-panel" value="mcp">
        <p>
          Portfolio Arena hosts an API-key-authenticated
          <a href="https://modelcontextprotocol.io" target="_blank" rel="noopener noreferrer">
            Model Context Protocol
          </a>
          server at <code>{mcpUrl}</code>. It exposes the application surface except API-key management.
        </p>

        <h2 class="flush">Core portfolio tools</h2>
        <ul class="tools">
          <li>
            <code>get_arena_overview()</code> — separate Managed and Rebuilt summaries with SPY-relative evidence.
          </li>
          <li>
            <code>get_rebuilt_analysis(...)</code> — Common, Portfolio tuned, or Signal Alpha results for a chosen
            objective, cost basis, and valid horizon.
          </li>
          <li>
            <code>get_portfolio(slug_or_id)</code> — canonical strategy, allocation policy, mode, and effective
            date. Rebuilt responses intentionally exclude all prior signal state and performance.
          </li>
          <li>
            <code>create_allocation(portfolio_id, positions, note?)</code> — managed portfolios only.
          </li>
          <li>
            <code>create_signal(portfolio_id, positions, note?)</code> — rebuilt portfolios only; creates one independent
            next-session signal.
          </li>
          <li>
            <code>update_signal(signal_id, positions?, note?)</code> and
            <code>delete_signal(signal_id)</code> — pending rebuilt signals only.
          </li>
        </ul>

        <h2>Catalog and operations</h2>
        <p>
          Additional tools manage portfolios, agents, models, prompts, evaluator settings and runs, validate
          symbols, inspect prompt text, and page through evaluator audit history. Mode changes require an
          empty history; reset the portfolio before switching tracks.
        </p>

        <h2>Connecting</h2>
        <p>
          Create a key in the admin panel's <strong>API keys</strong> tab, copy it when shown, and pass it as a
          bearer token to the streamable HTTP endpoint:
        </p>
        <div class="code-wrap">
          <button class="btn small copy-btn" type="button" onclick={copyConfig}>
            {copied ? "Copied" : "Copy"}
          </button>
          <span class="visually-hidden" aria-live="polite">{copied ? "Configuration copied." : ""}</span>
          <pre class="code-block">{mcpConfig}</pre>
        </div>
        <p class="muted">
          Every MCP request requires a valid key. Revoke keys at any time from the same tab.
        </p>
      </Tabs.Content>
    </Tabs.Root>
  </div>
</article>

<style>
  .about {
    width: min(100%, 860px);
  }

  h1 {
    margin: 0 0 20px;
    font-size: clamp(28px, 8vw, 42px);
    letter-spacing: -0.04em;
  }

  h2 {
    margin: 32px 0 10px;
    font-size: 18px;
  }

  h2.flush {
    margin-top: 4px;
  }

  h3 {
    margin: 4px 0 8px;
    font-size: 18px;
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

  .track-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 1px;
    border: 1px solid var(--border-subtle);
    background: var(--border-subtle);
  }

  .track-grid section {
    padding: 16px;
    background: var(--bg-base);
  }

  .track-grid span {
    color: var(--text-tertiary);
    font-size: 9px;
    font-weight: 750;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  .track-grid p {
    margin: 0;
    color: var(--text-secondary);
    font-size: 12px;
  }

  ul.tools {
    padding-left: 0;
    list-style: none;
  }

  ul.tools li {
    padding-bottom: 10px;
    border-bottom: 1px solid var(--border-subtle);
  }

  code {
    font-family: var(--font-mono);
    font-size: 0.9em;
  }

  .evidence {
    display: inline-flex;
    min-height: 20px;
    align-items: center;
    padding: 2px 5px;
    border: 1px solid var(--border-strong);
    font-size: 8px;
    font-weight: 750;
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }

  .evidence.positive {
    color: var(--pos);
  }

  .evidence.negative {
    color: var(--neg);
  }

  .evidence.inconclusive {
    color: var(--warn);
  }

  .evidence.pending {
    color: var(--text-tertiary);
    border-style: dashed;
  }

  .code-wrap {
    position: relative;
  }

  .copy-btn {
    position: absolute;
    top: 10px;
    right: 10px;
    z-index: 1;
  }

  .code-block {
    max-width: 100%;
    padding: 18px;
    overflow-x: auto;
    border: 1px solid var(--border-subtle);
    background: var(--bg-inset);
    font-family: var(--font-mono);
    font-size: 11px;
    line-height: 1.6;
  }

  @media (max-width: 620px) {
    .track-grid {
      grid-template-columns: 1fr;
    }
  }
</style>
