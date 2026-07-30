#include <WiFi.h>
#include <PubSubClient.h>
#include <DHT.h>
#include <ArduinoJson.h>

// =========================
// CONFIG
// =========================
#define WIFI_SSID     "WifiNAme" // Replace with name of your Wifi
#define WIFI_PASS     "WifiPass"  // Replace with the password of your Wifi
#define MQTT_BROKER   "xxx.xxx.X.X"  // Replace with Rock Pi IP
#define MQTT_PORT     xxxx
#define MQTT_USER     "esp32user"  // Replace with MQTT user name for ESP32
#define MQTT_PASS     "yourpassword"  // Replace with the password you set

#define DHT_PIN      4
#define DHT_TYPE     DHT11
#define MQ131_PIN    34

#define NODE_ID      "node1"

// =========================
// OBJECTS
// =========================
DHT dht(DHT_PIN, DHT_TYPE);
WiFiClient espClient;
PubSubClient mqtt(espClient);

// =========================
// TOPICS
// =========================
const char* PUB_DATA    = "iot/node1/data";
const char* PUB_STATUS  = "iot/network/status";
const char* SUB_COMMAND = "iot/node1/command";
const char* SUB_NODE2   = "iot/node2/data";
const char* SUB_NODE3   = "iot/node3/data";
const char* SUB_NETWORK = "iot/network/message";

