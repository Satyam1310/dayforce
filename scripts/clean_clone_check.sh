#!/usr/bin/env bash
# Clone the repo fresh into a temp folder and follow the README steps that need no Docker:
# install, lint, backend tests, golden-set evaluation, frontend lint/format/test/build.
# Reports PASS/FAIL per step and exits non-zero if any step failed.
#
#   scripts/clean_clone_check.sh                  # clones origin, branch main
#   BRANCH=feature/x scripts/clean_clone_check.sh
#   REPO=/path/or/url scripts/clean_clone_check.sh
#   KEEP=1 scripts/clean_clone_check.sh           # keep the temp folder to look around
#
# Needs: git, Node 20+, and either uv or python3.11. Never reads or needs backend/.env (a fresh
# clone has none), never calls a real model (LLM_PROVIDER=mock, no key), and needs no database:
# the database tests skip and the system tests are left out (they need Docker or DATABASE_URL).
set -u

REPO="${REPO:-$(git -C "$(dirname "$0")/.." config --get remote.origin.url)}"
BRANCH="${BRANCH:-main}"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/dayforce-clean-clone.XXXXXX")"
RESULTS=()
FAILED=0

cleanup() { [ "${KEEP:-0}" = "1" ] && echo "kept: $WORK" || rm -rf "$WORK"; }
trap cleanup EXIT

step() {  # step "name" command...   (runs in the clone, prints only a verdict and the tail on failure)
  local name="$1"; shift
  local log="$WORK/step.log" start=$SECONDS
  if "$@" >"$log" 2>&1; then
    RESULTS+=("PASS  $name ($((SECONDS - start))s)")
    # keep a one-line summary of test runs
    grep -E "passed|Test Files|Tests " "$log" | tail -2 | sed 's/^/      /' >>"$WORK/summary.txt" || true
  else
    FAILED=1
    RESULTS+=("FAIL  $name ($((SECONDS - start))s)")
    echo "---- $name failed; last lines ----"; tail -15 "$log"; echo "----"
  fi
}

echo "cloning $REPO ($BRANCH) into $WORK"
git clone --quiet --branch "$BRANCH" --depth 1 "$REPO" "$WORK/repo" || { echo "clone failed"; exit 2; }
cd "$WORK/repo" || exit 2
echo "commit: $(git rev-parse --short HEAD)"
[ -e backend/.env ] && echo "note: the clone contains backend/.env (it should not)"

# a clean environment for the tests: no key, no database, the mock model
export LLM_PROVIDER=mock LLM_API_KEY= LLM_BASE_URL= LLM_MODEL= DATABASE_URL= RETRIEVER=memory

make_venv() {
  if command -v uv >/dev/null 2>&1; then uv venv --python 3.11 "$WORK/venv" -q && uv pip install -q --python "$WORK/venv/bin/python" -r backend/requirements-dev.txt
  elif command -v python3.11 >/dev/null 2>&1; then python3.11 -m venv "$WORK/venv" && "$WORK/venv/bin/pip" install -q -r backend/requirements-dev.txt
  else echo "need uv or python3.11"; return 1; fi
}
PY="$WORK/venv/bin/python"

step "backend: create venv and install requirements-dev.txt" make_venv
if [ -x "$PY" ]; then
  step "backend: ruff check"                    bash -c "cd backend && '$PY' -m ruff check ."
  step "backend: pytest (no database, no Docker)" bash -c "cd backend && '$PY' -m pytest -q --ignore=tests/system"
  step "backend: golden-set evaluation (mock)"  bash -c "cd backend && '$PY' -m scripts.evaluate && git diff --exit-code ../docs/evaluation_report.md"
  step "backend: openapi.yaml is up to date"    bash -c "cd backend && '$PY' -m scripts.export_openapi && git diff --exit-code ../docs/openapi.yaml"
fi
if command -v npm >/dev/null 2>&1; then
  step "frontend: npm ci"            bash -c "cd frontend && npm ci --no-audit --no-fund"
  step "frontend: lint"              bash -c "cd frontend && npm run lint"
  step "frontend: format:check"      bash -c "cd frontend && npm run format:check"
  step "frontend: tests"             bash -c "cd frontend && npm test"
  step "frontend: build"             bash -c "cd frontend && npm run build"
else
  RESULTS+=("SKIP  frontend steps (npm not found)"); FAILED=1
fi

echo; echo "== clean clone check: $BRANCH @ $(git rev-parse --short HEAD) =="
printf '%s\n' "${RESULTS[@]}"
[ -f "$WORK/summary.txt" ] && { echo "test output:"; cat "$WORK/summary.txt"; }
[ "$FAILED" = 0 ] && echo "RESULT: all steps passed" || echo "RESULT: something failed"
exit "$FAILED"
