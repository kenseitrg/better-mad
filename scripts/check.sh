#!/usr/bin/env bash
# Local checks: lint, format check, types, tests. Run before every commit.
set -euo pipefail
cd "$(dirname "$0")/.."

uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pytest -q
