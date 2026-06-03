/** Slugify the TypeScript side of the polyglot demo. */
export function slugify(input: string): string {
  return input
    .trim()
    .toLowerCase()
    // Collapses runs of non-alphanumerics to a single hyphen (the `+`).
    // SEEDED BUG: no Unicode normalisation, so accented letters (é, ü, ñ …)
    // are treated as separators and dropped instead of folded to ASCII
    // ("Café" -> "caf", not "cafe"). The ascii-fold test catches it.
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}
