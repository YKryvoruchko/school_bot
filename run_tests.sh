#!/usr/bin/env bash
# run_tests.sh — install test deps and run the full suite
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Installing test dependencies ==="
# Use the same python that will run the tests to ensure correct pip/env
PYTHON="${PYTHON:-python}"
"$PYTHON" -m pip install \
  pytest pytest-asyncio pytest-timeout \
  aiosqlite sqlalchemy \
  passlib "bcrypt==4.0.1" \
  sqladmin starlette wtforms markupsafe python-multipart \
  pillow \
  aiogram \
  --quiet

echo ""
echo "=== Running tests ==="
"$PYTHON" -m pytest tests/ -v --tb=short "$@"