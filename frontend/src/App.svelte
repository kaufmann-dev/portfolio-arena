<script lang="ts">
  import { auth } from "./lib/stores/auth.svelte";
  import { link, router } from "./lib/stores/router.svelte";
  import { theme } from "./lib/stores/theme.svelte";
  import Leaderboard from "./lib/pages/Leaderboard.svelte";
  import PortfolioDetail from "./lib/pages/PortfolioDetail.svelte";
  import PromptDetail from "./lib/pages/PromptDetail.svelte";
  import AgentDetail from "./lib/pages/AgentDetail.svelte";
  import Admin from "./lib/pages/Admin.svelte";
  import About from "./lib/pages/About.svelte";

  $effect(() => {
    void auth.restore();
  });
</script>

<div class="shell">
  <header class="topbar">
    <a href="/" class="brand" onclick={(e) => link(e, "/")}>
      <span class="brand-mark" aria-hidden="true">◮</span>
      Portfolio Arena
    </a>
    <nav aria-label="Main">
      <a href="/" class:active={router.route.name === "home"} onclick={(e) => link(e, "/")}>
        Leaderboard
      </a>
      <a href="/about" class:active={router.route.name === "about"} onclick={(e) => link(e, "/about")}>
        About
      </a>
      <a href="/admin" class:active={router.route.name === "admin"} onclick={(e) => link(e, "/admin")}>
        Admin
      </a>
    </nav>
    <div class="topbar-right">
      {#if auth.isAdmin}
        <span class="muted session-email">{auth.email}</span>
      {/if}
      <button
        class="btn small"
        onclick={() => theme.toggle()}
        aria-label="Switch to {theme.theme === 'dark' ? 'light' : 'dark'} theme"
      >
        {theme.theme === "dark" ? "☀ Light" : "☾ Dark"}
      </button>
    </div>
  </header>

  <main>
    {#if router.route.name === "home"}
      <Leaderboard />
    {:else if router.route.name === "portfolio"}
      <PortfolioDetail slug={router.route.params.slug} />
    {:else if router.route.name === "prompt"}
      <PromptDetail slug={router.route.params.slug} />
    {:else if router.route.name === "agent"}
      <AgentDetail slug={router.route.params.slug} />
    {:else if router.route.name === "admin"}
      <Admin />
    {:else}
      <About />
    {/if}
  </main>

  <footer class="footer muted">
    Paper portfolios valued from Yahoo Finance adjusted closes. Not investment advice.
  </footer>
</div>

<style>
  .shell {
    max-width: 1240px;
    margin: 0 auto;
    padding: 0 16px 40px;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
  }

  .topbar {
    display: flex;
    align-items: center;
    gap: 20px;
    padding: 14px 0;
    border-bottom: 1px solid var(--border-subtle);
    margin-bottom: 22px;
    flex-wrap: wrap;
  }

  .brand {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 16px;
    font-weight: 700;
    color: var(--text-primary);
  }

  .brand:hover {
    text-decoration: none;
    color: var(--accent);
  }

  .brand-mark {
    color: var(--accent);
  }

  nav {
    display: flex;
    gap: 4px;
  }

  nav a {
    padding: 6px 12px;
    border-radius: var(--radius-sm);
    color: var(--text-secondary);
    font-weight: 500;
  }

  nav a:hover {
    color: var(--text-primary);
    background: var(--bg-surface-hover);
    text-decoration: none;
  }

  nav a.active {
    color: var(--accent);
    background: var(--accent-bg);
  }

  .topbar-right {
    margin-left: auto;
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .session-email {
    font-size: 12.5px;
  }

  main {
    flex: 1;
  }

  .footer {
    margin-top: 48px;
    padding-top: 16px;
    border-top: 1px solid var(--border-subtle);
    font-size: 12.5px;
  }

  @media (max-width: 640px) {
    .session-email {
      display: none;
    }
  }
</style>
