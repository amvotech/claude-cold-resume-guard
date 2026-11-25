import io
import json
import os
import subprocess
from pathlib import Path

import shutil
import pytest

from cold_resume_guard import cli

REAL_RUN, REAL_POPEN = subprocess.run, subprocess.Popen  # saved before the isolation fixture blocks them
shutil_which = shutil.which

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """No test may touch the real home folder or start a real program (PowerShell, clip.exe, agents).
    Tests that need a program pass a fake `which` / `run` / `popen` explicitly."""
    monkeypatch.setattr(cli.Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)

    def no_real_programs(*args, **kwargs):
        raise AssertionError(f"test tried to run a real program: {args[0] if args else kwargs}")

    monkeypatch.setattr(cli.subprocess, "run", no_real_programs)
    monkeypatch.setattr(cli.subprocess, "Popen", no_real_programs)


# --- Claude Code project folders (T-W102) ---

@pytest.mark.parametrize("path, expected", [
    ("/Users/dinesh/tech/L2Cache", "-Users-dinesh-tech-L2Cache"),
    ("/home/me/my app/v1.2_beta", "-home-me-my-app-v1-2-beta"),
    ("/mnt/c/Users/me/proj", "-mnt-c-Users-me-proj"),
    ("C:\\Users\\me\\proj", "C--Users-me-proj"),
    ("/private/tmp/claude-501/x", "-private-tmp-claude-501-x"),
])
def test_claude_project_folder_name(path, expected):
    assert cli.claude_project_folder_name(path) == expected


def _claude_session(home, project_dir, name="abc.jsonl"):
    folder = home / ".claude" / "projects" / cli.claude_project_folder_name(os.path.realpath(project_dir))
    folder.mkdir(parents=True, exist_ok=True)
    f = folder / name
    f.write_text('{"sessionId":"abc"}\n', encoding="utf-8")
    return f


