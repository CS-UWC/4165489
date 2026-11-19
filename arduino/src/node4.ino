#include <WiFi.h>
#define MQTT_MAX_PACKET_SIZE 512
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <Wire.h>
#include <Adafruit_AHTX0.h>

// =========================
// CONFIG
// =========================
#define WIFI_SSID     "WifiNAme"       // Replace with name of your Wifi
#define WIFI_PASS     "WifiPass"       // Replace with the password of your Wifi
#define MQTT_BROKER   "xxx.xxx.X.X"    // Replace with Rock Pi IP
#define MQTT_PORT     1883
#define MQTT_USER     "esp32user"     // Replace with MQTT user name for ESP32
#define MQTT_PASS     "yourpassword"  // Replace with the password you set

// AHT20 uses I2C — SDA and SCL
// ESP32 DevKit V1 default I2C pins:
//   SDA → GPIO 21
//   SCL → GPIO 22
// No need to define them — Wire uses them automatically

// Analog ambient light sensor
// ADC1 pins only (ADC2 unavailable with WiFi)
#define LIGHT_PIN    34

#define NODE_ID      "node4"

#define PEER_TIMEOUT_MS 15000UL

// Alert thresholds — calibrate to your environment
#define TEMP_ALERT    35.0   // degrees C
#define LIGHT_ALERT   3000   // raw ADC value

// =========================
// OBJECTS
// =========================
Adafruit_AHTX0 aht;
WiFiClient espClient;
PubSubClient mqtt(espClient);

// =========================
// TOPICS
// =========================
const char* PUB_DATA    = "iot/node4/data";
const char* PUB_STATUS  = "iot/network/status";
const char* PUB_MSG     = "iot/network/message";
const char* SUB_COMMAND = "iot/node4/command";
const char* SUB_NODE1   = "iot/node1/data";
const char* SUB_NODE2   = "iot/node2/data";
const char* SUB_NODE3   = "iot/node3/data";
const char* SUB_NETWORK = "iot/network/message";

// =========================
// SHARED STATE FROM OTHER NODES
// =========================
bool node1_online   = false;
bool node2_online   = false;
bool node3_online   = false;
int  network_alerts = 0;

