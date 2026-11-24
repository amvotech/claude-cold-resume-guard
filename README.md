# cold-resume-guard

> Prevent cold-cache token spikes and quota exhaustion in **Claude Code** and **Codex CLI**.

When an AI coding session sits idle past its prompt cache TTL (60 minutes on Claude Pro/Team subscriptions, 5 minutes on API keys), the model's KV-cache expires. Resuming that session forces a complete cache write across the accumulated context at **1.25× base input pricing**.

A single message sent into a cold 140,000-token conversation can burn **~175,000 weighted tokens** (~5%–7% of your 5-hour quota and ~1%–2% of your weekly allowance).

`cold-resume-guard` is a lightweight terminal helper that inspects local session transcripts deterministically on disk (`~/.claude/projects/` and `~/.codex/sessions/`), computes the cache expiration status, and warns you before you transmit a cold rewrite.

---

## The Math: Cold Resume vs Context Handoff

| Metric | Raw Cold Resume | With Guard + `/clear` Handoff |
|---|---|---|
| **Context Evaluated** | 140,000 tokens | ~3,000 tokens |
| **Pricing Tier** | 1.25× (Cache Write) | 1.0× (Standard Input) |
| **Effective Tokens Charged** | **~175,000 tokens** | **~3,000 tokens** |
| **Quota Consumption** | 5%–7% of 5-hr limit | <0.1% |
| **Token Reduction** | Baseline (0%) | **~98% Reduction** |

### Why Not Keepwarm Pings?
Keepwarm daemons send background prompts every 4 minutes. A 100k-token session pinged 15 times/hour burns **36,000,000 tokens per day** while you are away or sleeping. 

`cold-resume-guard` burns **0 background tokens**. It performs 0 network requests, runs 0 background pings, and executes strictly on-device.

---

## Installation Options

### 1. via pip / pipx (Recommended for Python devs)
```bash
pip install claude-cold-resume-guard
# or run without installing
pipx run claude-cold-resume-guard
```

### 2. via Homebrew (Mac developers)
```bash
brew tap amvotech/tap
brew install claude-cold-resume-guard
```

### 3. Quick 1-Line Curl Installer (Zero setup)
```bash
curl -fsSL https://raw.githubusercontent.com/amvotech/claude-cold-resume-guard/main/install.sh | bash
```

### 4. Git Clone
```bash
git clone https://github.com/amvotech/claude-cold-resume-guard.git
cd claude-cold-resume-guard
python3 guard.py
```

---

## CLI Usage & Commands

You can use `cold-resume-guard` **immediately without configuring any shell aliases or functions**:

### Direct Usage (No Configuration Required)
```bash
# Guard the latest Claude session in current directory, then launch Claude
cold-resume-guard

# Guard a specific Claude session before resuming
cold-resume-guard --resume e539a748
# or shorthand:
cold-resume-guard e539a748

# Guard the latest OpenAI Codex session, then launch Codex
codex-guard
# or:
cold-resume-guard --agent codex

# Guard a specific Codex session before resuming
codex-guard resume 01a1f7e5
# or:
cold-resume-guard resume 01a1f7e5

# List recent Claude & Codex sessions in project with cache status & token counts
cold-resume-guard --list

# Report-only mode (prints metrics without blocking or launching)
cold-resume-guard --check-only
```

---

## Zero-Friction Setup: Replace `claude` and `codex` Commands

If you want `claude` and `codex` in your terminal to automatically be guarded without having to remember to type `cold-resume-guard`:

### Option A: Automatic 1-Command Setup
Run:
```bash
cold-resume-guard --setup
```
This safely detects your shell (`~/.zshrc` or `~/.bashrc`) and adds the transparent function wrappers:
```bash
claude() {
    cold-resume-guard "$@"
}
codex() {
    cold-resume-guard --agent codex "$@"
}
```
Then reload your terminal: `source ~/.zshrc`.

### Option B: Manual Setup
Add the snippet above to your `~/.zshrc` or `~/.bashrc` manually.

### Why a shell function instead of an alias?
In POSIX shells, an alias like `alias claude="cold-resume-guard && claude"` drops arguments passed to the first command, so running `claude --resume <id>` would leave `cold-resume-guard` without the session ID. Using a shell function forwards all arguments (`--resume <id>`, `resume <id>`, prompts, and flags) to `cold-resume-guard`. The guard checks the exact session on disk, warns you if cold, and once confirmed (or if warm), launches `claude` or `codex` directly via process execution (`os.execvp`).

---

## OpenAI Codex CLI Support

OpenAI Codex CLI has a **10-minute (600s) prompt cache TTL** (compared to 60 minutes for Claude Pro). Sessions go cold 6× faster!

`cold-resume-guard` includes built-in support for Codex:
- **Session Discovery:** Scans `~/.codex/sessions/**/rollout-*.jsonl` matching your project's working directory (`cwd`).
- **Context Inspection:** Computes the active turn context (`input_tokens`) and warns before sending cold turns over the network.
- **Handoff Suggestion:** Recommends typing `/new` inside Codex to begin a fresh turn with an L2Cache context summary (~3k tokens) instead of burning ~150k+ tokens on a cold resume.
- **Command Syntax:** Seamlessly supports `codex resume <id>`, `codex resume --last`, and `codex <prompt>`.

---

## Full Native macOS GUI & MCP Server

For automated handoff injection, multi-agent session history, and 12 local MCP tools without terminal scripting, check out [L2Cache for Mac](https://l2cache.app):

- **Automatic Handoffs:** Press `/clear` in Claude Code to automatically inject git branch state, modified file summaries, and pending tasks at ~3k tokens.
- **Unified ⌘K History:** Search Claude Code, Codex, GitHub Copilot, Grok Build, and shell history in SQLite FTS5.
- **Local MCP Server:** 12 tools for Claude Code, Codex, and Cursor to search past bug resolutions and project context.
- **Pricing:** Free download with unlimited clipboard history + $19.99 one-time lifetime Pro unlock (no subscriptions).

---

## License

MIT License · Amvotech
