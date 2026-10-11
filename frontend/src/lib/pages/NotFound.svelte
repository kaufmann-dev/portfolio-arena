<script lang="ts">
  import { link, versionHref } from "../stores/router.svelte";
</script>

<section class="empty-state">
  <h1>Page not found</h1>
  <p>This page doesn't exist. It may have been moved, or the address has a typo.</p>
  <p>
    <a class="btn primary" href={versionHref("/")} onclick={(event) => link(event, "/")}>Back to the Arena</a>
  </p>
</section>

<style>
  h1 {
    margin: 0 0 8px;
    color: var(--text-primary);
    font-size: clamp(28px, 8vw, 42px);
    letter-spacing: -0.04em;
  }

  p {
    margin: 0 0 16px;
  }
</style>
