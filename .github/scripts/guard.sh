#!/usr/bin/env bash
#
# Repo tripwires for the public mdhumanviewer repo. Pure `git grep` over TRACKED
# files — no dependencies beyond git + openssl (both present on every CI runner).
# Fast; runs before lint/test.
#
# Three checks:
#   1. No local absolute paths (generic hygiene; also catches private-project paths).
#   2. No residual "v2" rename tokens (the public name is mdhumanviewer / mdhv).
#   3. No private-corpus identifiers. Those needles are base64-OBSCURED below — not
#      encrypted, just so this PUBLIC file is not itself a plaintext, searchable
#      index of the identifiers it guards against — and decoded only at runtime.
#
# This script excludes ITSELF from every scan, since it necessarily references the
# shapes it looks for.
set -euo pipefail

self=':(exclude).github/scripts/guard.sh'
nopng=':(exclude)*.png'
status=0

# 1) Local absolute paths must never be committed.
if git grep -nIE '/(Users|home)/[A-Za-z0-9._-]+/' -- . "$nopng" "$self"; then
  echo '::error::Local absolute path committed (e.g. /Users/<you>/...). Use a relative path.'
  status=1
fi

# 2) Residual v2 rename tokens (case-insensitive also catches mdHumanViewer2).
if git grep -nIiE 'mdhumanviewer2|mdhv2' -- . "$nopng" "$self"; then
  echo '::error::RENAME: residual v2 token found (public name is mdhumanviewer / mdhv).'
  status=1
fi

# 3) Private-corpus tripwire. Needles are matched as EXACT fixed strings, so
#    intentional generic non-Latin test data (Привет / Введение / раздел / п.)
#    is NOT flagged — only the specific private phrases are.
needles_b64='eC1hbGdvcml0aG0KcmFua2luZ19zY29yZXIKcGhvZW5peC1yZWFkbWUKc3BhbS1mZWVkCtC90LXQtNC10LvRjyAyCtCn0LXQs9C+INC40LfQsdC10LPQsNGC0YwK'
while IFS= read -r needle; do
  [ -n "$needle" ] || continue
  if git grep -nIF -e "$needle" -- . "$nopng" "$self"; then
    echo '::error::PRIVACY LEAK: forbidden private-corpus token found above.'
    status=1
  fi
done < <(printf '%s' "$needles_b64" | openssl base64 -d)

if [ "$status" -eq 0 ]; then
  echo 'guard: clean — no forbidden tokens in tracked files.'
fi
exit "$status"
