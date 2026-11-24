#!/usr/bin/env python3
"""
Cold-Resume Guard for Claude Code & OpenAI Codex CLI.
Detects expired prompt cache TTLs from local JSONL transcripts and displays
estimated token penalties before sending cold resumes over the network.
"""

import sys
import os
import json
import time
from pathlib import Path

# Cache TTL constants
CLAUDE_PRO_TTL = 3600    # 1 hour for Claude Pro / Team subscriptions
CODEX_TTL = 600          # 10 minutes for OpenAI prompt caching
CACHE_WRITE_MULTIPLIER = 1.25

def get_claude_project_dir(project_dir=None):
    if not project_dir:
        project_dir = os.getcwd()
    claude_projects = Path.home() / ".claude" / "projects"
    if not claude_projects.exists():
        return None, []
    
    sanitized = project_dir.replace("/", "%2F").replace(" ", "%20")
    project_match = claude_projects / sanitized
    target_dir = project_match if project_match.exists() else claude_projects
    
    jsonl_files = list(target_dir.glob("**/*.jsonl"))
    if not jsonl_files:
        jsonl_files = list(claude_projects.glob("**/*.jsonl"))
        
    return target_dir, jsonl_files

def get_codex_sessions_for_dir(project_dir=None):
    if not project_dir:
        project_dir = os.getcwd()
    codex_sessions_dir = Path.home() / ".codex" / "sessions"
    if not codex_sessions_dir.exists():
        return []
        
    matching_files = []
    # Search latest 50 rollout files
    all_files = sorted(codex_sessions_dir.glob("**/*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)[:50]
    for f in all_files:
        try:
            with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                for _ in range(5):
                    line = fh.readline()
                    if not line: break
                    if "session_meta" in line and project_dir in line:
                        matching_files.append(f)
                        break
        except Exception:
            continue
    return matching_files

def find_session(project_dir=None, session_query=None, agent="auto"):
    if not project_dir:
        project_dir = os.getcwd()
        
    claude_files = []
    codex_files = []
    
    if agent in ("auto", "claude"):
        _, claude_files = get_claude_project_dir(project_dir)
    if agent in ("auto", "codex"):
        codex_files = get_codex_sessions_for_dir(project_dir)
        
    candidates = []
    for f in claude_files:
        candidates.append((f, "claude", f.stat().st_mtime))
    for f in codex_files:
        candidates.append((f, "codex", f.stat().st_mtime))
        
    if not candidates:
        return None, None
        
    if session_query:
        query_clean = session_query.strip().lower()
        matches = [c for c in candidates if query_clean in c[0].name.lower()]
        if matches:
            best = max(matches, key=lambda c: c[2])
            return best[0], best[1]
            
    best = max(candidates, key=lambda c: c[2])
    return best[0], best[1]

def inspect_claude_session(session_file):
    last_event_time = session_file.stat().st_mtime
    idle_seconds = time.time() - last_event_time
    active_tokens = 0
    message_count = 0
    session_title = ""
    last_prompt = ""
    git_branch = ""
    session_id = session_file.stem
    
    try:
        with open(session_file, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if not line.strip(): continue
                try:
                    data = json.loads(line)
                    message_count += 1
                    
                    if "aiTitle" in data and not session_title:
                        session_title = data["aiTitle"]
                    if "lastPrompt" in data:
                        last_prompt = data["lastPrompt"]
                    if "gitBranch" in data and not git_branch:
                        git_branch = data["gitBranch"]
                    if "sessionId" in data:
                        session_id = data["sessionId"]
                    
                    msg = data.get("message", {})
                    usage = msg.get("usage") if isinstance(msg, dict) else None
                    if not usage and "usage" in data:
                        usage = data["usage"]
                        
                    if usage and isinstance(usage, dict):
                        total_turn_tokens = (
                            usage.get("cache_read_input_tokens", 0) +
                            usage.get("cache_creation_input_tokens", 0) +
                            usage.get("input_tokens", 0)
                        )
                        if total_turn_tokens > 0:
                            active_tokens = total_turn_tokens
                except json.JSONDecodeError:
                    continue
    except Exception:
        pass
    
    if active_tokens == 0:
        active_tokens = min(int(session_file.stat().st_size / 4.0), 200000)
        
    meta = {
        "agent": "Claude Code",
        "title": session_title or "Untitled Claude Session",
        "last_prompt": last_prompt,
        "branch": git_branch,
        "session_id": session_id[:8] if session_id else "unknown",
        "full_id": session_id,
        "last_active": time.strftime("%Y-%m-%d %H:%M", time.localtime(last_event_time)),
        "handoff_cmd": "/clear"
    }
    return idle_seconds, active_tokens, message_count, meta

def inspect_codex_session(session_file):
    last_event_time = session_file.stat().st_mtime
    idle_seconds = time.time() - last_event_time
    active_tokens = 0
    message_count = 0
    session_id = None
    session_title = ""
    last_prompt = ""
    
    try:
        with open(session_file, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if not line.strip(): continue
                try:
                    data = json.loads(line)
                    message_count += 1
                    t = data.get("type", "")
                    
                    if t == "session_meta":
                        session_id = data.get("payload", {}).get("session_id")
                        
                    if t == "response_item":
                        p = data.get("payload", {})
                        if p.get("type") == "message" and p.get("role") == "user":
                            for c in p.get("content", []):
                                if c.get("type") == "input_text":
                                    txt = c.get("text", "").strip()
                                    if not txt.startswith("<") and not txt.startswith("# AGENTS"):
                                        if not session_title:
                                            session_title = txt.split("\n")[0][:50]
                                        last_prompt = txt.split("\n")[0][:80]
                                        
                    if t == "token_usage_record":
                        u = data.get("payload", {}).get("usage", {})
                        in_tok = u.get("input_tokens", 0)
                        if in_tok > 0:
                            active_tokens = in_tok
                    elif t == "event_msg" and data.get("payload", {}).get("type") == "token_count":
                        info = data.get("payload", {}).get("info", {})
                        u = info.get("last_token_usage", {})
                        in_tok = u.get("input_tokens", 0)
                        if in_tok > 0:
                            active_tokens = in_tok
                except json.JSONDecodeError:
                    continue
    except Exception:
        pass
        
    if not session_id:
        parts = session_file.stem.split("-")
        for i in range(len(parts) - 4):
            candidate = "-".join(parts[i:i+5])
            if len(candidate) == 36:
                session_id = candidate
                break
        if not session_id:
            session_id = session_file.stem[:8]

    if active_tokens == 0:
        active_tokens = min(int(session_file.stat().st_size / 4.0), 100000)
        
    meta = {
        "agent": "OpenAI Codex CLI",
        "title": session_title or f"Codex Session {session_id[:8]}",
        "last_prompt": last_prompt or "Recent terminal turn",
        "branch": "",
        "session_id": session_id[:8] if session_id else "unknown",
        "full_id": session_id or session_file.stem,
        "last_active": time.strftime("%Y-%m-%d %H:%M", time.localtime(last_event_time)),
        "handoff_cmd": "/new"
    }
    return idle_seconds, active_tokens, message_count, meta

def inspect_session(session_file, agent_type="claude"):
    if agent_type == "codex":
        return inspect_codex_session(session_file)
    return inspect_claude_session(session_file)

def list_recent_sessions(limit=8):
    claude_dir, claude_files = get_claude_project_dir()
    codex_files = get_codex_sessions_for_dir()
    
    candidates = [(f, "claude") for f in claude_files] + [(f, "codex") for f in codex_files]
    if not candidates:
        print("No coding agent sessions found for this directory.")
        return
        
    sorted_files = sorted(candidates, key=lambda c: c[0].stat().st_mtime, reverse=True)[:limit]
    
    print(f"\n\033[1mRecent AI Sessions in this project:\033[0m")
    print(f"{'AGENT':<13} {'ID':<10} {'STATUS':<8} {'CONTEXT':<12} {'LAST ACTIVE':<14} {'TITLE'}")
    print("-" * 88)
    
    for f, ag in sorted_files:
        ttl = CODEX_TTL if ag == "codex" else CLAUDE_PRO_TTL
        idle_seconds, tokens, _, meta = inspect_session(f, ag)
        status = "\033[32mWARM\033[0m" if idle_seconds <= ttl else "\033[31mCOLD\033[0m"
        idle_h = f"{round(idle_seconds/3600, 1)}h ago" if idle_seconds >= 3600 else f"{int(idle_seconds//60)}m ago"
        title_trunc = meta["title"][:28] + ("..." if len(meta["title"]) > 28 else "")
        print(f"{meta['agent']:<13} {meta['session_id']:<10} {status:<17} ~{tokens:<10,} {idle_h:<14} {title_trunc}")
    print()

def install_shell_hook():
    home = Path.home()
    zshrc = home / ".zshrc"
    bashrc = home / ".bashrc"
    target = zshrc if zshrc.exists() else bashrc
    
    snippet = """
# >>> cold-resume-guard begin >>>
claude() {
    cold-resume-guard "$@"
}
codex() {
    cold-resume-guard --agent codex "$@"
}
# <<< cold-resume-guard end <<<
"""
    try:
        content = target.read_text(encoding="utf-8") if target.exists() else ""
        if "cold-resume-guard begin" in content:
            print(f"✓ Shell hooks already installed in {target}")
            return
            
        with open(target, "a", encoding="utf-8") as f:
            f.write(snippet)
        print(f"\033[1;32m✓ Shell hooks successfully added to {target}!\033[0m")
        print(f"Run: \033[1;36msource {target}\033[0m (or restart your terminal) to activate.")
    except Exception as e:
        print(f"\033[1;31mError updating shell config: {e}\033[0m")
        sys.exit(1)

def extract_forward_args(argv):
    forward = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--check-only", "--list", "-l", "--setup", "--install-hook"):
            i += 1
            continue
        if arg in ("--ttl",):
            i += 2
            continue
        if arg in ("--agent", "-a"):
            i += 2
            continue
        if arg.startswith("--ttl=") or arg.startswith("--agent="):
            i += 1
            continue
        forward.append(arg)
        i += 1
    return forward

def launch_agent(agent_type, forward_args):
    cmd = "codex" if agent_type == "codex" else "claude"
    try:
        os.execvp(cmd, [cmd] + forward_args)
    except FileNotFoundError:
        print(f"\033[1;31mError: '{cmd}' command not found in your PATH.\033[0m")
        sys.exit(1)

def main():
    prog_name = os.path.basename(sys.argv[0])
    
    # Infer agent preference from binary name or flags
    default_agent = "auto"
    if "codex" in prog_name:
        default_agent = "codex"
    elif "claude" in prog_name:
        default_agent = "claude"

    # Quick pre-checks
    if "--help" in sys.argv or "-h" in sys.argv:
        print("""
Cold-Resume Guard for Claude Code & OpenAI Codex CLI

Usage:
  cold-resume-guard [options] [agent flags / prompts...]
  claude-guard [options] [agent flags / prompts...]
  codex-guard [options] [agent flags / prompts...]

Commands:
  cold-resume-guard              Inspect latest session in project, warn if cold, then launch
  cold-resume-guard --resume <id> Inspect specific Claude session before resuming
  cold-resume-guard resume <id>  Inspect specific Codex session before resuming
  cold-resume-guard --list, -l   List recent sessions in project with TTL status & tokens
  cold-resume-guard --setup      Automatically add shell wrappers to ~/.zshrc or ~/.bashrc
  cold-resume-guard --check-only Inspect session and print metrics without launching

Options:
  --agent, -a <claude|codex>     Specify target agent (default: auto-detect)
  --ttl <seconds>                Override prompt cache TTL (default: 3600s Claude, 600s Codex)
  --help, -h                     Show this help message
""")
        sys.exit(0)

    if "--setup" in sys.argv or "--install-hook" in sys.argv:
        install_shell_hook()
        sys.exit(0)

    if "--list" in sys.argv or "-l" in sys.argv:
        list_recent_sessions()
        sys.exit(0)

    # Manual argument extraction to preserve forwarded flags cleanly
    session_query = None
    agent = default_agent
    ttl_override = None
    check_only = "--check-only" in sys.argv

    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg in ("--agent", "-a") and i + 1 < len(sys.argv):
            agent = sys.argv[i + 1]
            i += 2
            continue
        elif arg.startswith("--agent="):
            agent = arg.split("=", 1)[1]
            i += 1
            continue
        elif arg == "--ttl" and i + 1 < len(sys.argv):
            try:
                ttl_override = int(sys.argv[i + 1])
            except ValueError:
                pass
            i += 2
            continue
        elif arg.startswith("--ttl="):
            try:
                ttl_override = int(arg.split("=", 1)[1])
            except ValueError:
                pass
            i += 1
            continue
        elif arg in ("--resume", "-r") and i + 1 < len(sys.argv):
            session_query = sys.argv[i + 1]
            i += 2
            continue
        elif arg.startswith("--resume="):
            session_query = arg.split("=", 1)[1]
            i += 1
            continue
        elif arg == "resume" and i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith("-"):
            session_query = sys.argv[i + 1]
            if agent == "auto":
                agent = "codex"
            i += 2
            continue
        elif arg == "resume" and (i + 1 == len(sys.argv) or sys.argv[i + 1].startswith("-")):
            # `codex resume` or `codex resume --last`
            if agent == "auto":
                agent = "codex"
            i += 1
            continue
        elif not arg.startswith("-") and len(arg) >= 6 and not session_query:
            session_query = arg
            i += 1
            continue
        i += 1

    forward_args = extract_forward_args(sys.argv[1:])

    session_file, agent_type = find_session(session_query=session_query, agent=agent)
    if not session_file:
        if not check_only:
            launch_agent("codex" if agent == "codex" else "claude", forward_args)
        sys.exit(0)
        
    idle_seconds, tokens, messages, meta = inspect_session(session_file, agent_type)
    idle_minutes = int(idle_seconds // 60)
    idle_hours = round(idle_seconds / 3600, 1)
    
    ttl = ttl_override or (CODEX_TTL if agent_type == "codex" else CLAUDE_PRO_TTL)
    is_cold = idle_seconds > ttl
    
    # Ensure shorthand session IDs are normalized to resume syntax for underlying agent
    if session_query:
        if agent_type == "codex":
            if "resume" not in forward_args:
                forward_args = ["resume", meta["session_id"]] + [a for a in forward_args if a != session_query]
        else:
            if not any(a in ("--resume", "-r") for a in forward_args):
                forward_args = ["--resume", meta["full_id"]] + [a for a in forward_args if a != session_query]
    
    if is_cold and tokens > 20000:
        penalty_tokens = int(tokens * CACHE_WRITE_MULTIPLIER)
        pct_of_quota = round((penalty_tokens / 2500000) * 100, 1)
        
        project_name = os.path.basename(os.getcwd())
        time_display = f"{idle_hours}h ago ({meta['last_active']})" if idle_minutes >= 60 else f"{idle_minutes}m ago"
        
        print(f"\n\033[1;33m⚠️  [{meta['agent']} Guard] Prompt Cache Expired\033[0m")
        print(f"• \033[1mSession:\033[0m {meta['title']} \033[90m(id: {meta['session_id']})\033[0m")
        if meta.get("branch"):
            print(f"• \033[1mContext:\033[0m Project: \033[36m{project_name}\033[0m | Branch: \033[35m{meta['branch']}\033[0m")
        if meta.get("last_prompt"):
            prompt_preview = meta["last_prompt"][:80] + ("..." if len(meta["last_prompt"]) > 80 else "")
            print(f"• \033[1mLast Prompt:\033[0m \"{prompt_preview}\"")
        print(f"• \033[1mLast Active:\033[0m {time_display} · \033[90m(TTL is {ttl // 60}m)\033[0m")
        print(f"• \033[1mAccumulated Context:\033[0m ~{tokens:,} tokens ({messages} events)")
        print(f"• \033[1;31mCold Reload Penalty:\033[0m ~{penalty_tokens:,} weighted input tokens \033[1;31m(~{pct_of_quota}% of quota)\033[0m")
        print(f"\n\033[1mOptions:\033[0m Type \033[1;36m{meta['handoff_cmd']}\033[0m inside {meta['agent']} for fresh handoff (~3k tokens).")
        
        if not check_only:
            try:
                choice = input(f"\n\033[1;31mProceed into cold {meta['agent']} session anyway? [y/N]: \033[0m").strip().lower()
                if choice not in ("y", "yes"):
                    print("\033[1;32m✓ Launch blocked.\033[0m Run fresh session or use L2Cache handoff.\n")
                    sys.exit(1)
            except (KeyboardInterrupt, EOFError):
                print("\n\033[1;32m✓ Launch cancelled.\033[0m\n")
                sys.exit(1)
        print()
    elif not check_only:
        launch_agent(agent_type, forward_args)

    if not check_only:
        launch_agent(agent_type, forward_args)

if __name__ == "__main__":
    main()
