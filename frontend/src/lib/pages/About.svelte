<script lang="ts">
  import { link } from "../stores/router.svelte";
</script>

<article class="about">
  <h1>About Portfolio Arena</h1>
  <p>
    A long-term experiment: <strong>can LLMs pick portfolios that beat SPY?</strong> On a recurring
    cadence, the operator prompts AI agents (Claude, Codex, Gemini, …) with portfolio-management
    prompts and enters each agent's proposed allocation here by hand. The app simulates it as a
    paper portfolio from Yahoo Finance data and tracks it against SPY on the
    <a href="/" onclick={(e) => link(e, "/")}>public leaderboard</a>. The app never calls an LLM —
    it is an arena for honest, deterministic measurement.
  </p>

  <h2>Integrity rules</h2>
  <ul>
    <li>
      <strong>No backdating, no lookahead.</strong> An allocation entered at time T takes effect at
      the first market close strictly after T. Entered Saturday → effective Monday's close. Prices
      before entry can never be claimed.
    </li>
    <li>
      <strong>Positions lock at the effective close.</strong> Until then there is a typo-correction
      window; afterwards the positions backing the track record are frozen forever. Only metadata
      (prompt reference, notes, raw response) stays editable.
    </li>
    <li>
      <strong>Benchmarks run through the identical engine.</strong> SPY (primary) and RSP
      (equal-weight, secondary) are system portfolios valued by the same code path — at zero cost,
      since holding SPY really is near-free.
    </li>
    <li>
      <strong>Costs are real.</strong> Every trade pays a flat fee (default 10 bps of traded
      notional). Frictionless tracking would flatter high-turnover AI portfolios against
      buy-and-hold SPY.
    </li>
    <li>
      <strong>Honesty labels.</strong> Portfolios younger than 6 months are marked "too early to
      judge". Sample size is displayed, never hidden.
    </li>
  </ul>

  <h2>Measurement details</h2>
  <ul>
    <li>Long-only equities &amp; ETFs plus multi-currency cash; weights sum to exactly 100%.</li>
    <li>
      Total-return basis: Yahoo <em>adjusted closes</em> (dividends included) for positions and for
      SPY. Base currency is USD.
    </li>
    <li>
      Raw indices, FX pairs, and futures are rejected at entry — investable ETFs (SPY, SH, SSO,
      GLD, TLT, …) cover those use cases without the roll artifacts of continuous futures
      contracts.
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
    <li>
      <strong>No interest on cash</strong> in any currency. This slightly penalizes cash-heavy
      contestants; the benchmark is unaffected.
    </li>
    <li>Daily closes only — no intraday prices.</li>
    <li>Sharpe ratio uses rf = 0 and is labeled as such.</li>
  </ul>

  <p class="muted">
    Nothing here is investment advice. It is a measurement bench for language-model decision
    quality.
  </p>
</article>

<style>
  .about {
    max-width: 760px;
  }

  h1 {
    font-size: 22px;
    margin-bottom: 12px;
  }

  h2 {
    font-size: 16px;
    margin: 24px 0 8px;
  }

  p,
  li {
    margin-bottom: 8px;
    line-height: 1.65;
  }

  ul {
    padding-left: 20px;
  }
</style>
