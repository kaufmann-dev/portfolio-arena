import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "./client";
import { getPublicQuery, PUBLIC_REFRESH_MS, PublicQueryCache } from "./publicCache";

function response(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
}
function deferred() {
  let resolve!: (value: Response) => void;
  const promise = new Promise<Response>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-09-12T12:00:00Z"));
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("public display snapshots", () => {
  it("retains independent contexts across navigation without another request", async () => {
    const fetch = vi.fn(async (url: string) => response({ url }));
    vi.stubGlobal("fetch", fetch);
    const cache = new PublicQueryCache();
    const urls = [
      "/api/arena/rebuilt?direction=long&version_id=1&objective=ci_lower",
      "/api/arena/rebuilt?direction=short&version_id=1&objective=ci_lower",
      "/api/arena/managed?direction=long&version_id=1",
      "/api/arena/rebuilt?direction=long&version_id=2&objective=ci_lower",
      "/api/arena/rebuilt?direction=long&version_id=1&objective=sharpe",
      "/api/portfolios/example?objective=sharpe",
      "/api/compare?slugs=a,b&direction=long&track=rebuilt&version_id=1",
    ];
    for (const url of urls) await cache.get(url).load();
    for (const url of urls) {
      const revisited = cache.get<{ url: string }>(url);
      expect(revisited.data?.url).toBe(revisited.url);
      await revisited.load();
    }
    expect(fetch).toHaveBeenCalledTimes(urls.length);
    expect(cache.get("/api/arena/managed?version_id=1&direction=long")).toBe(cache.get(urls[2]));
  });
  it("shares in-flight work and keeps data visible during a five-minute refresh", async () => {
    const next = deferred();
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(response({ revision: 1 }))
      .mockReturnValueOnce(next.promise);
    vi.stubGlobal("fetch", fetch);
    const query = new PublicQueryCache().get("/api/versions");
    const first = query.load();
    expect(query.load()).toBe(first);
    await first;
    vi.advanceTimersByTime(PUBLIC_REFRESH_MS - 1);
    await query.load();
    expect(fetch).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(1);
    const refresh = query.load();
    expect(query.data).toEqual({ revision: 1 });
    expect(query.loading).toBe(true);
    expect(query.load()).toBe(refresh);
    next.resolve(response({ revision: 2 }));
    await refresh;
    expect(query.data).toEqual({ revision: 2 });
    expect(query.loading).toBe(false);
  });
  it("keeps saved results on transient refresh failure and permits retry", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(response({ revision: 1 }))
        .mockResolvedValueOnce(response({ detail: "Temporarily unavailable" }, 503))
        .mockResolvedValueOnce(response({ revision: 2 })),
    );
    const query = new PublicQueryCache().get("/api/versions");
    await query.load();
    vi.advanceTimersByTime(PUBLIC_REFRESH_MS);
    await query.load();
    expect(query.data).toEqual({ revision: 1 });
    expect(query.error).toBe("Temporarily unavailable");
    await query.load();
    expect(query.data).toEqual({ revision: 2 });
    expect(query.error).toBe("");
  });
  it("removes a deleted portfolio's snapshot", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(response({ name: "Deleted" }))
        .mockResolvedValueOnce(response({ detail: "Portfolio not found" }, 404)),
    );
    const query = new PublicQueryCache().get("/api/portfolios/deleted");
    await query.load();
    await query.load(true);
    expect(query.data).toBeNull();
    expect(query.error).toBe("Portfolio not found");
  });
  it("prevents a late pre-edit request from restoring an invalidated snapshot", async () => {
    const old = deferred();
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockReturnValueOnce(old.promise)
        .mockResolvedValueOnce(response({ revision: 2 })),
    );
    const cache = new PublicQueryCache();
    const query = cache.get("/api/versions");
    const beforeEdit = query.load();
    cache.clear();
    const newQuery = cache.get("/api/versions");
    await newQuery.load();
    old.resolve(response({ revision: 1 }));
    await beforeEdit;
    expect(query.data).toBeNull();
    expect(newQuery.data).toEqual({ revision: 2 });
    expect(cache.get("/api/versions")).toBe(newQuery);
  });
  it("invalidates after successful writes, excluding activity and failed writes", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url, options) =>
        options?.method === "DELETE" ? response({ detail: "Rejected" }, 403) : response({ ok: true }),
      ),
    );
    const url = "/api/portfolios/mutation-check";
    const query = getPublicQuery(url);
    await query.load();
    await apiFetch("/api/auth/activity", { method: "POST" });
    expect(getPublicQuery(url)).toBe(query);
    await expect(apiFetch("/api/portfolios/1", { method: "DELETE" })).rejects.toThrow("Rejected");
    expect(getPublicQuery(url)).toBe(query);
    await apiFetch("/api/portfolios/1", { method: "PATCH" });
    expect(query.data).toBeNull();
    expect(getPublicQuery(url)).not.toBe(query);
  });
  it("bounds retained snapshots while retaining recently revisited entries", () => {
    const cache = new PublicQueryCache();
    const oldest = cache.get("/api/portfolios/0");
    const evicted = cache.get("/api/portfolios/1");
    for (let i = 2; i < 64; i++) cache.get(`/api/portfolios/${i}`);
    expect(cache.get("/api/portfolios/0")).toBe(oldest);
    cache.get("/api/portfolios/64");
    expect(cache.get("/api/portfolios/0")).toBe(oldest);
    expect(cache.get("/api/portfolios/1")).not.toBe(evicted);
  });
  it("cannot retain admin responses or external requests", () => {
    const cache = new PublicQueryCache();
    for (const url of [
      "/api/admin/portfolios",
      "/api/keys",
      "/api/auth/me",
      "https://example.com/api/versions",
    ])
      expect(() => cache.get(url)).toThrow("Only public analysis endpoints");
  });
});
