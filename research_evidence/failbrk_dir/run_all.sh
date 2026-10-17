#!/usr/bin/env bash
# failed_breakout_scalp direction-vs-regime comparison (Codex spec, ZackBot2 #13 comment 6228353544).
# Dev window only (ends 2026-09-28T00:00:00Z exclusive). The runner refuses a dirty tree, so packets are written to $OUT
# OUTSIDE the checkout and copied into research_evidence/failbrk_dir/ afterwards. CORE-10 first, then CORE-20; 4 in parallel.
#   OUT=<scratch dir> REPO=<clean checkout at the runner commit> bash run_all.sh
set -u
D=${OUT:?set OUT dir}
REPO=${REPO:?set REPO checkout}
C15="$LOCALAPPDATA\ZackBot\candles_15m_research"
C4="$LOCALAPPDATA\ZackBot\candles_4h_research"
W=2024-10-14T00:00:00Z/2026-09-28T00:00:00Z
mkdir -p "$D/logs"
one() {  # tier side view arm
  local tier=$1 side=$2 view=$3 arm=$4 extra=""
  [ "$view" = fixr ] && extra="--start 1e9 --risk 1e-6"
  local tag=${tier}_${side}_${arm}
  mkdir -p "$D/$view"
  cd "$REPO" && py -3.14 tools/research/run_breadth_candidate.py --cache "$C15" --regime-cache "$C4" --window $W \
     --tier $tier --key failed_breakout_scalp --side $side --mgmt defaults --regime-refuse $arm $extra \
     --out "$D/$view/$tag.json" > "$D/logs/${view}_$tag.log" 2>&1; echo "rc=$? $view $tag" >> "$D/logs/${view}_$tag.log"
}
export -f one; export D REPO C15 C4 W
for tier in CORE-10 CORE-20; do
  for s in long short; do for v in acct fixr; do for a in none counter; do
    echo "$tier $s $v $a"; done; done; done | xargs -P 4 -L 1 bash -c 'one "$@"' _
done