def test_claude_dir_matches_this_project(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "work" / "my proj"
    proj.mkdir(parents=True)
    session = _claude_session(home, proj)
    folder, files = cli.get_claude_project_dir(str(proj), home=home)
    assert folder == session.parent
    assert files == [session]


def test_claude_dir_never_falls_back_to_other_projects(tmp_path):
    home, here, other = tmp_path / "home", tmp_path / "here", tmp_path / "other"
    here.mkdir()
    other.mkdir()
    _claude_session(home, other)
    assert cli.get_claude_project_dir(str(here), home=home) == (None, [])
    assert cli.find_session(project_dir=str(here), agent="claude", home=home) == (None, None)
    assert len(cli.get_all_claude_sessions(home=home)) == 1


def test_claude_dir_without_claude_install(tmp_path):
    assert cli.get_claude_project_dir(str(tmp_path), home=tmp_path / "nohome") == (None, [])
    assert cli.get_all_claude_sessions(home=tmp_path / "nohome") == []


# --- Codex sessions (T-W103) ---

def _codex_session(home, cwd, name="rollout-1.jsonl", top_level=False):
    folder = home / ".codex" / "sessions" / "2026" / "11" / "25"
    folder.mkdir(parents=True, exist_ok=True)
    meta = {"type": "session_meta", "payload": {"session_id": "s1"}}
    if top_level:
        meta["cwd"] = cwd
    else:
        meta["payload"]["cwd"] = cwd
    f = folder / name
    f.write_text(json.dumps(meta) + "\n", encoding="utf-8")
    return f


@pytest.mark.parametrize("path, windows, expected", [
    ("/Users/me/app/", False, "/Users/me/app"),
    ("/", False, "/"),
    ("C:/Users/Me/App/", True, "c:\\users\\me\\app"),
    ("c:\\users\\me\\app", True, "c:\\users\\me\\app"),
])
def test_normalize_path(path, windows, expected):
    assert cli.normalize_path(path, windows) == expected


def test_codex_session_cwd_reads_payload_and_top_level(tmp_path):
    assert cli.codex_session_cwd(_codex_session(tmp_path, "/a/b")) == "/a/b"
    assert cli.codex_session_cwd(_codex_session(tmp_path, "/c", name="r2.jsonl", top_level=True)) == "/c"


def test_codex_session_cwd_ignores_broken_or_missing(tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"type":"session_meta", broken\n{"type":"other"}\n', encoding="utf-8")
    assert cli.codex_session_cwd(bad) is None
    assert cli.codex_session_cwd(tmp_path / "missing.jsonl") is None


def test_codex_matches_posix_cwd(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    match = _codex_session(home, os.path.realpath(proj))
    _codex_session(home, "/somewhere/else", name="rollout-2.jsonl")
    assert cli.get_codex_sessions_for_dir(str(proj), home=home, windows=False) == [match]


def test_codex_matches_windows_cwd_with_escaped_backslashes_and_case(tmp_path, monkeypatch):
    home = tmp_path / "home"
    # JSON on disk holds "C:\\Users\\Me\\App"; the shell reports a different drive-letter case.
    match = _codex_session(home, "C:\\Users\\Me\\App")
    assert '\\\\' in match.read_text(encoding="utf-8")
    monkeypatch.setattr(cli.os.path, "realpath", lambda p: p)
    assert cli.get_codex_sessions_for_dir("c:\\users\\me\\app", home=home, windows=True) == [match]
    assert cli.get_codex_sessions_for_dir("C:\\Users\\Me\\Other", home=home, windows=True) == []


def test_codex_without_codex_install(tmp_path):
    assert cli.get_codex_sessions_for_dir(str(tmp_path), home=tmp_path / "nohome") == []


# --- Clipboard (T-W104) ---

def test_clipboard_commands_per_platform():
    none = lambda _: None
    assert cli.clipboard_commands("darwin") == [(["pbcopy"], "utf-8")]
    assert cli.clipboard_commands("win32") == [(["clip.exe"], "utf-16")]
    assert cli.clipboard_commands("linux", {}, none, wsl=True) == [(["clip.exe"], "utf-16")]
    found = lambda name: "/usr/bin/" + name
    assert cli.clipboard_commands("linux", {"WAYLAND_DISPLAY": "w"}, found, wsl=False) == [
        (["wl-copy"], "utf-8"), (["xclip", "-selection", "clipboard"], "utf-8"), (["xsel", "-b", "-i"], "utf-8")]
    assert cli.clipboard_commands("linux", {}, none, wsl=False) == []


class _FakeProc:
    def __init__(self, returncode, sink):
        self.returncode, self.sink = returncode, sink

    def communicate(self, data):
        self.sink.append(data)


def test_copy_to_clipboard_falls_through_and_encodes():
    sent, calls = [], []

    def popen(cmd, **_):
        calls.append(cmd[0])
        if cmd[0] == "missing":
            raise FileNotFoundError
        return _FakeProc(1 if cmd[0] == "failing" else 0, sent)

    commands = [(["missing"], "utf-8"), (["failing"], "utf-8"), (["clip.exe"], "utf-16")]
    assert cli.copy_to_clipboard("é ü 🚀", commands, popen) is True
    assert calls == ["missing", "failing", "clip.exe"]
    assert sent[-1][:2] == b"\xff\xfe"  # UTF-16LE byte-order mark, so clip.exe keeps Unicode
    assert sent[-1].decode("utf-16") == "é ü 🚀"
    assert cli.copy_to_clipboard("x", [], popen) is False


def test_is_wsl_reads_proc_version(monkeypatch):
    monkeypatch.setattr("builtins.open", lambda *a, **k: io.StringIO("Linux 5.15 microsoft-standard-WSL2"))
    assert cli.is_wsl() is True

    def missing(*a, **k):
        raise OSError

    monkeypatch.setattr("builtins.open", missing)
    assert cli.is_wsl() is False


# --- Launching the agent (T-W105) ---

def test_launch_on_windows_runs_child_and_passes_exit_code(monkeypatch):
    seen = {}

    class Proc:
        def __init__(self, args):
            seen["args"] = args
            self.waits = 0

        def wait(self):
            self.waits += 1
            if self.waits == 1:
                raise KeyboardInterrupt  # Ctrl+C belongs to the agent; keep waiting
            return 3

    monkeypatch.setattr(cli.shutil, "which", lambda c: "C:\\npm\\" + c + ".cmd")
    monkeypatch.setattr(cli.subprocess, "Popen", Proc)
    with pytest.raises(SystemExit) as e:
        cli.launch_agent("codex", ["resume", "s1"], windows=True)
    assert e.value.code == 3
    assert seen["args"] == ["C:\\npm\\codex.cmd", "resume", "s1"]


def test_launch_on_windows_reports_missing_agent(monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda c: None)
    with pytest.raises(SystemExit) as e:
        cli.launch_agent("claude", [], windows=True)
    assert e.value.code == 1


def test_launch_on_posix_execs(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli.os, "execvp", lambda cmd, args: seen.update(cmd=cmd, args=args))
    cli.launch_agent("claude", ["--resume", "x"], windows=False)
    assert seen == {"cmd": "claude", "args": ["claude", "--resume", "x"]}

    def missing(cmd, args):
        raise FileNotFoundError

    monkeypatch.setattr(cli.os, "execvp", missing)
    with pytest.raises(SystemExit):
        cli.launch_agent("codex", [], windows=False)


# --- Shell wrappers (T-W106) ---

def test_add_and_remove_hook_restores_original(tmp_path):
    rc = tmp_path / ".bashrc"
    original = "export A=1\n"
    rc.write_text(original, encoding="utf-8")
    assert cli.add_hook_block(rc, cli.POSIX_SNIPPET) == "added"
    assert cli.add_hook_block(rc, cli.POSIX_SNIPPET) is None  # second run changes nothing
    assert rc.read_text(encoding="utf-8").count(cli.HOOK_BEGIN) == 1
    assert cli.remove_hook_block(rc) is True
    assert rc.read_text(encoding="utf-8") == original  # user content kept, only our block removed
    assert cli.remove_hook_block(rc) is False
    assert cli.remove_hook_block(tmp_path / "missing") is False


def test_remove_deletes_file_that_only_held_the_block(tmp_path):
    profile = tmp_path / "profile.ps1"
    cli.add_hook_block(profile, cli.POWERSHELL_SNIPPET)
    assert cli.remove_hook_block(profile) is True
    assert not profile.exists()


def test_add_hook_creates_powershell_profile_folders(tmp_path):
    profile = tmp_path / "Documents" / "PowerShell" / "Microsoft.PowerShell_profile.ps1"
    assert cli.add_hook_block(profile, cli.POWERSHELL_SNIPPET) == "added"
    assert "function claude { cold-resume-guard --agent claude @args }" in profile.read_text(encoding="utf-8")


OLD_V02_BLOCK = """
# >>> cold-resume-guard begin >>>
claude() {
    cold-resume-guard "$@"
}
codex() {
    cold-resume-guard --agent codex "$@"
}
# <<< cold-resume-guard end <<<
"""


def test_setup_upgrades_an_older_wrapper_in_place(tmp_path):
    """v0.2 wrappers called the guard without --agent, so `claude` could check (and launch) a Codex session."""
    rc = tmp_path / ".zshrc"
    rc.write_text("export A=1\n" + OLD_V02_BLOCK + "alias ll='ls -l'\n", encoding="utf-8")
    assert cli.add_hook_block(rc, cli.POSIX_SNIPPET) == "updated"
    content = rc.read_text(encoding="utf-8")
    assert content.count(cli.HOOK_BEGIN) == 1
    assert 'cold-resume-guard --agent claude "$@"' in content
    assert content.startswith("export A=1\n") and content.endswith("alias ll='ls -l'\n")
    assert cli.add_hook_block(rc, cli.POSIX_SNIPPET) is None


@pytest.mark.parametrize("snippet", [cli.POSIX_SNIPPET, cli.GIT_BASH_SNIPPET, cli.POWERSHELL_SNIPPET])
def test_every_wrapper_names_its_agent(snippet):
    assert "--agent claude" in snippet and "--agent codex" in snippet


@pytest.mark.skipif(os.name == "nt" or not shutil_which("bash"), reason="POSIX bash simulation; real Git Bash is checked in the VM")
def test_git_bash_wrapper_falls_back_to_cmd_launcher(tmp_path, monkeypatch):
    """Git Bash can't run cold-resume-guard.cmd by bare name; the wrapper must try the .cmd name."""
    monkeypatch.setattr(cli.subprocess, "Popen", REAL_POPEN)  # this test runs a real bash on purpose
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    launcher = bin_dir / "cold-resume-guard.cmd"
    launcher.write_text('#!/bin/sh\necho "cmd launcher: $*"\n', encoding="utf-8")
    launcher.chmod(0o755)
    script = cli.GIT_BASH_SNIPPET + '\nclaude --ttl 60 "two words"\ncodex resume abc\n'
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path)}
    out = REAL_RUN(["bash", "-c", script], capture_output=True, text=True, env=env).stdout
    assert out.splitlines() == ["cmd launcher: --agent claude --ttl 60 two words",
                                "cmd launcher: --agent codex resume abc"]


class _Run:
    def __init__(self, stdout="", returncode=0, fail=False):
        self.stdout, self.returncode, self.fail = stdout, returncode, fail
        self.calls = []

    def __call__(self, args, **_):
        self.calls.append(args)
        if self.fail:
            raise OSError
        return subprocess.CompletedProcess(args, self.returncode, self.stdout, "")


def test_windows_targets_ask_powershell_for_profile(tmp_path):
    which = lambda exe: exe if exe in ("powershell", "pwsh") else None
    run = _Run(stdout="D:\\OneDrive\\Documents\\profile.ps1\n")
    targets = cli.shell_hook_targets(tmp_path, windows=True, which=which, run=run)
    assert [t[0] for t in targets] == [Path("D:\\OneDrive\\Documents\\profile.ps1")] * 2 + [tmp_path / ".bashrc"]
    assert targets[0][1] == cli.POWERSHELL_SNIPPET and targets[-1][1] == cli.GIT_BASH_SNIPPET


def test_windows_targets_fall_back_to_default_profiles(tmp_path):
    which = lambda exe: exe if exe == "powershell" else None
    targets = cli.shell_hook_targets(tmp_path, windows=True, which=which, run=_Run(fail=True))
    assert targets[0][0] == tmp_path / "Documents" / "WindowsPowerShell" / "Microsoft.PowerShell_profile.ps1"
    assert len(targets) == 2


def test_posix_targets_prefer_zshrc(tmp_path):
    assert cli.shell_hook_targets(tmp_path, windows=False)[0][0] == tmp_path / ".bashrc"
    (tmp_path / ".zshrc").write_text("", encoding="utf-8")
    assert cli.shell_hook_targets(tmp_path, windows=False)[0][0] == tmp_path / ".zshrc"


class _PolicyRun:
    """Fake PowerShell: each edition reports its own execution policy."""
    def __init__(self, policies):
        self.policies = policies

    def __call__(self, args, **_):
        if args[0] not in self.policies:
            raise OSError
        return subprocess.CompletedProcess(args, 0, self.policies[args[0]] + "\r\n", "")


@pytest.mark.parametrize("policies, blocked", [
    ({"powershell": "Restricted", "pwsh": "RemoteSigned"}, ["Windows PowerShell"]),
    ({"powershell": "Undefined", "pwsh": "AllSigned"}, ["Windows PowerShell", "PowerShell 7"]),
    ({"powershell": "RemoteSigned", "pwsh": "Bypass"}, []),
])
def test_each_powershell_edition_has_its_own_policy(policies, blocked):
    assert cli.powershell_editions_blocking(lambda e: e, _PolicyRun(policies)) == blocked


def test_policy_check_skips_missing_or_failing_powershell():
    assert cli.powershell_editions_blocking(lambda e: None, _Run()) == []
    assert cli.powershell_editions_blocking(lambda e: e, _Run(fail=True)) == []


@pytest.mark.parametrize("windows", [False, True])
def test_platform_is_read_at_call_time(windows, monkeypatch, tmp_path):
    """Regression: defaults used to be fixed at import, so tests on Windows wrote the real PowerShell profile."""
    monkeypatch.setattr(cli, "IS_WINDOWS", windows)
    targets = cli.shell_hook_targets(tmp_path)
    assert [t[0] for t in targets] == [tmp_path / ".bashrc"]  # no PowerShell found, so only Git Bash / bash
    assert cli.normalize_path("C:/A/") == ("c:\\a" if windows else "C:/A")


def test_install_and_remove_shell_hook_end_to_end(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(cli, "IS_WINDOWS", False)
    cli.install_shell_hook()
    cli.install_shell_hook()
    out = capsys.readouterr().out
    assert "added in" in out and "already installed" in out
    cli.remove_shell_hook()
    cli.remove_shell_hook()
    out = capsys.readouterr().out
    assert "removed" in out and "No cold-resume-guard shell wrappers found." in out
    assert not (tmp_path / ".bashrc").exists()  # created by setup, gone after remove


def test_install_on_windows_explains_execution_policy(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "IS_WINDOWS", True)
    monkeypatch.setattr(cli, "shell_hook_targets", lambda: [(tmp_path / ".bashrc", cli.POSIX_SNIPPET)])
    monkeypatch.setattr(cli, "powershell_editions_blocking", lambda: ["Windows PowerShell"])
    cli.install_shell_hook()
    out = capsys.readouterr().out
    assert "Windows PowerShell is set to block profile scripts" in out
    assert cli.ALLOW_PROFILES_CMD in out and "pwsh -NoProfile" not in out


# --- Colours (T-W107) ---

class _Stream(io.StringIO):
    def __init__(self, tty):
        super().__init__()
        self._tty = tty

    def isatty(self):
        return self._tty


def test_colours_stripped_when_piped_or_no_color():
    for stream, env in ((_Stream(False), {}), (_Stream(True), {"NO_COLOR": "1"})):
        out = cli.setup_colours(stream, env, windows=False)
        out.write("\033[1;31mred\033[0m plain")
        assert stream.getvalue() == "red plain"
        assert out.isatty() == stream.isatty()  # other attributes pass through


def test_symbols_never_crash_on_legacy_windows_code_page(tmp_path):
    """Regression: piped output on Windows is cp1252, which has no ✓; printing it used to crash --setup."""
    with open(tmp_path / "out.txt", "w", encoding="cp1252") as raw:
        out = cli.setup_colours(raw, {}, windows=False)
        out.write("✓ done ⚠️ 📋")
    assert (tmp_path / "out.txt").read_text(encoding="cp1252") == "? done ?? ?"  # ⚠️ is two code points


def test_colours_kept_on_terminal(monkeypatch):
    calls = []
    monkeypatch.setattr(cli.os, "system", lambda c: calls.append(c))
    stream = _Stream(True)
    assert cli.setup_colours(stream, {}, windows=True) is stream
    assert calls == [""]


def test_check_only_reports_a_session_it_will_not_warn_about(tmp_path, monkeypatch, capsys):
    """Regression: --check-only printed nothing for a warm or small session, which looked broken."""
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    session = _claude_session(home, proj)
    session.write_text('{"sessionId":"abcd1234","message":{"usage":{"input_tokens":500}}}\n', encoding="utf-8")
    monkeypatch.chdir(proj)
    monkeypatch.setattr(cli.sys, "argv", ["cold-resume-guard", "--check-only", "--ttl", "1"])
    monkeypatch.setattr(cli.sys, "stdout", cli.sys.stdout)
    os.utime(session, (0, 0))
    cli.main()
    assert "only ~500 tokens, so resuming is cheap" in capsys.readouterr().out


def test_check_only_says_when_folder_has_no_sessions(tmp_path, monkeypatch, capsys):
    """Regression: --check-only used to exit silently, which looked like a failure."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli.sys, "argv", ["cold-resume-guard", "--check-only"])
    monkeypatch.setattr(cli.sys, "stdout", cli.sys.stdout)
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 0
    assert "No Claude Code or Codex sessions found for this folder." in capsys.readouterr().out


class _Launched(Exception):
    pass


def _run_main(monkeypatch, argv, cwd):
    """Run main() and return (agent, args) it would launch, or None if it exited without launching."""
    def fake_launch(agent, args, windows=None):
        raise _Launched(agent, args)
    monkeypatch.setattr(cli, "launch_agent", fake_launch)
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(cli.sys, "argv", argv)
    monkeypatch.setattr(cli.sys, "stdout", cli.sys.stdout)
    try:
        cli.main()
    except _Launched as e:
        return e.args
    except SystemExit:
        return None


def _old_big_session(home, proj):
    s = _claude_session(home, proj, name="f39616a9-1111.jsonl")
    s.write_text('{"sessionId":"f39616a9-1111","timestamp":"2020-01-01T00:00:00Z",'
                 '"message":{"usage":{"cache_read_input_tokens":90000}}}\n', encoding="utf-8")
    return s


def test_fresh_claude_through_wrapper_skips_the_check(tmp_path, monkeypatch, capsys):
    """A new session reloads nothing, so `claude` (wrapper adds --agent claude) launches straight away."""
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    _old_big_session(home, proj)
    monkeypatch.setattr(cli.Path, "home", lambda: home)
    launched = _run_main(monkeypatch, ["cold-resume-guard", "--agent", "claude", "-p", "hi"], proj)
    assert launched == ("claude", ["-p", "hi"])
    assert "Expired" not in capsys.readouterr().out


@pytest.mark.parametrize("argv, forwarded", [
    (["--agent", "claude", "--resume"], ["--resume"]),
    (["--agent", "claude", "--continue"], ["--continue"]),
    (["--agent", "claude", "-c"], ["-c"]),
    (["--agent", "claude", "--resume", "f39616a9"], ["--resume", "f39616a9"]),
    (["f39616a9"], ["--resume", "f39616a9-1111"]),  # bare ID: guard, then resume that session
])
def test_resume_commands_are_guarded_and_forwarded(argv, forwarded, tmp_path, monkeypatch, capsys):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    _old_big_session(home, proj)
    monkeypatch.setattr(cli.Path, "home", lambda: home)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")  # proceed despite the warning
    launched = _run_main(monkeypatch, ["cold-resume-guard", "--ttl", "60"] + argv, proj)
    assert "Prompt Cache Expired" in capsys.readouterr().out
    assert launched == ("claude", forwarded)


def test_codex_dash_c_is_config_not_continue(tmp_path, monkeypatch):
    launched = _run_main(monkeypatch, ["cold-resume-guard", "--agent", "codex", "-c", "model=x"], tmp_path)
    assert launched == ("codex", ["-c", "model=x"])


def test_session_id_found_in_another_project(tmp_path, monkeypatch):
    home, here, other = tmp_path / "home", tmp_path / "here", tmp_path / "other"
    here.mkdir()
    other.mkdir()
    session = _old_big_session(home, other)
    assert cli.find_session(project_dir=str(here), session_query="f39616a9", home=home) == (session, "claude")
    assert cli.find_session(project_dir=str(here), session_query="deadbeef", home=home) == (None, None)


def test_idle_time_comes_from_message_timestamps(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    s = _old_big_session(tmp_path / "home", proj)  # file is new, last message is from 2020
    idle, tokens, _, _ = cli.inspect_session(s, "claude")
    assert idle > 365 * 24 * 3600 and tokens == 90000
    assert cli.parse_iso_timestamp("not a date") is None and cli.parse_iso_timestamp(None) is None


def test_subfolder_matches_sessions_started_at_git_root(tmp_path, monkeypatch):
    home, repo = tmp_path / "home", tmp_path / "repo"
    sub = repo / "src" / "app"
    sub.mkdir(parents=True)
    session = _claude_session(home, repo)
    monkeypatch.setattr(cli, "get_git_root", lambda p: os.path.realpath(repo))
    assert cli.get_claude_project_dir(str(sub), home=home) == (session.parent, [session])


# --- Arguments and packaging ---

def test_extract_forward_args_drops_guard_options():
    argv = ["--ttl", "60", "--agent", "codex", "--remove", "--check-only", "--ttl=5", "resume", "abc", "-p", "hi"]
    assert cli.extract_forward_args(argv) == ["resume", "abc", "-p", "hi"]


def test_guard_py_matches_package_source():
    """guard.py is what install scripts download; it must stay a copy of the package source."""
    assert (REPO / "guard.py").read_text(encoding="utf-8") == (REPO / "src" / "cold_resume_guard" / "cli.py").read_text(encoding="utf-8")
