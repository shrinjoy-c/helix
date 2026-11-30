import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { useState } from "react"
import { describe, expect, it } from "vitest"
import type { ProviderFields } from "../api/types"
import { ProviderSelector } from "./ProviderSelector"

function Harness() {
  const [value, setValue] = useState<ProviderFields>({ provider: "mock" })
  return <ProviderSelector value={value} onChange={setValue} />
}

describe("ProviderSelector", () => {
  it("shows no model/base URL/key fields for the default mock provider", () => {
    render(<Harness />)
    expect(screen.queryByText("Model")).not.toBeInTheDocument()
    expect(screen.queryByText("Base URL")).not.toBeInTheDocument()
  })

  it("switching to gemini fills in its default model and shows the API key env field, not base URL", async () => {
    const user = userEvent.setup()
    render(<Harness />)

    await user.selectOptions(screen.getByRole("combobox"), "gemini")

    expect(screen.getByLabelText("Model")).toHaveValue("gemini-2.0-flash")
    expect(screen.getByPlaceholderText("GEMINI_API_KEY")).toBeInTheDocument()
    expect(screen.queryByText("Base URL")).not.toBeInTheDocument()
  })

  it("switching to ollama fills in its local defaults and shows base URL, not an API key field", async () => {
    const user = userEvent.setup()
    render(<Harness />)

    await user.selectOptions(screen.getByRole("combobox"), "ollama")

    expect(screen.getByLabelText("Model")).toHaveValue("qwen3:4b")
    expect(screen.getByLabelText("Base URL")).toHaveValue("http://localhost:11434")
    expect(screen.queryByText(/API key environment variable/)).not.toBeInTheDocument()
  })

  it("switching to openai_compatible shows both base URL and the API key env field", async () => {
    const user = userEvent.setup()
    render(<Harness />)

    await user.selectOptions(screen.getByRole("combobox"), "openai_compatible")

    expect(screen.getByLabelText("Base URL")).toHaveValue("https://api.openai.com/v1")
    expect(screen.getByPlaceholderText("OPENAI_API_KEY")).toBeInTheDocument()
  })

  it("switching back to mock clears the extra fields again", async () => {
    const user = userEvent.setup()
    render(<Harness />)

    const select = screen.getByRole("combobox")
    await user.selectOptions(select, "anthropic")
    expect(screen.getByLabelText("Model")).toHaveValue("claude-sonnet-5")

    await user.selectOptions(select, "mock")
    expect(screen.queryByText("Model")).not.toBeInTheDocument()
  })
})
