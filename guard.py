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

def get_latest_claude_session(project_dir=None):
    if not project_dir:
        project_dir = os.getcwd()
    
    # Claude projects path: ~/.claude/projects/<sanitized_path>/
    claude_projects = Path.home() / ".claude" / "projects"
    if not claude_projects.exists():
        return None
    
    # Match the project directory
    sanitized = project_dir.replace("/", "%2F").replace(" ", "%20")
    project_match = claude_projects / sanitized
    
    target_dir = project_match if project_match.exists() else claude_projects
    jsonl_files = list(target_dir.glob("**/*.jsonl"))
    if not jsonl_files:
        return None
    
    # Return most recently modified session
    return max(jsonl_files, key=lambda f: f.stat().st_mtime)

def inspect_session(session_file):
    last_event_time = session_file.stat().st_mtime
    idle_seconds = time.time() - last_event_time
    
    active_tokens = 0
    message_count = 0
    
    try:
        with open(session_file, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    message_count += 1
                    
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
        
    return idle_seconds, active_tokens, message_count

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Cold-Resume Guard for Claude Code & Codex")
    parser.add_argument("--check-only", action="store_true", help="Print status without interactive prompt")
    parser.add_argument("--ttl", type=int, default=PRO_TTL_SECONDS, help="TTL in seconds (default: 3600 for Pro, 300 for API)")
    args = parser.parse_args()

    session_file = get_latest_claude_session()
    if not session_file:
        sys.exit(0)
        
    idle_seconds, tokens, messages = inspect_session(session_file)
    idle_minutes = int(idle_seconds // 60)
    
    is_cold = idle_seconds > args.ttl
    
    if is_cold and tokens > 20000:
        penalty_tokens = int(tokens * CACHE_WRITE_MULTIPLIER)
        pct_of_5hr = round((penalty_tokens / 2500000) * 100, 1)
        
        print("\n\033[1;33m⚠️  [Cold-Resume Guard] Prompt Cache Expired\033[0m")
        print(f"• Idle Time: {idle_minutes} minutes (TTL is {args.ttl // 60}m)")
        print(f"• Active Context: ~{tokens:,} tokens ({messages} events)")
        print(f"• Cold Reload Hit: ~{penalty_tokens:,} weighted input tokens (~{pct_of_5hr}% of 5-hour allowance)")
        print("\033[1mOptions:\033[0m Type \033[1;36m/clear\033[0m inside Claude Code for fresh handoff (~3k tokens).")
        
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
