#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

// =========================
// CONFIG
// =========================
#define WIFI_SSID     "WifiNAme" // Replace with name of your Wifi
#define WIFI_PASS     "WifiPass"  // Replace with the password of your Wifi
#define MQTT_BROKER   "192.168.1.X"  // Replace with Rock Pi IP
#define MQTT_PORT     1883
#define MQTT_USER     "esp32user"  // Replace with MQTT user name for ESP32
#define MQTT_PASS     "yourpassword"  // Replace with the password you set

#define SOUND_PIN    35
#define LIGHT_PIN    32

#define NODE_ID      "node2"

// =========================
// OBJECTS
// =========================
WiFiClient espClient;
PubSubClient mqtt(espClient);

// =========================
// TOPICS
// =========================
const char* PUB_DATA    = "iot/node2/data";
const char* PUB_STATUS  = "iot/network/status";
const char* SUB_COMMAND = "iot/node2/command";
const char* SUB_NODE1   = "iot/node1/data";      // listen to node1 sensor data
const char* SUB_NODE3   = "iot/node3/data";      // listen to node3 data
const char* SUB_NETWORK = "iot/network/message"; // listen to all network messages

// =========================
// SHARED STATE FROM OTHER NODES
// =========================
float node1_temp     = 0;
float node1_humidity = 0;
int   node1_ozone    = 0;
int   node3_uptime   = 0;
bool  node1_online   = false;
bool  node3_online   = false;

// =========================
// WIFI
// =========================
void connectWiFi() {
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("Connecting to WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nWiFi connected: " + WiFi.localIP().toString());
}

// =========================
// MQTT RECEIVE — inter-node communication
// =========================
void onMessage(char* topic, byte* payload, unsigned int length) {
  String msg = "";
  for (int i = 0; i < length; i++) msg += (char)payload[i];

  Serial.println("MSG from [" + String(topic) + "]: " + msg);

  // ── Commands from dashboard ──
  if (String(topic) == "iot/node2/command") {
    if (msg == "PING") {
      mqtt.publish(PUB_STATUS, "{\"node\":\"node2\",\"status\":\"alive\"}");
      Serial.println("PING received — responded alive");
    }
    if (msg == "STATUS") {
      int sound = analogRead(SOUND_PIN);
      int light = analogRead(LIGHT_PIN);
      StaticJsonDocument<200> doc;
      doc["node"]      = "node2";
      doc["sound"]     = sound;
      doc["light"]     = light;
      doc["requested"] = true;
      char buf[200];
      serializeJson(doc, buf);
      mqtt.publish(PUB_DATA, buf);
    }
  }

  // ── Data from Node 1 ──
  if (String(topic) == "iot/node1/data") {
    StaticJsonDocument<300> doc;
    DeserializationError err = deserializeJson(doc, msg);
    if (!err) {
      node1_temp     = doc["temp"]     | 0.0;
      node1_humidity = doc["humidity"] | 0.0;
      node1_ozone    = doc["ozone"]    | 0;
      node1_online   = true;
      Serial.println("Node1 → temp:" + String(node1_temp) + " humidity:" + String(node1_humidity) + " ozone:" + String(node1_ozone));

      // React: if temp from node1 is high send alert
      if (node1_temp > 35.0) {
        String alert = "{\"from\":\"node2\",\"alert\":\"Node1 high temperature\",\"value\":" + String(node1_temp) + "}";
        mqtt.publish("iot/network/message", alert.c_str());
        Serial.println("Alert sent: Node1 high temp");
      }

      // React: if ozone from node1 is high send alert
      if (node1_ozone > 800) {
        String alert = "{\"from\":\"node2\",\"alert\":\"Node1 high ozone\",\"value\":" + String(node1_ozone) + "}";
        mqtt.publish("iot/network/message", alert.c_str());
        Serial.println("Alert sent: Node1 high ozone");
      }
    }
  }

  // ── Data from Node 3 ──
  if (String(topic) == "iot/node3/data") {
    StaticJsonDocument<256> doc;
    DeserializationError err = deserializeJson(doc, msg);
    if (!err) {
      node3_uptime = doc["uptime"] | 0;
      node3_online = true;
      Serial.println("Node3 → uptime:" + String(node3_uptime) + "s");
    }
  }

  // ── Network-wide messages from other nodes ──
  if (String(topic) == "iot/network/message") {
    Serial.println("NETWORK MSG: " + msg);
  }
}

// =========================
// MQTT CONNECT
// =========================
void connectMQTT() {
  while (!mqtt.connected()) {
    Serial.print("Connecting to MQTT...");
    if (mqtt.connect(NODE_ID, MQTT_USER, MQTT_PASS)) {
      Serial.println("connected");
      mqtt.subscribe(SUB_COMMAND);
      mqtt.subscribe(SUB_NODE1);
      mqtt.subscribe(SUB_NODE3);
      mqtt.subscribe(SUB_NETWORK);
      mqtt.publish(PUB_STATUS, "{\"node\":\"node2\",\"status\":\"online\"}");
      // Announce to the network
      mqtt.publish("iot/network/message", "{\"from\":\"node2\",\"event\":\"joined\"}");
    } else {
      Serial.print("failed, rc=");
      Serial.print(mqtt.state());
      Serial.println(" — retrying in 2s");
      delay(2000);
    }
  }
}

// =========================
// SETUP
// =========================
void setup() {
  Serial.begin(115200);
  connectWiFi();
  mqtt.setServer(MQTT_BROKER, MQTT_PORT);
  mqtt.setCallback(onMessage);
  connectMQTT();
}

// =========================
// LOOP
// =========================
void loop() {
  if (!mqtt.connected()) connectMQTT();
  mqtt.loop();

  int   sound    = analogRead(SOUND_PIN);
  int   light    = analogRead(LIGHT_PIN);
  float sound_v  = sound * (3.3 / 4095.0);
  float light_v  = light * (3.3 / 4095.0);

  // Build JSON — include awareness of other nodes
  StaticJsonDocument<300> doc;
  doc["node"]         = "node2";
  doc["sound"]        = sound;
  doc["sound_v"]      = round(sound_v * 100) / 100.0;
  doc["light"]        = light;
  doc["light_v"]      = round(light_v * 100) / 100.0;
  doc["ip"]           = WiFi.localIP().toString();
  doc["rssi"]         = WiFi.RSSI();
  doc["node1_online"] = node1_online;
  doc["node3_online"] = node3_online;

  char buffer[300];
  serializeJson(doc, buffer);

  mqtt.publish(PUB_DATA, buffer);
  Serial.println("Published: " + String(buffer));

  delay(2000);
}
}
