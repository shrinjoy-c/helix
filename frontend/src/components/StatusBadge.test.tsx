import { render, screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"
import { StatusBadge } from "./StatusBadge"

describe("StatusBadge", () => {
  it.each([
    "completed",
    "running",
    "pending",
    "failed",
    "needs_revision",
    "cancelled",
    "timed_out",
    "budget_exceeded",
  ])("renders readable text for status %s", (status) => {
    render(<StatusBadge status={status} />)
    expect(screen.getByText(status.replace(/_/g, " "))).toBeInTheDocument()
  })

  it("falls back to a default style for an unrecognized status rather than crashing", () => {
    render(<StatusBadge status="some_future_status" />)
    expect(screen.getByText("some future status")).toBeInTheDocument()
  })
})
