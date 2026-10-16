#!/usr/bin/env bash
# Dev-window research runs only (window ends 2026-09-28T00:00:00Z exclusive). Run from clean worktree at a53a649.
set -u
D=${OUT:?set OUT dir}
REPO=${REPO:?set REPO checkout}
C15="$LOCALAPPDATA\ZackBot\candles_15m_research"
C4="$LOCALAPPDATA\ZackBot\candles_4h_research"
W=2024-10-14T00:00:00Z/2026-09-28T00:00:00Z
one() {  # tier key side view
  local tier=$1 key=$2 side=$3 view=$4 extra=""
  [ "$view" = fixr ] && extra="--start 1e9 --risk 1e-6"
  local tag=${tier}_${key}_${side}
  mkdir -p $D/$view
  cd $REPO && py -3.14 tools/research/run_breadth_candidate.py --cache "$C15" --regime-cache "$C4" --window $W \
     --tier $tier --key $key --side $side --mgmt defaults $extra --out "$D/$view/$tag.json" \
     > "$D/logs/${view}_$tag.log" 2>&1; echo "rc=$? $view $tag" >> "$D/logs/${view}_$tag.log"
}
export -f one; export D REPO C15 C4 W
for tier in CORE-10 CORE-20; do
  for k in range_scalp failed_breakout_scalp momentum_burst_scalp quick_bank_runner; do for s in long short; do for v in acct fixr; do
    echo "$tier $k $s $v"; done; done; done | xargs -P 4 -L 1 bash -c 'one "$@"' _
done
