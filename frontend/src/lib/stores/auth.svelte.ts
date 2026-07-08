import { apiJson, getToken, postJson, removeToken, setToken, setUnauthorizedHandler } from "../api/client";

interface LoginResponse {
  token: string;
  email: string;
}

class AuthStore {
  email = $state<string | null>(null);
  /** true while the stored token is being validated on startup */
  restoring = $state(true);

  get isAdmin(): boolean {
    return this.email !== null;
  }

  async restore(): Promise<void> {
    if (!getToken()) {
      this.restoring = false;
      return;
    }
    try {
      const me = await apiJson<{ email: string }>("/api/auth/me");
      this.email = me.email;
    } catch {
      this.email = null;
    } finally {
      this.restoring = false;
    }
  }

  async login(email: string, password: string): Promise<void> {
    const data = await postJson<LoginResponse>("/api/auth/login", { email, password }, { auth: false });
    setToken(data.token);
    this.email = data.email;
  }

  logout(): void {
    removeToken();
    this.email = null;
  }
}

export const auth = new AuthStore();

setUnauthorizedHandler(() => auth.logout());
