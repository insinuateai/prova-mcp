# prova-mcp

> Let any AI agent verify its own reasoning before answering — and kernel-check the proof on its own machine.

`prova-mcp` is a [Model Context Protocol](https://modelcontextprotocol.io/) server that exposes the [Prova reasoning verifier](https://prova.cobound.dev) as tools any MCP client can call. Drop it into Claude Code, Cursor, Windsurf, Zed, or ChatGPT desktop and the host model gains a self-verification loop:

1. The agent drafts a multi-step argument.
2. It calls `verify_reasoning` on the draft. Prova returns a verdict (`VALID` / `INVALID`), a confidence score, and — for `INVALID` — the exact step that's broken.
3. If `VALID`, the agent calls `kernel_check_proof` on the emitted Lean 4 proof. The local Lean kernel either accepts every step or rejects the proof. There is no third option, and Prova is not in that loop — it's purely a property of the proof and the kernel on your machine.

Result: agents that catch their own circular arguments, contradictions, and unsupported leaps before they reach a user — with a tamper-evident certificate as audit trail.

---

## Tools

| Tool | What it does |
|---|---|
| `verify_reasoning(reasoning, retain?, source_url?, domain?, metadata?)` | Verify a reasoning chain. Returns verdict, confidence, certificate URL, and (if invalid) the failing step. |
| `get_certificate(certificate_id)` | Look up an existing certificate by ID (e.g. `PRV-2026-A7X4`). |
| `download_lean_proof(certificate_id)` | Fetch the self-contained Lean 4 proof source for a `VALID` certificate. |
| `kernel_check_proof(lean_source)` | Run the local `lean` binary on a proof string. Exit 0 = accepted. |
| `verify_and_kernel_check(reasoning, ...)` | One-shot: verify, then locally kernel-check the emitted proof. |

It also exposes each certificate as a resource at `prova://certificate/{id}` for clients that consume MCP resources.

---

## Install

```bash
pip install prova-mcp
```

Optional but recommended — install Lean 4 so `kernel_check_proof` actually runs:

```bash
curl https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh -sSf | sh
```

---

## Configure

Set environment variables wherever your MCP client launches the server:

| Variable | Default | Purpose |
|---|---|---|
| `PROVA_API_KEY` | _(unset → demo tier)_ | API key from [prova.cobound.dev](https://prova.cobound.dev). Demo is rate-limited. |
| `PROVA_API_BASE_URL` | `https://api.prova.cobound.dev` | Override for self-hosted Prova. |
| `PROVA_LEAN_BIN` | `lean` | Path to the Lean 4 executable. |
| `PROVA_DEFAULT_RETAIN` | `false` | Whether `verify_reasoning` defaults to persisting the original reasoning text. |
| `PROVA_MCP_LOG_LEVEL` | `WARNING` | Server log level. |

---

## Wire it up

### Claude Code

```bash
claude mcp add prova prova-mcp -e PROVA_API_KEY=sk_live_...
```

### Cursor / Windsurf / Zed / ChatGPT desktop

Add to your client's MCP config (typically `mcp.json` or `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "prova": {
      "command": "prova-mcp",
      "env": {
        "PROVA_API_KEY": "sk_live_..."
      }
    }
  }
}
```

A copy of this config is shipped at [`examples/claude_desktop_config.json`](examples/claude_desktop_config.json).

---

## How an agent uses it

A good system prompt nudge:

> Before producing a multi-step argument, call `prova.verify_reasoning` on your draft. If the verdict is `INVALID`, repair the failing step and re-verify. If `VALID`, attach the certificate URL to your answer.

That single line turns Prova into a default reflex for the model — every reasoning answer ships with a verifiable certificate, and broken arguments are caught before the user ever sees them.

---

## Why this exists

Verifiers are only useful if they sit where the reasoning happens. MCP is now the universal bridge between LLM clients and external tools — so shipping Prova as an MCP server makes formal reasoning verification the easiest thing to add to any agent stack on the planet. Install one package, set one key, get a tamper-evident proof of every important argument your agent makes.

---

## License

MIT. See [`LICENSE`](LICENSE).
