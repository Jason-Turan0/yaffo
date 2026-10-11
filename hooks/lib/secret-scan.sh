#!/bin/bash
#
# Fallback secret scan, run by hooks/pre-commit when gitleaks is not installed.
# A smaller pattern list than gitleaks; install gitleaks for full coverage.
#
# Scans the staged version of each added or changed file (not the working tree),
# so what is checked is exactly what gets committed. A line that must keep a
# match (a test fixture, say) can be allowed with a trailing "secret-scan: allow".
#

# Provider formats: checked in every text file, Markdown included, since these
# are what GitHub's push protection rejects.
PROVIDER_PATTERNS=(
    "sk-ant-[a-zA-Z0-9_-]{20,}"                 # Anthropic API key
    "sk-(proj-)?[a-zA-Z0-9_-]{40,}"             # OpenAI API key
    "GOCSPX-[a-zA-Z0-9_-]{20,}"                 # Google OAuth client secret
    "AIza[0-9A-Za-z_-]{35}"                     # Google API key
    "ya29\.[0-9A-Za-z_-]{20,}"                  # Google OAuth access token
    "AKIA[0-9A-Z]{16}"                          # AWS access key id
    "gh[pousr]_[A-Za-z0-9]{36,}"                # GitHub token
    "github_pat_[A-Za-z0-9_]{40,}"              # GitHub fine-grained token
    "xox[abposr]-[A-Za-z0-9-]{10,}"             # Slack token
    "sk_live_[A-Za-z0-9]{20,}"                  # Stripe live key
    "-----BEGIN ([A-Z]+ )?PRIVATE KEY-----"     # PEM private key
)

# Generic "name = literal" assignments: skipped in Markdown, where docs show
# placeholder values.
GENERIC_PATTERNS=(
    "(api|secret)[_-]?key[[:space:]]*[:=][[:space:]]*['\"]?[a-zA-Z0-9_-]{20,}"
    "client[_-]?secret[[:space:]]*[:=][[:space:]]*['\"][a-zA-Z0-9_-]{16,}"
    "(password|passwd|token)[[:space:]]*[:=][[:space:]]*['\"][^'\"[:space:]]{16,}['\"]"
)

# Files never scanned.
SKIP_FILES=(
    ".env.example"
    "yaffo_ui_tests/.env.example"
    "hooks/lib/secret-scan.sh"
)

FOUND_SECRETS=0

report() {  # file, pattern, matching lines
    echo "ERROR: Potential secret found in $1"
    echo "  Pattern matched: $2"
    echo "$3" | head -3 | sed 's/^/    /'
    echo ""
    FOUND_SECRETS=1
}

while IFS= read -r -d '' FILE; do
    SKIP=0
    for SKIP_PATTERN in "${SKIP_FILES[@]}"; do
        if [[ "$FILE" == $SKIP_PATTERN ]]; then
            SKIP=1
            break
        fi
    done
    [ $SKIP -eq 1 ] && continue

    CONTENT=$(git show ":$FILE" 2>/dev/null) || continue
    # Skip binary (and empty) files: grep -I never matches inside binary data.
    if ! git show ":$FILE" | LC_ALL=C grep -qI ''; then
        continue
    fi
    CONTENT=$(echo "$CONTENT" | grep -v "secret-scan: allow")

    for PATTERN in "${PROVIDER_PATTERNS[@]}"; do
        MATCHES=$(echo "$CONTENT" | grep -nE -- "$PATTERN")
        [ -n "$MATCHES" ] && report "$FILE" "$PATTERN" "$MATCHES"
    done

    [[ "$FILE" == *.md ]] && continue
    for PATTERN in "${GENERIC_PATTERNS[@]}"; do
        MATCHES=$(echo "$CONTENT" | grep -niE -- "$PATTERN")
        [ -n "$MATCHES" ] && report "$FILE" "$PATTERN" "$MATCHES"
    done
done < <(git diff --cached --name-only -z --diff-filter=ACM)

if [ $FOUND_SECRETS -eq 1 ]; then
    echo "=========================================="
    echo "COMMIT REJECTED: Potential secrets detected"
    echo "=========================================="
    echo ""
    echo "Keep the value out of the repository (an environment variable, the OS"
    echo "keychain, or a build-time stamp). If this is a false positive, you can:"
    echo "  1. End the line with a 'secret-scan: allow' comment"
    echo "  2. Add the file to SKIP_FILES in hooks/lib/secret-scan.sh"
    echo "  3. Use 'git commit --no-verify' to bypass (not recommended)"
    echo ""
    exit 1
fi

exit 0
