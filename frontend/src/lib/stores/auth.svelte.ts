import { apiJson, setUnauthorizedCallback } from "../api/client";
import type { AuthMe } from "../api/types";

class AuthStore {
  #restorePromise: Promise<void> | null = null;
  displayName = $state<string | null>(null);
  /** true while the cookie-backed session is being checked on startup */
  restoring = $state(true);

  constructor() {
    setUnauthorizedCallback(() => {
      this.displayName = null;
    });
  }

  get isAuthenticated(): boolean {
    return this.displayName !== null;
  }

  restore(): Promise<void> {
    this.#restorePromise ??= this.performRestore();
    return this.#restorePromise;
  }

  private async performRestore(): Promise<void> {
    try {
      const me = await apiJson<AuthMe>("/api/auth/me");
      this.displayName = me.displayName;
    } catch {
      this.displayName = null;
    } finally {
      this.restoring = false;
    }
  }
}

export const auth = new AuthStore();
