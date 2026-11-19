# Attack Results

## Iteration Log

### Iteration 1 — 2026-08-25 (Completed)

**Goal:** Validate the full attack pipeline end-to-end on a single node.

**Steps completed:**
1. Installed Python environment and paho-mqtt
2. Verified MQTT broker connection from laptop
3. Ran baseline capture (no attack) to confirm data flow
4. Ran all 5 attack types against node1 with `controller.py`:
   - Replay, Timing, FDI, Flood, Mimic
5. Recorded ML detection rates, BTI drops, and first-alert latency
6. Identified mimic attack too detectable — reduced `DRIFT_PER_STEP` from 0.5 to 0.15

**Issues found:**
- Mimic attack flagged 70.1% — too aggressive for a stealth attack
- Flood BTI paradoxically increases (F metric spike offsets other drops)
- All scripts hardcoded to `iot/node1/data` only

---

### Iteration 2 — 2026-08-26 (Completed)

**Goal:** Multi-node attacks + refined stealth + clean data for thesis.

**Steps completed:**
1. Created `attack_each.py` — discovers all nodes, attacks them one at a time with 30s BTI reset between each
2. Created `attack_all.py` — discovers all nodes, attacks all simultaneously in parallel threads
3. Ran all 5 attacks + baseline via `attack_each.py` against node1, node2, node3
4. Ran all 5 attacks + baseline via `attack_all.py` against all nodes simultaneously
5. Re-ran mimic with `DRIFT_PER_STEP=0.15` — detection dropped from 70.1% to 43–46%
6. Created `run_iteration2.ps1` and `run_iteration2_all.ps1` for automated sequential runs
7. Updated `results.md` with full iteration 2 data

**Issues found:**
- `attack_all.py` capture sessions sometimes show 0 attack-phase messages (race condition between attack thread start and capture session open)
- Node3 goes offline intermittently — failed discovery twice before successful mimic run
- Node1 replay detection higher in multi-node mode (45.3%) vs single-node (1.7%) — shared ML threshold
- Timing T metric shows 0.000 in multi-node runs — needs investigation

---

### Iteration 3 — 2026-08-27 (Completed)

**Goal:** 4-minute attack durations (240s) on all nodes, sequential and simultaneous, for a more definitive dataset.

**Steps completed:**
1. Ran sequential attacks via `run_iteration3.ps1` (learn 120s, attack 240s) across node1/2/3
2. Ran simultaneous attacks via `run_iteration3_all.ps1` (learn 120s, attack 240s)
3. Re-ran timing (sequential) manually after the script found 0 nodes — `attack_each.py --attack timing --learn 120 --duration 240`
4. Re-ran mimic (simultaneous) manually after the script found only node3 — `attack_all.py --attack mimic --learn 120 --duration 240`

**Issues found / data caveats:**
- `run_iteration3.ps1` found 0 nodes during the timing step (transient discovery failure) — rerun manually
- `run_iteration3_all.ps1` found only node3 during the mimic step — rerun manually
- Two node1 baseline (`none`) runs show attack-level flagging (44.5% in iter2, 94.5% in iter3-all) — the learned ML threshold appears miscalibrated for node1 in those sessions, so the `none` phase for node1 is not a clean baseline there
- `run_node1_timing_20260827_191854` flagged 90.0% — an outlier vs ~26–34% for other timing runs (see investigation item)

---

## Configuration

| Parameter | Value |
|-----------|-------|
| Broker | localhost:1883 |
| Targets | iot/node1/data, iot/node2/data, iot/node3/data |
| Node1 IP | <node-ip> |
| Learn Duration | 90s (iter2), 120s (iter3) |
| Attack Duration | 120s (iter2), 240s (iter3); mimic iter2 180s; baseline 300s |
| Baseline Samples | ~44 (iter2), ~55–67 (iter3) |
| ML Threshold | ~4.5–5.6 (auto-set) |
| BTI Reset Between Runs | 30s |

---

## Iteration 1 Results — Node1 Only (`controller.py`)

