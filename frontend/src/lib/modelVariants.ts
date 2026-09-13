export function parseModelVariants(text: string): string[] {
  const variants = text
    .split("\n")
    .map((variant) => variant.trim())
    .filter(Boolean);

  if (variants.length > 20) throw new Error("Enter at most 20 variants.");
  if (variants.some((variant) => [...variant].length > 50)) {
    throw new Error("Each variant must be at most 50 characters.");
  }
  if (new Set(variants).size !== variants.length) throw new Error("Variant names must be unique.");

  return variants;
}
