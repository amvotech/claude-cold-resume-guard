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

## How to Hook into Claude Code

Add this alias to your `~/.zshrc` or `~/.bashrc`:
```bash
alias claude="cold-resume-guard && claude"
```
Whenever you launch Claude Code or return to a session, the guard runs instantaneously in `<5ms`. If your cache is warm, it stays completely silent. If your session is expired and large, it warns you before you hit Return.

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
