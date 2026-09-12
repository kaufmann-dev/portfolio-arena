import { onApiMutation } from "./client";
import { PublicQuery } from "./publicQuery.svelte";
export { PUBLIC_REFRESH_MS } from "./publicQuery.svelte";

const MAX_PUBLIC_QUERIES = 64;

function publicUrl(url: string): string {
  const parsed = new URL(url, "https://portfolio-arena.invalid");
  if (
    !url.startsWith("/api/") ||
    !/^\/api\/(versions|arena\/(managed|rebuilt)|portfolios\/[^/]+|compare)$/.test(parsed.pathname)
  ) {
    throw new Error("Only public analysis endpoints may use the display cache.");
  }
  parsed.searchParams.sort();
  return parsed.pathname + parsed.search;
}

export class PublicQueryCache {
  private entries = new Map<string, PublicQuery<unknown>>();

  get<T>(url: string): PublicQuery<T> {
    const key = publicUrl(url);
    const query = this.entries.get(key) ?? new PublicQuery<unknown>(key);
    this.entries.delete(key);
    this.entries.set(key, query);
    if (this.entries.size > MAX_PUBLIC_QUERIES) {
      this.entries.delete(this.entries.keys().next().value!);
    }
    return query as PublicQuery<T>;
  }

  clear(): void {
    for (const query of this.entries.values()) query.invalidate();
    this.entries.clear();
  }
}

// This client-rendered SPA shares public snapshots across route remounts.
// Admin responses never enter this cache; a reload starts with an empty cache.
const publicQueries = new PublicQueryCache();
onApiMutation(() => publicQueries.clear());
export const getPublicQuery = <T>(url: string): PublicQuery<T> => publicQueries.get<T>(url);
