# Run-level statistics (oct22)

Each CSV file = one repeat. 95% CI uses t distribution (df = n-1). `ml_anomaly`/BTI columns are read from archived CSVs.

## Per-run

| attack | node | file | n_base | n_atk | flagged % | BTI drop % | first alert (s) |
|---|---|---|---:|---:|---:|---:|---:|
| mimic | node1 | `run_node1_mimic_20261110_112640.csv` | 57 | 231 | 41.1 | +21.1% | 2.1 |
| mimic | node2 | `run_node2_mimic_20261110_113946.csv` | 59 | 238 | 0.0 | +8.7% | -- |
| mimic | node3 | `run_node3_mimic_20261110_114621.csv` | 60 | 238 | 25.2 | +0.1% | 118.1 |
| mimic | node4 | `run_node4_mimic_20261110_113314.csv` | 58 | 232 | 28.9 | -0.9% | 0.0 |
| mimic | node1 | `run_node1_mimic_20261110_121849.csv` | 58 | 231 | 15.2 | +22.1% | 6.2 |
| mimic | node2 | `run_node2_mimic_20261110_120545.csv` | 59 | 236 | 0.8 | +7.5% | 18.4 |
| mimic | node3 | `run_node3_mimic_20261110_115913.csv` | 60 | 239 | 25.1 | +0.1% | 119.0 |
| mimic | node4 | `run_node4_mimic_20261110_121216.csv` | 58 | 233 | 29.2 | +4.8% | 42.3 |
| mimic | node1 | `run_node1_mimic_20261110_130540.csv` | 58 | 231 | 0.0 | +0.3% | -- |
| mimic | node2 | `run_node2_mimic_20261110_125907.csv` | 59 | 237 | 0.8 | +12.8% | 36.6 |
| mimic | node3 | `run_node3_mimic_20261110_125235.csv` | 60 | 240 | 25.8 | -0.3% | 112.2 |
| mimic | node4 | `run_node4_mimic_20261110_124602.csv` | 58 | 233 | 18.9 | +3.2% | 8.2 |

## Aggregate by attack × node (mean ± 95% CI, N runs)

| attack | node | N | flagged % mean [95% CI] | BTI drop % mean [95% CI] | first alert s mean [95% CI] | per-run flagged % | per-run BTI drop % |
|---|---|---:|---:|---:|---:|---|---|
| mimic | node1 | 3 | +18.76% [-32.91%, +70.43%] | +14.51% [-16.04%, +45.05%] | 4.13 [-21.79, 30.05] | 41.1, 15.2, 0.0 | +21.1%, +22.1%, +0.3% |
| mimic | node2 | 3 | +0.56% [-0.65%, +1.78%] | +9.66% [+2.74%, +16.58%] | 27.47 [-88.33, 143.26] | 0.0, 0.8, 0.8 | +8.7%, +7.5%, +12.8% |
| mimic | node3 | 3 | +25.38% [+24.40%, +26.36%] | -0.04% [-0.60%, +0.51%] | 116.44 [107.24, 125.63] | 25.2, 25.1, 25.8 | +0.1%, +0.1%, -0.3% |
| mimic | node4 | 3 | +25.65% [+11.09%, +40.21%] | +2.39% [-4.85%, +9.63%] | 16.84 [-38.90, 72.59] | 28.9, 29.2, 18.9 | -0.9%, +4.8%, +3.2% |
