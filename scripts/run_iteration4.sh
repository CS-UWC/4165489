#!/usr/bin/env bash
# =====================================================================
# ITERATION 4 - headless repeat sweep for the Raspberry Pi 5
#
# Bash port of run_iteration4.ps1. Same protocol: the baseline stage
# must pass check_baselines.py BEFORE any attack data is collected,
# and every condition is repeated so the paper can report mean +/- std.
#
# Usage (from the repo directory):
#   ./run_iteration4.sh                  # 3 repeats, sequential
#   REPEATS=10 ./run_iteration4.sh
#   MODE=all ./run_iteration4.sh         # simultaneous instead
#   MAX_FLAG=8 ./run_iteration4.sh
#   DRY_RUN=1 ./run_iteration4.sh         # print the plan and exit
#
# STOP A RUNNING SWEEP:  touch stop_sweep   (checked between stages)
#
# Broker address comes from $MQTT_BROKER (default localhost).
# Env knobs, all optional:
#   MQTT_BROKER  REPEATS  MAX_FLAG  MODE  LEARN  DURATION
#   BASELINE_DUR  RESET_SLEEP  OUT_ROOT  MIN_FREE_MB
# =====================================================================
set -uo pipefail

REPEATS="${REPEATS:-3}"
MAX_FLAG="${MAX_FLAG:-5.0}"
MODE="${MODE:-each}"
LEARN="${LEARN:-120}"
DURATION="${DURATION:-240}"
BASELINE_DUR="${BASELINE_DUR:-300}"
RESET_SLEEP="${RESET_SLEEP:-30}"
OUT_ROOT="${OUT_ROOT:-runs}"
MIN_FREE_MB="${MIN_FREE_MB:-2000}"
DRY_RUN="${DRY_RUN:-0}"
# Restrict the sweep to specific nodes (comma-separated). Empty = all discovered
# nodes. Set this to exclude a node whose baseline is unstable for measurement
# reasons, e.g. NODES=node1,node2,node3 while node4's ambient-light drift is
# investigated separately.
NODES="${NODES:-}"
# Only used in the "no CSVs" diagnostic message; the SSID itself is compiled
# into each Node*.ino, so this is a reminder, not something the sweep reads.
SSID_EXPECTED="${WIFI_SSID:-YOUR_WIFI_SSID}"

# Resolve the repo directory from this script's own location, so the sweep
# works no matter which directory it is launched from.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE" || exit 1

RUNNER="attack_each.py"
[ "$MODE" = "all" ] && RUNNER="attack_all.py"

ATTACKS="replay timing fdi flood mimic"
STOP_FILE="$HERE/stop_sweep"
STAMP="$(date +%Y%m%d_%H%M%S)"
LOG="$HERE/iter4_${STAMP}.log"

log() { printf '%s [iter4] %s\n' "$(date '+%H:%M:%S')" "$*" | tee -a "$LOG"; }

# Non-zero exit if the stop file exists. Checked between stages so an
# unattended sweep can be halted without killing the SSH session.
should_stop() {
    if [ -f "$STOP_FILE" ]; then
        log "STOP FILE FOUND at $STOP_FILE - halting after this stage."
        rm -f "$STOP_FILE"
        return 0
    fi
    return 1
}

free_mb() { df -Pm . | awk 'NR==2 {print $4}'; }

if [ "$DRY_RUN" = "1" ]; then
    log "DRY RUN - no hardware commands will run."
    log "  runner      : $RUNNER"
    log "  repeats     : $REPEATS"
    log "  gate        : every baseline phase must flag <= ${MAX_FLAG}%"
    log "  learn/attack: ${LEARN}s / ${DURATION}s   baseline capture ${BASELINE_DUR}s"
    log "  output root : $OUT_ROOT/iter4_r<N>"
    log "  broker      : ${MQTT_BROKER:-localhost (default)}"
    log "  python      : $(command -v python3 || echo 'NOT FOUND')"
    exit 0
fi

command -v python3 >/dev/null 2>&1 || command -v python >/dev/null 2>&1 || {
    log "FATAL: no python on PATH."; exit 1; }
PY="$(command -v python3 || command -v python)"

# paho-mqtt is the only hard dependency. Fail fast with a clear message
# rather than crashing on the first connect 10 minutes into a repeat.
if ! "$PY" -c "import paho.mqtt.client" >/dev/null 2>&1; then
    log "FATAL: paho-mqtt not installed. Run: pip install paho-mqtt"
    exit 1
fi

log "============================================================"
log "ITERATION 4 SWEEP"
log "  runner=$RUNNER repeats=$REPEATS gate=${MAX_FLAG}% mode=$MODE"
log "  learn=${LEARN}s attack=${DURATION}s baseline=${BASELINE_DUR}s"
log "  broker=${MQTT_BROKER:-localhost (default)}"
log "  log=$LOG"
log "============================================================"

