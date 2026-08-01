import { describe, expect, it } from "vitest";

import { parsePath } from "./router.svelte";

describe("application routes", () => {
  it("routes the dedicated Meta Arena independently from the normal Arena", () => {
    expect(parsePath("/meta")).toEqual({ name: "meta", params: {} });
    expect(parsePath("/")).toEqual({ name: "home", params: {} });
  });

  it("does not treat nested unknown paths as the Meta Arena", () => {
    expect(parsePath("/meta-analysis")).toEqual({ name: "home", params: {} });
  });
});
