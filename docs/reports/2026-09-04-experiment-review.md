# Portfolio Arena: experiment review

Written September 9 from the September 8 MCP analysis. Historical performance covers
**August 3–September 4, 2026: 24 daily observations** across 72 ordinary portfolios,
18 per category. Four Arena Synthesis portfolios are excluded from these averages.

**The strongest results came from specific prompt-and-mode combinations.** Business
Quality and Secular Change led managed portfolios; Contrarian Misperception stood
out in rebuilt long, and Regime Interpreter in rebuilt short. The experiment did
not establish a broad, repeatable advantage over SPY.

Long portfolios generally made money but underperformed their benchmark on average.
Short portfolios slightly outperformed their benchmark while still losing money.
Long SPY returned **+1.65%**; synthetic Short SPY returned **−1.71%**.

| Category             | Mean net return | Mean benchmark excess | Beat benchmark | Profitable |
| -------------------- | --------------: | --------------------: | -------------: | ---------: |
| Managed long         |          +1.15% |              −0.50 pp |           9/18 |      11/18 |
| Rebuilt long, Tuned  |          +0.28% |              −1.37 pp |           7/18 |      11/18 |
| Managed short        |          −1.62% |              +0.09 pp |           8/18 |       6/18 |
| Rebuilt short, Tuned |          −1.36% |              +0.35 pp |          12/18 |       8/18 |

These are equal-weight averages; excess is in percentage points. Managed long beat
rebuilt for 12/18 prompts, averaging a 0.88-point advantage. Rebuilt short beat
managed for 10/18 prompts, averaging a smaller 0.26-point advantage.

Managed portfolios retain holdings, notes, and history. Rebuilt signals are independent,
with simulated exposure spread across holding-period cohorts and unused sleeves in
the matching SPY reference. **Tuned selects holding periods on the history being scored**;
its results are retrospective. Common had no eligible ordinary portfolios. Managed
positions were capped at 25%; rebuilt signals could hold one security at 100%.
Consequently, this comparison does not isolate the effect of memory.

The most promising prompts differed by mode:

- **Managed long:** Business Quality (+8.93%), Unrestricted Selection (+7.70%), and
  Secular Change (+7.60%) led, each with maximum drawdown below 4%. Stress Test
  (+5.56%) and Policy and Geopolitical Anticipation (+5.72%) also performed well.
- **Managed short:** Business Quality (+9.64%), Capital Cycle (+8.86%), and Secular
  Change (+8.67%) were strongest. Fresh Information Repricing (+6.45%) and Stress
  Test (+2.61%) also made money. Business Quality and Secular Change kept drawdowns
  below 4%; Fresh Information Repricing suffered an 8.29% drawdown.
- **Rebuilt long:** Contrarian Misperception, Business Quality, Stress Test, and
  Catalyst showed positive signals across many horizons. Their H5 mean daily-equivalent
  alpha was respectively +0.48%, +0.24%, +0.21%, and +0.21%. Fresh Information
  Repricing was an exploratory fifth choice: +0.73% at H1 but −0.05% at H5 suggests
  testing a short holding period. Its Tuned net return was −0.30%.
- **Rebuilt short:** Regime Interpreter and Stress Test showed broad positive
  patterns; Capital Cycle looked stronger at shorter horizons. Constraint Economics
  improved at longer horizons, reaching +0.31% daily-equivalent alpha at H10.
  Narrative Lifecycle was a reasonable fifth candidate, positive across H1–H13.
  Regime Interpreter received positive evidence labels at H11–H15.

H5 means five sessions. Signal alpha is **gross daily-equivalent relative performance**,
not realized portfolio daily return. Horizons contain different completed cohorts;
apparent improvement or decay needs confirmation on matched cohorts.

**Operating Inflection and Economic Read-Through underperformed their benchmarks
in all four categories.** Barebones and Unrestricted Selection were particularly
weak managed shorts. Fresh Information Repricing's rebuilt short had a severe
negative tail despite its successful managed short. Managed success therefore
does not justify copying a prompt into every category.

The results support these hypotheses for improving performance:

1. **Match the method to the thesis.** Managing durable quality and secular theses
   looks promising; independent selection favors Contrarian Misperception and
   Catalyst longs. These associations do not establish causation.
2. **Require economic evidence, valuation discipline, and explicit invalidation.**
   Business Quality and Stress Test combine these requirements with relatively
   consistent results. Elaborate instructions alone did not ensure success.
3. **Treat short risk and turnover as central.** Successful Business Quality and
   Secular Change managed shorts had roughly 65–67% turnover, versus 388–400% for
   losing Unrestricted Selection and Barebones shorts. Test selective changes
   justified by evidence; low turnover itself is not proven to cause better returns.
4. **Test holding periods and stronger models prospectively.** Freeze prompts,
   sizing, costs, schedules, and policy choices; compare models from a common start
   date. Include controls and match sizing when testing memory. Astra's ability to
   improve performance remains a hypothesis.

Confidence is limited by three material issues:

- **Small samples and selection:** H5 usually had 16–19 completed signals;
  overlapping horizons are not independent confirmations. All ordinary Tuned
  policies were inconclusive. Business Quality short was the only managed positive
  evidence label, without correction across managed portfolios. These selected
  winners need a fresh confirmation period.
- **Execution and coverage:** the audit found 46 managed allocations entered after
  their assigned close and 52 rebuilt runs finishing after their scheduled close.
  The [documented scheduled-date convention](../../README.md#experiment-integrity-rules-enforced-in-code)
  permits this, creating potential look-ahead bias. Also, 377 of 1,900 scheduled
  evaluations failed; missing runs affect managed holdings and rebuilt exposure
  differently. Executable-price revaluation and reliable coverage are needed before
  interpreting the differences as investment skill.
- **Attribution and costs:** most historical decisions used Sol despite the
  then-current Luna names. Net returns include 10 bps transaction costs but omit
  borrow and financing fees, limiting conclusions about executable short performance.

On September 8, the session created **20 Astra High portfolios: five in each
managed/rebuilt × long/short category**, using the five primary candidates listed
for each category above. Weekday evaluations were enabled with 10 bps transaction
costs. These new portfolios had no performance evidence in the analyzed window.

Sources: Portfolio Arena MCP `get_arena_overview`, `get_rebuilt_analysis`
(Common, Tuned, and Signal), `get_portfolio`, `get_prompt`, `list_agents`,
`list_evaluation_runs`, and `get_evaluator_dashboard`, read during the September 8
session; repository valuation and scheduling logic inspected in that session.
