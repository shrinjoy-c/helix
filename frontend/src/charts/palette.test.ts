import { describe, expect, it } from "vitest"
import { CATEGORICAL, colorForIndex } from "./palette"

describe("colorForIndex", () => {
  it("returns the matching color for indices within range", () => {
    expect(colorForIndex(0)).toBe(CATEGORICAL[0])
    expect(colorForIndex(3)).toBe(CATEGORICAL[3])
  })

  it("wraps around rather than going out of bounds for a 9th series", () => {
    expect(colorForIndex(CATEGORICAL.length)).toBe(CATEGORICAL[0])
    expect(colorForIndex(CATEGORICAL.length + 2)).toBe(CATEGORICAL[2])
  })
})

describe("CATEGORICAL palette", () => {
  it("is a fixed, non-empty hue order of valid hex colors", () => {
    expect(CATEGORICAL.length).toBeGreaterThan(0)
    for (const color of CATEGORICAL) {
      expect(color).toMatch(/^#[0-9a-f]{6}$/i)
    }
  })

  it("has no duplicate hues", () => {
    expect(new Set(CATEGORICAL).size).toBe(CATEGORICAL.length)
  })
})