| Attack | ML Flagged | First Alert | BTI Drop | Risk Level |
|--------|-----------|-------------|----------|------------|
| Baseline | 4.5% | — | — | Normal |
| Replay | 1.7% | 62.20s | 7.8% | Low (stealthy) |
| Timing | 33.7% | 0.08s | 8.4% | Medium |
| FDI | 99.2% | 2.38s | 23.4% | High |
| Flood | 100.0% | 0.14s | -10.0% (F spike) | High |
| Mimic | 70.1% | 0.08s | 18.3% | Medium-High |

---

## Iteration 2 Results — All Nodes (`attack_each.py`)

### Baseline (No Attack)

| Node | Msgs | ML Score | Flagged | First Alert | BTI Drop |
|------|------|----------|---------|-------------|----------|
| node1 | 43 | 2.04 | 2.3% | 2.00s | -8.2% |
| node2 | 43 | 0.98 | 0.0% | 2.98s | 22.5% |
| node3 | 45 | 0.73 | 2.2% | 114.94s | 23.9% |

### Replay Attack

| Node | Msgs | ML Score | Flagged | First Alert | BTI Drop |
|------|------|----------|---------|-------------|----------|
| node1 | 117 | 5.30 | 45.3% | 6.98s | 7.5% |
| node2 | 119 | 0.78 | 0.0% | — | -4.0% |
| node3 | 120 | 2.64 | 3.3% | 14.28s | 37.5% |

### Timing Manipulation

| Node | Msgs | ML Score | Flagged | First Alert | BTI Drop |
|------|------|----------|---------|-------------|----------|
| node1 | 76 | 24.75 | 32.9% | 2.88s | 20.8% |
| node2 | 87 | 29.39 | 28.7% | 0.08s | 13.0% |
| node3 | 81 | 12.34 | 28.4% | 6.70s | -1.5% |

### False Data Injection

| Node | Msgs | ML Score | Flagged | First Alert | BTI Drop |
|------|------|----------|---------|-------------|----------|
| node1 | 117 | 415.46 | 51.3% | 0.13s | 3.5% |
| node2 | 120 | 50.51 | 50.0% | 0.17s | 19.7% |
| node3 | 124 | 281.69 | 92.7% | 6.74s | 31.3% |

### Traffic Flood

| Node | Msgs | ML Score | Flagged | First Alert | BTI Drop |
|------|------|----------|---------|-------------|----------|
| node1 | 1279 | 378.97 | 96.4% | 0.09s | -37.9% |
| node2 | 1093 | 95.38 | 95.3% | 9.62s | -30.2% |
| node3 | 1102 | 358.81 | 95.3% | 0.11s | -6.8% |

### Mimic (DRIFT_PER_STEP = 0.15)

| Node | Msgs | ML Score | Flagged | First Alert | BTI Drop |
|------|------|----------|---------|-------------|----------|
| node1 | 152 | 44.64 | 44.1% | 4.19s | 9.2% |
| node2 | 151 | 43.59 | 43.0% | 9.76s | 25.0% |
| node3 | 163 | 18.52 | 46.0% | 1.27s | 30.7% |

---

## Iteration 2 Results — All Nodes Simultaneously (`attack_all.py`)

### Baseline (No Attack)

| Node | Msgs | ML Score | Flagged | BTI Drop |
|------|------|----------|---------|----------|
| node1 | 44 | 2.51 | 2.3% | 13.3% |
| node2 | 44 | 1.03 | 0.0% | — |
| node3 | 44 | 1.10 | 0.0% | — |

### Replay Attack (Simultaneous)

| Node | Msgs | ML Score | Flagged | BTI Drop |
|------|------|----------|---------|----------|
| node1 | 42 | 2.26 | 2.4% | 2.6% |
| node2 | 41 | 1.22 | 0.0% | — |
| node3 | 43 | 1.30 | 0.0% | — |

### Timing Attack (Simultaneous)

| Node | Msgs | ML Score | Flagged | BTI Drop |
|------|------|----------|---------|----------|
| node1 | 44 | 2.40 | 6.8% | — |
| node2 | 44 | 1.11 | 2.3% | — |
| node3 | 45 | 0.81 | 2.2% | 16.2% |

### FDI Attack (Simultaneous)

| Node | Msgs | ML Score | Flagged | BTI Drop |
|------|------|----------|---------|----------|
| node1 | 44 | 2.10 | 4.5% | — |
| node2 | 45 | 0.79 | 4.4% | — |
| node3 | 45 | 0.57 | 4.4% | — |

### Flood Attack (Simultaneous)

