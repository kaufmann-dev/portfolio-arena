import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ArenaPrefetchQueue, arenaQueryUrl, type ArenaView } from "./arenaPrefetch";
import { PublicQueryCache } from "./publicCache";

const initial: ArenaView = {
  versionId: 7,
  track: "rebuilt",
  direction: "long",
  objective: "ci_lower",
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("arena background loading", () => {
  it.each([
    ["rebuilt", "long"],
    ["rebuilt", "short"],
    ["managed", "long"],
    ["managed", "short"],
  ] as const)("warms exactly the other three views from %s %s after yielding", async (track, direction) => {
    const load = vi.fn(async (_url: string) => {});
    const queue = new ArenaPrefetchQueue(load, () => true);
    const view = { ...initial, track, direction };
    queue.schedule(view);
    expect(load).not.toHaveBeenCalled();
    await vi.runAllTimersAsync();
    const urls = load.mock.calls.map(([url]) => new URL(url, "http://localhost"));
    expect(urls).toHaveLength(3);
    expect(new Set(urls.map(String)).size).toBe(3);
    expect(load).not.toHaveBeenCalledWith(arenaQueryUrl(view));
    expect(urls[0].pathname).toBe(`/api/arena/${track === "rebuilt" ? "managed" : "rebuilt"}`);
    expect(urls[0].searchParams.get("direction")).toBe(direction);
    for (const url of urls) {
      expect(url.searchParams.get("version_id")).toBe("7");
      expect(url.searchParams.get("objective")).toBe(url.pathname.endsWith("rebuilt") ? "ci_lower" : null);
    }
  });

  it("runs one request at a time and drops queued work when cancelled", async () => {
    const first = deferred<void>();
    const second = deferred<void>();
    const load = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const queue = new ArenaPrefetchQueue(load, () => true);
    queue.schedule(initial);
    await vi.runAllTimersAsync();
    expect(load).toHaveBeenCalledTimes(1);
    first.resolve();
    await vi.runAllTimersAsync();
    expect(load).toHaveBeenCalledTimes(2);
    queue.cancel();
    second.resolve();
    await vi.runAllTimersAsync();
    expect(load).toHaveBeenCalledTimes(2);
    queue.schedule(initial);
    queue.cancel();
    await vi.runAllTimersAsync();
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("replaces obsolete queued context without overlapping speculative requests", async () => {
    const first = deferred<void>();
    const load = vi.fn().mockReturnValueOnce(first.promise).mockResolvedValue(undefined);
    const queue = new ArenaPrefetchQueue(load, () => true);
    queue.schedule(initial);
    await vi.runAllTimersAsync();
    queue.schedule({ ...initial, versionId: 9, objective: "sharpe" });
    await vi.runAllTimersAsync();
    expect(load).toHaveBeenCalledTimes(1);
    first.resolve();
    await vi.runAllTimersAsync();
    expect(load).toHaveBeenCalledTimes(4);
    for (const [url] of load.mock.calls.slice(1)) {
      const parsed = new URL(url, "http://localhost");
      expect(parsed.searchParams.get("version_id")).toBe("9");
      if (parsed.pathname.endsWith("rebuilt")) expect(parsed.searchParams.get("objective")).toBe("sharpe");
    }
  });

  it("pauses remaining work when the page is hidden or the selected view is loading", async () => {
    let canRun = false;
    const first = deferred<void>();
    const load = vi.fn().mockReturnValueOnce(first.promise).mockResolvedValue(undefined);
    const queue = new ArenaPrefetchQueue(load, () => canRun);
    queue.schedule(initial);
    await vi.runAllTimersAsync();
    expect(load).not.toHaveBeenCalled();
    canRun = true;
    queue.schedule(initial);
    await vi.runAllTimersAsync();
    canRun = false;
    first.resolve();
    await vi.runAllTimersAsync();
    expect(load).toHaveBeenCalledTimes(1);
  });

  it("continues warming after an isolated failure", async () => {
    const load = vi.fn().mockRejectedValueOnce(new Error("Unavailable")).mockResolvedValue(undefined);
    new ArenaPrefetchQueue(load, () => true).schedule(initial);
    await vi.runAllTimersAsync();
    expect(load).toHaveBeenCalledTimes(3);
  });

  it("shares a warming request with navigation and serves later switches from the cache", async () => {
    const pending = deferred<Response>();
    const fetch = vi
      .fn()
      .mockReturnValueOnce(pending.promise)
      .mockImplementation(async () => new Response("{}"));
    vi.stubGlobal("fetch", fetch);
    const cache = new PublicQueryCache();
    const queue = new ArenaPrefetchQueue(
      (url) => cache.get(url).load(),
      () => true,
    );
    queue.schedule(initial);
    await vi.runAllTimersAsync();
    const managed = cache.get(arenaQueryUrl({ ...initial, track: "managed" }));
    const navigation = managed.load();
    expect(fetch).toHaveBeenCalledTimes(1);
    pending.resolve(new Response(JSON.stringify({ track: "managed" })));
    await navigation;
    await vi.runAllTimersAsync();
    expect(managed.data).toEqual({ track: "managed" });
    expect(fetch).toHaveBeenCalledTimes(3);
    for (const track of ["managed", "rebuilt"] as const) {
      for (const direction of ["long", "short"] as const) {
        if (track === initial.track && direction === initial.direction) continue;
        expect(cache.get(arenaQueryUrl({ ...initial, track, direction })).data).not.toBeNull();
        await cache.get(arenaQueryUrl({ ...initial, track, direction })).load();
      }
    }
    queue.schedule(initial);
    await vi.runAllTimersAsync();
    expect(fetch).toHaveBeenCalledTimes(3);
  });
});
