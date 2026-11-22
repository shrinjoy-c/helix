// Validated categorical palette (dark steps), fixed order - see the dataviz
// skill's references/palette.md. Used unmodified; this app is dark-only, so
// only the dark column is needed (no light/dark CSS-variable switching).
export const CATEGORICAL = [
  "#3987e5", // 1 blue
  "#d95926", // 2 orange
  "#199e70", // 3 aqua
  "#c98500", // 4 yellow
  "#d55181", // 5 magenta
  "#008300", // 6 green
  "#9085e9", // 7 violet
  "#e66767", // 8 red
]

export const CHROME = {
  surface: "#1a1a19",
  primaryInk: "#ffffff",
  secondaryInk: "#c3c2b7",
  mutedInk: "#898781",
  gridline: "#2c2c2a",
  baseline: "#383835",
}

export function colorForIndex(i: number): string {
  return CATEGORICAL[i % CATEGORICAL.length]
}
