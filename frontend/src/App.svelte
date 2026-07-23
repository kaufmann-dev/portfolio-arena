<script lang="ts">
  import { ChevronDown, Landmark, LogOut, Menu, Moon, Settings, Sun, UserRound, X } from "@lucide/svelte";
  import { Dialog, DropdownMenu, Tooltip } from "bits-ui";
  import { onMount } from "svelte";

  import { apiFetch } from "./lib/api/client";
  import About from "./lib/pages/About.svelte";
  import Admin from "./lib/pages/Admin.svelte";
  import AgentDetail from "./lib/pages/AgentDetail.svelte";
  import Leaderboard from "./lib/pages/Leaderboard.svelte";
  import PortfolioDetail from "./lib/pages/PortfolioDetail.svelte";
  import PromptDetail from "./lib/pages/PromptDetail.svelte";
  import { auth } from "./lib/stores/auth.svelte";
  import { link, router } from "./lib/stores/router.svelte";
  import { theme } from "./lib/stores/theme.svelte";

  const ACTIVITY_INTERVAL_MS = 5 * 60 * 1000;
  const ACTIVITY_EVENT_TYPES = new Set(["pointerdown", "keydown", "click"]);
  const navItems = [
    { href: "/", label: "Leaderboard", route: "home" },
    { href: "/about", label: "About", route: "about" },
    { href: "/admin", label: "Admin", route: "admin" },
  ] as const;

  let lastActivitySentAt = Number.NEGATIVE_INFINITY;
  let mobileNavOpen = $state(false);
  let hasMountedRoute = false;

  const routeLabel = $derived.by(() => {
    switch (router.route.name) {
      case "home":
        return "Leaderboard";
      case "portfolio":
        return `${router.route.params.slug} portfolio`;
      case "prompt":
        return `${router.route.params.slug} prompt`;
      case "agent":
        return `${router.route.params.slug} agent`;
      case "admin":
        return "Admin";
      case "about":
        return "About";
    }
  });
  const pageTitle = $derived(`${routeLabel} · Portfolio Arena`);
  const nextThemeLabel = $derived(theme.theme === "dark" ? "Use light theme" : "Use dark theme");

  function reportActivity(event: Event): void {
    if (!event.isTrusted || !ACTIVITY_EVENT_TYPES.has(event.type) || !auth.isAuthenticated) return;

    const now = performance.now();
    if (now - lastActivitySentAt < ACTIVITY_INTERVAL_MS) return;
    lastActivitySentAt = now;

    void apiFetch("/api/auth/activity", {
      method: "POST",
      headers: { "X-Portfolio-Arena-Activity": "1" },
    }).catch(() => undefined);
  }

  function navigateFromMobile(event: MouseEvent, path: string): void {
    mobileNavOpen = false;
    link(event, path);
  }

  function submitLogout(event: Event): void {
    (event.currentTarget as HTMLElement).closest("form")?.requestSubmit();
  }

  function focusRoute(element: HTMLElement): void {
    if (hasMountedRoute) {
      element.focus({ preventScroll: true });
    } else {
      hasMountedRoute = true;
    }
  }

  onMount(() => {
    void auth.restore();
  });
</script>

<svelte:head>
  <title>{pageTitle}</title>
</svelte:head>

<svelte:window onpointerdown={reportActivity} onkeydown={reportActivity} onclick={reportActivity} />

<a class="skip-link" href="#main-content">Skip to content</a>