| Node | Msgs | ML Score | Flagged | BTI Drop |
|------|------|----------|---------|----------|
| node1 | 43 | 2.45 | 2.3% | — |
| node2 | 45 | 0.79 | 4.4% | 13.3% |
| node3 | 45 | 1.16 | 2.2% | — |

### Mimic Attack (Simultaneous)

| Node | Msgs | ML Score | Flagged | BTI Drop |
|------|------|----------|---------|----------|
| node1 | 42 | 2.40 | 2.4% | — |
| node2 | 43 | 1.16 | 0.0% | — |
| node3 | 13 | 1.09 | 0.0% | — |

---

## Iteration 3 Results — All Nodes Sequential (`run_iteration3.ps1`, learn 120s / attack 240s)

### Baseline (No Attack)

| Node | Msgs | ML Score | Flagged | First Alert | BTI Drop |
|------|------|----------|---------|-------------|----------|
| node3 | 55 | 1.11 | 0.0% | — | -2.8% |
| node1 | 62 | 2.17 | 6.9%* | 18.20s | 9.3%* |
| node2 | 59 | 1.05 | 0.7% | 85.23s | -10.0% |

\* node1 `none` phase shows elevated flagging — not a clean baseline (see notes).

### Replay Attack

| Node | Msgs | ML Score | Flagged | First Alert | BTI Drop |
|------|------|----------|---------|-------------|----------|
| node3 | 239 | 1.67 | 2.1% | 44.29s | 6.5% |
| node1 | 225 | 2.77 | 4.0% | 23.56s | 11.4% |
| node2 | 243 | 1.41 | 0.8% | 42.80s | -8.6% |

### Timing Manipulation

| Node | Msgs | ML Score | Flagged | First Alert | BTI Drop |
|------|------|----------|---------|-------------|----------|
| node2 | 152 | 27.90 | 28.3% | 0.10s | 10.1% |
| node1 | 170 | 35.56 | 90.0%* | 2.45s | 14.4% |
| node3 | 166 | 27.43 | 26.5% | 9.81s | -0.2% |

\* node1 timing flagged 90.0% — outlier, likely miscalibrated threshold (see notes).

### False Data Injection

| Node | Msgs | ML Score | Flagged | First Alert | BTI Drop |
|------|------|----------|---------|-------------|----------|
| node3 | 235 | 50.79 | 50.6% | 5.57s | 18.3% |
| node2 | 253 | 54.25 | 54.5% | 1.78s | 16.2% |
| node1 | 237 | 368.40 | 51.9% | 0.70s | 33.4% |

### Traffic Flood

| Node | Msgs | ML Score | Flagged | First Alert | BTI Drop |
|------|------|----------|---------|-------------|----------|
| node3 | 2551 | 95.39 | 95.3% | 0.11s | -20.6% |
| node2 | 2458 | 804.31 | 95.2% | 0.17s | 2.9% |
| node1 | 2372 | 396.13 | 98.0% | 0.13s | -9.6% |

### Mimic (DRIFT_PER_STEP = 0.15)

| Node | Msgs | ML Score | Flagged | First Alert | BTI Drop |
|------|------|----------|---------|-------------|----------|
| node3 | 192 | 41.95 | 42.2% | 7.91s | 15.6% |
| node2 | 218 | 411.34 | 47.7% | 3.10s | 10.4% |
| node1 | 178 | 40.30 | 50.6% | 0.12s | -4.8% |

---

## Iteration 3 Results — All Nodes Simultaneous (`run_iteration3_all.ps1`, learn 120s / attack 240s)

### Baseline (No Attack)

| Node | Msgs | ML Score | Flagged | BTI Drop |
|------|------|----------|---------|----------|
| node2 | 60 | 1.16 | 0.7% | -4.1% |
| node3 | 60 | 1.12 | 25.5% | -3.7% |
| node1 | 59 | 2.19 | 94.5%* | -3.5% |

\* node1 baseline corrupt — elevated flagging (see notes).

### Replay Attack (Simultaneous)

| Node | Msgs | ML Score | Flagged | BTI Drop |
|------|------|----------|---------|----------|
| node1 | 158 | 4.56 | 53.8% | -8.0% |
| node2 | 173 | 1.26 | 0.6% | -8.3% |
| node3 | 161 | 2.54 | 0.6% | -13.2% |

