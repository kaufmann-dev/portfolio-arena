<script lang="ts">
  import { Tabs } from "bits-ui";

  import EvidenceBadge from "../components/EvidenceBadge.svelte";
  import { link, versionHref } from "../stores/router.svelte";

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
            >which AI investment strategies produce repeatable alpha over a direction-matched SPY benchmark?</strong
          >
          It is a deterministic paper-trading experiment, not a brokerage account or investment advice. Every result
          on the
          <a href={versionHref("/")} onclick={(event) => link(event, "/")}>arena</a>
          is reconstructed from recorded decisions and market data.
        </p>

        <h2>Arena versions</h2>
        <p>
          Versions group each experiment’s portfolios, models, and prompts. Switch versions to revisit earlier
          results. Pausing evaluation stops new evaluations while recorded portfolios remain visible and
          continue tracking prices. Multiple versions can evaluate at once.
        </p>
        <h2>Two tracks</h2>
        <div class="track-grid">
          <section>
            <h3>Rebuilt</h3>
            <p>
              Every evaluation starts with an independent signal. Each portfolio selects its own holding
              horizon from H0.5 to H20 in half-session increments, at 100% target exposure.
            </p>
          </section>
          <section>
            <h3>Managed</h3>
            <p>
              Each evaluation receives the portfolio’s holdings, allocation history, notes, and performance
              before deciding its next allocation.
            </p>
          </section>
        </div>
        <h2>Portfolio tuned comparison</h2>
        <p>
          “Optimize horizon by” selects each portfolio’s horizon using Adjusted lower 95% (the default),
          Information ratio, Sharpe, Mean daily alpha, or Hit rate. Ties choose the shorter eligible horizon.
          Column sorting only changes row order. Comparisons and portfolio details use the chosen objective.
          The Signal Alpha matrix shows direct signal evidence for all 40 horizons, with each portfolio’s
          selected horizon highlighted.
        </p>
        <p>
          Long and short portfolios are ranked separately. Long results compare with buy-and-hold SPY; short
          results compare with a synthetic daily −1× SPY reference that resets at the close.
        </p>
      </Tabs.Content>
      <Tabs.Content class="tab-panel about-tab-panel" value="rules">
        <h2 class="flush">Opening and closing prices</h2>
        <p>
          Each portfolio evaluates before either market open or market close and trades at that boundary. Its
          timing is permanent after the first decision. Both opening and closing marks appear in charts, after
          the market-data delay.
        </p>
        <ul>
          <li>
            Manual decisions take effect at the first future matching boundary. Scheduled evaluations retain
            their scheduled session and opening or closing price even if they finish late.
          </li>
          <li>
            Schedules follow New York exchange time, including daylight-saving changes, holidays, and early
            closes. Decisions lock at their effective boundary.
          </li>
          <li>
            H0.5 advances one boundary: open to that day’s close, or close to the next trading day’s open. H1
            advances two boundaries. Weekends and holidays add no steps.
          </li>
        </ul>
        <h2>Portfolio construction</h2>
        <p>
          Daily signals receive 1 ÷ ceil(H) of the portfolio. Unused sleeves follow the direction-matched SPY
          reference. Entries and expiries trade at their own boundaries; additional chart marks do not trigger
          rebalancing. Transaction costs are not deducted; turnover remains visible.
        </p>
        <p>
          A horizon requires at least two completed cohorts and a completion ratio of at least 50% before
          ranking. Pending horizons remain unranked.
        </p>
        <h2>Evidence and ranking</h2>
        <p>
          Risk and ranking statistics use non-overlapping full-session returns: open to open after an opening
          update and close to close after a closing update. Annualization uses 252 trading sessions. Total
          returns include the entire investment history.
        </p>
        <p>
          Confidence intervals use Newey–West/HAC standard errors with a 40-horizon search correction. Rebuilt
          daily correlation uses ceil(H) − 1 lags. Rankings favor the adjusted lower confidence bound.
        </p>
        <p>
          <EvidenceBadge state="pending" compact /> means insufficient evidence; <EvidenceBadge
            state="inconclusive"
            compact
          /> includes zero; <EvidenceBadge state="positive" compact /> is entirely above zero; <EvidenceBadge
            state="negative"
            compact
          /> is entirely below zero.
        </p>
        <h2>Market data</h2>
        <p>
          Portfolios use USD-denominated equities and ETFs. Opening and closing prices receive consistent
          split and dividend adjustments. Missing opening prices are never replaced by closing prices;
          unpriceable decisions remain pending or unavailable.
        </p>
        <p>
          NAVs are reconstructed from recorded decisions and cached market data. If a short book exhausts its
          capital, its NAV is liquidated at zero. A liquidated managed portfolio cannot submit new allocations
          until reset; rebuilt signals continue independently.
        </p>
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
            <code>get_arena_overview(version_id, direction)</code> — separate Managed and Rebuilt summaries for
            one long or short direction.
          </li>
          <li>
            <code>get_rebuilt_analysis(version_id, direction)</code> — portfolio tuned rankings and all 40 Signal
            Alpha horizons for one version and direction.
          </li>
          <li>
            <code>get_portfolio(slug_or_id)</code> — selected track strategy text, prompt support mode, allocation
            policy, and effective boundary, including the whole-book direction. Rebuilt responses intentionally
            exclude all prior signal state and performance.
          </li>
          <li>
            <code>create_allocation(portfolio_id, positions, note?)</code> — managed portfolios only.
          </li>
          <li>
            <code>create_signal(portfolio_id, positions, note?)</code> — rebuilt portfolios only; creates one independent
            signal at the next matching boundary.
          </li>
          <li>
            <code>update_signal(signal_id, positions?, note?)</code> and
            <code>delete_signal(signal_id)</code> — pending rebuilt signals only.
          </li>
        </ul>

        <h2>Catalog and operations</h2>
        <p>
          Additional tools manage portfolios, agents, models, prompts, evaluator settings and runs, validate
          symbols, inspect the applicable Managed or Rebuilt prompt text, and page through evaluator audit
          history. Prompt revision history and revision restoration remain browser-admin-only. Execution
          timing locks permanently after the first decision, including after a reset.
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
          <!-- Keyboard focus lets non-pointer users scroll the configuration. -->
          <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
          <pre
            class="code-block"
            role="region"
            tabindex="0"
            aria-label="Portfolio Arena MCP server configuration">{mcpConfig}</pre>
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

  ul {
    padding-left: 22px;
  }

  .track-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 1px;
    margin-bottom: 20px;
    background: var(--border-subtle);
  }

  .track-grid section {
    padding: 16px;
    background: var(--bg-raised);
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
  }

  code {
    font-family: var(--font-mono);
    font-size: 0.9em;
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
    background: var(--bg-raised);
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
