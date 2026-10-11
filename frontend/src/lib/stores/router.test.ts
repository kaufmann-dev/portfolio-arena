import { describe, expect, it } from "vitest";
import { parsePath, router, versionHref } from "./router.svelte";
describe("application routes", () => {
  it("keeps the selected version in links opened in a new tab", () => {
    router.version = "1";
    expect(versionHref("/about")).toBe("/about?version=1");
    expect(versionHref("/?version=2&direction=short")).toBe("/?version=2&direction=short");
    router.version = null;
    expect(versionHref("/admin")).toBe("/admin");
  });
  it("routes portfolio and administration pages", () => {
    expect(parsePath("/p/example")).toEqual({ name: "portfolio", params: { slug: "example" } });
    expect(parsePath("/admin")).toEqual({ name: "admin", params: {} });
    expect(parsePath("/prompt/daily%20signal")).toEqual({ name: "prompt", params: { slug: "daily signal" } });
  });
  it("routes the root to the Arena and unknown paths to the not-found page", () => {
    expect(parsePath("/")).toEqual({ name: "home", params: {} });
    expect(parsePath("/unknown")).toEqual({ name: "notFound", params: {} });
    expect(parsePath("/admin/extra")).toEqual({ name: "notFound", params: {} });
    expect(parsePath("/p/example/extra")).toEqual({ name: "notFound", params: {} });
  });
});
