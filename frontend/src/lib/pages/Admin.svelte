<script lang="ts">
  import { onMount } from "svelte";

  import { apiJson, del, patchJson, postJson, putJson } from "../api/client";
  import type {
    AgentOut,
    AllocationOut,
    ApiKeyCreated,
    ApiKeyOut,
    ApiKeysResponse,
    HarnessDefinition,
    HarnessesResponse,
    LeaderboardResponse,
    ModelDefinition,
    ModelHarnessCapability,
    PortfolioDetail,
    PortfolioSummary,
    PromptOut,
  } from "../api/types";
  import AllocationForm, { type AllocationPayload } from "../components/AllocationForm.svelte";
  import AutomationPanel from "../components/AutomationPanel.svelte";
  import { fmtDate, num, pctPoints, signClass } from "../format";
  import { auth } from "../stores/auth.svelte";

  type Tab =
    "allocation" | "automation" | "portfolio" | "models" | "agents" | "prompts" | "keys" | "settings";
  let tab = $state<Tab>("allocation");

  // ── Shared data ──────────────────────────────
  let portfolios = $state<PortfolioSummary[]>([]);
  let prompts = $state<PromptOut[]>([]);
  let agents = $state<AgentOut[]>([]);
  let models = $state<ModelDefinition[]>([]);
  let harnesses = $state<HarnessDefinition[]>([]);
  let notice = $state("");
  let noticeTimer: ReturnType<typeof setTimeout> | undefined;

  function flash(message: string) {
    notice = message;
    clearTimeout(noticeTimer);
    noticeTimer = setTimeout(() => (notice = ""), 5000);
  }

  async function loadAll() {
    const [leaderboard, promptsPayload, agentsPayload, modelsPayload, harnessesPayload] = await Promise.all([
      apiJson<LeaderboardResponse>("/api/leaderboard"),
      apiJson<{ prompts: PromptOut[] }>("/api/prompts"),
      apiJson<{ agents: AgentOut[] }>("/api/agents"),
      apiJson<{ models: ModelDefinition[] }>("/api/models"),
      apiJson<HarnessesResponse>("/api/harnesses"),
    ]);
    portfolios = leaderboard.portfolios;
    prompts = promptsPayload.prompts;
    agents = agentsPayload.agents;
    models = modelsPayload.models;
    harnesses = harnessesPayload.harnesses;
  }

  async function refreshPrompts() {
    const payload = await apiJson<{ prompts: PromptOut[] }>("/api/prompts");
    prompts = payload.prompts;
  }

  onMount(() => {
    void auth.restore().then(() => {
      if (auth.isAuthenticated) void loadAll();
    });
  });

  const contestants = $derived(portfolios.filter((portfolio) => !portfolio.is_benchmark));

  // ── Tab 1: allocations ───────────────────────
  let selectedSlug = $state("");
  let detail = $state<PortfolioDetail | null>(null);
  let detailAsOf = $state<string | null>(null);
  let detailLoading = $state(false);
  let editingAllocation = $state<AllocationOut | null>(null);
  let formKey = $state(0);

  async function selectPortfolio(event: Event) {
    selectedSlug = (event.currentTarget as HTMLSelectElement).value;
    if (!selectedSlug) {
      detail = null;
      editingAllocation = null;
      return;
    }
    await loadDetail(selectedSlug);
  }

  async function loadDetail(slug: string) {
    detailLoading = true;
    editingAllocation = null;
    try {
      const summary = portfolios.find((portfolio) => portfolio.slug === slug);
      if (!summary) {
        detail = null;
        return;
      }
      // Admin endpoint: carries per-position notes + holding entry/current prices.
      const payload = await apiJson<{ as_of: string | null; portfolio: PortfolioDetail }>(
        `/api/portfolios/${summary.id}/detail`,
      );
      detail = payload.portfolio;
      detailAsOf = payload.as_of;
      formKey += 1;
    } finally {
      detailLoading = false;
    }
  }

  const latestAllocation = $derived(detail?.allocations[0] ?? null);

  function buildHandoff(): string {
    if (!detail) return "";
    const lines = [
      `${detail.name} — current state as of ${detailAsOf ?? "n/a"}`,
      `Overall note: ${latestAllocation?.note?.trim() || "—"}`,
      "",
      "Holdings:",
    ];
    if (detail.holdings.length) {
      for (const h of detail.holdings) {
        if (h.entry_price != null && h.current_price != null) {
          const chg = h.entry_price ? (h.current_price / h.entry_price - 1) * 100 : 0;
          const sign = chg >= 0 ? "+" : "";
          lines.push(
            `- ${h.symbol}: bought @ ${h.entry_price.toFixed(2)} → now ${h.current_price.toFixed(2)} ` +
              `(${sign}${chg.toFixed(2)}%); weight ${h.weight_pct.toFixed(1)}% (target ${h.target_weight_pct.toFixed(1)}%)`,
          );
        } else {
          lines.push(
            `- ${h.symbol}: weight ${h.weight_pct.toFixed(1)}% (target ${h.target_weight_pct.toFixed(1)}%)`,
          );
        }
        lines.push(`  note: ${h.note?.trim() || "—"}`);
      }
    } else {
      // No drifted holdings yet (first allocation still pending) — fall back to targets.
      for (const p of latestAllocation?.positions ?? []) {
        lines.push(`- ${p.symbol}: target ${p.weight_pct.toFixed(1)}%`);
        lines.push(`  note: ${p.note?.trim() || "—"}`);
      }
    }
    return lines.join("\n");
  }

  async function copyHandoff() {
    try {
      await navigator.clipboard.writeText(buildHandoff());
      flash("Handoff copied.");
    } catch {
      flash("Copy failed — copy the text manually.");
    }
  }

  async function submitRebalance(payload: AllocationPayload) {
    if (!detail) return;
    await postJson(`/api/portfolios/${detail.id}/allocations`, payload);
    flash(`Rebalance for ${detail.name} entered.`);
    await loadDetail(detail.slug);
  }

  async function submitEdit(payload: AllocationPayload) {
    if (!editingAllocation || !detail) return;
    const body: Record<string, unknown> = { note: payload.note };
    if (!editingAllocation.locked) body.positions = payload.positions;
    await putJson(`/api/allocations/${editingAllocation.id}`, body);
    flash("Allocation updated.");
    await loadDetail(detail.slug);
  }

  async function deleteAllocation(allocation: AllocationOut) {
    if (!detail) return;
    if (!confirm(`Delete the allocation effective ${allocation.effective_date}?`)) return;
    try {
      await del(`/api/allocations/${allocation.id}`);
      flash("Allocation deleted.");
      await loadDetail(detail.slug);
      await loadAll();
    } catch (e) {
      flash(e instanceof Error ? e.message : "Delete failed");
    }
  }

  // ── Tab 2: portfolios ────────────────────────
  let newName = $state("");
  let newAgentId = $state<number | null>(null);
  let newPromptId = $state<number | null>(null);
  let newCostBps = $state<string>("");
  let portfolioError = $state("");

  async function createPortfolio(event: SubmitEvent) {
    event.preventDefault();
    portfolioError = "";
    if (!newName.trim() || newAgentId === null || newPromptId === null) {
      portfolioError = "Portfolio name, agent, and prompt are required.";
      return;
    }
    const body: Record<string, unknown> = {
      name: newName.trim(),
      agent_id: newAgentId,
      prompt_id: newPromptId,
    };
    if (newCostBps.trim() !== "") body.cost_bps = parseInt(newCostBps, 10);
    try {
      const created = await postJson<{ slug: string; name: string }>("/api/portfolios", body);
      flash(`Portfolio ${created.name} created — enter its first allocation.`);
      newName = "";
      newAgentId = null;
      newPromptId = null;
      newCostBps = "";
      await loadAll();
      // Jump to the Allocations tab with the new portfolio selected.
      selectedSlug = created.slug;
      tab = "allocation";
      await loadDetail(created.slug);
    } catch (e) {
      portfolioError = e instanceof Error ? e.message : "Create failed";
    }
  }

  // ── Models and agents ────────────────────────
  let editModel = $state<ModelDefinition | null>(null);
  let newModelName = $state("");
  let newModelNotes = $state("");
  let newModelCapabilities = $state<ModelHarnessCapability[]>([]);

  function capabilityPayload(capabilities: ModelHarnessCapability[]) {
    return capabilities.map(({ harness, execution_model_id, reasoning_efforts }) => ({
      harness,
      execution_model_id,
      reasoning_efforts,
    }));
  }

  function toggleModelHarness(
    capabilities: ModelHarnessCapability[],
    harness: HarnessDefinition,
    enabled: boolean,
  ): ModelHarnessCapability[] {
    if (!enabled) return capabilities.filter((capability) => capability.harness !== harness.id);
    if (capabilities.some((capability) => capability.harness === harness.id)) return capabilities;
    return [
      ...capabilities,
      {
        harness: harness.id,
        harness_name: harness.name,
        execution_model_id: "",
        reasoning_efforts: [],
      },
    ];
  }

  function toggleCapabilityEffort(capability: ModelHarnessCapability, effort: string, enabled: boolean) {
    capability.reasoning_efforts = enabled
      ? [...capability.reasoning_efforts, effort]
      : capability.reasoning_efforts.filter((item) => item !== effort);
  }

  async function createModel(event: SubmitEvent) {
    event.preventDefault();
    if (!newModelName.trim()) return;
    try {
      await postJson("/api/models", {
        name: newModelName.trim(),
        notes: newModelNotes.trim(),
        capabilities: capabilityPayload($state.snapshot(newModelCapabilities)),
      });
      newModelName = "";
      newModelNotes = "";
      newModelCapabilities = [];
      await loadAll();
      flash("Model created.");
    } catch (e) {
      flash(e instanceof Error ? e.message : "Create failed");
    }
  }

  async function saveModel(event: SubmitEvent) {
    event.preventDefault();
    if (!editModel) return;
    try {
      await patchJson(`/api/models/${editModel.id}`, {
        name: editModel.name,
        notes: editModel.notes,
        capabilities: capabilityPayload($state.snapshot(editModel.capabilities)),
      });
      editModel = null;
      await loadAll();
      flash("Model saved.");
    } catch (e) {
      flash(e instanceof Error ? e.message : "Save failed");
    }
  }

  async function deleteModel(model: ModelDefinition) {
    if (!confirm(`Delete model ${model.name}?`)) return;
    try {
      await del(`/api/models/${model.id}`);
      await loadAll();
      flash("Model deleted.");
    } catch (e) {
      flash(e instanceof Error ? e.message : "Delete failed");
    }
  }

  interface AgentDraft {
    id?: number;
    model_id: number;
    harness: string;
    reasoning_effort: string;
    notes: string;
  }

  let editAgent = $state<AgentDraft | null>(null);
  let newAgentModelId = $state(0);
  let newAgentHarness = $state("");
  let newAgentReasoningEffort = $state("");
  let newAgentNotes = $state("");

  function modelById(modelId: number) {
    return models.find((model) => model.id === modelId);
  }

  function selectedCapability(modelId: number, harness: string) {
    return modelById(modelId)?.capabilities.find((capability) => capability.harness === harness);
  }

  const newAgentCapability = $derived(selectedCapability(newAgentModelId, newAgentHarness));
  const editAgentCapability = $derived(
    editAgent ? selectedCapability(editAgent.model_id, editAgent.harness) : null,
  );

  function resetAgentExecution(draft: AgentDraft) {
    const capability = selectedCapability(draft.model_id, draft.harness);
    if (!capability) {
      draft.harness = "";
      draft.reasoning_effort = "";
      return;
    }
    draft.reasoning_effort = capability.reasoning_efforts[0] ?? "";
  }

  function setNewAgentModel(event: Event) {
    newAgentModelId = Number((event.currentTarget as HTMLSelectElement).value);
    newAgentHarness = "";
    newAgentReasoningEffort = "";
  }

  function setNewAgentHarness(event: Event) {
    newAgentHarness = (event.currentTarget as HTMLSelectElement).value;
    newAgentReasoningEffort =
      selectedCapability(newAgentModelId, newAgentHarness)?.reasoning_efforts[0] ?? "";
  }

  function setEditAgentModel(event: Event) {
    if (!editAgent) return;
    editAgent.model_id = Number((event.currentTarget as HTMLSelectElement).value);
    editAgent.harness = "";
    editAgent.reasoning_effort = "";
  }

  function setEditAgentHarness(event: Event) {
    if (!editAgent) return;
    editAgent.harness = (event.currentTarget as HTMLSelectElement).value;
    resetAgentExecution(editAgent);
  }

  async function createAgent(event: SubmitEvent) {
    event.preventDefault();
    if (!newAgentModelId) return;
    try {
      await postJson("/api/agents", {
        model_id: newAgentModelId,
        harness: newAgentHarness || null,
        reasoning_effort: newAgentReasoningEffort || null,
        notes: newAgentNotes.trim(),
      });
      newAgentModelId = 0;
      newAgentHarness = "";
      newAgentReasoningEffort = "";
      newAgentNotes = "";
      await loadAll();
      flash("Agent created.");
    } catch (e) {
      flash(e instanceof Error ? e.message : "Create failed");
    }
  }

  async function saveAgent(event: SubmitEvent) {
    event.preventDefault();
    if (!editAgent?.id) return;
    try {
      await patchJson(`/api/agents/${editAgent.id}`, {
        model_id: editAgent.model_id,
        harness: editAgent.harness || null,
        reasoning_effort: editAgent.reasoning_effort || null,
        notes: editAgent.notes,
      });
      editAgent = null;
      await loadAll();
      flash("Agent saved.");
    } catch (e) {
      flash(e instanceof Error ? e.message : "Save failed");
    }
  }

  async function deleteAgent(agent: AgentOut) {
    if (!confirm(`Delete agent ${agent.name}?`)) return;
    try {
      await del(`/api/agents/${agent.id}`);
      flash("Agent deleted.");
      await loadAll();
    } catch (e) {
      flash(e instanceof Error ? e.message : "Delete failed");
    }
  }

  // ── Tab 4: prompts ───────────────────────────
  let editPrompt = $state<PromptOut | null>(null);
  let newPromptName = $state("");
  let newPromptText = $state("");
  let newPromptNotes = $state("");
  let newPromptMinWeight = $state(10);
  let newPromptMaxWeight = $state(25);

  async function createPrompt(event: SubmitEvent) {
    event.preventDefault();
    if (!newPromptName.trim() || !newPromptText.trim()) return;
    try {
      await postJson("/api/prompts", {
        name: newPromptName.trim(),
        text: newPromptText,
        notes: newPromptNotes.trim(),
        allocation_policy: {
          min_position_weight_pct: newPromptMinWeight,
          max_position_weight_pct: newPromptMaxWeight,
        },
      });
      newPromptName = "";
      newPromptText = "";
      newPromptNotes = "";
      newPromptMinWeight = 10;
      newPromptMaxWeight = 25;
      await refreshPrompts();
      flash("Prompt created.");
    } catch (e) {
      flash(e instanceof Error ? e.message : "Create failed");
    }
  }

  async function savePrompt(event: SubmitEvent) {
    event.preventDefault();
    if (!editPrompt) return;
    await patchJson(`/api/prompts/${editPrompt.id}`, {
      name: editPrompt.name,
      text: editPrompt.text,
      notes: editPrompt.notes,
      allocation_policy: {
        min_position_weight_pct: editPrompt.allocation_policy.min_position_weight_pct,
        max_position_weight_pct: editPrompt.allocation_policy.max_position_weight_pct,
      },
    });
    editPrompt = null;
    await refreshPrompts();
    flash("Prompt saved.");
  }

  async function deletePrompt(prompt: PromptOut) {
    if (!confirm(`Delete prompt ${prompt.name}?`)) return;
    try {
      await del(`/api/prompts/${prompt.id}`);
      flash("Prompt deleted.");
      await refreshPrompts();
    } catch (e) {
      flash(e instanceof Error ? e.message : "Delete failed");
    }
  }

  // ── Portfolio management (Portfolios tab) ────
  let editPortfolio = $state<{
    id: number;
    name: string;
    agent_id: number;
    prompt_id: number;
    cost_bps: string;
  } | null>(null);

  async function toggleArchive(portfolio: PortfolioSummary) {
    await patchJson(`/api/portfolios/${portfolio.id}`, {
      status: portfolio.status === "active" ? "archived" : "active",
    });
    await loadAll();
  }

  async function savePortfolio(event: SubmitEvent) {
    event.preventDefault();
    if (!editPortfolio) return;
    try {
      await patchJson(`/api/portfolios/${editPortfolio.id}`, {
        name: editPortfolio.name,
        agent_id: editPortfolio.agent_id,
        prompt_id: editPortfolio.prompt_id,
        cost_bps: parseInt(editPortfolio.cost_bps, 10) || 0,
      });
      editPortfolio = null;
      await loadAll();
      flash("Portfolio saved.");
    } catch (e) {
      flash(e instanceof Error ? e.message : "Save failed");
    }
  }

  async function deletePortfolio(portfolio: PortfolioSummary) {
    if (!confirm(`Delete ${portfolio.name} and all its allocations? This is irreversible.`)) return;
    try {
      await del(`/api/portfolios/${portfolio.id}`);
      if (selectedSlug === portfolio.slug) {
        selectedSlug = "";
        detail = null;
        editingAllocation = null;
      }
      flash(`Portfolio ${portfolio.name} deleted.`);
      await loadAll();
    } catch (e) {
      flash(e instanceof Error ? e.message : "Delete failed");
    }
  }

  // ── Tab: API keys ────────────────────────────
  let apiKeys = $state<ApiKeyOut[]>([]);
  let newKeyName = $state("");
  let createdKey = $state<ApiKeyCreated | null>(null);
  let keyError = $state("");

  async function loadKeys() {
    const payload = await apiJson<ApiKeysResponse>("/api/keys");
    apiKeys = payload.keys;
  }

  async function createKey(event: SubmitEvent) {
    event.preventDefault();
    keyError = "";
    if (!newKeyName.trim()) return;
    try {
      createdKey = await postJson<ApiKeyCreated>("/api/keys", { name: newKeyName.trim() });
      newKeyName = "";
      await loadKeys();
    } catch (e) {
      keyError = e instanceof Error ? e.message : "Create failed";
    }
  }

  async function copyKey() {
    if (!createdKey) return;
    try {
      await navigator.clipboard.writeText(createdKey.key);
      flash("API key copied.");
    } catch {
      flash("Copy failed — select and copy the key manually.");
    }
  }

  async function revokeKey(key: ApiKeyOut) {
    if (!confirm(`Revoke key "${key.name}"? Clients using it stop working immediately.`)) return;
    try {
      await del(`/api/keys/${key.id}`);
      if (createdKey?.id === key.id) createdKey = null;
      flash("API key revoked.");
      await loadKeys();
    } catch (e) {
      flash(e instanceof Error ? e.message : "Revoke failed");
    }
  }

  // ── Tab 4: settings ──────────────────────────
  let defaultCostBps = $state<string>("");
  let settingsError = $state("");

  async function loadSettings() {
    try {
      const payload = await apiJson<{ default_cost_bps: number }>("/api/settings");
      defaultCostBps = String(payload.default_cost_bps);
    } catch {
      defaultCostBps = "";
    }
  }

  function selectTab(id: Tab) {
    tab = id;
    if (id === "keys") void loadKeys();
    if (id === "settings") void loadSettings();
  }

  async function saveSettings(event: SubmitEvent) {
    event.preventDefault();
    settingsError = "";
    try {
      await putJson("/api/settings", { default_cost_bps: parseInt(defaultCostBps, 10) || 0 });
      flash("Settings saved.");
    } catch (e) {
      settingsError = e instanceof Error ? e.message : "Save failed";
    }
  }

  async function clearCache() {
    const result = await del<{ deleted: number }>("/api/prices/cache");
    flash(`Price cache cleared (${result.deleted} entries).`);
  }
