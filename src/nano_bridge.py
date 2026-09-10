"""
node3_bridge.py
Reads Arduino Nano serial output and publishes
to MQTT broker as node3
"""

import serial
import time
import json
import glob
import paho.mqtt.client as mqtt

# =========================
# CONFIG
# =========================
MQTT_BROKER = "XXX.XXX.XXX.XXX"
MQTT_PORT   = 1883
MQTT_USER   = "esp32user"
MQTT_PASS   = "XXXXXXXXXX"
BAUD_RATE   = 9600

PUB_DATA   = "iot/node3/data"
PUB_STATUS = "iot/network/status"
SUB_NODE1  = "iot/node1/data"
SUB_NODE2  = "iot/node2/data"
SUB_NETWORK = "iot/network/message"

# =========================
# SHARED STATE FROM OTHER NODES (like Node3.ino)
# =========================
node1_online    = False
node2_online    = False
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
    global node1_online, node2_online, node1_temp, node1_humidity
    global node1_ozone, node2_sound, network_alerts
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

    ser = serial.Serial(port, BAUD_RATE, timeout=2)
    print(f"Reading from {port} at {BAUD_RATE} baud")
    print("============================\n")

    count = 0

    while True:
        try:
            raw = ser.readline()
            if not raw:
                continue

            line = raw.decode("utf-8").strip()
            if not line or "NODE3" in line:
                continue

            print(f"RAW: {line}")
            values = parse_line(line)

            if values.get("NODE") != "3":
                continue

            # Republish status every 10 readings
            count += 1
            if count % 10 == 0:
                client.publish(PUB_STATUS,
                    json.dumps({"node": "node3", "status": "online"}))

            dist    = float(values.get("DIST",    -1))
            prox    = values.get("PROX",   "unknown")
            reed    = values.get("REED",   "clear")
            reed_ao = int(values.get("REEDAO", 0))
            magnet  = (reed == "magnet")

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
