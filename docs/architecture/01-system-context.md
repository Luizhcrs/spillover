# 01 — System Context (C4 Level 1)

Spillover sits between any LLM-API client and the upstream provider, intercepting every request to externalise overflow context and inject relevant past episodes.

```mermaid
graph TB
    classDef external fill:#4f7896,stroke:#fff,color:#fff
    classDef system fill:#0b5394,stroke:#fff,color:#fff,stroke-width:3px
    classDef person fill:#08427b,stroke:#fff,color:#fff

    user["Developer<br/>uses Claude Code / Codex /<br/>Cursor / Continue.dev"]:::person

    spillover["<b>spillover</b><br/>transparent LLM proxy<br/>with overflow memory"]:::system

    anthropic["Anthropic API<br/>api.anthropic.com<br/>/v1/messages"]:::external
    openai["OpenAI API<br/>api.openai.com<br/>/v1/chat/completions"]:::external
    prometheus["Prometheus<br/>(optional scrape target)"]:::external

    user -->|"HTTP requests<br/>via wrapper"| spillover
    spillover -->|"forwarded requests<br/>with OAuth/API key"| anthropic
    spillover -->|"forwarded requests"| openai
    prometheus -.->|"GET /metrics<br/>scrape interval"| spillover
```

## Actors

| actor | role |
|---|---|
| Developer | invokes spillover via wrapper or sets `ANTHROPIC_BASE_URL` manually |
| Anthropic API | upstream LLM provider; spillover forwards traffic |
| OpenAI API | second supported upstream |
| Prometheus | scrapes `/metrics` at any chosen interval |

## What spillover owns

1. The HTTP loopback proxy daemon at `:8787` (port configurable).
2. Per-project memory stores under `~/.spillover/projects/<sha1(cwd)>/`.
3. The wrappers that launch each supported CLI with spillover wired in.
4. Counter-compaction defenses applied transparently to all forwarded requests.

## What spillover does not own

- Any cloud infrastructure. Everything runs locally on the developer's workstation.
- Authentication. Spillover forwards whatever auth header the client provides (OAuth bearer or `sk-ant-…` API key).
- The provider API itself. Spillover is a transparent passthrough by default.