// =========================
// SHARED STATE FROM OTHER NODES
// =========================
int   node2_sound    = 0;
float node3_distance = 0;
bool  node3_magnet   = false;
bool  node2_online   = false;
bool  node3_online   = false;
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
// MQTT RECEIVE
// =========================
void onMessage(char* topic, byte* payload, unsigned int length) {
  String msg = "";
  for (int i = 0; i < length; i++) msg += (char)payload[i];

  Serial.println("MSG from [" + String(topic) + "]: " + msg);

  // ── Commands from dashboard ──
  if (String(topic) == "iot/node1/command") {
    if (msg == "PING") {
      mqtt.publish(PUB_STATUS, "{\"node\":\"node1\",\"status\":\"alive\"}");
      Serial.println("PING — responded alive");
    }
    if (msg == "STATUS") {
      float temp     = dht.readTemperature();
      float humidity = dht.readHumidity();
      int   ozone    = analogRead(MQ131_PIN);
      StaticJsonDocument<256> doc;
      doc["node"]      = "node1";
      doc["temp"]      = temp;
      doc["humidity"]  = humidity;
      doc["ozone"]     = ozone;
      doc["requested"] = true;
      char buf[256];
      serializeJson(doc, buf);
      mqtt.publish(PUB_DATA, buf);
    }
  }

  // ── Data from Node 2 ──
  if (String(topic) == "iot/node2/data") {
    StaticJsonDocument<300> doc;
    DeserializationError err = deserializeJson(doc, msg);
    if (!err) {
      node2_sound  = doc["sound"] | 0;
      node2_online = true;
      Serial.println("Node2 → sound:" + String(node2_sound));

      // React if sound is very loud
      if (node2_sound > 3000) {
        String alert = "{\"from\":\"node1\",\"alert\":\"Node2 loud sound detected\",\"value\":"
                       + String(node2_sound) + "}";
        mqtt.publish("iot/network/message", alert.c_str());
        Serial.println("Alert sent: Node2 loud sound");
      }
    }
  }

  // ── Data from Node 3 ──
  if (String(topic) == "iot/node3/data") {
    StaticJsonDocument<350> doc;
    DeserializationError err = deserializeJson(doc, msg);
    if (!err) {
      node3_distance = doc["distance"] | 0.0;
      node3_magnet   = (String(doc["reed_do"] | "") == "magnet");
      node3_online   = true;
      Serial.println("Node3 → distance:" + String(node3_distance) +
                     "cm magnet:" + String(node3_magnet ? "YES" : "NO"));

      // React if object is very close
      if (node3_distance > 0 && node3_distance < 10) {
        String alert = "{\"from\":\"node1\",\"alert\":\"Node3 object very close\",\"value\":"
                       + String(node3_distance) + "}";
        mqtt.publish("iot/network/message", alert.c_str());
        Serial.println("Alert sent: Node3 object close");
      }

      // React if magnet detected
      if (node3_magnet) {
        mqtt.publish("iot/network/message",
                     "{\"from\":\"node1\",\"alert\":\"Node3 magnetic field via node1\"}");
        Serial.println("Alert sent: Node3 magnet detected");
      }
    }
  }

  // ── Network-wide messages ──
  if (String(topic) == "iot/network/message") {
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
      mqtt.subscribe(SUB_NODE2);
      mqtt.subscribe(SUB_NODE3);
      mqtt.subscribe(SUB_NETWORK);
      mqtt.publish(PUB_STATUS, "{\"node\":\"node1\",\"status\":\"online\"}");
      mqtt.publish("iot/network/message",
                   "{\"from\":\"node1\",\"event\":\"joined\"}");
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
  dht.begin();

  Serial.println("=== NODE 1 ===");
  Serial.println("DHT11 + MQ-131");
  Serial.println("MQ-131 warming up — 60 seconds...");

  for (int i = 60; i > 0; i--) {
    Serial.print("Warmup: ");
    Serial.print(i);
    Serial.println("s remaining");
    delay(1000);
  }

  Serial.println("Warmup done!");

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

  float temp     = dht.readTemperature();
  float humidity = dht.readHumidity();
  int   ozone    = analogRead(MQ131_PIN);
  float volt     = ozone * (3.3 / 4095.0);

  if (isnan(temp) || isnan(humidity)) {
    Serial.println("DHT11 read failed — check wiring on GPIO 4!");
    delay(2000);
    return;
  }

  // Alert if temp is high
  if (temp > 35.0) {
    String alert = "{\"from\":\"node1\",\"alert\":\"High temperature\",\"value\":"
                   + String(temp) + "}";
    mqtt.publish("iot/network/message", alert.c_str());
    Serial.println("Alert sent: High temperature");
  }

  // Alert if ozone is high
  if (ozone > 800) {
    String alert = "{\"from\":\"node1\",\"alert\":\"High ozone level\",\"value\":"
                   + String(ozone) + "}";
    mqtt.publish("iot/network/message", alert.c_str());
    Serial.println("Alert sent: High ozone");
  }

  Serial.println("------------------------------");
  Serial.print("Temperature:  "); Serial.print(temp);     Serial.println(" C");
  Serial.print("Humidity:     "); Serial.print(humidity); Serial.println(" %");
  Serial.print("Ozone raw:    "); Serial.println(ozone);
  Serial.print("Ozone volts:  "); Serial.print(volt);     Serial.println(" V");
  Serial.print("Node2 online: "); Serial.println(node2_online ? "YES" : "NO");
  Serial.print("Node3 online: "); Serial.println(node3_online ? "YES" : "NO");
  Serial.print("Node2 sound:  "); Serial.println(node2_sound);
  Serial.print("Node3 dist:   "); Serial.print(node3_distance); Serial.println(" cm");
  Serial.print("Node3 magnet: "); Serial.println(node3_magnet ? "YES" : "NO");
  Serial.print("Net alerts:   "); Serial.println(network_alerts);

  // Build JSON
  StaticJsonDocument<350> doc;
  doc["node"]           = "node1";
  doc["temp"]           = temp;
  doc["humidity"]       = humidity;
  doc["ozone"]          = ozone;
  doc["ozone_v"]        = round(volt * 100) / 100.0;
  doc["ip"]             = WiFi.localIP().toString();
  doc["rssi"]           = WiFi.RSSI();
  doc["node2_online"]   = node2_online;
  doc["node3_online"]   = node3_online;
  doc["node2_sound"]    = node2_sound;
  doc["node3_distance"] = node3_distance;
  doc["node3_magnet"]   = node3_magnet;
  doc["network_alerts"] = network_alerts;

  char buffer[350];
  serializeJson(doc, buffer);

  mqtt.publish(PUB_DATA, buffer);
  Serial.println("Published: " + String(buffer));

  delay(2000);
}
