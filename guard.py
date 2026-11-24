#!/usr/bin/env python3
"""
Cold-Resume Guard for Claude Code & Codex CLI.
Detects expired prompt cache TTLs from local JSONL transcripts and displays
estimated token penalties before sending cold resumes over the network.
"""

import sys
import os
import json
import time
from pathlib import Path

# Cache TTL constants
PRO_TTL_SECONDS = 3600    # 1 hour for Claude Pro / Team subscriptions
API_TTL_SECONDS = 300     # 5 minutes for direct API usage
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

def find_claude_session(project_dir=None, session_query=None):
    _, jsonl_files = get_claude_project_dir(project_dir)
    if not jsonl_files:
        return None
        
    if session_query:
        query_clean = session_query.strip().lower()
        matches = [f for f in jsonl_files if query_clean in f.name.lower()]
        if matches:
            return max(matches, key=lambda f: f.stat().st_mtime)
            
    return max(jsonl_files, key=lambda f: f.stat().st_mtime)

def inspect_session(session_file):
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
                if not line.strip():
                    continue
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
                    
                    # Check turn-level usage inside message
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
        "title": session_title or "Untitled Session",
        "last_prompt": last_prompt,
        "branch": git_branch,
        "session_id": session_id[:8] if session_id else "unknown",
        "full_id": session_id,
        "last_active": time.strftime("%Y-%m-%d %H:%M", time.localtime(last_event_time)),
    }
    return idle_seconds, active_tokens, message_count, meta

def list_recent_sessions(limit=5):
    _, jsonl_files = get_claude_project_dir()
    if not jsonl_files:
        print("No Claude Code sessions found.")
        return
        
    sorted_files = sorted(jsonl_files, key=lambda f: f.stat().st_mtime, reverse=True)[:limit]
    
    print(f"\n\033[1mRecent Claude Code Sessions in this project:\033[0m")
    print(f"{'ID':<10} {'STATUS':<8} {'CONTEXT':<12} {'LAST ACTIVE':<14} {'TITLE'}")
    print("-" * 75)
    
    for f in sorted_files:
        idle_seconds, tokens, _, meta = inspect_session(f)
        status = "\033[32mWARM\033[0m" if idle_seconds <= PRO_TTL_SECONDS else "\033[31mCOLD\033[0m"
        idle_h = f"{round(idle_seconds/3600, 1)}h ago" if idle_seconds >= 3600 else f"{int(idle_seconds//60)}m ago"
        title_trunc = meta["title"][:30] + ("..." if len(meta["title"]) > 30 else "")
        print(f"{meta['session_id']:<10} {status:<17} ~{tokens:<10,} {idle_h:<14} {title_trunc}")
    print()

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Cold-Resume Guard for Claude Code & Codex")
    parser.add_argument("session", nargs="?", default=None, help="Session ID or prefix to inspect")
    parser.add_argument("--resume", "-r", dest="resume_id", default=None, help="Session ID passed to claude --resume")
    parser.add_argument("--list", "-l", action="store_true", help="List recent sessions in project")
    parser.add_argument("--check-only", action="store_true", help="Print status without interactive prompt")
    parser.add_argument("--ttl", type=int, default=PRO_TTL_SECONDS, help="TTL in seconds (default: 3600 for Pro, 300 for API)")
    
    # Parse known args so trailing Claude flags (like -p, --cwd, etc.) don't fail
    args, unknown = parser.parse_known_args()

    if args.list:
        list_recent_sessions()
        sys.exit(0)

    # Determine session query from --resume, positional argument, or unknown args
    session_query = args.resume_id or args.session
    if not session_query and unknown:
        for i, arg in enumerate(unknown):
            if arg in ("--resume", "-r") and i + 1 < len(unknown):
                session_query = unknown[i + 1]
                break
            elif not arg.startswith("-") and len(arg) >= 6:
                # Might be a positional session ID
                session_query = arg
                break

    session_file = find_claude_session(session_query=session_query)
    if not session_file:
        sys.exit(0)
        
    idle_seconds, tokens, messages, meta = inspect_session(session_file)
    idle_minutes = int(idle_seconds // 60)
    idle_hours = round(idle_seconds / 3600, 1)
    
    is_cold = idle_seconds > args.ttl
    
    if is_cold and tokens > 20000:
        penalty_tokens = int(tokens * CACHE_WRITE_MULTIPLIER)
        pct_of_5hr = round((penalty_tokens / 2500000) * 100, 1)
        
        project_name = os.path.basename(os.getcwd())
        time_display = f"{idle_hours}h ago ({meta['last_active']})" if idle_minutes >= 60 else f"{idle_minutes}m ago"
        
        print("\n\033[1;33m⚠️  [Cold-Resume Guard] Prompt Cache Expired\033[0m")
        print(f"• \033[1mSession:\033[0m {meta['title']} \033[90m(id: {meta['session_id']})\033[0m")
        if meta.get("branch"):
            print(f"• \033[1mContext:\033[0m Project: \033[36m{project_name}\033[0m | Branch: \033[35m{meta['branch']}\033[0m")
        if meta.get("last_prompt"):
            prompt_preview = meta["last_prompt"][:80] + ("..." if len(meta["last_prompt"]) > 80 else "")
            print(f"• \033[1mLast Prompt:\033[0m \"{prompt_preview}\"")
        print(f"• \033[1mLast Active:\033[0m {time_display} · \033[90m(TTL is {args.ttl // 60}m)\033[0m")
        print(f"• \033[1mAccumulated Context:\033[0m ~{tokens:,} tokens ({messages} events)")
        print(f"• \033[1;31mCold Reload Penalty:\033[0m ~{penalty_tokens:,} weighted input tokens \033[1;31m(~{pct_of_5hr}% of 5-hour quota)\033[0m")
        print("\n\033[1mOptions:\033[0m Type \033[1;36m/clear\033[0m inside Claude Code for fresh handoff (~3k tokens).")
        
        if not args.check_only:
            try:
                choice = input("\n\033[1;31mProceed with cold resume anyway? [y/N]: \033[0m").strip().lower()
                if choice not in ("y", "yes"):
                    print("\033[1;32m✓ Launch blocked.\033[0m Run fresh session or use L2Cache handoff.\n")
                    sys.exit(1)
            except (KeyboardInterrupt, EOFError):
                print("\n\033[1;32m✓ Launch cancelled.\033[0m\n")
                sys.exit(1)
        print()

if __name__ == "__main__":
    main()
