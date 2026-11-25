#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="$HOME/.local/bin"
GUARD_FILE="$INSTALL_DIR/cold-resume-guard"
RAW_URL="https://raw.githubusercontent.com/l2cache-app/claude-cold-resume-guard/main/guard.py"

echo "==> Installing claude-cold-resume-guard..."

mkdir -p "$INSTALL_DIR"

# Download the latest guard script directly from GitHub
if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$RAW_URL" -o "$GUARD_FILE"
elif command -v wget >/dev/null 2>&1; then
    wget -qO "$GUARD_FILE" "$RAW_URL"
else
    echo "Error: curl or wget is required to install."
    exit 1
fi

chmod +x "$GUARD_FILE"

# Create helper symlinks
ln -sf "$GUARD_FILE" "$INSTALL_DIR/claude-guard"
ln -sf "$GUARD_FILE" "$INSTALL_DIR/codex-guard"

# Ensure ~/.local/bin is on PATH
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    SHELL_PROFILE="$HOME/.zshrc"
    if [[ ! -f "$SHELL_PROFILE" && -f "$HOME/.bashrc" ]]; then
        SHELL_PROFILE="$HOME/.bashrc"
    fi
    echo "export PATH=\"\$HOME/.local/bin:\$PATH\"" >> "$SHELL_PROFILE"
    echo "==> Added ~/.local/bin to $SHELL_PROFILE"
fi

echo "==> Successfully installed to $GUARD_FILE"
echo "==> Helper binaries created:"
echo "    • cold-resume-guard"
echo "    • claude-guard"
echo "    • codex-guard"
echo ""
echo "Run: cold-resume-guard --help"
