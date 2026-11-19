# Live demo runbook

Absolute maximum **4:00**. Target finish **3:30-3:40**, hard stop **3:45**.

The demo is live. Slides are fallback only. One 38-second hardware/component
clip is pre-recorded; everything else is shown running on the testbed.

## Before you start

- Gateway: broker, dashboard, node3 bridge and outbound tunnel are running.
- The tunnel URL is loaded as an **OBS browser source** (no address bar on screen).
- Confirm the dashboard shows all four nodes and 7/7 checks passing.
- On the Raspberry Pi, `cd /home/filrasp/Honours/run` and use `python3` for all
  attack commands.
- Keep the tunnel hostname, token, credentials, public URL and private device
  IPs off screen at all times.

## OBS scenes

| Scene | Source | Purpose |
|-------|--------|---------|
| `01 LIVE` | Browser source pointing at the tunnel URL | Live dashboard |
| `02 CLIP` | Media source, pre-recorded 38 s hardware clip | Component overview |

## Timeline

| Time | Action |
|------|--------|
| 0:00 - 0:18 | Intro. Scene `01 LIVE` on the running dashboard. |
| 0:18 - 0:56 | Switch to `02 CLIP`: 38 s hardware/component clip. |
| 0:54 | Start **FDI** attack on the Pi (overlaps the last 2 s of the clip). |
| 0:56 - 1:26 | Back to `01 LIVE`: 30 s guided tour of the dashboard components. |
| 1:26 - 2:19 | Live trust scores / anomaly response from the FDI run. |
| 2:19 | Start **replay** attack (only after FDI has fully stopped). |
| 2:19 - 3:30 | Show replay response and explain the BTI behaviour. |
| 3:30 - 3:40 | Wrap up. |
| 3:45 | Hard stop regardless of state. |

## Running attacks

Run one attack process at a time. Never run FDI and replay concurrently.

```bash
# On the Pi, from /home/filrasp/Honours/run
python3 Attack_FalseData.py --broker <broker> --user <user> --password <pass>
# wait for it to finish completely before the next one
python3 Attack_Replay.py --broker <broker> --user <user> --password <pass>
```

Both clients learn a baseline before publishing and restore normal traffic when
they finish. Attacks are telemetry-only on the isolated testbed.

## Fallback

If the live dashboard fails, switch to `02 CLIP` and narrate from the saved
screenshots. Do not paste the tunnel URL, broker address or credentials into
any visible window.

## Gateway startup (fast path)

Background services are started detached with logs and PID files so they
survive the session. The dashboard binds `0.0.0.0:5000`; the tunnel forwards
`http://127.0.0.1:5000` over HTTP/2 (using `localhost` caused 502 errors).
The node3 bridge auto-detects its serial port — do not pass a fake device path.
