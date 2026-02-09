#!/bin/bash
#
# Install git hooks from project hooks/ directory
#

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GIT_HOOKS_DIR="$(git rev-parse --git-dir)/hooks"

echo "Installing git hooks..."

for hook in "$SCRIPT_DIR"/*; do
    hook_name=$(basename "$hook")

    # Skip this install script
    if [ "$hook_name" = "install.sh" ]; then
        continue
    fi

    # Create symlink
    ln -sf "$hook" "$GIT_HOOKS_DIR/$hook_name"
    echo "  Installed: $hook_name"
done

echo "Done."