#!/usr/bin/env bash
# Integration selftest for dream.sh: the time/evidence gates and the lock.
# Runs dream.sh against a throwaway store with a stub `claude`, so no model
# is called and no real memory is touched. Exit 0 only if every case holds.
set -u
SRC="$(cd "$(dirname "$0")" && pwd)"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
D="$T/mem/debrief"; L="$D/.system/hook.log"
mkdir -p "$T/home/.local/bin" "$D/.system/prompts" "$D/dreams"
cp "$SRC/dream.sh" "$D/.system/"; cp "$SRC/prompts/auto-dream.md" "$D/.system/prompts/"
printf '#!/bin/sh\necho STUB-CLAUDE-RAN\n' >"$T/home/.local/bin/claude"; chmod +x "$T/home/.local/bin/claude"
fails=0
run() { : >"$L"; HOME="$T/home" bash "$D/.system/dream.sh" "$@"; grep -q STUB-CLAUDE-RAN "$L" && echo ran || echo skipped; }
expect() { local got; got="$(run "${@:3}")"; if [ "$got" = "$2" ]; then echo "ok $1"; else echo "FAIL $1: got $got"; fails=$((fails + 1)); fi; }

printf 'x\n\nlast dream: never\n' >"$D/OBSERVATIONS.md"
expect "never dreamed: runs" ran
printf 'x\n\nlast dream: 2026-10-20\n' >"$D/OBSERVATIONS.md"
touch "$D/2026-10-20-same-day.md" "$D/2026-10-21-a.md" "$D/2026-10-22-b.md"
expect "2 new dailies (same-day one not counted): skips" skipped
touch "$D/2026-10-23-c.md"
expect "3 new dailies: runs" ran
touch "$D/dreams/2026-10-20.md"
expect "last audit under 24 h old: skips" skipped
expect "--force skips the gates" ran --force
mkdir "$D/.system/.dream.lock"; echo $$ >"$D/.system/.dream.lock/pid"
expect "live lock: skips even with --force" skipped --force
echo 999999 >"$D/.system/.dream.lock/pid"
expect "stale lock: taken over" ran --force
[ ! -d "$D/.system/.dream.lock" ] && echo "ok lock released on exit" || { echo "FAIL lock left behind"; fails=$((fails + 1)); }
exit "$fails"
