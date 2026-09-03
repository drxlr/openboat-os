#!/usr/bin/env bash
# Wire the private-content check into git, so a slip is caught before it is public.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
hook="$root/.git/hooks/pre-commit"
cat > "$hook" <<'HOOK'
#!/usr/bin/env bash
# Installed by scripts/install-hooks.sh — refuses a commit carrying a private fact.
exec python3 "$(git rev-parse --show-toplevel)/scripts/check-private.py" --staged
HOOK
chmod +x "$hook"
echo "pre-commit hook installed at .git/hooks/pre-commit"
echo "test it with: python3 scripts/check-private.py"
