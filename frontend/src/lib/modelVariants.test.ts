import { describe, expect, it } from "vitest";

import { parseModelVariants } from "./modelVariants";

describe("model variants", () => {
  it("trims pasted lines while preserving provider-specific names and order", () => {
    expect(parseModelVariants("  high\r\n\n custom/Deep-Research \n")).toEqual([
      "high",
      "custom/Deep-Research",
    ]);
  });

  it("uses the provider default for empty input", () => {
    expect(parseModelVariants(" \n\t\r\n")).toEqual([]);
  });

  it("rejects duplicate choices after trimming", () => {
    expect(() => parseModelVariants("high\n high ")).toThrow("Variant names must be unique.");
  });

  it("enforces the model choice count", () => {
    const variants = Array.from({ length: 20 }, (_, index) => `variant-${index}`);
    expect(parseModelVariants(variants.join("\n"))).toEqual(variants);
    expect(() => parseModelVariants([...variants, "another"].join("\n"))).toThrow(
      "Enter at most 20 variants.",
    );
  });

  it("enforces the same character limit as the server", () => {
    expect(parseModelVariants("🧠".repeat(50))).toEqual(["🧠".repeat(50)]);
    expect(() => parseModelVariants("x".repeat(51))).toThrow("Each variant must be at most 50 characters.");
  });
});
