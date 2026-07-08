<script lang="ts">
  import { apiJson, del, patchJson, postJson, putJson } from "../api/client";
  import type {
    AgentOut,
    AllocationOut,
    LeaderboardResponse,
    PortfolioDetail,
    PortfolioSummary,
    PromptOut,
  } from "../api/types";
  import AllocationForm, { type AllocationPayload } from "../components/AllocationForm.svelte";
  import { fmtDate, pctPoints } from "../format";
  import { auth } from "../stores/auth.svelte";
  import { router } from "../stores/router.svelte";

  type Tab = "allocation" | "portfolio" | "manage" | "settings";
  let tab = $state<Tab>("allocation");

  // ── Login ────────────────────────────────────
  let email = $state("");
  let password = $state("");
  let loginError = $state("");
  let loggingIn = $state(false);

  async function login(event: SubmitEvent) {
    event.preventDefault();
    loggingIn = true;
    loginError = "";
    try {
      await auth.login(email, password);
      password = "";
    } catch (e) {
      loginError = e instanceof Error ? e.message : "Login failed";
    } finally {
      loggingIn = false;
    }
  }

  // ── Shared data ──────────────────────────────
  let portfolios = $state<PortfolioSummary[]>([]);
  let prompts = $state<PromptOut[]>([]);
  let agents = $state<AgentOut[]>([]);
  let notice = $state("");
  let noticeTimer: ReturnType<typeof setTimeout> | undefined;

  function flash(message: string) {
    notice = message;
    clearTimeout(noticeTimer);
    noticeTimer = setTimeout(() => (notice = ""), 5000);
  }

  async function loadAll() {
    const [leaderboard, promptsPayload, agentsPayload] = await Promise.all([
      apiJson<LeaderboardResponse>("/api/leaderboard", { auth: false }),
      apiJson<{ prompts: PromptOut[] }>("/api/prompts", { auth: false }),
      apiJson<{ agents: AgentOut[] }>("/api/agents", { auth: false }),
    ]);
    portfolios = leaderboard.portfolios;
    prompts = promptsPayload.prompts;
    agents = agentsPayload.agents.filter((agent) => agent.slug !== "benchmark");
  }

  async function refreshPrompts() {
    const payload = await apiJson<{ prompts: PromptOut[] }>("/api/prompts", { auth: false });
    prompts = payload.prompts;
  }

  $effect(() => {
    if (auth.isAdmin) void loadAll();
  });

  const contestants = $derived(portfolios.filter((portfolio) => !portfolio.is_benchmark));

  // ── Tab 1: allocations ───────────────────────
  let selectedSlug = $state("");
  let detail = $state<PortfolioDetail | null>(null);
  let detailLoading = $state(false);
  let editingAllocation = $state<AllocationOut | null>(null);
  let formKey = $state(0);

  $effect(() => {
    if (!selectedSlug) {
      detail = null;
      editingAllocation = null;
      return;
    }
    void loadDetail(selectedSlug);
  });

  async function loadDetail(slug: string) {
    detailLoading = true;
    editingAllocation = null;
    try {
      const payload = await apiJson<{ portfolio: PortfolioDetail }>(`/api/portfolios/${slug}`, {
        auth: false,
      });
      detail = payload.portfolio;
      formKey += 1;
    } finally {
      detailLoading = false;
    }
  }

  const latestAllocation = $derived(detail?.allocations[0] ?? null);

  async function submitRebalance(payload: AllocationPayload) {
    if (!detail) return;
    await postJson(`/api/portfolios/${detail.id}/allocations`, payload);
    flash(`Rebalance for ${detail.name} entered.`);
    await loadDetail(detail.slug);
  }

  async function submitEdit(payload: AllocationPayload) {
    if (!editingAllocation || !detail) return;
    const body: Record<string, unknown> = {
      prompt_id: payload.prompt_id,
      note: payload.note,
      raw_response: payload.raw_response,
    };
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

  // ── Tab 2: new portfolio ─────────────────────
  let newName = $state("");
  let newAgentId = $state<number | null>(null);
  let newCostBps = $state<string>("");
  let newAgentOpen = $state(false);
  let newAgentName = $state("");
  let portfolioError = $state("");

  async function createAgentInline() {
    if (!newAgentName.trim()) return;
    const created = await postJson<AgentOut>("/api/agents", { name: newAgentName.trim() });
    await loadAll();
    newAgentId = created.id;
    newAgentOpen = false;
    newAgentName = "";
  }

  async function submitNewPortfolio(payload: AllocationPayload) {
    portfolioError = "";
    if (!newName.trim() || newAgentId === null) {
      throw new Error("Portfolio name and agent are required.");
    }
    const body: Record<string, unknown> = {
      name: newName.trim(),
      agent_id: newAgentId,
      allocation: payload,
    };
    if (newCostBps.trim() !== "") body.cost_bps = parseInt(newCostBps, 10);
    const created = await postJson<{ slug: string; name: string }>("/api/portfolios", body);
    flash(`Portfolio ${created.name} created.`);
    newName = "";
    newAgentId = null;
    newCostBps = "";
    await loadAll();
    router.navigate(`/p/${created.slug}`);
  }

  // ── Tab 3: manage ────────────────────────────
  let editPrompt = $state<PromptOut | null>(null);
  let editAgent = $state<AgentOut | null>(null);

  async function savePrompt(event: SubmitEvent) {
    event.preventDefault();
    if (!editPrompt) return;
    await patchJson(`/api/prompts/${editPrompt.id}`, {
      name: editPrompt.name,
      text: editPrompt.text,
      notes: editPrompt.notes,
    });
    editPrompt = null;
    await refreshPrompts();
    flash("Prompt saved.");
  }

  async function saveAgent(event: SubmitEvent) {
    event.preventDefault();
    if (!editAgent) return;
    await patchJson(`/api/agents/${editAgent.id}`, { name: editAgent.name, notes: editAgent.notes });
    editAgent = null;
    await loadAll();
    flash("Agent saved.");
  }

  async function toggleArchive(portfolio: PortfolioSummary) {
    await patchJson(`/api/portfolios/${portfolio.id}`, {
      status: portfolio.status === "active" ? "archived" : "active",
    });
    await loadAll();
  }

  // ── Tab 4: settings ──────────────────────────
  let defaultCostBps = $state<string>("");
  let currentPassword = $state("");
  let newPassword = $state("");
  let settingsError = $state("");

  $effect(() => {
    if (auth.isAdmin && tab === "settings") {
      apiJson<{ default_cost_bps: number }>("/api/settings")
        .then((payload) => (defaultCostBps = String(payload.default_cost_bps)))
        .catch(() => {});
    }
  });

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

  async function changePassword(event: SubmitEvent) {
    event.preventDefault();
    settingsError = "";
    try {
      await putJson("/api/auth/password", {
        current_password: currentPassword,
        new_password: newPassword,
      });
      currentPassword = "";
      newPassword = "";
      flash("Password changed.");
    } catch (e) {
      settingsError = e instanceof Error ? e.message : "Password change failed";
    }
  }

  async function clearCache() {
    const result = await del<{ deleted: number }>("/api/prices/cache");
    flash(`Price cache cleared (${result.deleted} entries).`);
  }
</script>

{#if auth.restoring}
  <div class="loading-block"><span class="spinner" aria-hidden="true"></span></div>
{:else if !auth.isAdmin}
  <div class="login-wrap">
    <form class="card login" onsubmit={login}>
      <h1>Admin login</h1>
      <div class="field">
        <label for="login-email">Email</label>
        <input id="login-email" type="email" bind:value={email} autocomplete="username" required />
      </div>
      <div class="field">
        <label for="login-password">Password</label>
        <input
          id="login-password"
          type="password"
          bind:value={password}
          autocomplete="current-password"
          required
        />
      </div>
      {#if loginError}
        <div class="error-box" role="alert">{loginError}</div>
      {/if}
      <button class="btn primary" type="submit" disabled={loggingIn}>
        {loggingIn ? "Signing in…" : "Sign in"}
      </button>
    </form>
  </div>
{:else}
  <div class="admin-head">
    <h1>Admin</h1>
    <button class="btn small" onclick={() => auth.logout()}>Log out</button>
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
        onclick={() => (tab = id)}
      >
        {label}
      </button>
    {/snippet}
    {@render tabBtn("allocation", "Allocations")}
    {@render tabBtn("portfolio", "New portfolio")}
    {@render tabBtn("manage", "Agents & prompts")}
    {@render tabBtn("settings", "Settings")}
  </div>

  {#if tab === "allocation"}
    <section class="card">
      <div class="field">
        <label for="portfolio-select">Portfolio</label>
        <select id="portfolio-select" bind:value={selectedSlug}>
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
        <h2>Allocation history</h2>
        <div class="table-scroll history">
          <table>
            <thead>
              <tr>
                <th>Effective</th>
                <th>Prompt</th>
                <th>Status</th>
                <th class="right">Positions</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {#each detail.allocations as allocation (allocation.id)}
                <tr>
                  <td class="num">{fmtDate(allocation.effective_date)}</td>
                  <td>{allocation.prompt?.name ?? "—"}</td>
                  <td>
                    {#if allocation.locked}
                      <span class="badge">locked</span>
                    {:else}
                      <span class="badge warn">editable until close</span>
                    {/if}
                  </td>
                  <td class="right muted">
                    {allocation.positions
                      .map((p) => `${p.symbol} ${pctPoints(p.weight_pct, 1)}`)
                      .join(", ")}
                  </td>
                  <td class="right actions">
                    <button class="btn small" onclick={() => (editingAllocation = allocation)}>
                      Edit
                    </button>
                    {#if !allocation.locked}
                      <button class="btn small danger" onclick={() => deleteAllocation(allocation)}>
                        Delete
                      </button>
                    {/if}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>

        {#if editingAllocation}
          <h2>
            Edit allocation effective {editingAllocation.effective_date}
            <button class="btn small" onclick={() => (editingAllocation = null)}>Cancel</button>
          </h2>
          {#key editingAllocation.id}
            <AllocationForm
              {prompts}
              onPromptsChanged={refreshPrompts}
              initialPositions={editingAllocation.positions}
              initialPromptId={editingAllocation.prompt?.id ?? null}
              initialNote={editingAllocation.note}
              initialRawResponse={editingAllocation.raw_response}
              positionsEditable={!editingAllocation.locked}
              submitLabel="Save changes"
              onSubmit={submitEdit}
            />
          {/key}
        {:else}
          <h2>New rebalance</h2>
          <p class="muted prefill-note">
            Pre-filled with the previous allocation's target weights as the starting point.
          </p>
          {#key formKey}
            <AllocationForm
              {prompts}
              onPromptsChanged={refreshPrompts}
              initialPositions={latestAllocation?.positions ?? []}
              initialPromptId={latestAllocation?.prompt?.id ?? null}
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
  {:else if tab === "portfolio"}
    <section class="card">
      <h2>New portfolio</h2>
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
        <div class="agent-line">
          <select id="np-agent" bind:value={newAgentId}>
            <option value={null} disabled>Select an agent…</option>
            {#each agents as agent (agent.id)}
              <option value={agent.id}>{agent.name}</option>
            {/each}
          </select>
          <button type="button" class="btn" onclick={() => (newAgentOpen = !newAgentOpen)}>
            {newAgentOpen ? "Cancel" : "+ New agent"}
          </button>
        </div>
      </div>
      {#if newAgentOpen}
        <div class="card inline-create">
          <div class="field">
            <label for="np-agent-name">Agent name <span class="muted">(model + harness)</span></label>
            <input
              id="np-agent-name"
              type="text"
              bind:value={newAgentName}
              placeholder="Claude Opus 4.8 (Claude Code)"
            />
          </div>
          <button
            type="button"
            class="btn primary"
            onclick={createAgentInline}
            disabled={!newAgentName.trim()}
          >
            Create agent
          </button>
        </div>
      {/if}

      {#if portfolioError}
        <div class="error-box" role="alert">{portfolioError}</div>
      {/if}

      <h2>First allocation</h2>
      <AllocationForm
        {prompts}
        onPromptsChanged={refreshPrompts}
        submitLabel="Create portfolio"
        onSubmit={submitNewPortfolio}
      />
    </section>
  {:else if tab === "manage"}
    <div class="manage-grid">
      <section class="card">
        <h2>Prompts</h2>
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
              <div class="edit-actions">
                <button class="btn primary" type="submit">Save</button>
                <button class="btn" type="button" onclick={() => (editPrompt = null)}>Cancel</button>
              </div>
            </form>
          {:else}
            <div class="manage-row">
              <div>
                <strong>{prompt.name}</strong>
                <span class="muted"> · {prompt.portfolio_count ?? 0} portfolio(s)</span>
                <p class="muted preview">{prompt.text.slice(0, 140)}{prompt.text.length > 140 ? "…" : ""}</p>
              </div>
              <button class="btn small" onclick={() => (editPrompt = { ...prompt })}>Edit</button>
            </div>
          {/if}
        {:else}
          <div class="empty-state"><p>No prompts yet — create one from an allocation form.</p></div>
        {/each}
      </section>

      <section class="card">
        <h2>Agents</h2>
        {#each agents as agent (agent.id)}
          {#if editAgent?.id === agent.id}
            <form class="edit-form" onsubmit={saveAgent}>
              <div class="field">
                <label for="ea-name-{agent.id}">Name</label>
                <input id="ea-name-{agent.id}" type="text" bind:value={editAgent.name} />
              </div>
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
            <div class="manage-row">
              <div>
                <strong>{agent.name}</strong>
                {#if agent.notes}<p class="muted preview">{agent.notes}</p>{/if}
              </div>
              <button class="btn small" onclick={() => (editAgent = { ...agent })}>Edit</button>
            </div>
          {/if}
        {:else}
          <div class="empty-state"><p>No agents yet — create one with your first portfolio.</p></div>
        {/each}

        <h2 class="spaced">Portfolios</h2>
        {#each contestants as portfolio (portfolio.id)}
          <div class="manage-row">
            <div>
              <strong>{portfolio.name}</strong>
              <span class="muted"> · {portfolio.status}</span>
            </div>
            <button class="btn small" onclick={() => toggleArchive(portfolio)}>
              {portfolio.status === "active" ? "Archive" : "Unarchive"}
            </button>
          </div>
        {/each}
      </section>
    </div>
  {:else}
    <div class="manage-grid">
      <section class="card">
        <h2>Defaults</h2>
        <form onsubmit={saveSettings}>
          <div class="field">
            <label for="set-cost">Default cost bps for new portfolios</label>
            <input id="set-cost" type="number" min="0" bind:value={defaultCostBps} />
          </div>
          <button class="btn primary" type="submit">Save settings</button>
        </form>

        <h2 class="spaced">Price cache</h2>
        <p class="muted cache-note">
          Series are cached for an hour. Clearing forces a fresh Yahoo fetch on the next request.
        </p>
        <button class="btn" onclick={clearCache}>Clear price cache</button>
      </section>

      <section class="card">
        <h2>Change password</h2>
        <form onsubmit={changePassword}>
          <div class="field">
            <label for="pw-current">Current password</label>
            <input
              id="pw-current"
              type="password"
              bind:value={currentPassword}
              autocomplete="current-password"
              required
            />
          </div>
          <div class="field">
            <label for="pw-new">New password <span class="muted">(min 8 chars)</span></label>
            <input
              id="pw-new"
              type="password"
              bind:value={newPassword}
              autocomplete="new-password"
              minlength="8"
              required
            />
          </div>
          <button class="btn primary" type="submit">Change password</button>
        </form>
        {#if settingsError}
          <div class="error-box" role="alert">{settingsError}</div>
        {/if}
      </section>
    </div>
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

  .grid-2 {
    display: grid;
    grid-template-columns: 1fr 200px;
    gap: 14px;
  }

  .agent-line {
    display: flex;
    gap: 8px;
  }

  .agent-line select {
    flex: 1;
  }

  .inline-create {
    border-style: dashed;
    margin-bottom: 14px;
  }

  .manage-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
    align-items: start;
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

  @media (max-width: 800px) {
    .manage-grid,
    .grid-2 {
      grid-template-columns: 1fr;
    }
  }
</style>
