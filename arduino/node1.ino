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

#define DHT_PIN       4
#define DHT_TYPE      DHT11
#define MQ131_PIN     34

#define NODE_ID       "node1"

// =========================
// OBJECTS
// =========================
DHT dht(DHT_PIN, DHT_TYPE);
WiFiClient espClient;
PubSubClient mqtt(espClient);

// =========================
// TOPICS
// =========================
const char* PUB_DATA      = "iot/node1/data";
const char* PUB_STATUS    = "iot/network/status";
const char* PUB_MESSAGE   = "iot/node1/message";   // node1 sends messages to network
const char* SUB_COMMAND   = "iot/node1/command";   // receive commands from dashboard
const char* SUB_NODE2     = "iot/node2/data";      // listen to node2 sensor data
const char* SUB_NODE3     = "iot/node3/data";      // listen to node3 data
const char* SUB_NETWORK   = "iot/network/message"; // listen to all network messages

// =========================
// SHARED STATE FROM OTHER NODES
// =========================
float node2_sound  = 0;
float node2_light  = 0;
int   node3_uptime = 0;
bool  node2_online = false;
bool  node3_online = false;

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
  if (String(topic) == "iot/node1/command") {
    if (msg == "PING") {
      mqtt.publish(PUB_STATUS, "{\"node\":\"node1\",\"status\":\"alive\"}");
      Serial.println("PING received — responded alive");
    }
    if (msg == "STATUS") {
      // Publish current readings on demand
      float temp     = dht.readTemperature();
      float humidity = dht.readHumidity();
      int   ozone    = analogRead(MQ131_PIN);
      StaticJsonDocument<200> doc;
      doc["node"]     = "node1";
      doc["temp"]     = temp;
      doc["humidity"] = humidity;
      doc["ozone"]    = ozone;
      doc["requested"] = true;
      char buf[200];
      serializeJson(doc, buf);
      mqtt.publish(PUB_DATA, buf);
    }
  }

  // ── Data from Node 2 ──
  if (String(topic) == "iot/node2/data") {
    StaticJsonDocument<256> doc;
    DeserializationError err = deserializeJson(doc, msg);
    if (!err) {
      node2_sound  = doc["sound"]  | 0.0;
      node2_light  = doc["light"]  | 0.0;
      node2_online = true;
      Serial.println("Node2 → sound:" + String(node2_sound) + " light:" + String(node2_light));

      // React: if sound is very loud notify the network
      if (node2_sound > 3000) {
        String alert = "{\"from\":\"node1\",\"alert\":\"Node2 high sound detected\",\"value\":" + String(node2_sound) + "}";
        mqtt.publish("iot/network/message", alert.c_str());
        Serial.println("Alert sent: Node2 high sound");
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
      mqtt.subscribe(SUB_NODE2);
      mqtt.subscribe(SUB_NODE3);
      mqtt.subscribe(SUB_NETWORK);
      mqtt.publish(PUB_STATUS, "{\"node\":\"node1\",\"status\":\"online\"}");
      // Announce to the network
      mqtt.publish("iot/network/message", "{\"from\":\"node1\",\"event\":\"joined\"}");
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
    Serial.println("DHT read failed — check wiring!");
    delay(2000);
    return;
  }

  // Build JSON — include awareness of other nodes
  StaticJsonDocument<300> doc;
  doc["node"]        = "node1";
  doc["temp"]        = temp;
  doc["humidity"]    = humidity;
  doc["ozone"]       = ozone;
  doc["ozone_v"]     = round(volt * 100) / 100.0;
  doc["ip"]          = WiFi.localIP().toString();
  doc["rssi"]        = WiFi.RSSI();
  doc["node2_online"] = node2_online;
  doc["node3_online"] = node3_online;

  char buffer[300];
  serializeJson(doc, buffer);

  mqtt.publish(PUB_DATA, buffer);
  Serial.println("Published: " + String(buffer));

  delay(2000);
}