</script>

{#if auth.restoring}
  <div class="loading-block"><span class="spinner" aria-hidden="true"></span></div>
{:else if !auth.isAuthenticated}
  <div class="login-wrap">
    <section class="card login">
      <h1>Admin access</h1>
      <p class="muted">Sign in with the configured identity provider to manage Portfolio Arena.</p>
      <a class="btn primary" href="/api/auth/login">Sign in</a>
    </section>
  </div>
{:else}
  <div class="admin-head">
    <h1>Admin</h1>
  </div>

  {#if notice}
    <div class="notice" role="status">{notice}</div>
  {/if}

  <div class="tabs" role="tablist" aria-label="Admin sections">
    {#snippet tabBtn(id: Tab, label: string)}
      <button
        role="tab"
        aria-selected={tab === id}
        class="tab"
        class:active={tab === id}
        onclick={() => selectTab(id)}
      >
        {label}
      </button>
    {/snippet}
    {@render tabBtn("allocation", "Allocations")}
    {@render tabBtn("automation", "Automation")}
    {@render tabBtn("portfolio", "Portfolios")}
    {@render tabBtn("models", "Models")}
    {@render tabBtn("agents", "Agents")}
    {@render tabBtn("prompts", "Prompts")}
    {@render tabBtn("keys", "API Keys")}
    {@render tabBtn("settings", "Settings")}
  </div>

  {#if tab === "allocation"}
    <section class="card">
      <div class="field">
        <label for="portfolio-select">Portfolio</label>
        <select id="portfolio-select" value={selectedSlug} onchange={selectPortfolio}>
          <option value="">Select a portfolio…</option>
          {#each contestants as portfolio (portfolio.slug)}
            <option value={portfolio.slug}>
              {portfolio.name} — {portfolio.agent.name}
              {portfolio.status === "archived" ? " (archived)" : ""}
            </option>
          {/each}
        </select>
      </div>

      {#if detailLoading}
        <div class="loading-block"><span class="spinner" aria-hidden="true"></span></div>
      {:else if detail}
        <p class="muted picked-meta">
          {detail.agent.name} · prompt {detail.prompt?.name ?? "—"}
        </p>
        <h2>Allocation history</h2>
        <div class="table-scroll history">
          <table>
            <thead>
              <tr>
                <th>Effective</th>
                <th>Status</th>
                <th class="right">Positions</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {#each detail.allocations as allocation (allocation.id)}
                <tr>
                  <td class="num">{fmtDate(allocation.effective_date)}</td>
                  <td>
                    {#if allocation.locked}
                      <span class="badge">locked</span>
                    {:else}
                      <span class="badge warn">editable until close</span>
                    {/if}
                  </td>
                  <td class="right muted">
                    {allocation.positions.map((p) => `${p.symbol} ${pctPoints(p.weight_pct, 1)}`).join(", ")}
                  </td>
                  <td class="right actions">
                    <button class="btn small" onclick={() => (editingAllocation = allocation)}> Edit </button>
                    {#if !allocation.locked}
                      <button class="btn small danger" onclick={() => deleteAllocation(allocation)}>
                        Delete
                      </button>
                    {/if}
                  </td>
                </tr>
              {:else}
                <tr>
                  <td colspan="4" class="muted">No allocations yet — enter the first one below.</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>

        <div class="state-head">
          <h2>Current state <span class="muted">(drifted, admin-only)</span></h2>
          <button class="btn small" onclick={copyHandoff} disabled={!detail.allocations.length}>
            Copy handoff for next agent
          </button>
        </div>
        {#if detail.holdings.length}
          <div class="table-scroll history">
            <table>
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th class="right">Buy</th>
                  <th class="right">Now</th>
                  <th class="right">Change</th>
                  <th class="right">Weight</th>
                  <th class="right">Target</th>
                  <th>Note</th>
                </tr>
              </thead>
              <tbody>
                {#each detail.holdings as holding (holding.symbol)}
                  {@const chg =
                    holding.entry_price && holding.current_price
                      ? (holding.current_price / holding.entry_price - 1) * 100
                      : null}
                  <tr>
                    <td class="num">{holding.symbol}</td>
                    <td class="right num">{holding.entry_price != null ? num(holding.entry_price) : "—"}</td>
                    <td class="right num"
                      >{holding.current_price != null ? num(holding.current_price) : "—"}</td
                    >
                    <td class="right num {signClass(chg)}">{chg != null ? pctPoints(chg, 2) : "—"}</td>
                    <td class="right num">{pctPoints(holding.weight_pct)}</td>
                    <td class="right num">{pctPoints(holding.target_weight_pct)}</td>
                    <td class="muted preview">{holding.note || "—"}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
        {:else}
          <p class="muted prefill-note">No drifted holdings yet — the first allocation is still pending.</p>
        {/if}

        {#if editingAllocation}
          <h2>
            Edit allocation effective {editingAllocation.effective_date}
            <button class="btn small" onclick={() => (editingAllocation = null)}>Cancel</button>
          </h2>
          {#key editingAllocation.id}
            <AllocationForm
              initialPositions={editingAllocation.positions}
              initialNote={editingAllocation.note}
              positionsEditable={!editingAllocation.locked}
              policy={detail.prompt?.allocation_policy}
              submitLabel="Save changes"
              onSubmit={submitEdit}
            />
          {/key}
        {:else if detail.allocations.length === 0}
          <h2>First allocation</h2>
          {#key formKey}
            <AllocationForm
              policy={detail.prompt?.allocation_policy}
              submitLabel="Enter first allocation"
              onSubmit={submitRebalance}
            />
          {/key}
        {:else}
          <h2>New rebalance</h2>
          <p class="muted prefill-note">
            Pre-filled with the previous allocation's target weights as the starting point.
          </p>
          {#key formKey}
            <AllocationForm
              initialPositions={latestAllocation?.positions ?? []}
              policy={detail.prompt?.allocation_policy}
              submitLabel="Enter rebalance"
              onSubmit={submitRebalance}
            />
          {/key}
        {/if}
      {:else}
        <div class="empty-state">
          <p>Select a portfolio to enter a rebalance or fix a pending allocation.</p>
        </div>
      {/if}
    </section>
  {:else if tab === "automation"}
    <AutomationPanel />
  {:else if tab === "portfolio"}
    <section class="card">
      <h2>New portfolio</h2>
      <form onsubmit={createPortfolio}>
        <div class="grid-2">
          <div class="field">
            <label for="np-name">Portfolio name</label>
            <input id="np-name" type="text" bind:value={newName} placeholder="Claude Weekly Manager" />
          </div>
          <div class="field">
            <label for="np-cost">Cost bps <span class="muted">(blank = default)</span></label>
            <input id="np-cost" type="number" min="0" bind:value={newCostBps} placeholder="10" />
          </div>
        </div>
        <div class="field">
          <label for="np-agent">Agent</label>
          <select id="np-agent" bind:value={newAgentId}>
            <option value={null} disabled>Select an agent…</option>
            {#each agents as agent (agent.id)}
              <option value={agent.id}>{agent.name}</option>
            {/each}
          </select>
          {#if agents.length === 0}
            <p class="muted hint">No agents yet — create one in the Agents tab first.</p>
          {/if}
        </div>
        <div class="field">
          <label for="np-prompt">Prompt</label>
          <select id="np-prompt" bind:value={newPromptId}>
            <option value={null} disabled>Select a prompt…</option>
            {#each prompts as prompt (prompt.id)}
              <option value={prompt.id}>{prompt.name}</option>
            {/each}
          </select>
          {#if prompts.length === 0}
            <p class="muted hint">No prompts yet — create one in the Prompts tab first.</p>
          {/if}
        </div>

        {#if portfolioError}
          <div class="error-box" role="alert">{portfolioError}</div>
        {/if}

        <button
          class="btn primary"
          type="submit"
          disabled={!newName.trim() || newAgentId === null || newPromptId === null}
        >
          Create portfolio
        </button>
        <p class="muted hint">Enter its first allocation from the Allocations tab.</p>
      </form>

      <h2 class="spaced">Existing portfolios</h2>
      {#each contestants as portfolio (portfolio.id)}
        {#if editPortfolio?.id === portfolio.id}
          <form class="edit-form" onsubmit={savePortfolio}>
            <div class="field">
              <label for="epf-name-{portfolio.id}">Name</label>
              <input id="epf-name-{portfolio.id}" type="text" bind:value={editPortfolio.name} />
            </div>
            <div class="field">
              <label for="epf-agent-{portfolio.id}">Agent</label>
              <select id="epf-agent-{portfolio.id}" bind:value={editPortfolio.agent_id}>
                {#each agents as agent (agent.id)}
                  <option value={agent.id}>{agent.name}</option>
                {/each}
              </select>
            </div>
            <div class="field">
              <label for="epf-prompt-{portfolio.id}">Prompt</label>
              <select id="epf-prompt-{portfolio.id}" bind:value={editPortfolio.prompt_id}>
                {#each prompts as prompt (prompt.id)}
                  <option value={prompt.id}>{prompt.name}</option>
                {/each}
              </select>
            </div>
            <div class="field">
              <label for="epf-cost-{portfolio.id}">Cost bps</label>
              <input id="epf-cost-{portfolio.id}" type="number" min="0" bind:value={editPortfolio.cost_bps} />
            </div>
            <div class="edit-actions">
              <button class="btn primary" type="submit">Save</button>
              <button class="btn" type="button" onclick={() => (editPortfolio = null)}>Cancel</button>
            </div>
          </form>
        {:else}
          <div class="manage-row">
            <div>
              <strong>{portfolio.name}</strong>
              <span class="muted">
                · {portfolio.agent.name} · {portfolio.cost_bps} bps · {portfolio.status}</span
              >
            </div>
            <div class="row-actions">
              <button
                class="btn small"
                onclick={() =>
                  (editPortfolio = {
                    id: portfolio.id,
                    name: portfolio.name,
                    agent_id: portfolio.agent.id ?? agents[0]?.id ?? 0,
                    prompt_id: portfolio.prompt?.id ?? prompts[0]?.id ?? 0,
                    cost_bps: String(portfolio.cost_bps),
                  })}
              >
                Edit
              </button>
              <button class="btn small" onclick={() => toggleArchive(portfolio)}>
                {portfolio.status === "active" ? "Archive" : "Unarchive"}
              </button>
              <button class="btn small danger" onclick={() => deletePortfolio(portfolio)}>Delete</button>
            </div>
          </div>
        {/if}
      {:else}
        <div class="empty-state"><p>No portfolios yet.</p></div>
      {/each}
    </section>
  {:else if tab === "models"}
    <section class="card">
      <h2>New model</h2>
      <form onsubmit={createModel}>
        <div class="field">
          <label for="nm-name">Display name</label>
          <input id="nm-name" type="text" bind:value={newModelName} placeholder="GPT-5.6 Sol" />
        </div>
        <div class="field">
          <label for="nm-notes">Notes <span class="muted">(optional)</span></label>
          <input id="nm-notes" type="text" bind:value={newModelNotes} />
        </div>
        {#each harnesses as harness (harness.id)}
          {@const capability = newModelCapabilities.find((item) => item.harness === harness.id)}
          <fieldset class="capability">
            <label class="toggle-label">
              <input
                type="checkbox"
                checked={Boolean(capability)}
                onchange={(event) =>
                  (newModelCapabilities = toggleModelHarness(
                    newModelCapabilities,
                    harness,
                    event.currentTarget.checked,
                  ))}
              />
              Supports {harness.name}
            </label>
            {#if capability}
              <div class="field">
                <label for="nm-execution-{harness.id}">Execution model ID</label>
                <input
                  id="nm-execution-{harness.id}"
                  type="text"
                  bind:value={capability.execution_model_id}
                  placeholder="gpt-5.6-sol"
                />
              </div>
              <span class="field-label">Supported reasoning efforts</span>
              <div class="check-group">
                {#each harness.reasoning_efforts as effort (effort.id)}
                  <label>
                    <input
                      type="checkbox"
                      checked={capability.reasoning_efforts.includes(effort.id)}
                      onchange={(event) =>
                        toggleCapabilityEffort(capability, effort.id, event.currentTarget.checked)}
                    />
                    {effort.name}
                  </label>
                {/each}
              </div>
              <p class="muted hint">
                Leave every effort unchecked when this model exposes no effort control.
              </p>
            {/if}
          </fieldset>
        {/each}
        <button class="btn primary" type="submit" disabled={!newModelName.trim()}>Create model</button>
      </form>

      <h2 class="spaced">Models</h2>
      {#each models as model (model.id)}
        {#if editModel?.id === model.id}
          <form class="edit-form" onsubmit={saveModel}>
            <div class="field">
              <label for="em-name-{model.id}">Display name</label>
              <input id="em-name-{model.id}" type="text" bind:value={editModel.name} />
            </div>
            <div class="field">
              <label for="em-notes-{model.id}">Notes</label>
              <input id="em-notes-{model.id}" type="text" bind:value={editModel.notes} />
            </div>
            {#each harnesses as harness (harness.id)}
              {@const capability = editModel.capabilities.find((item) => item.harness === harness.id)}
              <fieldset class="capability">
                <label class="toggle-label">
                  <input
                    type="checkbox"
                    checked={Boolean(capability)}
                    onchange={(event) => {
                      if (editModel) {
                        editModel.capabilities = toggleModelHarness(
                          editModel.capabilities,
                          harness,
                          event.currentTarget.checked,
                        );
                      }
                    }}
                  />
                  Supports {harness.name}
                </label>
                {#if capability}
                  <div class="field">
                    <label for="em-execution-{model.id}-{harness.id}">Execution model ID</label>
                    <input
                      id="em-execution-{model.id}-{harness.id}"
                      type="text"
                      bind:value={capability.execution_model_id}
                    />
                  </div>
                  <span class="field-label">Supported reasoning efforts</span>
                  <div class="check-group">
                    {#each harness.reasoning_efforts as effort (effort.id)}
                      <label>
                        <input
                          type="checkbox"
                          checked={capability.reasoning_efforts.includes(effort.id)}
                          onchange={(event) =>
                            toggleCapabilityEffort(capability, effort.id, event.currentTarget.checked)}
                        />
                        {effort.name}
                      </label>
                    {/each}
                  </div>
                {/if}
              </fieldset>
            {/each}
            <p class="muted hint">
              Execution-ID changes affect future runs for {model.agent_count} agent(s). Existing runs keep their
              snapshot.
            </p>
            <div class="edit-actions">
              <button class="btn primary" type="submit">Save</button>
              <button class="btn" type="button" onclick={() => (editModel = null)}>Cancel</button>
            </div>
          </form>
        {:else}
          <div class="manage-row">
            <div>
              <strong>{model.name}</strong>
              <span class="muted"> · {model.agent_count} agent(s)</span>
              {#each model.capabilities as capability (capability.harness)}
                <div class="muted num">
                  {capability.harness_name}: {capability.execution_model_id}
                  · {capability.reasoning_efforts.length
                    ? capability.reasoning_efforts.join(", ")
                    : "no reasoning control"}
                </div>
              {/each}
              {#if model.notes}<p class="muted preview">{model.notes}</p>{/if}
            </div>
            <div class="row-actions">
              <button
                class="btn small"
                onclick={() =>
                  (editModel = {
                    ...model,
                    capabilities: model.capabilities.map((capability) => ({
                      ...capability,
                      reasoning_efforts: [...capability.reasoning_efforts],
                    })),
                  })}>Edit</button
              >
              <button
                class="btn small danger"
                onclick={() => deleteModel(model)}
                disabled={model.agent_count > 0}
              >
                Delete
              </button>
            </div>
          </div>
        {/if}
      {:else}
        <div class="empty-state"><p>No models yet — create one above.</p></div>
      {/each}
    </section>
  {:else if tab === "agents"}
    <section class="card">
      <h2>New agent</h2>
      <form onsubmit={createAgent}>
        <div class="field">
          <label for="na-model">Model</label>
          <select id="na-model" value={newAgentModelId || ""} onchange={setNewAgentModel}>
            <option value="">Select a model…</option>
            {#each models as model (model.id)}
              <option value={model.id}>{model.name}</option>
            {/each}
          </select>
        </div>
        <div class="field">
          <label for="na-harness">Harness</label>
          <select id="na-harness" value={newAgentHarness} onchange={setNewAgentHarness}>
            <option value="">No supported harness</option>
            {#each modelById(newAgentModelId)?.capabilities ?? [] as capability (capability.harness)}
              <option value={capability.harness}>{capability.harness_name}</option>
            {/each}
          </select>
        </div>
        {#if newAgentCapability?.reasoning_efforts.length}
          <div class="field">
            <label for="na-reasoning">Reasoning effort</label>
            <select id="na-reasoning" bind:value={newAgentReasoningEffort} required>
              {#each newAgentCapability.reasoning_efforts as effort (effort)}
                <option value={effort}>{effort}</option>
              {/each}
            </select>
          </div>
        {/if}
        <div class="field">
          <label for="na-notes">Notes <span class="muted">(optional)</span></label>
          <input id="na-notes" type="text" bind:value={newAgentNotes} />
        </div>
        <button class="btn primary" type="submit" disabled={!newAgentModelId}>Create agent</button>
      </form>

      <h2 class="spaced">Agents</h2>
      {#each agents as agent (agent.id)}
        {#if editAgent?.id === agent.id}
          <form class="edit-form" onsubmit={saveAgent}>
            <div class="field">
              <label for="ea-model-{agent.id}">Model</label>
              <select id="ea-model-{agent.id}" value={editAgent.model_id} onchange={setEditAgentModel}>
                {#each models as model (model.id)}
                  <option value={model.id}>{model.name}</option>
                {/each}
              </select>
            </div>
            <div class="field">
              <label for="ea-harness-{agent.id}">Harness</label>
              <select id="ea-harness-{agent.id}" value={editAgent.harness} onchange={setEditAgentHarness}>
                <option value="">No supported harness</option>
                {#each modelById(editAgent.model_id)?.capabilities ?? [] as capability (capability.harness)}
                  <option value={capability.harness}>{capability.harness_name}</option>
                {/each}
              </select>
            </div>
            {#if editAgentCapability?.reasoning_efforts.length}
              <div class="field">
                <label for="ea-reasoning-{agent.id}">Reasoning effort</label>
                <select id="ea-reasoning-{agent.id}" bind:value={editAgent.reasoning_effort} required>
                  {#each editAgentCapability.reasoning_efforts as effort (effort)}
                    <option value={effort}>{effort}</option>
                  {/each}
                </select>
              </div>
            {/if}
            <div class="field">
              <label for="ea-notes-{agent.id}">Notes</label>
              <input id="ea-notes-{agent.id}" type="text" bind:value={editAgent.notes} />
            </div>
            <div class="edit-actions">
              <button class="btn primary" type="submit">Save</button>
              <button class="btn" type="button" onclick={() => (editAgent = null)}>Cancel</button>
            </div>
          </form>
        {:else}
          {@const used = agent.portfolios?.length ?? agent.portfolio_count ?? 0}
          <div class="manage-row">
            <div>
              <strong>{agent.name}</strong>
              <span class="muted"> · {used} portfolio(s)</span>
              {#if agent.notes}<p class="muted preview">{agent.notes}</p>{/if}
            </div>
            <div class="row-actions">
              <button
                class="btn small"
                onclick={() =>
                  (editAgent = {
                    id: agent.id,
                    model_id: agent.model.id,
                    harness: agent.harness?.id ?? "",
                    reasoning_effort: agent.reasoning_effort ?? "",
                    notes: agent.notes,
                  })}>Edit</button
              >
              <button
                class="btn small danger"
                onclick={() => deleteAgent(agent)}
                disabled={used > 0}
                title={used > 0 ? `${used} portfolio(s) still use this agent` : ""}
              >
                Delete
              </button>
            </div>
          </div>
        {/if}
      {:else}
        <div class="empty-state"><p>No agents yet — create one above.</p></div>
      {/each}
    </section>
  {:else if tab === "prompts"}
    <section class="card">
      <h2>New prompt</h2>
      <form onsubmit={createPrompt}>
        <div class="field">
          <label for="np-prompt-name">Name <span class="muted">(e.g. weekly-manager-v2)</span></label>
          <input id="np-prompt-name" type="text" bind:value={newPromptName} />
        </div>
        <div class="field">
          <label for="np-prompt-text">Text</label>
          <textarea id="np-prompt-text" bind:value={newPromptText} rows="6"></textarea>
        </div>
        <div class="field">
          <label for="np-prompt-notes">Notes <span class="muted">(optional)</span></label>
          <input id="np-prompt-notes" type="text" bind:value={newPromptNotes} />
        </div>
        <div class="grid-2 weight-grid">
          <div class="field">
            <label for="np-prompt-min">Minimum position weight (%)</label>
            <input
              id="np-prompt-min"
              type="number"
              min="0.0001"
              max="100"
              step="0.0001"
              bind:value={newPromptMinWeight}
              required
            />
          </div>
          <div class="field">
            <label for="np-prompt-max">Maximum position weight (%)</label>
            <input
              id="np-prompt-max"
              type="number"
              min="0.0001"
              max="100"
              step="0.0001"
              bind:value={newPromptMaxWeight}
              required
            />
          </div>
        </div>
        <p class="muted hint">
          These defaults produce {Math.ceil(100 / newPromptMaxWeight)}–{Math.floor(100 / newPromptMinWeight)} positions.
          The server enforces the limits on every allocation.
        </p>
        <button class="btn primary" type="submit" disabled={!newPromptName.trim() || !newPromptText.trim()}>
          Create prompt
        </button>
      </form>

      <h2 class="spaced">Prompts</h2>
      {#each prompts as prompt (prompt.id)}
        {#if editPrompt?.id === prompt.id}
          <form class="edit-form" onsubmit={savePrompt}>
            <div class="field">
              <label for="ep-name-{prompt.id}">Name</label>
              <input id="ep-name-{prompt.id}" type="text" bind:value={editPrompt.name} />
            </div>
            <div class="field">
              <label for="ep-text-{prompt.id}">Text</label>
              <textarea id="ep-text-{prompt.id}" bind:value={editPrompt.text} rows="8"></textarea>
            </div>
            <div class="field">
              <label for="ep-notes-{prompt.id}">Notes</label>
              <input id="ep-notes-{prompt.id}" type="text" bind:value={editPrompt.notes} />
            </div>
            <div class="grid-2 weight-grid">
              <div class="field">
                <label for="ep-min-{prompt.id}">Minimum position weight (%)</label>
                <input
                  id="ep-min-{prompt.id}"
                  type="number"
                  min="0.0001"
                  max="100"
                  step="0.0001"
                  bind:value={editPrompt.allocation_policy.min_position_weight_pct}
                  required
                />
              </div>
              <div class="field">
                <label for="ep-max-{prompt.id}">Maximum position weight (%)</label>
                <input
                  id="ep-max-{prompt.id}"
                  type="number"
                  min="0.0001"
                  max="100"
                  step="0.0001"
                  bind:value={editPrompt.allocation_policy.max_position_weight_pct}
                  required
                />
              </div>
            </div>
            <div class="edit-actions">
              <button class="btn primary" type="submit">Save</button>
              <button class="btn" type="button" onclick={() => (editPrompt = null)}>Cancel</button>
            </div>
          </form>
        {:else}
          {@const used = prompt.portfolio_count ?? 0}
          <div class="manage-row">
            <div>
              <strong>{prompt.name}</strong>
              <span class="muted"> · {used} portfolio(s)</span>
              <span class="muted">
                · {prompt.allocation_policy.min_position_weight_pct}%–{prompt.allocation_policy
                  .max_position_weight_pct}% per position
              </span>
              <p class="muted preview">{prompt.text.slice(0, 140)}{prompt.text.length > 140 ? "…" : ""}</p>
            </div>
            <div class="row-actions">
              <button
                class="btn small"
                onclick={() =>
                  (editPrompt = {
                    ...prompt,
                    allocation_policy: { ...prompt.allocation_policy },
                  })}>Edit</button
              >
              <button
                class="btn small danger"
                onclick={() => deletePrompt(prompt)}
                disabled={used > 0}
                title={used > 0 ? "Used by existing allocations" : ""}
              >
                Delete
              </button>
            </div>
          </div>
        {/if}
      {:else}
        <div class="empty-state"><p>No prompts yet — create one above.</p></div>
      {/each}
    </section>
  {:else if tab === "keys"}
    <section class="card">
      <h2>New API key</h2>
      <p class="muted cache-note">
        API keys authenticate the MCP server, which can do everything the admin panel can (manage portfolios,
        agents, prompts, allocations, and read performance) except manage keys.
      </p>
      <form onsubmit={createKey}>
        <div class="field">
          <label for="nk-name">Name <span class="muted">(what this key is for)</span></label>
          <input id="nk-name" type="text" bind:value={newKeyName} placeholder="Claude rebalancer" />
        </div>
        {#if keyError}
          <div class="error-box" role="alert">{keyError}</div>
        {/if}
        <button class="btn primary" type="submit" disabled={!newKeyName.trim()}>Create key</button>
      </form>

      {#if createdKey}
        <div class="key-reveal">
          <p><strong>{createdKey.name}</strong> — copy this key now. It won't be shown again.</p>
          <div class="key-value">
            <code>{createdKey.key}</code>
            <button class="btn small" onclick={copyKey}>Copy</button>
          </div>
        </div>
      {/if}

      <h2 class="spaced">Keys</h2>
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Prefix</th>
              <th>Created</th>
              <th>Last used</th>
              <th>Status</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {#each apiKeys as key (key.id)}
              <tr class:revoked={key.revoked}>
                <td>{key.name}</td>
                <td class="num">{key.prefix}…</td>
                <td class="num">{fmtDate(key.created_at)}</td>
                <td class="num">{key.last_used_at ? fmtDate(key.last_used_at) : "—"}</td>
                <td>
                  {#if key.revoked}
                    <span class="badge">revoked</span>
                  {:else}
                    <span class="badge warn">active</span>
                  {/if}
                </td>
                <td class="right">
                  {#if !key.revoked}
                    <button class="btn small danger" onclick={() => revokeKey(key)}>Revoke</button>
                  {/if}
                </td>
              </tr>
            {:else}
              <tr><td colspan="6" class="muted">No API keys yet — create one above.</td></tr>
            {/each}
          </tbody>
        </table>
      </div>
    </section>
  {:else}
    <section class="card">
      <h2>Defaults</h2>
      <form onsubmit={saveSettings}>
        <div class="field">
          <label for="set-cost">Default cost bps for new portfolios</label>
          <input id="set-cost" type="number" min="0" bind:value={defaultCostBps} />
        </div>
        <button class="btn primary" type="submit">Save settings</button>
      </form>
      {#if settingsError}
        <div class="error-box" role="alert">{settingsError}</div>
      {/if}

      <h2 class="spaced">Price cache</h2>
      <p class="muted cache-note">
        Series are cached for an hour. Clearing forces a fresh Yahoo fetch on the next request.
      </p>
      <button class="btn" onclick={clearCache}>Clear price cache</button>
    </section>
  {/if}
{/if}

<style>
  .login-wrap {
    display: flex;
    justify-content: center;
    padding: 60px 0;
  }

  .login {
    width: 380px;
    max-width: 100%;
    display: flex;
    flex-direction: column;
    gap: 14px;
  }

  h1 {
    font-size: 20px;
  }

  h2 {
    font-size: 15px;
    margin: 4px 0 12px;
    display: flex;
    align-items: center;
    gap: 10px;
  }

  h2.spaced {
    margin-top: 26px;
  }

  .capability {
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-sm);
    padding: 12px;
    margin: 12px 0;
  }

  .check-group {
    display: flex;
    gap: 12px;
    flex-wrap: wrap;
    margin-top: 6px;
  }

  .check-group label,
  .toggle-label {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .admin-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 14px;
  }

  .notice {
    border: 1px solid var(--pos);
    color: var(--pos);
    background: color-mix(in srgb, var(--pos) 8%, transparent);
    border-radius: var(--radius-sm);
    padding: 10px 12px;
    margin-bottom: 12px;
  }

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

  .field {
    margin-bottom: 14px;
  }

  .history {
    margin-bottom: 20px;
  }

  .actions {
    white-space: nowrap;
  }

  .actions .btn + .btn {
    margin-left: 6px;
  }

  .prefill-note {
    font-size: 12.5px;
    margin: -6px 0 12px;
  }

  .state-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
    margin-top: 20px;
  }

  .state-head h2 {
    margin: 0;
  }

  .grid-2 {
    display: grid;
    grid-template-columns: 1fr 200px;
    gap: 14px;
  }

  .weight-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .hint {
    font-size: 12.5px;
    margin-top: 6px;
  }

  .row-actions {
    display: flex;
    gap: 6px;
    white-space: nowrap;
  }

  .manage-row {
    display: flex;
    align-items: start;
    justify-content: space-between;
    gap: 12px;
    padding: 10px 0;
    border-bottom: 1px solid var(--border-subtle);
  }

  .manage-row:last-child {
    border-bottom: none;
  }

  .preview {
    font-size: 12.5px;
    margin-top: 2px;
  }

  .edit-form {
    padding: 12px;
    border: 1px dashed var(--border-strong);
    border-radius: var(--radius-sm);
    margin: 8px 0;
  }

  .edit-actions {
    display: flex;
    gap: 8px;
  }

  .cache-note {
    font-size: 12.5px;
    margin-bottom: 10px;
  }

  .key-reveal {
    border: 1px solid var(--pos);
    background: color-mix(in srgb, var(--pos) 8%, transparent);
    border-radius: var(--radius-sm);
    padding: 12px;
    margin: 14px 0;
  }

  .key-reveal p {
    margin: 0 0 8px;
    font-size: 13px;
  }

  .key-value {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .key-value code {
    flex: 1;
    overflow-x: auto;
    white-space: nowrap;
    padding: 6px 8px;
    background: var(--surface-sunken, rgba(127, 127, 127, 0.12));
    border-radius: var(--radius-sm);
    font-size: 12.5px;
  }

  tr.revoked td {
    color: var(--text-secondary);
    opacity: 0.6;
  }

  @media (max-width: 800px) {
    .grid-2 {
      grid-template-columns: 1fr;
    }
  }
</style>
