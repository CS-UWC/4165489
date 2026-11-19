# Reproducing the experiments

This project was run on an isolated testbed. No step requires internet access
other than installing packages. All credentials and addresses are supplied via
environment variables or a local `.env` file (see `.env.example`).

## 1. Prerequisites

- Gateway (Rock Pi 4 SE or similar Linux host) with Python 3.
- Mosquitto broker with authenticated users.
- Four nodes: ESP32 ×3, Arduino Nano ×1 on USB serial.
- ESP32 and Arduino IDE toolchains to flash firmware.

```bash
python3 -m pip install -r requirements.txt
```

## 2. Broker setup

```bash
sudo apt install mosquitto mosquitto-clients
```

Example `/etc/mosquitto/mosquitto.conf`:

```
listener 1883
protocol mqtt
allow_anonymous false
password_file /etc/mosquitto/passwd
acl_file /etc/mosquitto/acl
persistence true
persistence_location /var/lib/mosquitto/
log_dest file /var/log/mosquitto/mosquitto.log
```

Create a user and restart:

```bash
sudo mosquitto_passwd -c /etc/mosquitto/passwd esp32user
sudo systemctl restart mosquitto
```

Keep the password in `.env`, never in source. The dashboard connects on
`localhost:1883`; attacks and capture clients take `--broker`, `--user` and
`--password` flags or read the same environment variables.

## 3. Firmware

Open each sketch in `arduino/src/` and replace the placeholder Wi-Fi and MQTT
values with your own before flashing:

- `node1.ino`, `node2.ino`, `node4.ino` → ESP32 Dev Module
- `nano_node3.ino` → Arduino Nano, upload speed 115200

If firmware cannot connect, confirm the broker user/password and that the
gateway firewall allows port 1883 on the testbed network only.

## 4. Run the live system

```bash
# Terminal 1 - dashboard
python3 src/app.py

# Terminal 2 - node3 serial bridge (auto-detects /dev/ttyUSB* or /dev/ttyACM*)
python3 src/node3_bridge.py

# Optional check before a run
python3 src/preflight.py --broker <broker> --user <user> --password <pass>
```

Preflight verifies that every node emits the expected schema fields and that a
baseline window can be learned. Do not start an attack until all four nodes
pass.

## 5. Capture a run

The iteration runner handles baseline learning, attack execution and capture:

```bash
./scripts/run_iteration4.sh
```

Configure `MQTT_BROKER` and credentials in the environment first. The runner
executes attacks one at a time — never run two attack processes concurrently,
as they would publish to the same topics and corrupt the sample.

Individual attack clients are also available:

```
src/Attack_Replay.py     src/Attack_Time.py    src/Attack_FalseData.py
src/Attack_Flood.py      src/Attack_Mimic.py
src/attack_each.py       src/attack_all.py     src/controller.py
```

Each records a baseline before publishing and restores normal traffic at the
end. Attacks are telemetry-only: they publish well-formed sensor messages and
never target network infrastructure.

## 6. Analyse captured data

```bash
python3 src/tiny_ml.py          # per-node anomaly model
python3 src/summarise_runs.py    # run-level summaries
python3 src/make_results.py      # result tables
python3 src/analyze_rate_aware_f.py  # rate-aware sensitivity analysis
```

`src/capture.py` provides the shared `CaptureSession` and profile-learning
helpers used by the attack clients and analysis scripts.

## 7. Archived results

Curated summaries and per-run CSVs are in `results/`. They were produced from
three independent repeats per node and attack. The rate-aware analysis is
explicitly a post-hoc counterfactual over archived messages, not a new
experiment.

## 8. Security reminders

- Never commit `.env`, filled-in firmware sketches, tunnel URLs or device IPs.
- Publish experiment data only after replacing any captured IP fields.
- Testbed attacks must stay on an isolated network.
