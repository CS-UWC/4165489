"""
node3_bridge.py
Reads Arduino Nano serial output and publishes
to MQTT broker as node3
"""

import os
import serial
import time
import json
import glob
import paho.mqtt.client as mqtt

# =========================
# CONFIG
# =========================
MQTT_BROKER = os.environ.get("MQTT_BROKER", "localhost")
MQTT_PORT   = 1883
MQTT_USER   = "esp32user"
MQTT_PASS   = os.environ.get("MQTT_PASS", "")
BAUD_RATE   = int(os.environ.get("BRIDGE_BAUD", 115200))

PUB_DATA   = "iot/node3/data"
PUB_STATUS = "iot/network/status"
SUB_NODE1  = "iot/node1/data"
SUB_NODE2  = "iot/node2/data"
SUB_NODE4  = "iot/node4/data"
SUB_NETWORK = "iot/network/message"

# =========================
# SHARED STATE FROM OTHER NODES (like Node3.ino)
# =========================
# node4_online mirrors what Node3.ino now publishes. The bridge stands in for
# the ESP32 that would otherwise be node3, so it must publish the same field
# or node3's row in the dashboard will be the only one missing a node4 badge.
node1_online    = False
node2_online    = False
node4_online    = False
node1_temp      = 0.0
node1_humidity  = 0.0
node1_ozone     = 0
node2_sound     = 0
network_alerts  = 0

# =========================
# MQTT CLIENT
# =========================
def make_client():
    try:
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION1,
            client_id="node3_bridge"
        )
    except AttributeError:
        client = mqtt.Client(client_id="node3_bridge")
    return client

def on_message(client, userdata, msg):
    global node1_online, node2_online, node4_online
    global node1_temp, node1_humidity, node1_ozone
    global node2_sound, network_alerts
    topic = msg.topic
    try:
        data = json.loads(msg.payload.decode())
    except Exception:
        data = {}

    if topic == "iot/node1/data":
        node1_online   = True
        node1_temp     = data.get("temp", 0.0)
        node1_humidity = data.get("humidity", 0.0)
        node1_ozone    = data.get("ozone", 0)
    elif topic == "iot/node2/data":
        node2_online = True
        node2_sound  = data.get("sound", 0)
    elif topic == SUB_NODE4:
        node4_online = True
    elif topic == "iot/network/message":
        if "alert" in msg.payload.decode():
            network_alerts += 1

def connect_mqtt():
    global node1_online, node2_online
    client = make_client()
    client.username_pw_set(MQTT_USER, MQTT_PASS)

    def on_connect(client, userdata, flags, rc):
        if rc == 0:
            print("Bridge connected to MQTT")
            client.subscribe(SUB_NODE1)
            client.subscribe(SUB_NODE2)
            client.subscribe(SUB_NODE4)
            client.subscribe(SUB_NETWORK)
            client.publish(PUB_STATUS,
                json.dumps({"node": "node3", "status": "online"}))
        else:
            print(f"MQTT failed rc={rc}")

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    client.loop_start()
    return client

# =========================
# FIND NANO PORT
# =========================
def find_nano():
    ports = glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*")
    if not ports:
        print("No serial device found!")
        return None
    print(f"Found serial port: {ports[0]}")
    return ports[0]

# =========================
# PARSE SERIAL LINE
# NODE=3;DIST=45.2;PROX=near;REED=clear;REEDAO=123;
# =========================
def parse_line(line):
    parts = line.strip().split(";")
    values = {}
    for p in parts:
        if "=" in p:
            k, v = p.split("=", 1)
            values[k.strip()] = v.strip()
    return values

def _to_float(v):
    """Best-effort float. Returns None when the serial line was corrupted.

    The Nano runs at 115200 baud. A dropped character must cost us one sample,
    not the whole stream, so unparseable fields become None rather than
    raising and killing the read loop.
    """
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

def _to_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None