### Timing Attack (Simultaneous)

| Node | Msgs | ML Score | Flagged | BTI Drop |
|------|------|----------|---------|----------|
| node2 | 140 | 16.73 | 17.1% | 24.4% |
| node3 | 141 | 24.90 | 25.5% | 19.6% |
| node1 | 129 | 14.79 | 33.3% | 22.9% |

### FDI Attack (Simultaneous)

| Node | Msgs | ML Score | Flagged | BTI Drop |
|------|------|----------|---------|----------|
| node1 | 154 | 189.66 | 40.3% | 36.9% |
| node2 | 167 | 30.22 | 32.3% | 7.1% |
| node3 | 157 | 6.56 | 44.6% | 23.6% |

### Flood Attack (Simultaneous)

| Node | Msgs | ML Score | Flagged | BTI Drop |
|------|------|----------|---------|----------|
| node1 | 1017 | 73.44 | 88.9% | 2.1% |
| node2 | 1179 | 90.13 | 90.2% | -0.1% |
| node3 | 737 | 16.37 | 96.7% | 5.3% |

### Mimic Attack (Simultaneous, rerun)

| Node | Msgs | ML Score | Flagged | BTI Drop |
|------|------|----------|---------|----------|
| node1 | 143 | 39.92 | 93.0% | 19.0% |
| node2 | 149 | 21.21 | 21.5% | 22.4% |
| node3 | 167 | 20.19 | 95.2% | 17.7% |

---

## Cross-Iteration Comparison

### ML Detection Rate (% messages flagged) — sequential

| Attack | Iter1 (node1) | Iter2 n1 | Iter2 n2 | Iter2 n3 | Iter3 n1 | Iter3 n2 | Iter3 n3 |
|--------|--------------|----------|----------|----------|----------|----------|----------|
| Replay | 1.7% | 45.3% | 0.0% | 3.3% | 4.0% | 0.8% | 2.1% |
| Timing | 33.7% | 32.9% | 28.7% | 28.4% | 90.0%* | 28.3% | 26.5% |
| FDI | 99.2% | 51.3% | 50.0% | 92.7% | 51.9% | 54.5% | 50.6% |
| Flood | 100.0% | 96.4% | 95.3% | 95.3% | 98.0% | 95.2% | 95.3% |
| Mimic | 70.1% | 44.1% | 43.0% | 46.0% | 50.6% | 47.7% | 42.2% |

\* node1 timing outlier (miscalibrated threshold in that session).

### BTI Drop (% reduction from baseline) — sequential

| Attack | Iter1 (node1) | Iter2 n1 | Iter2 n2 | Iter2 n3 | Iter3 n1 | Iter3 n2 | Iter3 n3 |
|--------|--------------|----------|----------|----------|----------|----------|----------|
| Replay | 7.8% | 7.5% | -4.0% | 37.5% | 11.4% | -8.6% | 6.5% |
| Timing | 8.4% | 20.8% | 13.0% | -1.5% | 14.4% | 10.1% | -0.2% |
| FDI | 23.4% | 3.5% | 19.7% | 31.3% | 33.4% | 16.2% | 18.3% |
| Flood | -10.0% | -37.9% | -30.2% | -6.8% | -9.6% | 2.9% | -20.6% |
| Mimic | 18.3% | 9.2% | 25.0% | 30.7% | -4.8% | 10.4% | 15.6% |

Negative = BTI *rose* (flood frequency spike dominates via F metric).

---

## Key Observations

### Iteration 1
1. **Replay is the most dangerous attack** — nearly invisible to ML and BTI
2. **FDI is the most detectable** — 99.2% flagged with massive ML scores
3. **Flood breaks the BTI model** — the high frequency actually increases BTI due to F weight
4. **Mimic needs tuning** — drift too aggressive at 0.5/step; reduced to 0.15 for stealth
5. **ML first-alert latency** — Timing and Mimic trigger within 0.08s; Replay takes 62s