<Tooltip.Provider delayDuration={350}>
  <div class="shell">
    <header class="topbar">
      <div class="topbar-inner">
        <a href="/" class="brand" onclick={(event) => link(event, "/")}>
          <span class="brand-mark" aria-hidden="true"><Landmark size={19} strokeWidth={1.8} /></span>
          <span>Portfolio Arena</span>
        </a>

        <nav class="desktop-nav" aria-label="Main navigation">
          {#each navItems as item (item.href)}
            <a
              href={item.href}
              class={{ active: router.route.name === item.route }}
              aria-current={router.route.name === item.route ? "page" : undefined}
              onclick={(event) => link(event, item.href)}
            >
              {item.label}
            </a>
          {/each}
        </nav>

        <div class="desktop-actions">
          {#if !auth.restoring && auth.isAuthenticated}
            <DropdownMenu.Root>
              <DropdownMenu.Trigger class="account-trigger" aria-label="Open account menu">
                <UserRound size={17} aria-hidden="true" />
                <span class="account-name">{auth.displayName}</span>
                <ChevronDown size={14} aria-hidden="true" />
              </DropdownMenu.Trigger>
              <DropdownMenu.Portal>
                <DropdownMenu.Content class="account-menu" align="end" sideOffset={8} loop>
                  <DropdownMenu.Group>
                    <DropdownMenu.GroupHeading class="account-heading">Account</DropdownMenu.GroupHeading>
                    <DropdownMenu.Item class="account-item" onSelect={() => router.navigate("/admin")}>
                      <Settings size={16} aria-hidden="true" />
                      Admin settings
                    </DropdownMenu.Item>
                    <DropdownMenu.Item class="account-item" onSelect={() => theme.toggle()}>
                      {#if theme.theme === "dark"}
                        <Sun size={16} aria-hidden="true" />
                        Light theme
                      {:else}
                        <Moon size={16} aria-hidden="true" />
                        Dark theme
                      {/if}
                    </DropdownMenu.Item>
                    <DropdownMenu.Separator class="account-separator" />
                    <form method="POST" action="/api/auth/logout">
                      <DropdownMenu.Item class="account-item danger-item" onSelect={submitLogout}>
                        <LogOut size={16} aria-hidden="true" />
                        Log out
                      </DropdownMenu.Item>
                    </form>
                  </DropdownMenu.Group>
                </DropdownMenu.Content>
              </DropdownMenu.Portal>
            </DropdownMenu.Root>
          {:else if !auth.restoring}
            <a class="btn small sign-in" href="/api/auth/login">Sign in</a>
          {/if}

          <Tooltip.Root>
            <Tooltip.Trigger
              class="icon-action"
              type="button"
              onclick={() => theme.toggle()}
              aria-label={nextThemeLabel}
            >
              {#if theme.theme === "dark"}
                <Sun size={18} aria-hidden="true" />
              {:else}
                <Moon size={18} aria-hidden="true" />
              {/if}
            </Tooltip.Trigger>
            <Tooltip.Portal>
              <Tooltip.Content class="shell-tooltip" sideOffset={8}>{nextThemeLabel}</Tooltip.Content>
            </Tooltip.Portal>
          </Tooltip.Root>
        </div>

        <div class="mobile-actions">
          <Tooltip.Root>
            <Tooltip.Trigger
              class="icon-action"
              type="button"
              onclick={() => theme.toggle()}
              aria-label={nextThemeLabel}
            >
              {#if theme.theme === "dark"}
                <Sun size={19} aria-hidden="true" />
              {:else}
                <Moon size={19} aria-hidden="true" />
              {/if}
            </Tooltip.Trigger>
            <Tooltip.Portal>
              <Tooltip.Content class="shell-tooltip" sideOffset={8}>{nextThemeLabel}</Tooltip.Content>
            </Tooltip.Portal>
          </Tooltip.Root>

          <Dialog.Root bind:open={mobileNavOpen}>
            <Dialog.Trigger class="icon-action" aria-label="Open navigation">
              <Menu size={21} aria-hidden="true" />
            </Dialog.Trigger>
            <Dialog.Portal>
              <Dialog.Overlay class="mobile-nav-overlay" />
              <Dialog.Content class="mobile-nav-panel">
                <div class="mobile-nav-head">
                  <div>
                    <Dialog.Title class="mobile-nav-title">Navigation</Dialog.Title>
                    <Dialog.Description class="visually-hidden">Primary navigation</Dialog.Description>
                  </div>
                  <Dialog.Close class="icon-action" aria-label="Close navigation">
                    <X size={21} aria-hidden="true" />
                  </Dialog.Close>
                </div>

                <nav class="mobile-nav-links" aria-label="Mobile navigation">
                  {#each navItems as item (item.href)}
                    <a
                      href={item.href}
                      class={{ active: router.route.name === item.route }}
                      aria-current={router.route.name === item.route ? "page" : undefined}
                      onclick={(event) => navigateFromMobile(event, item.href)}
                    >
                      <span>{item.label}</span>
                      <span aria-hidden="true">→</span>
                    </a>
                  {/each}
                </nav>

                <div class="mobile-session">
                  {#if !auth.restoring && auth.isAuthenticated}
                    <p>
                      <span class="mobile-session-label">Signed in as</span>
                      <strong>{auth.displayName}</strong>
                    </p>
                    <form method="POST" action="/api/auth/logout">
                      <button class="btn mobile-session-action" type="submit">
                        <LogOut size={17} aria-hidden="true" />
                        Log out
                      </button>
                    </form>
                  {:else if !auth.restoring}
                    <a class="btn primary mobile-session-action" href="/api/auth/login">Sign in</a>
                  {/if}
                </div>
              </Dialog.Content>
            </Dialog.Portal>
          </Dialog.Root>
        </div>
      </div>
    </header>

    <div class="route-announcer" aria-live="polite" aria-atomic="true">{routeLabel}</div>

    {#key pageTitle}
      <main id="main-content" tabindex="-1" {@attach focusRoute}>
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
    {/key}
  </div>
</Tooltip.Provider>

<style>
  .skip-link {
    position: fixed;
    top: 0;
    left: 0;
    z-index: 100;
    padding: 10px 14px;
    border: 1px solid var(--accent);
    background: var(--bg-base);
    color: var(--text-primary);
    transform: translateY(-120%);
  }

  .skip-link:focus {
    transform: translateY(0);
  }

  .shell {
    min-height: 100vh;
    display: flex;
    flex-direction: column;
  }

  .topbar {
    position: sticky;
    top: 0;
    z-index: 30;
    border-bottom: 1px solid var(--border-subtle);
    background: var(--bg-base);
  }

  .topbar-inner {
    width: min(100%, 1440px);
    min-height: 56px;
    margin: 0 auto;
    padding: 0 12px;
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .brand {
    min-height: 44px;
    display: inline-flex;
    align-items: center;
    gap: 9px;
    color: var(--text-primary);
    font-size: 15px;
    font-weight: 720;
    letter-spacing: -0.02em;
    text-decoration: none;
  }

  .brand:hover,
  .brand:focus-visible {
    color: var(--text-primary);
    text-decoration: none;
  }

  .brand-mark {
    display: inline-flex;
    color: var(--accent);
  }

  .desktop-nav,
  .desktop-actions {
    display: none;
  }

  .mobile-actions {
    margin-left: auto;
    display: flex;
    align-items: center;
    gap: 4px;
  }

  :global(.icon-action) {
    width: 44px;
    height: 44px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border: 1px solid transparent;
    border-radius: 0;
    color: var(--text-secondary);
  }

  :global(.icon-action:hover) {
    border-color: var(--border-strong);
    background: var(--bg-surface-hover);
    color: var(--text-primary);
  }

  main {
    width: min(100%, 1440px);
    margin: 0 auto;
    padding: 24px 12px 48px;
    flex: 1;
  }

  main:focus {
    outline: none;
  }

  .route-announcer {
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

  :global(.shell-tooltip) {
    z-index: 80;
    padding: 6px 9px;
    border: 1px solid var(--border-strong);
    border-radius: 0;
    background: var(--bg-surface);
    color: var(--text-primary);
    font-size: 12px;
  }

  :global(.account-trigger) {
    min-height: 40px;
    max-width: 240px;
    padding: 0 10px;
    display: inline-flex;
    align-items: center;
    gap: 7px;
    border: 1px solid var(--border-subtle);
    border-radius: 0;
    background: transparent;
    color: var(--text-secondary);
  }

  :global(.account-trigger:hover),
  :global(.account-trigger[data-state="open"]) {
    border-color: var(--border-strong);
    background: var(--bg-surface-hover);
    color: var(--text-primary);
  }

  .account-name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  :global(.account-menu) {
    z-index: 70;
    min-width: 220px;
    padding: 4px;
    border: 1px solid var(--border-strong);
    border-radius: 0;
    background: var(--bg-surface);
    color: var(--text-primary);
  }

  :global(.account-heading) {
    padding: 8px 10px 6px;
    color: var(--text-tertiary);
    font-size: 11px;
    font-weight: 650;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  :global(.account-item) {
    min-height: 40px;
    padding: 8px 10px;
    display: flex;
    align-items: center;
    gap: 9px;
    outline: none;
    color: var(--text-secondary);
    cursor: pointer;
  }

  :global(.account-item[data-highlighted]) {
    background: var(--bg-surface-hover);
    color: var(--text-primary);
  }

  :global(.account-separator) {
    height: 1px;
    margin: 4px 0;
    background: var(--border-subtle);
  }

  :global(.danger-item) {
    color: var(--neg);
  }

  :global(.mobile-nav-overlay) {
    position: fixed;
    inset: 0;
    z-index: 60;
    background: rgb(0 0 0 / 72%);
  }

  :global(.mobile-nav-panel) {
    position: fixed;
    inset: 0 0 0 auto;
    z-index: 61;
    width: min(88vw, 380px);
    padding: 18px;
    display: flex;
    flex-direction: column;
    border-left: 1px solid var(--border-strong);
    border-radius: 0;
    background: var(--bg-base);
    color: var(--text-primary);
    outline: none;
  }

  .mobile-nav-head {
    display: flex;
    align-items: start;
    justify-content: space-between;
    gap: 16px;
    padding-bottom: 18px;
    border-bottom: 1px solid var(--border-subtle);
  }

  :global(.mobile-nav-title) {
    display: block;
    font-size: 20px;
    font-weight: 700;
    letter-spacing: -0.02em;
  }

  .mobile-nav-links {
    display: flex;
    flex-direction: column;
  }

  .mobile-nav-links a {
    min-height: 56px;
    padding: 0 4px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    border-bottom: 1px solid var(--border-subtle);
    color: var(--text-secondary);
    font-size: 17px;
    font-weight: 600;
    text-decoration: none;
  }

  .mobile-nav-links a:hover,
  .mobile-nav-links a.active {
    color: var(--text-primary);
    text-decoration: none;
  }

  .mobile-nav-links a.active {
    border-bottom-color: var(--accent);
  }

  .mobile-session {
    margin-top: auto;
    padding-top: 18px;
    border-top: 1px solid var(--border-subtle);
  }

  .mobile-session p {
    margin-bottom: 12px;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .mobile-session-label {
    color: var(--text-tertiary);
    font-size: 11px;
    font-weight: 650;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  .mobile-session-action {
    width: 100%;
    min-height: 44px;
    justify-content: center;
  }

  @media (min-width: 760px) {
    .topbar-inner {
      min-height: 64px;
      padding-inline: 24px;
      gap: 24px;
    }

    .desktop-nav {
      display: flex;
      align-items: stretch;
      align-self: stretch;
      gap: 4px;
    }

    .desktop-nav a {
      min-width: 44px;
      padding: 0 10px;
      display: inline-flex;
      align-items: center;
      border-bottom: 2px solid transparent;
      color: var(--text-secondary);
      font-size: 13px;
      font-weight: 550;
      text-decoration: none;
    }

    .desktop-nav a:hover {
      color: var(--text-primary);
      text-decoration: none;
    }

    .desktop-nav a.active {
      border-bottom-color: var(--accent);
      color: var(--text-primary);
    }

    .desktop-actions {
      margin-left: auto;
      display: flex;
      align-items: center;
      gap: 6px;
    }

    .mobile-actions {
      display: none;
    }

    main {
      padding: 36px 24px 64px;
    }
  }

  @media (min-width: 1200px) {
    .topbar-inner,
    main {
      padding-inline: 32px;
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .skip-link {
      transition: none;
    }
  }
</style>