PASSED=0
FAILED=0
FAILED_REPEATS=""

for r in $(seq 1 "$REPEATS"); do
    if should_stop; then break; fi

    OUT_DIR="$OUT_ROOT/iter4_r$r"

    # Never write into a directory that already holds CSVs. check_baselines.py
    # scans every run_*.csv it finds, so a leftover dir from an earlier sweep
    # would mix that sweep's baselines into this gate and could mask a real
    # failure (or pass on the old, healthier data).
    if [ -d "$OUT_DIR" ] && [ -n "$(ls -A "$OUT_DIR" 2>/dev/null)" ]; then
        log "ABORT: $OUT_DIR already exists and is not empty."
        log "Rename it or set OUT_ROOT to a fresh directory, then restart."
        log "  (continuing would let two sweeps share one baseline gate)"
        exit 1
    fi
    mkdir -p "$OUT_DIR"

    log ""
    log "############################################################"
    log "REPEAT $r of $REPEATS  ->  $OUT_DIR"
    log "############################################################"

    free=$(free_mb)
    if [ "$free" -lt "$MIN_FREE_MB" ]; then
        log "ABORT: only ${free}MB free on the card (need ${MIN_FREE_MB}MB)."
        log "Pull runs/ off the Pi and clear space, then restart."
        FAILED=$((FAILED + 1))
        FAILED_REPEATS="$FAILED_REPEATS $r"
        break
    fi

    # ---- STAGE 1: baseline + GATE ----------------------------------
    log "[1/6] Baseline (learn ${LEARN}s, capture ${BASELINE_DUR}s)"
    "$PY" "$RUNNER" --attack none --learn "$LEARN" --duration "$BASELINE_DUR" \
        --out "$OUT_DIR" ${NODES:+--nodes "$NODES"} 2>&1 | tee -a "$LOG"
    [ "${PIPESTATUS[0]}" -ne 0 ] && log "WARNING: baseline run exited non-zero"

    # check_baselines.py returns 0 when it finds no CSVs at all, so an
    # undetected node set (broker up, ESP32s asleep, wrong address) would
    # sail through the gate with zero data. Insist on real output first.
    n_csv=$(ls -1 "$OUT_DIR"/run_*.csv 2>/dev/null | wc -l)
    if [ "$n_csv" -eq 0 ]; then
        log "      NO CSVs WRITTEN - no nodes were detected."
        log "      Check: ESP32s powered, on $SSID_EXPECTED, broker reachable,"
        log "      and MQTT_BROKER correct. Treating as a FAILED repeat."
        FAILED=$((FAILED + 1))
        FAILED_REPEATS="$FAILED_REPEATS $r"
        continue
    fi
    log "      $n_csv baseline CSV(s) written."

    if should_stop; then break; fi

    log "      Gating: every baseline phase must flag <= ${MAX_FLAG}%"
    "$PY" check_baselines.py --dir "$OUT_DIR" --max-flag "$MAX_FLAG" 2>&1 | tee -a "$LOG"
    gate=${PIPESTATUS[0]}

    if [ "$gate" -ne 0 ]; then
        log "      GATE FAILED on repeat $r. Attack data for this repeat is NOT trustworthy."
        FAILED=$((FAILED + 1))
        FAILED_REPEATS="$FAILED_REPEATS $r"
        log "      Skipping attacks for this repeat (saves ~33 min)."
        continue
    fi
    log "      GATE PASSED."
    PASSED=$((PASSED + 1))

    # ---- STAGE 2: attacks -------------------------------------------
    step=1
    for a in $ATTACKS; do
        if should_stop; then break; fi
        step=$((step + 1))
        log "[$step/6] Attack: $a (learn ${LEARN}s, duration ${DURATION}s)"
        "$PY" "$RUNNER" --attack "$a" --learn "$LEARN" --duration "$DURATION" \
            --out "$OUT_DIR" ${NODES:+--nodes "$NODES"} 2>&1 | tee -a "$LOG"
        rc=${PIPESTATUS[0]}
        [ "$rc" -ne 0 ] && log "      WARNING: $a exited with code $rc"
        log "      Waiting ${RESET_SLEEP}s for BTI to reset"
        sleep "$RESET_SLEEP"
    done
done

# ---- SUMMARY -------------------------------------------------------
log ""
log "============================================================"
log "SWEEP SUMMARY"
log "  passed: $PASSED    failed: $FAILED"
[ -n "$FAILED_REPEATS" ] && log "  failed repeats:$FAILED_REPEATS"
log "  data: $OUT_ROOT/iter4_r*"
log "  log : $LOG"
log "============================================================"

if [ "$FAILED" -gt 0 ]; then
    log "RESULT: $FAILED repeat(s) failed the baseline gate. Do NOT report those numbers."
    exit 1
fi
log "RESULT: all completed repeats passed the gate."
exit 0
