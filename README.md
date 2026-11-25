# cold-resume-guard

> Prevent cold-cache token spikes and quota exhaustion in **Claude Code** and **OpenAI Codex CLI**.

When an AI coding session sits idle past its prompt cache TTL (60 minutes on Claude Pro/Team subscriptions, 10 minutes on Codex CLI, 5 minutes on direct API keys), the model's KV-cache expires. Resuming that session forces a complete cache write across the accumulated context at **1.25× base input pricing**.

A single message sent into a cold 140,000-token conversation burns **~175,000 weighted tokens** (~5%–7% of your 5-hour quota and ~1%–2% of your entire weekly allowance).

`cold-resume-guard` is a lightweight terminal helper that inspects local session transcripts deterministically on disk (`~/.claude/projects/` and `~/.codex/sessions/`), computes cache expiration status, warns you before you transmit a cold rewrite, and **automatically copies an enriched context handoff to your clipboard** if you choose to start fresh.

---

## What It Looks Like in Your Terminal

```text
⚠️  [Claude Code Guard] Prompt Cache Expired
• Session: L2Cache cache rewrite attribution (id: e539a748)
• Context: Project: L2Cache | Branch: main
• Last Prompt: "i checked google . it says This site is currently moving"
• Last Active: 24.9h ago · (TTL is 60m)
• Accumulated Context: ~303,598 tokens (1337 events)
• Cold Reload Penalty: ~379,497 weighted input tokens (~15.2% of 5h quota · ~1.9% of weekly)

Options: Type /clear inside Claude Code for fresh handoff (~3k tokens).

Proceed into cold Claude Code session anyway? [y/N]: N

✓ Launch blocked.
📋 Rich handoff prompt copied to your clipboard:
   Resuming work on L2Cache [Branch: main | Session: e539a748]:
   • Goal: L2Cache cache rewrite attribution
   • Last Context: "i checked google . it says This site is currently moving"
   • Last Commit: d66c96c feat(cache): warn when a proxy may be breaking the prompt cache
   • Modified Files: L2cache.xcodeproj/project.pbxproj, tasks/todo.md (+14 more)
   Please inspect the current git state and continue work from where we left off.

Run a fresh session (claude) and press ⌘V to continue at ~3k tokens.
Built with ❤️ by L2Cache — Native developer memory & MCP server for Mac: https://l2cache.app
```

---

## The Math: Cold Resume vs Context Handoff

| Metric | Raw Cold Resume | With Guard + Clipboard Handoff |
|---|---|---|
| **Context Evaluated** | 140,000 tokens | ~75 tokens |
| **Pricing Tier** | 1.25× (Cache Write) | 1.0× (Standard Input) |
| **Effective Tokens Charged** | **~175,000 tokens** | **<100 tokens** |
| **Quota Consumption** | 5%–7% of 5-hr limit | <0.01% |
| **Token Reduction** | Baseline (0%) | **~99.9% Reduction** |

### Why Not Keepwarm Pings?
Keepwarm daemons send background prompts every 4 minutes. A 100k-token session pinged 15 times/hour burns **36,000,000 tokens per day** while you are away or sleeping. 

`cold-resume-guard` burns **0 background tokens**. It performs 0 network requests, runs 0 background pings, and executes strictly on-device.

---

## Installation Options

### 1. via Homebrew (Recommended for Mac developers)
```bash
brew install l2cache-app/tap/cold-resume-guard && cold-resume-guard --setup
```
*Installs `cold-resume-guard`, `claude-guard`, and `codex-guard`.*

### 2. Quick 1-Line Curl Installer (Zero setup)
```bash
curl -fsSL https://raw.githubusercontent.com/l2cache-app/claude-cold-resume-guard/main/install.sh | bash
```

### 3. Windows (PowerShell)
```powershell
irm https://raw.githubusercontent.com/l2cache-app/claude-cold-resume-guard/main/install.ps1 | iex
```
Then open a new terminal and run `cold-resume-guard --setup`. Needs Python 3.8+ (`winget install --id Python.Python.3.13 -e`). Works in PowerShell, Git Bash and WSL. Inside WSL, use the Linux installer above.

### 4. Git Clone
```bash
git clone https://github.com/l2cache-app/claude-cold-resume-guard.git
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
This safely detects your shell (`~/.zshrc` or `~/.bashrc`; on Windows, your PowerShell profile and Git Bash's `~/.bashrc`) and adds the transparent function wrappers:
```bash
claude() {
    cold-resume-guard "$@"
}
codex() {
    cold-resume-guard --agent codex "$@"
}
```
Then reload your terminal: `source ~/.zshrc`.

On Windows, PowerShell gets the equivalent:
```powershell
function claude { cold-resume-guard @args }
function codex { cold-resume-guard --agent codex @args }
```
Windows PowerShell (5.1) blocks profile scripts by default; PowerShell 7 allows them. Each keeps its own setting, so `--setup` names the edition that blocks them and prints the one command that allows them for your account, e.g. `powershell -NoProfile -Command "Set-ExecutionPolicy -Scope CurrentUser RemoteSigned"`. It never changes the policy itself.

With the wrappers in place, a plain `claude` or `codex` starts a fresh session straight away (a new session reloads nothing, so there's no cold-cache cost). Resume commands are guarded: `claude --resume [id]`, `claude --continue`, `codex resume [id]`, or a bare session ID.

To undo the setup on any platform: `cold-resume-guard --remove`.

### Option B: Manual Setup
Add the snippet above to your `~/.zshrc` or `~/.bashrc` manually.

### Why a shell function instead of an alias?
In POSIX shells, an alias like `alias claude="cold-resume-guard && claude"` drops arguments passed to the first command, so running `claude --resume <id>` would leave `cold-resume-guard` without the session ID. Using a shell function forwards all arguments (`--resume <id>`, `resume <id>`, prompts, and flags) to `cold-resume-guard`. The guard checks the exact session on disk, warns you if cold, and once confirmed (or if warm), launches `claude` or `codex` directly via process execution (`os.execvp`; on Windows it runs the agent as a child process and passes its exit code back).

---

## Automatic Smart Handoff (New in v0.1.9)

When you press `[N]` to block a cold resume, `cold-resume-guard` doesn't just stop the launch—it solves the context problem on the spot:

1. **Gathers Live Git State:** Inspects `git status -s` and `git log -1` to identify uncommitted modified files, your active branch, and the last commit checkpoint.
2. **Synthesizes Intent:** Extracts your session goal and the exact last message you sent before leaving.
3. **Copies to Clipboard:** Uses native macOS `pbcopy` to place a structured handoff prompt on your clipboard.
4. **Zero-Friction Continuation:** Simply run `claude` or `codex`, press `⌘V`, and your agent resumes work with clean, warm context for **under 100 tokens**.

---

## OpenAI Codex CLI Support

OpenAI Codex CLI has a **10-minute (600s) prompt cache TTL** (compared to 60 minutes for Claude Pro). Sessions go cold 6× faster!

`cold-resume-guard` includes built-in support for Codex:
- **Session Discovery:** Scans `~/.codex/sessions/**/rollout-*.jsonl` matching your project's working directory (`cwd`).
- **Context Inspection:** Computes the active turn context (`last_token_usage.input_tokens`) and warns before sending cold turns over the network.
- **Handoff Suggestion:** Recommends typing `/new` inside Codex to begin a fresh turn with clean context instead of burning ~150k+ tokens on a cold resume.
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