unsigned long lastNode1 = 0;
unsigned long lastNode2 = 0;
unsigned long lastNode3 = 0;

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
// MQTT RECEIVE
// =========================
void onMessage(char* topic, byte* payload, unsigned int length) {
  String msg = "";
  for (int i = 0; i < length; i++) msg += (char)payload[i];

  String t = String(topic);

  // ── Commands from dashboard ──
  if (t == "iot/node4/command") {
    if (msg == "PING") {
      mqtt.publish(PUB_STATUS, "{\"node\":\"node4\",\"status\":\"alive\"}");
      Serial.println("PING: responded alive");
    }
    if (msg == "STATUS") {
      sensors_event_t humidity, temp;
      aht.getEvent(&humidity, &temp);
      int light = analogRead(LIGHT_PIN);
      StaticJsonDocument<1024> doc;
      doc["node"]      = "node4";
      doc["temp"]      = temp.temperature;
      doc["humidity"]  = humidity.relative_humidity;
      doc["light"]     = light;
      doc["requested"] = true;
      char buf[1024];
      serializeJson(doc, buf);
      mqtt.publish(PUB_DATA, buf);
    }
    return;
  }

  // ── Peer liveness tracking ──
  if (t == "iot/node1/data" || t == "iot/node2/data" || t == "iot/node3/data") {
    StaticJsonDocument<1024> doc;
    if (!deserializeJson(doc, msg)) {
      if (t == "iot/node1/data") { node1_online = true; lastNode1 = millis(); }
      if (t == "iot/node2/data") { node2_online = true; lastNode2 = millis(); }
      if (t == "iot/node3/data") { node3_online = true; lastNode3 = millis(); }
    }
    return;
  }

  // ── Network-wide messages ──
  if (t == "iot/network/message") {
    Serial.println("NETWORK MSG: " + msg);
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
      mqtt.subscribe(SUB_NODE3);
      mqtt.subscribe(SUB_NETWORK);
      mqtt.publish(PUB_STATUS, "{\"node\":\"node4\",\"status\":\"online\"}");
      mqtt.publish(PUB_MSG, "{\"from\":\"node4\",\"event\":\"joined\"}");
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
  analogReadResolution(12);   // 0-4095

  Serial.println("=== NODE 4 ===");
  Serial.println("AHT20 (Temp + Humidity) + Ambient Light Sensor");

  // Initialise AHT20
  Wire.begin();
  if (!aht.begin()) {
    Serial.println("AHT20 not found — check wiring on SDA(21) SCL(22)!");
    while (1) delay(1000);
  }
  Serial.println("AHT20 ready!");

  connectWiFi();
  mqtt.setServer(MQTT_BROKER, MQTT_PORT);
  mqtt.setCallback(onMessage);
  mqtt.setBufferSize(512);
  Serial.println("MQTT buffer: " + String(mqtt.getBufferSize()) + " bytes");
  connectMQTT();
}

// =========================
// LOOP
// =========================
void loop() {
  if (!mqtt.connected()) connectMQTT();
  mqtt.loop();

  unsigned long now = millis();

  // Expire peer liveness
  if (node1_online && (now - lastNode1) > PEER_TIMEOUT_MS) node1_online = false;
  if (node2_online && (now - lastNode2) > PEER_TIMEOUT_MS) node2_online = false;
  if (node3_online && (now - lastNode3) > PEER_TIMEOUT_MS) node3_online = false;

  // ── AHT20 readings ──
  sensors_event_t humidity, temp;
  aht.getEvent(&humidity, &temp);

  float temperature = temp.temperature;
  float hum         = humidity.relative_humidity;

  // ── Ambient light ──
  int   light  = analogRead(LIGHT_PIN);
  float lightV = light * (3.3 / 4095.0);

  // ── Validate AHT20 ──
  if (isnan(temperature) || isnan(hum)) {
    Serial.println("AHT20 read failed!");
    delay(2000);
    return;
  }

  // ── Alerts ──
  if (temperature > TEMP_ALERT) {
    String alert = "{\"from\":\"node4\",\"alert\":\"High temperature\",\"value\":"
                   + String(temperature) + "}";
    mqtt.publish(PUB_MSG, alert.c_str());
    Serial.println("Alert sent: High temperature");
  }

  if (light > LIGHT_ALERT) {
    String alert = "{\"from\":\"node4\",\"alert\":\"High light level\",\"value\":"
                   + String(light) + "}";
    mqtt.publish(PUB_MSG, alert.c_str());
    Serial.println("Alert sent: High light");
  }

  Serial.println("------------------------------");
  Serial.print("Temperature:  "); Serial.print(temperature); Serial.println(" C");
  Serial.print("Humidity:     "); Serial.print(hum);         Serial.println(" %");
  Serial.print("Light raw:    "); Serial.println(light);
  Serial.print("Light volts:  "); Serial.print(lightV, 2);   Serial.println(" V");
  Serial.print("Node1 online: "); Serial.println(node1_online ? "YES" : "NO");
  Serial.print("Node2 online: "); Serial.println(node2_online ? "YES" : "NO");
  Serial.print("Node3 online: "); Serial.println(node3_online ? "YES" : "NO");
  Serial.print("Net alerts:   "); Serial.println(network_alerts);

  // ── Build JSON ──
  StaticJsonDocument<1024> doc;
  doc["node"]           = "node4";
  doc["temp"]           = temperature;
  doc["humidity"]       = hum;
  doc["light"]          = light;
  doc["light_v"]        = round(lightV * 100) / 100.0;
  doc["ip"]             = WiFi.localIP().toString();
  doc["rssi"]           = WiFi.RSSI();
  doc["node1_online"]   = node1_online;
  doc["node2_online"]   = node2_online;
  doc["node3_online"]   = node3_online;
  doc["network_alerts"] = network_alerts;

  char buffer[1024];
  serializeJson(doc, buffer);

  mqtt.publish(PUB_DATA, buffer);
  Serial.println("Published: " + String(buffer));

  delay(2000);
}
