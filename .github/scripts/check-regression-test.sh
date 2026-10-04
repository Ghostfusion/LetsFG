#!/usr/bin/env bash
#
# Regression-test merge gate (trvl study P1.1).
#
# A PR that reads as a fix — its title, or any of its commit subjects, says
# `fix`, `hotfix` or `bugfix` — must change a test alongside the source it
# changes, in every package it touches. A fix that ships without one is the
# change least likely to stay fixed.
#
# Bypass: the `skip-regression-test` PR label, applied in the job's `if:`.
#
# Inputs. CI passes them as environment (PR_TITLE, BASE_REF, from the workflow's
# `env:` block); the same values can be given positionally for local
# reproduction, which needs no exported environment:
#
#   bash .github/scripts/check-regression-test.sh "fix: something" <base-sha> <head-sha>
#
#   $1 PR_TITLE   $2 base SHA   $3 head SHA   (all optional)

set -euo pipefail

FIX_PATTERN='^(fix|hotfix|bugfix)(\(|:| )'
PACKAGES=(sdk/js sdk/mcp)

pr_title=${1:-}
if [ -z "$pr_title" ]; then
  pr_title=${PR_TITLE:-}
fi

BASE=${2:-}
if [ -z "$BASE" ]; then
  BASE=${BASE_SHA:-}
fi
if [ -z "$BASE" ]; then
  BASE="origin/${BASE_REF:-}"
fi
if [ "$BASE" = "origin/" ]; then
  echo "::error::no diff base — set BASE_REF, or pass a base SHA as \$2"
  exit 1
fi

HEAD_REF=${3:-}
if [ -z "$HEAD_REF" ]; then
  HEAD_REF=${HEAD_SHA:-}
fi
if [ -z "$HEAD_REF" ]; then
  HEAD_REF=HEAD
fi

is_fix() {
  [[ "$1" =~ $FIX_PATTERN ]]
}

triggered=false
if is_fix "$pr_title"; then
  triggered=true
  echo "PR title reads as a fix: ${pr_title}"
fi
while IFS= read -r subject; do
  if [ -n "$subject" ] && is_fix "$subject"; then
    triggered=true
    echo "commit reads as a fix: ${subject}"
  fi
done < <(git log --format=%s "${BASE}..${HEAD_REF}" 2>/dev/null || true)

if [ "$triggered" != true ]; then
  echo "Not a fix PR — no regression test required."
  exit 0
fi

CHANGED=$(git diff --name-only "${BASE}...${HEAD_REF}")

failed=false
for pkg in "${PACKAGES[@]}"; do
  source_changes=$(grep -E "^${pkg}/src/.*\.(ts|tsx)$" <<<"$CHANGED" \
    | grep -vE '\.test\.(ts|tsx)$' | grep -vE '\.d\.ts$' || true)
  [ -z "$source_changes" ] && continue

  test_changes=$(grep -E "^${pkg}/src/.*\.test\.(ts|tsx)$" <<<"$CHANGED" || true)
  if [ -z "$test_changes" ]; then
    echo "::error::${pkg}/src changed with no test change — a fix PR must carry a regression test."
    while IFS= read -r file; do
      echo "    source: $file"
    done <<<"$source_changes"
    failed=true
  else
    echo "OK: ${pkg}/src carries a test change."
  fi
done

if [ "$failed" = true ]; then
  echo ""
  echo "Add or extend a *.test.ts beside the fix, or apply the 'skip-regression-test' label."
  exit 1
fi

echo "✓ Every package that changed source also changed a test."
