/// <reference types="vite/client" />

// @ts-expect-error Vitest executes this regression test in Node; the production bundle excludes it.
import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

const appCss = readFileSync(new URL("./app.css", import.meta.url), "utf8");

const componentSources = import.meta.glob("./**/*.svelte", {
  eager: true,
  import: "default",
  query: "?raw",
}) as Record<string, string>;
const styleSources = { ...componentSources, "./app.css": appCss };

function sourceEnding(suffix: string): string {
  const entry = Object.entries(styleSources).find(([path]) => path.endsWith(suffix));
  if (!entry) throw new Error(`Missing design source: ${suffix}`);
  return entry[1];
}

function ruleBody(source: string, selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = source.match(new RegExp(`${escaped}\\s*\\{([^}]+)\\}`));
  if (!match) throw new Error(`Missing CSS rule: ${selector}`);
  return match[1];
}

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

  it("provides a reusable single-line truncation contract", () => {
    const declarations = ruleBody(sourceEnding("app.css"), ".truncate");

    expect(declarations).toMatch(/min-width\s*:\s*0/);
    expect(declarations).toMatch(/overflow\s*:\s*hidden/);
    expect(declarations).toMatch(/text-overflow\s*:\s*ellipsis/);
    expect(declarations).toMatch(/white-space\s*:\s*nowrap/);
  });

  it("resets fieldsets and keeps focus indicators inside overflow regions", () => {
    const appCss = sourceEnding("app.css");

    expect(ruleBody(appCss, "fieldset")).toMatch(/border\s*:\s*0/);
    expect(ruleBody(appCss, ".tab-trigger:focus-visible")).toMatch(/outline-offset\s*:\s*-2px/);
  });

  it("constrains mobile ranking cards to the viewport", () => {
    const mobileRankingRules = [
      ...sourceEnding("app.css").matchAll(/\.arena-rankings \.mobile-rankings\s*\{([^}]+)\}/g),
    ].map((match) => match[1]);

    expect(
      mobileRankingRules.some(
        (declarations) =>
          /display\s*:\s*grid/.test(declarations) &&
          /grid-template-columns\s*:\s*minmax\(0,\s*1fr\)/.test(declarations),
      ),
    ).toBe(true);
  });

  it("renders evaluation reports in a full-width table row", () => {
    const automationPanel = sourceEnding("AutomationPanel.svelte");

    expect(automationPanel).toContain('class="run-report-row"');
    expect(automationPanel).toContain('colspan="8"');
    expect(automationPanel).not.toMatch(/max-width\s*:\s*420px\s*;/);
  });

  it("keeps single-line truncation wrappers inside evaluator table cells", () => {
    const automationPanel = sourceEnding("AutomationPanel.svelte");

    expect(automationPanel).not.toMatch(/<td[^>]*class="[^"]*\bcell-line\b/);
  });
});
