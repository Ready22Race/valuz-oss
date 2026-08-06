/**
 * Initials for the avatar fallback.
 *
 * Latin names collapse to first + last initial; a single token — which is what
 * every CJK name is, since they carry no spaces — keeps its first two
 * characters. `Array.from` rather than `slice` so an astral character (an emoji
 * nickname, a rare Han ideograph in the supplementary plane) is not cut in half
 * into a pair of replacement glyphs.
 *
 * Purely decorative: callers must render the result inside an `aria-hidden`
 * element, because the name it was derived from is on screen beside it and
 * announcing both reads the entity twice.
 *
 * (`ProfileTile` carries a private copy of this function predating the file.
 * Collapsing the two is a safe follow-up; it is left alone here so this change
 * touches no existing family.)
 */
export function initialsOf(name: unknown): string {
  const text = typeof name === "string" ? name.trim() : "";
  if (text === "") return "?";

  const words = text.split(/\s+/).filter(Boolean);
  if (words.length === 1) {
    return Array.from(words[0] ?? "")
      .slice(0, 2)
      .join("");
  }

  const first = Array.from(words[0] ?? "")[0] ?? "";
  const last = Array.from(words[words.length - 1] ?? "")[0] ?? "";
  return `${first}${last}`;
}
