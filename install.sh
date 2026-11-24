#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="$HOME/.local/bin"
GUARD_FILE="$INSTALL_DIR/cold-resume-guard"

echo "==> Installing claude-cold-resume-guard..."

mkdir -p "$INSTALL_DIR"

cat << 'EOF' > "$GUARD_FILE"
#!/usr/bin/env python3
import sys, os, json, time
from pathlib import Path

PRO_TTL = 3600
API_TTL = 300
MULTIPLIER = 1.25

def inspect():
    claude_projects = Path.home() / ".claude" / "projects"
    if not claude_projects.exists():
        return
    jsonl_files = list(claude_projects.glob("**/*.jsonl"))
    if not jsonl_files:
        return
    latest = max(jsonl_files, key=lambda f: f.stat().st_mtime)
    idle = time.time() - latest.stat().st_mtime
    if idle <= PRO_TTL:
        return
    tokens = 0
    try:
        with open(latest, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if not line.strip(): continue
                d = json.loads(line)
                u = d.get("usage", {})
                tokens = max(tokens, u.get("input_tokens", 0))
    except Exception:
        pass
    if tokens == 0:
        tokens = int(latest.stat().st_size / 3.5)
    if tokens > 20000:
        penalty = int(tokens * MULTIPLIER)
        pct = round((penalty / 2500000) * 100, 1)
        print("\n\033[1;33m⚠️  [Cold-Resume Guard] Prompt Cache Expired\033[0m")
        print(f"• Idle Time: {int(idle // 60)} minutes (TTL is 60m)")
        print(f"• Context: ~{tokens:,} tokens")
        print(f"• Cold Reload Hit: ~{penalty:,} weighted tokens (~{pct}% of 5-hour quota)")
        print("\033[1mTip:\033[0m Type \033[1;36m/clear\033[0m for fresh handoff (~3k tokens), or press Return to proceed.\n")

if __name__ == "__main__":
    inspect()
EOF

chmod +x "$GUARD_FILE"

# Ensure ~/.local/bin is on PATH
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    echo "export PATH=\"\$HOME/.local/bin:\$PATH\"" >> "$HOME/.zshrc"
    echo "==> Added ~/.local/bin to ~/.zshrc"
fi

echo "==> Successfully installed cold-resume-guard to $GUARD_FILE"
echo "==> Run: cold-resume-guard"
