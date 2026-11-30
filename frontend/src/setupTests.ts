import { cleanup } from "@testing-library/react"
import { afterEach } from "vitest"
import "@testing-library/jest-dom/vitest"

// Explicit, not implicit via vitest's `globals: true`: unmounts each
// rendered component after its test so markup never leaks between tests
// in the same file (e.g. two <select> elements both matching a query).
afterEach(() => {
  cleanup()
})
