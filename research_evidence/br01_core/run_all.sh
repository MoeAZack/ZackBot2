#!/usr/bin/env bash
# BR-01 dev-window research runs only (window ends 2026-09-28T00:00:00Z exclusive). Clean worktree at c27e9cc (PR #126 head).
# Fixed-R view (--start 1e9 --risk 1e-6), mgmt defaults, frozen runner via br01_bt.py (adds only mgmt['br01']).
set -u
D=${OUT:?set OUT dir}
REPO=/c/Dev/ZackBot2_brbt
C15="$LOCALAPPDATA\ZackBot\candles_15m_research"
C4="$LOCALAPPDATA\ZackBot\candles_4h_research"
W=2024-10-14T00:00:00Z/2026-09-28T00:00:00Z
one() {  # tier key side variant(control = none)
  local tier=$1 key=$2 side=$3 var=$4 vflag=""
  [ "$var" != control ] && vflag="--variant $var"
  local tag=${tier}_${key}_${side}_${var}
  cd $REPO && ZB_REPO=$REPO py -3.14 $D/br01_bt.py $vflag --cache "$C15" --regime-cache "$C4" --window $W \
     --tier $tier --key $key --side $side --mgmt defaults --start 1e9 --risk 1e-6 --out "$D/fixr/$tag.json" \
     > "$D/logs/$tag.log" 2>&1; echo "rc=$? $tag" >> "$D/logs/$tag.log"
}
export -f one; export D REPO C15 C4 W
mkdir -p $D/fixr $D/logs
cells() {
  for k in range_scalp failed_breakout_scalp momentum_burst_scalp quick_bank_runner; do
    echo "$k control"; echo "$k bounded_recovery"; echo "$k range_ladder"; done
  for k in range_scalp failed_breakout_scalp; do echo "$k reconfirm_recovery"; done
  for k in momentum_burst_scalp failed_breakout_scalp; do echo "$k anti_martingale"; done
  echo "failed_breakout_scalp failed_breakout_staged"
}
for tier in CORE-10 CORE-20; do      # CORE-10 first, then CORE-20; no tuning between tiers
  cells | while read k v; do for s in long short; do echo "$tier $k $s $v"; done; done | xargs -P 4 -L 1 bash -c 'one "$@"' _
done
