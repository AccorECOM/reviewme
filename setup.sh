#!/usr/bin/env bash
set -e

echo "=== ReviewMe Setup ==="
echo ""

# --- Prerequisites ---
errors=0

printf "Checking Python... "
# La version compte : `command -v python3` renvoie souvent le Python du système, trop
# ancien pour ce projet. uv installera de toute façon la bonne version dans le venv.
if command -v python3 &>/dev/null; then
    py_version=$(python3 --version 2>&1 | cut -d' ' -f2)
    py_major=$(echo "$py_version" | cut -d. -f1)
    py_minor=$(echo "$py_version" | cut -d. -f2)
    if [ "$py_major" -gt 3 ] || { [ "$py_major" -eq 3 ] && [ "$py_minor" -ge 13 ]; }; then
        echo "OK ($py_version)"
    else
        echo "$py_version — trop ancien, 3.13+ requis (uv l'installera dans le venv)"
    fi
else
    echo "MISSING - Install Python 3.13+"
    errors=1
fi

printf "Checking uv... "
if command -v uv &>/dev/null; then
    echo "OK"
else
    echo "MISSING"
    read -p "Install uv now? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        curl -LsSf https://astral.sh/uv/install.sh | sh
    else
        errors=1
    fi
fi

printf "Checking claude CLI... "
if command -v claude &>/dev/null; then
    echo "OK"
else
    echo "MISSING"
    read -p "Install claude CLI now? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        npm install -g @anthropic-ai/claude-code
    else
        errors=1
    fi
fi

if [ $errors -ne 0 ]; then
    echo ""
    echo "Fix the issues above and re-run ./setup.sh"
    exit 1
fi

echo ""
echo "--- Configuration ---"
echo ""

# --- .env setup ---
if [ -f .env ]; then
    read -p ".env already exists. Overwrite? (y/n) " -n 1 -r
    echo
    [[ ! $REPLY =~ ^[Yy]$ ]] && skip_env=1
fi

if [ -z "$skip_env" ]; then
    echo "Tip: Create a classic PAT at https://github.com/settings/tokens"
    echo "     Scope needed: repo"
    read -p "GitHub PAT: " gh_token

    echo ""
    echo "Example: facebook/react, my-org/my-app"
    read -p "GitHub repo (owner/repo): " gh_repo

    echo ""
    echo "Example: /Users/john/dev/my-app (absolute path to your local clone)"
    read -p "Path to local clone: " repo_path

    echo ""
    echo "Example: review-me, needs-review, claude-review"
    read -p "Label to watch [review-me]: " review_label
    review_label=${review_label:-review-me}

    echo ""
    echo "Optional. Each reviewer already carries its own persona (system.md),"
    echo "which is what you want in almost every case. Setting an agent here"
    echo "overrides them all with a single Claude Code agent, for every reviewer."
    echo "Example: pr-tech-lead-reviewer, installed in Claude Code with"
    echo "  /plugin marketplace add joey-barbier/ClaudeCode-Plugin"
    read -p "Claude agent name [none]: " claude_agent

    echo ""
    echo "Safety limit per review. A typical review costs ~$0.10-0.50"
    read -p "Max budget per review in USD [1.00]: " max_budget
    max_budget=${max_budget:-1.00}

    echo ""
    echo "How often to check for new PRs. 300 = every 5 min"
    read -p "Poll interval in seconds [300]: " poll_interval
    poll_interval=${poll_interval:-300}

    cat > .env <<EOF
GITHUB_TOKEN=${gh_token}
GITHUB_REPO=${gh_repo}
REPO_PATH=${repo_path}
REVIEW_LABEL=${review_label}
CLAUDE_AGENT=${claude_agent}
MAX_BUDGET_USD=${max_budget}
POLL_INTERVAL=${poll_interval}

# Reviewers specialises : creer une instance puis decommenter.
#   reviewme init-project <repo> --reviewers tech,i18n
# REVIEWME_CONFIG_HOME=
# PROJECT=
EOF

    echo ""
    echo ".env created"
fi

# --- Install deps ---
echo ""
echo "Installing dependencies..."
# Sans --extra, uv retire les extras deja installes. On preserve github-app s'il l'etait.
if [ -f .venv/bin/python ] && .venv/bin/python -c "import jwt" 2>/dev/null; then
    uv sync --extra github-app
else
    uv sync
    echo "  (auth GitHub App : uv sync --extra github-app)"
fi

# --- Test connection ---
echo ""
printf "Testing GitHub API access... "
status=$(uv run python -c "
from src.config import load_config
from src.github_client import GitHubClient
cfg = load_config()
gh = GitHubClient(cfg)
r = gh.check_rate_limit()
print(f'OK ({r[\"core\"][\"remaining\"]}/{r[\"core\"][\"limit\"]} requests)')
gh.close()
" 2>&1) && echo "$status" || echo "FAILED - check your token and repo"

echo ""
echo "=== Setup complete ==="
echo ""
echo "  Start the bot:       uv run reviewme"
echo "  Dashboard:           http://127.0.0.1:8420"
echo ""
