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

// =========================
// NODE 3 — TESTING PHASE
// Status: Active on network, sensors not yet assigned
// Pending: Sensor selection and wiring in progress
// Planned sensors: TBD
// =========================
#define NODE_ID      "node3"

// =========================
// OBJECTS
// =========================
WiFiClient espClient;
PubSubClient mqtt(espClient);

// =========================
// TOPICS
// =========================
const char* PUB_DATA    = "iot/node3/data";
const char* PUB_STATUS  = "iot/network/status";
const char* SUB_COMMAND = "iot/node3/command";
const char* SUB_NODE1   = "iot/node1/data";      // listen to node1
const char* SUB_NODE2   = "iot/node2/data";      // listen to node2
const char* SUB_NETWORK = "iot/network/message"; // listen to all network messages

// =========================
// SHARED STATE FROM OTHER NODES
// Node 3 acts as passive network monitor
// =========================
float node1_temp     = 0;
float node1_humidity = 0;
int   node1_ozone    = 0;
int   node2_sound    = 0;
int   node2_light    = 0;
bool  node1_online   = false;
bool  node2_online   = false;
int   network_alerts = 0;

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
// Node 3 monitors the full network passively
// =========================
void onMessage(char* topic, byte* payload, unsigned int length) {
  String msg = "";
  for (int i = 0; i < length; i++) msg += (char)payload[i];

  Serial.println("MSG from [" + String(topic) + "]: " + msg);

  // ── Commands from dashboard ──
  if (String(topic) == "iot/node3/command") {
    if (msg == "PING") {
      mqtt.publish(PUB_STATUS, "{\"node\":\"node3\",\"status\":\"alive\"}");
      Serial.println("PING received — responded alive");
    }
    // Dashboard can ask node3 for a full network summary
    if (msg == "NETWORK_SUMMARY") {
      StaticJsonDocument<400> doc;
      doc["from"]           = "node3";
      doc["type"]           = "summary";
      doc["node1_online"]   = node1_online;
      doc["node1_temp"]     = node1_temp;
      doc["node1_humidity"] = node1_humidity;
      doc["node1_ozone"]    = node1_ozone;
      doc["node2_online"]   = node2_online;
      doc["node2_sound"]    = node2_sound;
      doc["node2_light"]    = node2_light;
      doc["network_alerts"] = network_alerts;
      char buf[400];
      serializeJson(doc, buf);
      mqtt.publish("iot/network/message", buf);
      Serial.println("Network summary published");
    }
  }

  // ── Data from Node 1 — node3 monitors and logs ──
  if (String(topic) == "iot/node1/data") {
    StaticJsonDocument<300> doc;
    DeserializationError err = deserializeJson(doc, msg);
    if (!err) {
      node1_temp     = doc["temp"]     | 0.0;
      node1_humidity = doc["humidity"] | 0.0;
      node1_ozone    = doc["ozone"]    | 0;
      node1_online   = true;
      Serial.println("Monitoring Node1 → temp:" + String(node1_temp) +
                     " humidity:" + String(node1_humidity) +
                     " ozone:" + String(node1_ozone));
    }
  }

  // ── Data from Node 2 — node3 monitors and logs ──
  if (String(topic) == "iot/node2/data") {
    StaticJsonDocument<300> doc;
    DeserializationError err = deserializeJson(doc, msg);
    if (!err) {
      node2_sound  = doc["sound"] | 0;
      node2_light  = doc["light"] | 0;
      node2_online = true;
      Serial.println("Monitoring Node2 → sound:" + String(node2_sound) +
                     " light:" + String(node2_light));
    }
  }

  // ── Network-wide alerts — node3 counts and logs them ──
  if (String(topic) == "iot/network/message") {
    Serial.println("NETWORK MSG: " + msg);
    // Count alerts coming from other nodes
    if (msg.indexOf("alert") >= 0) {
      network_alerts++;
      Serial.println("Alert count: " + String(network_alerts));
    }
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
      mqtt.subscribe(SUB_NODE2);
      mqtt.subscribe(SUB_NETWORK);
      mqtt.publish(PUB_STATUS, "{\"node\":\"node3\",\"status\":\"online\"}");
      // Announce to the network
      mqtt.publish("iot/network/message", "{\"from\":\"node3\",\"event\":\"joined\"}");
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

  // -------------------------------------------------------
  // NOTE: Node 3 is currently in TESTING PHASE
  // No sensors connected yet — acting as network monitor
  // Publishes heartbeat + full network awareness summary
  // TODO: Add sensor reads here once sensors are selected
  // -------------------------------------------------------

  StaticJsonDocument<300> doc;
  doc["node"]           = "node3";
  doc["status"]         = "testing";
  doc["uptime"]         = millis() / 1000;
  doc["ip"]             = WiFi.localIP().toString();
  doc["rssi"]           = WiFi.RSSI();
  doc["node1_online"]   = node1_online;
  doc["node2_online"]   = node2_online;
  doc["node1_temp"]     = node1_temp;
  doc["node2_sound"]    = node2_sound;
  doc["network_alerts"] = network_alerts;

  // TODO: Add sensor readings here once selected
  // doc["sensor1"] = analogRead(PIN_X);

  char buffer[300];
  serializeJson(doc, buffer);

  mqtt.publish(PUB_DATA, buffer);
  Serial.println("Published: " + String(buffer));

  delay(2000);
}
