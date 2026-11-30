import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { api, ApiError } from "./client"

function jsonResponse(body: unknown, init: { status?: number; statusText?: string } = {}) {
  return new Response(JSON.stringify(body), {
    status: init.status ?? 200,
    statusText: init.statusText ?? "OK",
    headers: { "Content-Type": "application/json" },
  })
}

describe("api client", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it("returns parsed JSON on a successful request", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ status: "ok" }))
    const result = await api.health()
    expect(result).toEqual({ status: "ok" })
  })

  it("requests the correct path and method for a POST", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ dataset_id: "d1", description: "", metadata: {} }))
    await api.createDataset({ dataset_id: "d1" } as any)
    const [url, init] = vi.mocked(fetch).mock.calls[0]
    expect(String(url)).toContain("/datasets")
    expect(init?.method).toBe("POST")
  })

  it("builds query params only for filters that are actually set", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse([]))
    await api.experiments({ pipeline_name: "single_agent" })
    const [url] = vi.mocked(fetch).mock.calls[0]
    expect(String(url)).toContain("pipeline_name=single_agent")
    expect(String(url)).not.toContain("dataset_id")
  })

  it("throws ApiError with the response's detail field on a non-ok response", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse({ detail: "dataset not found" }, { status: 404, statusText: "Not Found" }),
    )
    await expect(api.dataset("missing")).rejects.toMatchObject(
      new ApiError(404, "dataset not found"),
    )
  })

  it("falls back to statusText when the error response has no JSON body", async () => {
    const response = new Response("", { status: 500, statusText: "Internal Server Error" })
    vi.mocked(fetch).mockResolvedValueOnce(response)
    await expect(api.health()).rejects.toMatchObject(new ApiError(500, "Internal Server Error"))
  })

  it("returns undefined for a 204 No Content response", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(new Response(null, { status: 204 }))
    const result = await api.health()
    expect(result).toBeUndefined()
  })
})