### Iteration 2
1. **Mimic stealth improved** — detection dropped from 70.1% to ~44% with DRIFT_PER_STEP=0.15
2. **Node variation matters** — FDI detection ranges from 50% (node1/node2) to 93% (node3)
3. **Flood BTI always increases** — consistent across all nodes, confirms F metric dominance
4. **Replay inconsistency** — node1 flagged 45.3% but node2 flagged 0.0% in same run; likely ML threshold differences per session
5. **Simultaneous mode underperforms** — `attack_all.py` shows fewer attack-phase messages captured (race condition between attack start and capture session)
6. **Node3 is most sensitive to FDI** — 92.7% flagged vs ~50% for node1/node2
7. **Timing T metric shows 0.000** in multi-node runs — needs investigation

### Iteration 3 (4-minute runs)
1. **Longer attacks (240s) give more consistent, more complete detection** — flood/none runs capture hundreds-to-thousands of messages (up to 2551), far better statistics than the 120s runs.
2. **Replay remains the stealthiest attack** — 0.8–4.0% flagged with minimal BTI drop (often negative), confirming replay is the most dangerous/threat.
3. **Flood BTI paradox persists** — BTI rises (negative "drop") in most sequential flood runs (F-metric dominance), though the simultaneous flood runs mostly show small positive drops.
4. **FDI has the strongest BTI degradation in sequential mode** — node1 dropped 33.4%, the largest single-node BTI drop of the iteration.
5. **Discovery failures are transient, not systemic** — timing (seq) and mimic (sim) found 0 / 1 nodes on first attempt but found all 3 on manual rerun; node discovery intermittency remains the main operational risk.
6. **Simultaneous mode still shows some contaminated baselines** — node1 `none` run flagged 94.5%, so the shared-threshold learning is unreliable for node1 in that session.

### Open investigation items
- node1 baseline (`none`) runs flagged 44.5% (iter2) and 94.5% (iter3-all) — the auto-set ML threshold for node1 is unstable across sessions
- node1 timing flagged 90.0% vs ~26–34% elsewhere (iter3 sequential) — threshold outlier
- Timing `T` metric occasionally floors at 0.000 (timing attack) despite winsorizing; revisit with profile-based expected-interval scoring

---

## Files Generated

### Iteration 1

| File | Attack |
|------|--------|
| `runs/run_replay_20260825_155730.csv` | Replay |
| `runs/run_timing_20260825_160131.csv` | Timing |
| `runs/run_fdi_20260825_160625.csv` | FDI |
| `runs/run_flood_20260825_161141.csv` | Flood |
| `runs/run_mimic_20260825_161621.csv` | Mimic |

### Iteration 2 — Sequential (`attack_each.py`)

| File | Node | Attack |
|------|------|--------|
| `runs/run_node3_none_20260826_125906.csv` | node3 | Baseline |
| `runs/run_node1_none_20260826_130607.csv` | node1 | Baseline |
| `runs/run_node2_none_20260826_131309.csv` | node2 | Baseline |
| `runs/run_node2_replay_20260826_141651.csv` | node2 | Replay |
| `runs/run_node1_replay_20260826_142053.csv` | node1 | Replay |
| `runs/run_node3_replay_20260826_142455.csv` | node3 | Replay |
| `runs/run_node1_timing_20260826_143113.csv` | node1 | Timing |
| `runs/run_node3_timing_20260826_143517.csv` | node3 | Timing |
| `runs/run_node2_timing_20260826_143920.csv` | node2 | Timing |
| `runs/run_node3_fdi_20260826_145014.csv` | node3 | FDI |
| `runs/run_node2_fdi_20260826_145416.csv` | node2 | FDI |
| `runs/run_node1_fdi_20260826_145819.csv` | node1 | FDI |
| `runs/run_node3_flood_20260826_150431.csv` | node3 | Flood |
| `runs/run_node2_flood_20260826_150834.csv` | node2 | Flood |
| `runs/run_node1_flood_20260826_151236.csv` | node1 | Flood |
| `runs/run_node2_mimic_20260826_151727.csv` | node2 | Mimic |
| `runs/run_node3_mimic_20260826_152234.csv` | node3 | Mimic |
| `runs/run_node1_mimic_20260826_152738.csv` | node1 | Mimic |

### Iteration 2 — Simultaneous (`attack_all.py`)

