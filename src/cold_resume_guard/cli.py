#!/usr/bin/env python3
"""
Cold-Resume Guard for Claude Code & OpenAI Codex CLI.
Detects expired prompt cache TTLs from local JSONL transcripts and displays
estimated token penalties before sending cold resumes over the network.
"""

import sys
import os
import re
import json
import time
import shutil
import subprocess
from pathlib import Path

# Cache TTL constants
CLAUDE_PRO_TTL = 3600    # 1 hour for Claude Pro / Team subscriptions
CODEX_TTL = 600          # 10 minutes for OpenAI prompt caching
CACHE_WRITE_MULTIPLIER = 1.25

IS_WINDOWS = sys.platform.startswith("win")
PASTE_KEY = "Ctrl+V" if IS_WINDOWS else "⌘V"

def claude_project_folder_name(path):
    """Claude Code names each project folder after the working directory, with every
    character that isn't a letter or digit replaced by '-' (C:\\Users\\me\\app -> C--Users-me-app)."""
    return re.sub(r"[^A-Za-z0-9]", "-", path)

def get_claude_project_dir(project_dir=None, home=None):
    if not project_dir:
        project_dir = os.getcwd()
    claude_projects = (home or Path.home()) / ".claude" / "projects"
    if not claude_projects.exists():
        return None, []

    project_match = claude_projects / claude_project_folder_name(os.path.realpath(project_dir))
    if not project_match.is_dir():
        return None, []
    return project_match, list(project_match.glob("**/*.jsonl"))

def get_all_claude_sessions(home=None):
    claude_projects = (home or Path.home()) / ".claude" / "projects"
    if not claude_projects.exists():
        return []
    return list(claude_projects.glob("**/*.jsonl"))

def normalize_path(path, windows=None):
    """Comparable form of a path: Windows paths ignore case and separator style."""
    if IS_WINDOWS if windows is None else windows:
        return path.replace("/", "\\").rstrip("\\").lower()
    return path.rstrip("/") or "/"

def codex_session_cwd(session_file):
    """The working directory recorded in a Codex rollout's session_meta line, or None."""
    try:
        with open(session_file, "r", encoding="utf-8", errors="ignore") as fh:
            for _ in range(5):
                line = fh.readline()
                if not line:
                    break
                if "session_meta" not in line:
                    continue
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                if data.get("type") != "session_meta":
                    continue
                payload = data.get("payload") or {}
                return payload.get("cwd") or data.get("cwd")
    except OSError:
        return None
    return None