# =========================
# REBIND AFTER USB RE-ENUMERATION
# =========================
def reopen_serial(old):
    """Close a dead serial handle and rebind to whatever port appears now.

    A reflash or replug makes the kernel drop the old ttyUSB node and create a
    new one, so the path recorded at startup goes stale and every read raises
    SerialException. Re-globbing on failure keeps the bridge alive across
    replugs instead of requiring a manual restart mid-run.
    """
    try:
        old.close()
    except Exception:
        pass

    for _ in range(30):
        port = find_nano()
        if port:
            try:
                fresh = serial.Serial(port, BAUD_RATE, timeout=10)
                print(f"Reconnected on {port} at {BAUD_RATE} baud")
                return fresh
            except Exception as exc:
                print(f"Reopen failed: {exc}")
        time.sleep(2)

    print("No serial device appeared, will keep retrying")
    return None


# =========================
# MAIN
# =========================
def main():
    print("=== NODE 3 SERIAL BRIDGE ===")

    port = find_nano()
    if not port:
        print("Plug in the Nano and try again.")
        return

    client = connect_mqtt()
    time.sleep(2)

    # The Nano sleeps 2000 ms between readings, so a shorter read timeout fires
    # constantly on an idle line. Keep it well above the send interval.
    ser = serial.Serial(port, BAUD_RATE, timeout=10)
    print(f"Reading from {port} at {BAUD_RATE} baud")
    print("============================\n")

    count = 0
    bad_frames = 0

    while True:
        try:
            raw = ser.readline()
        except serial.SerialException as e:
            # A dead handle repeats this forever, so rebind rather than spin.
            print(f"Serial lost ({e}). Rebinding...")
            ser = reopen_serial(ser)
            if ser is None:
                continue
            continue

        if not raw:
            continue

        try:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line or "NODE3" in line:
                continue

            values = parse_line(line)

            if values.get("NODE") != "3":
                continue

            # A corrupted frame is missing keys or has mangled values. Skip it
            # and report it once, otherwise the log is unreadable.
            if "DIST" not in values or "REED" not in values:
                bad_frames += 1
                print(f"corrupt frame skipped ({bad_frames}): {line}")
                continue

            dist    = _to_float(values.get("DIST"))
            reed_ao = _to_int(values.get("REEDAO"))
            if dist is None:
                bad_frames += 1
                print(f"unreadable DIST skipped ({bad_frames}): {line}")
                continue

            prox    = values.get("PROX", "unknown")
            reed    = values.get("REED", "clear")
            magnet  = (reed == "magnet")
            if reed_ao is None:
                reed_ao = 0

            if bad_frames:
                print(f"recovered after {bad_frames} corrupt frame(s)")
                bad_frames = 0

            # Republish status every 10 readings
            count += 1
            if count % 10 == 0:
                client.publish(PUB_STATUS,
                    json.dumps({"node": "node3", "status": "online"}))

            # Alert if object very close
            if dist > 0 and dist < 10:
                alert = json.dumps({
                    "from": "node3",
                    "alert": "Object very close",
                    "value": dist
                })
                client.publish("iot/network/message", alert)
                print("Alert sent: Object very close")

            # Alert if magnet detected
            if magnet:
                client.publish("iot/network/message",
                    json.dumps({
                        "from": "node3",
                        "alert": "Magnetic field detected"
                    }))
                print("Alert sent: Magnet detected")

            payload = {
                "node":      "node3",
                "distance":  dist if dist >= 0 else 0,
                "proximity": prox,
                "reed_do":   reed,
                "reed_ao":   reed_ao,
                "ip":        "nano-serial",
                "rssi":      0,
                "node1_online": node1_online,
                "node2_online": node2_online,
                "node4_online": node4_online,
                "node1_temp":   node1_temp,
                "node2_sound":  node2_sound,
                "network_alerts": network_alerts
            }

            client.publish(PUB_DATA, json.dumps(payload))
            print(f"Published: {payload}")

        except UnicodeDecodeError:
            continue
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nBridge stopped.")