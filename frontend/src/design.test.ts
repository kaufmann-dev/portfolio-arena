/// <reference types="vite/client" />

import { describe, expect, it } from "vitest";

const styleSources = import.meta.glob(["./**/*.svelte", "./app.css"], {
  eager: true,
  import: "default",
  query: "?raw",
}) as Record<string, string>;

describe("frontend geometry", () => {
  it("keeps every declared border radius at zero", () => {
    for (const [path, source] of Object.entries(styleSources)) {
      const declarations = source.matchAll(/\bborder-radius\s*:\s*([^;}]+)/g);

      for (const declaration of declarations) {
        expect(declaration[1].trim(), `${path} introduces rounded geometry`).toMatch(
          /^0(?:\.0+)?(?:[a-z%]+)?(?:\s*!important)?$/,
        );
      }
    }
  });

  it("uses borders and outlines instead of shadows", () => {
    for (const [path, source] of Object.entries(styleSources)) {
      expect(source, `${path} introduces a box shadow`).not.toMatch(/\bbox-shadow\s*:/);
    }
  });
});