def get_codex_sessions_for_dir(project_dir=None, home=None, windows=None):
    if not project_dir:
        project_dir = os.getcwd()
    codex_sessions_dir = (home or Path.home()) / ".codex" / "sessions"
    if not codex_sessions_dir.exists():
        return []

    wanted = normalize_path(os.path.realpath(project_dir), windows)
    matching_files = []
    # Search latest 50 rollout files
    all_files = sorted(codex_sessions_dir.glob("**/*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)[:50]
    for f in all_files:
        cwd = codex_session_cwd(f)
        if cwd and normalize_path(cwd, windows) == wanted:
            matching_files.append(f)
    return matching_files

def find_session(project_dir=None, session_query=None, agent="auto", home=None):
    if not project_dir:
        project_dir = os.getcwd()
        
    claude_files = []
    codex_files = []
    
    if agent in ("auto", "claude"):
        _, claude_files = get_claude_project_dir(project_dir, home=home)
    if agent in ("auto", "codex"):
        codex_files = get_codex_sessions_for_dir(project_dir, home=home)
        
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
    heading = "Recent AI Sessions in this project:"
    if not candidates:
        candidates = [(f, "claude") for f in get_all_claude_sessions()]
        heading = "No sessions for this folder. Recent Claude Code sessions in all projects:"
    if not candidates:
        print("No coding agent sessions found.")
        return
        
    sorted_files = sorted(candidates, key=lambda c: c[0].stat().st_mtime, reverse=True)[:limit]
    
    print(f"\n\033[1m{heading}\033[0m")
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

HOOK_BEGIN = "# >>> cold-resume-guard begin >>>"
HOOK_END = "# <<< cold-resume-guard end <<<"

POSIX_SNIPPET = f"""
{HOOK_BEGIN}
claude() {{
    cold-resume-guard --agent claude "$@"
}}
codex() {{
    cold-resume-guard --agent codex "$@"
}}
{HOOK_END}
"""

# Git Bash only finds .exe programs by bare name; install.ps1 installs cold-resume-guard.cmd.
GIT_BASH_SNIPPET = f"""
{HOOK_BEGIN}
_cold_resume_guard() {{
    if command -v cold-resume-guard >/dev/null 2>&1; then cold-resume-guard "$@"; else cold-resume-guard.cmd "$@"; fi
}}
claude() {{
    _cold_resume_guard --agent claude "$@"
}}
codex() {{
    _cold_resume_guard --agent codex "$@"
}}
{HOOK_END}
"""

POWERSHELL_SNIPPET = f"""
{HOOK_BEGIN}
function claude {{ cold-resume-guard --agent claude @args }}
function codex {{ cold-resume-guard --agent codex @args }}
{HOOK_END}
"""

def powershell_profiles(home, which=None, run=None):
    """Profile paths for Windows PowerShell 5.1 and PowerShell 7, asked from PowerShell itself
    (Documents may be redirected to OneDrive), falling back to the default locations."""
    defaults = {
        "powershell": home / "Documents" / "WindowsPowerShell" / "Microsoft.PowerShell_profile.ps1",
        "pwsh": home / "Documents" / "PowerShell" / "Microsoft.PowerShell_profile.ps1",
    }
    which = which or shutil.which
    run = run or subprocess.run
    profiles = []
    for exe, default in defaults.items():
        if not which(exe):
            continue
        path = default
        try:
            out = run([exe, "-NoProfile", "-Command", "$PROFILE.CurrentUserCurrentHost"],
                      capture_output=True, text=True, timeout=20)
            if out.returncode == 0 and out.stdout.strip():
                path = Path(out.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            pass
        profiles.append(path)
    return profiles

def shell_hook_targets(home=None, windows=None, which=None, run=None):
    """(file, snippet) pairs the wrappers go into on this platform."""
    home = home or Path.home()
    if IS_WINDOWS if windows is None else windows:
        targets = [(p, POWERSHELL_SNIPPET) for p in powershell_profiles(home, which, run)]
        targets.append((home / ".bashrc", GIT_BASH_SNIPPET))
        return targets
    zshrc = home / ".zshrc"
    return [(zshrc if zshrc.exists() else home / ".bashrc", POSIX_SNIPPET)]

HOOK_BLOCK = re.compile(r"\n?" + re.escape(HOOK_BEGIN) + r".*?" + re.escape(HOOK_END) + r"\n?", re.DOTALL)

def add_hook_block(path, snippet):
    """Install the wrapper block: "added", "updated" (an older block was replaced) or None (already current)."""
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    match = HOOK_BLOCK.search(content)
    if match:
        if match.group(0).strip() == snippet.strip():
            return None
        path.write_text(content[:match.start()] + snippet + content[match.end():], encoding="utf-8")
        return "updated"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(snippet)
    return "added"

def remove_hook_block(path):
    """Delete exactly the wrapper block written by add_hook_block. Returns True if the file changed."""
    if not path.exists():
        return False
    content = path.read_text(encoding="utf-8")
    updated, count = HOOK_BLOCK.subn("", content, count=1)
    if not count:
        return False
    if updated.strip():
        path.write_text(updated, encoding="utf-8")
    else:
        path.unlink()  # setup created it (or it held only our block): leave no empty file behind
    return True

POWERSHELL_EDITIONS = (("powershell", "Windows PowerShell"), ("pwsh", "PowerShell 7"))
ALLOW_PROFILES_CMD = 'powershell -NoProfile -Command "Set-ExecutionPolicy -Scope CurrentUser RemoteSigned"'

def powershell_editions_blocking(which=None, run=None):
    """Names of installed PowerShell editions whose execution policy stops the profile from loading.
    Each edition keeps its own policy: PowerShell 7 allows profiles by default, Windows PowerShell doesn't."""
    which = which or shutil.which
    run = run or subprocess.run
    blocked = []
    for exe, name in POWERSHELL_EDITIONS:
        if not which(exe):
            continue
        try:
            out = run([exe, "-NoProfile", "-Command", "Get-ExecutionPolicy"],
                      capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            continue
        if out.stdout.strip().lower() in ("restricted", "allsigned", "undefined"):
            blocked.append(name)
    return blocked

def install_shell_hook():
    try:
        for path, snippet in shell_hook_targets():
            result = add_hook_block(path, snippet)
            if result:
                print(f"\033[1;32m✓ Shell wrappers {result} in {path}\033[0m")
            else:
                print(f"✓ Shell wrappers already installed in {path}")
    except OSError as e:
        print(f"\033[1;31mError updating shell config: {e}\033[0m")
        sys.exit(1)
    if IS_WINDOWS:
        blocked = powershell_editions_blocking()
        if blocked:
            print(f"\n\033[1;33m{' and '.join(blocked)} is set to block profile scripts, so the wrappers won't load there yet.\033[0m")
            print("To allow them for your account only, run this once (from any terminal):")
            if "PowerShell 7" in blocked:
                print('  \033[1;36mpwsh -NoProfile -Command "Set-ExecutionPolicy -Scope CurrentUser RemoteSigned"\033[0m')
            if "Windows PowerShell" in blocked:
                print(f"  \033[1;36m{ALLOW_PROFILES_CMD}\033[0m")
        print("Open a new PowerShell or Git Bash window to activate.")
    else:
        print("Restart your terminal (or source your shell config) to activate.")

def remove_shell_hook():
    try:
        changed = [p for p, _ in shell_hook_targets() if remove_hook_block(p)]
    except OSError as e:
        print(f"\033[1;31mError updating shell config: {e}\033[0m")
        sys.exit(1)
    for p in changed:
        print(f"\033[1;32m✓ Shell wrappers removed from {p}\033[0m")
    if not changed:
        print("No cold-resume-guard shell wrappers found.")

def is_wsl():
    try:
        with open("/proc/version", "r", encoding="utf-8", errors="ignore") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False

def clipboard_commands(platform=None, environ=None, which=None, wsl=None):
    """Clipboard commands to try in order, each with the text encoding it expects."""
    platform = platform or sys.platform
    environ = os.environ if environ is None else environ
    which = which or shutil.which
    if platform == "darwin":
        return [(["pbcopy"], "utf-8")]
    if platform.startswith("win"):
        # clip.exe reads UTF-16LE when the input starts with a byte-order mark.
        return [(["clip.exe"], "utf-16")]
    commands = []
    if wsl if wsl is not None else is_wsl():
        commands.append((["clip.exe"], "utf-16"))
    if environ.get("WAYLAND_DISPLAY") and which("wl-copy"):
        commands.append((["wl-copy"], "utf-8"))
    if which("xclip"):
        commands.append((["xclip", "-selection", "clipboard"], "utf-8"))
    return commands

def copy_to_clipboard(text, commands=None, popen=None):
    popen = popen or subprocess.Popen
    for cmd, encoding in (commands if commands is not None else clipboard_commands()):
        try:
            proc = popen(cmd, stdin=subprocess.PIPE, close_fds=True)
            proc.communicate(text.encode(encoding))
            if proc.returncode == 0:
                return True
        except OSError:
            continue
    return False

def generate_handoff_prompt(project_name, meta):
    branch = meta.get("branch") or "main"
    title = meta.get("title") or ""
    last_prompt = meta.get("last_prompt") or ""
    session_id = meta.get("session_id") or ""
    
    modified_files = []
    last_commit = ""
    try:
        import subprocess
        status = subprocess.check_output(["git", "status", "-s"], text=True, stderr=subprocess.DEVNULL)
        for line in status.strip().split("\n"):
            if line.strip():
                parts = line.strip().split(maxsplit=1)
                if len(parts) == 2:
                    modified_files.append(parts[1])
    except Exception:
        pass

    try:
        import subprocess
        last_commit = subprocess.check_output(["git", "log", "-1", "--oneline"], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        pass

    lines = [
        f"Resuming work on {project_name} [Branch: {branch} | Session: {session_id}]:",
    ]
    if title and not title.startswith("Untitled"):
        lines.append(f"• Goal: {title}")
    if last_prompt:
        clean_prompt = last_prompt.strip().replace('"', "'")
        lines.append(f"• Last Context: \"{clean_prompt}\"")
    if last_commit:
        lines.append(f"• Last Commit: {last_commit}")
    if modified_files:
        files_str = ", ".join(modified_files[:4])
        if len(modified_files) > 4:
            files_str += f" (+{len(modified_files) - 4} more)"
        lines.append(f"• Modified Files: {files_str}")
    lines.append("Please inspect the current git state and continue work from where we left off.")
    return "\n".join(lines)

def extract_forward_args(argv):
    forward = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--check-only", "--list", "-l", "--setup", "--install-hook", "--remove"):
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

def launch_agent(agent_type, forward_args, windows=None):
    """Hand over to the real agent. Never returns."""
    cmd = "codex" if agent_type == "codex" else "claude"
    if IS_WINDOWS if windows is None else windows:
        # exec can't run npm .cmd shims and doesn't replace the process on Windows,
        # so run the agent as a child, let it own Ctrl+C, and pass its exit code on.
        exe = shutil.which(cmd)
        if not exe:
            print(f"\033[1;31mError: '{cmd}' command not found in your PATH.\033[0m")
            sys.exit(1)
        proc = subprocess.Popen([exe] + forward_args)
        while True:
            try:
                sys.exit(proc.wait())
            except KeyboardInterrupt:
                continue
    try:
        os.execvp(cmd, [cmd] + forward_args)
    except FileNotFoundError:
        print(f"\033[1;31mError: '{cmd}' command not found in your PATH.\033[0m")
        sys.exit(1)

ANSI_ESCAPE = re.compile(r"\033\[[0-9;]*m")

class _PlainOutput:
    """Wraps stdout and drops colour codes, for pipes, files and NO_COLOR."""
    def __init__(self, stream):
        self._stream = stream
    def write(self, text):
        return self._stream.write(ANSI_ESCAPE.sub("", text))
    def __getattr__(self, name):
        return getattr(self._stream, name)

def setup_colours(stream=None, environ=None, windows=None):
    """Returns the stream to print to: colour on a terminal, plain otherwise."""
    stream = stream or sys.stdout
    environ = os.environ if environ is None else environ
    # Redirected output on Windows uses a legacy code page without ✓ ⚠️ 📋; never crash over a symbol.
    if hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(errors="replace")
        except (ValueError, OSError):
            pass
    if environ.get("NO_COLOR") or not stream.isatty():
        return _PlainOutput(stream)
    if IS_WINDOWS if windows is None else windows:
        os.system("")  # switches the Windows console into ANSI mode
    return stream

def main():
    sys.stdout = setup_colours()
    prog_name = os.path.splitext(os.path.basename(sys.argv[0]))[0]
    
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
  cold-resume-guard --setup      Add claude/codex shell wrappers (zsh/bash; PowerShell + Git Bash on Windows)
  cold-resume-guard --remove     Remove the shell wrappers again
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

    if "--remove" in sys.argv:
        remove_shell_hook()
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
        if check_only:
            print("No Claude Code or Codex sessions found for this folder.")
        else:
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
    
    if check_only and not (is_cold and tokens > 20000):
        state = "cache still warm" if not is_cold else f"cache expired, but only ~{tokens:,} tokens, so resuming is cheap"
        print(f"✓ {meta['agent']} session {meta['session_id']} ({meta['title']}): {state}.")
    if is_cold and tokens > 20000:
        penalty_tokens = int(tokens * CACHE_WRITE_MULTIPLIER)
        pct_of_5h = round((penalty_tokens / 2500000) * 100, 1)
        pct_of_weekly = round((penalty_tokens / 20000000) * 100, 1)
        
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
        print(f"• \033[1;31mCold Reload Penalty:\033[0m ~{penalty_tokens:,} weighted input tokens \033[1;31m(~{pct_of_5h}% of 5h quota · ~{pct_of_weekly}% of weekly)\033[0m")
        print(f"\n\033[1mOptions:\033[0m Type \033[1;36m{meta['handoff_cmd']}\033[0m inside {meta['agent']} for fresh handoff (~3k tokens).")
        
        if not check_only:
            try:
                choice = input(f"\n\033[1;31mProceed into cold {meta['agent']} session anyway? [y/N]: \033[0m").strip().lower()
                if choice not in ("y", "yes"):
                    handoff = generate_handoff_prompt(project_name, meta)
                    copied = copy_to_clipboard(handoff)
                    print("\n\033[1;32m✓ Launch blocked.\033[0m")
                    if copied:
                        print("\033[1;36m📋 Rich handoff prompt copied to your clipboard:\033[0m")
                        print("\033[90m" + "\n".join("   " + line for line in handoff.split("\n")) + "\033[0m\n")
                        print(f"Run a fresh session (\033[1m{meta['agent'].split()[0].lower()}\033[0m) and press \033[1m{PASTE_KEY}\033[0m to continue at ~3k tokens.")
                    else:
                        print(f"\033[1mHandoff prompt:\033[0m\n{handoff}\n")
                    print("\033[90mBuilt with ❤️ by L2Cache — Native developer memory & MCP server for Mac: https://l2cache.app\033[0m\n")
                    sys.exit(1)
            except (KeyboardInterrupt, EOFError):
                handoff = generate_handoff_prompt(project_name, meta)
                copied = copy_to_clipboard(handoff)
                print("\n\033[1;32m✓ Launch cancelled.\033[0m")
                if copied:
                    print("\033[1;36m📋 Fresh handoff prompt copied to your clipboard!\033[0m")
                print("\033[90mBuilt with ❤️ by L2Cache — Native developer memory & MCP server for Mac: https://l2cache.app\033[0m\n")
                sys.exit(1)
        print()
    elif not check_only:
        launch_agent(agent_type, forward_args)

    if not check_only:
        launch_agent(agent_type, forward_args)

if __name__ == "__main__":
    main()