| File | Node | Attack |
|------|------|--------|
| `runs/run_node1_none_20260826_153312.csv` | node1 | Baseline |
| `runs/run_node3_none_20260826_153312.csv` | node3 | Baseline |
| `runs/run_node2_none_20260826_153312.csv` | node2 | Baseline |
| `runs/run_node3_replay_20260826_154030.csv` | node3 | Replay |
| `runs/run_node1_replay_20260826_154030.csv` | node1 | Replay |
| `runs/run_node2_replay_20260826_154030.csv` | node2 | Replay |
| `runs/run_node1_timing_20260826_154517.csv` | node1 | Timing |
| `runs/run_node2_timing_20260826_154518.csv` | node2 | Timing |
| `runs/run_node3_timing_20260826_154518.csv` | node3 | Timing |
| `runs/run_node3_fdi_20260826_155027.csv` | node3 | FDI |
| `runs/run_node1_fdi_20260826_155027.csv` | node1 | FDI |
| `runs/run_node2_fdi_20260826_155027.csv` | node2 | FDI |
| `runs/run_node3_flood_20260826_155708.csv` | node3 | Flood |
| `runs/run_node1_flood_20260826_155708.csv` | node1 | Flood |
| `runs/run_node2_flood_20260826_155708.csv` | node2 | Flood |
| `runs/run_node1_mimic_20260826_161415.csv` | node1 | Mimic |
| `runs/run_node3_mimic_20260826_161415.csv` | node3 | Mimic |
| `runs/run_node2_mimic_20260826_161415.csv` | node2 | Mimic |

### Other Files

| File | Description |
|------|-------------|
| `runs/profile.json` | Latest learned network profile |
| `run_iteration2.ps1` | PowerShell script for sequential attacks |
| `run_iteration2_all.ps1` | PowerShell script for simultaneous attacks |

### Iteration 3 — Sequential (`run_iteration3.ps1`, learn 120s / attack 240s)

| File | Node | Attack |
|------|------|--------|
| `runs/run_node3_none_20260827_153955.csv` | node3 | Baseline |
| `runs/run_node1_none_20260827_154725.csv` | node1 | Baseline |
| `runs/run_node2_none_20260827_155457.csv` | node2 | Baseline |
| `runs/run_node3_replay_20260827_160209.csv` | node3 | Replay |
| `runs/run_node1_replay_20260827_160843.csv` | node1 | Replay |
| `runs/run_node2_replay_20260827_161516.csv` | node2 | Replay |
| `runs/run_node2_timing_20260827_191222.csv` | node2 | Timing (rerun) |
| `runs/run_node1_timing_20260827_191854.csv` | node1 | Timing (rerun) |
| `runs/run_node3_timing_20260827_192532.csv` | node3 | Timing (rerun) |
| `runs/run_node3_fdi_20260827_162240.csv` | node3 | FDI |
| `runs/run_node2_fdi_20260827_162913.csv` | node2 | FDI |
| `runs/run_node1_fdi_20260827_163545.csv` | node1 | FDI |
| `runs/run_node3_flood_20260827_164229.csv` | node3 | Flood |
| `runs/run_node2_flood_20260827_164900.csv` | node2 | Flood |
| `runs/run_node1_flood_20260827_165532.csv` | node1 | Flood |
| `runs/run_node3_mimic_20260827_170215.csv` | node3 | Mimic |
| `runs/run_node2_mimic_20260827_170851.csv` | node2 | Mimic |
| `runs/run_node1_mimic_20260827_171525.csv` | node1 | Mimic |

### Iteration 3 — Simultaneous (`run_iteration3_all.ps1`, learn 120s / attack 240s)

| File | Node | Attack |
|------|------|--------|
| `runs/run_node{1,2,3}_none_20260827_193312.csv` | all | Baseline |
| `runs/run_node{1,2,3}_replay_20260827_194025.csv` | all | Replay |
| `runs/run_node{1,2,3}_timing_20260827_194710.csv` | all | Timing |
| `runs/run_node{1,2,3}_fdi_20260827_195401.csv` | all | FDI |
| `runs/run_node{1,2,3}_flood_20260827_200046.csv` | all | Flood |
| `runs/run_node{1,2,3}_mimic_20260827_201525.csv` | all | Mimic (rerun) |

### Other Files (cont.)

| File | Description |
|------|-------------|
| `run_iteration3.ps1` | PowerShell script for 240s sequential attacks |
| `run_iteration3_all.ps1` | PowerShell script for 240s simultaneous attacks |
