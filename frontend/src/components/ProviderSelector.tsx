import type { ProviderFields, ProviderName } from "../api/types"

const PROVIDER_HINTS: Record<ProviderName, string> = {
  mock: "Deterministic, free, offline rule engine - no real model call. See docs/agent_design.md.",
  anthropic: "Requires ANTHROPIC_API_KEY set on the server (not here) - a Claude Code/Ultra subscription does not provide this.",
  ollama: "Local, free, no API key - requires `ollama serve` running and the model pulled (e.g. `ollama pull qwen3:4b`).",
  openai_compatible: "Any OpenAI chat-completions API: OpenAI, Groq, Together, DeepSeek, a local vLLM/LM Studio server, etc.",
  gemini: "Requires GEMINI_API_KEY (or a custom env var below) set on the server - get one at aistudio.google.com/apikey.",
}

const PROVIDER_DEFAULTS: Record<ProviderName, { model: string; base_url: string }> = {
  mock: { model: "", base_url: "" },
  anthropic: { model: "claude-sonnet-5", base_url: "" },
  ollama: { model: "qwen3:4b", base_url: "http://localhost:11434" },
  openai_compatible: { model: "gpt-4o-mini", base_url: "https://api.openai.com/v1" },
  gemini: { model: "gemini-2.0-flash", base_url: "" },
}

export function providerDefaults(provider: ProviderName) {
  return PROVIDER_DEFAULTS[provider]
}

export function ProviderSelector({ value, onChange }: {
  value: ProviderFields
  onChange: (next: ProviderFields) => void
}) {
  const provider = value.provider ?? "mock"

  function setProvider(next: ProviderName) {
    onChange({ ...value, provider: next, ...providerDefaults(next) })
  }

  return (
    <div className="space-y-3 rounded-lg border border-slate-800 bg-slate-950/40 p-3">
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Model provider</p>
      <select className="input" value={provider} onChange={(e) => setProvider(e.target.value as ProviderName)}>
        <option value="mock">mock (default - free, deterministic)</option>
        <option value="ollama">ollama (local, free)</option>
        <option value="openai_compatible">openai_compatible (OpenAI, Groq, Together, ...)</option>
        <option value="anthropic">anthropic</option>
        <option value="gemini">gemini</option>
      </select>
      <p className="text-xs text-slate-500">{PROVIDER_HINTS[provider]}</p>

      {provider !== "mock" && (
        <div className="grid grid-cols-2 gap-3">
          <label className="block">
            <span className="mb-1 block text-xs font-medium text-slate-400">Model</span>
            <input
              className="input"
              value={value.model ?? ""}
              onChange={(e) => onChange({ ...value, model: e.target.value })}
            />
          </label>
          {(provider === "ollama" || provider === "openai_compatible") && (
            <label className="block">
              <span className="mb-1 block text-xs font-medium text-slate-400">Base URL</span>
              <input
                className="input"
                value={value.base_url ?? ""}
                onChange={(e) => onChange({ ...value, base_url: e.target.value })}
              />
            </label>
          )}
        </div>
      )}
      {(provider === "openai_compatible" || provider === "gemini") && (
        <label className="block">
          <span className="mb-1 block text-xs font-medium text-slate-400">
            API key environment variable (read server-side - never sent from here)
          </span>
          <input
            className="input"
            placeholder={provider === "gemini" ? "GEMINI_API_KEY" : "OPENAI_API_KEY"}
            value={value.api_key_env ?? ""}
            onChange={(e) => onChange({ ...value, api_key_env: e.target.value })}
          />
        </label>
      )}
    </div>
  )
}
