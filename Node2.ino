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
const char* SUB_NODE1   = "iot/node1/data";
const char* SUB_NODE3   = "iot/node3/data";

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

  if (String(topic) == "iot/node2/command") {
    if (msg == "PING") {
      mqtt.publish(PUB_STATUS, "{\"node\":\"node2\",\"status\":\"alive\"}");
    }
  }

  if (String(topic) == "iot/node1/data") {
    Serial.println("Node 1 update received");
  }

  if (String(topic) == "iot/node3/data") {
    Serial.println("Node 3 update received");
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
      mqtt.publish(PUB_STATUS, "{\"node\":\"node2\",\"status\":\"online\"}");
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

  int sound      = analogRead(SOUND_PIN);
  int light      = analogRead(LIGHT_PIN);
  float sound_v  = sound * (3.3 / 4095.0);
  float light_v  = light * (3.3 / 4095.0);

  // Build JSON
  StaticJsonDocument<256> doc;
  doc["node"]    = "node2";
  doc["sound"]   = sound;
  doc["sound_v"] = round(sound_v * 100) / 100.0;
  doc["light"]   = light;
  doc["light_v"] = round(light_v * 100) / 100.0;
  doc["ip"]      = WiFi.localIP().toString();
  doc["rssi"]    = WiFi.RSSI();

  char buffer[256];
  serializeJson(doc, buffer);

  mqtt.publish(PUB_DATA, buffer);
  Serial.println("Published: " + String(buffer));

  delay(2000);
}