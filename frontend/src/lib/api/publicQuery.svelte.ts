import { ApiError, apiJson } from "./client";

export const PUBLIC_REFRESH_MS = 5 * 60 * 1000;

export class PublicQuery<T> {
  data = $state.raw<T | null>(null);
  loading = $state(false);
  error = $state("");
  private updatedAt = Number.NEGATIVE_INFINITY;
  private generation = 0;
  private pending: Promise<void> | null = null;

  readonly url: string;

  constructor(url: string) {
    this.url = url;
  }

  load(force = false): Promise<void> {
    if (this.pending) return this.pending;
    if (!force && this.data !== null && Date.now() - this.updatedAt < PUBLIC_REFRESH_MS) {
      return Promise.resolve();
    }
    const generation = this.generation;
    this.loading = true;
    this.error = "";
    this.pending = apiJson<T>(this.url)
      .then((data) => {
        if (generation !== this.generation) return;
        this.data = data;
        this.updatedAt = Date.now();
      })
      .catch((error: unknown) => {
        if (generation !== this.generation) return;
        this.error = error instanceof Error ? error.message : "Could not load results.";
        if (error instanceof ApiError && [401, 403, 404].includes(error.status)) this.data = null;
      })
      .finally(() => {
        if (generation !== this.generation) return;
        this.loading = false;
        this.pending = null;
      });
    return this.pending;
  }

  invalidate(): void {
    this.generation += 1;
    this.updatedAt = Number.NEGATIVE_INFINITY;
    this.pending = null;
    this.data = null;
    this.error = "";
    this.loading = false;
  }
}
