#include <WiFi.h>
#include <PubSubClient.h>
#include <DHT.h>
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
const char* PUB_DATA    = "iot/node1/data";
const char* PUB_STATUS  = "iot/network/status";
const char* SUB_COMMAND = "iot/node1/command";
const char* SUB_NODE2   = "iot/node2/data";

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

  if (String(topic) == "iot/node1/command") {
    if (msg == "PING") {
      mqtt.publish(PUB_STATUS, "{\"node\":\"node1\",\"status\":\"alive\"}");
    }
  }

  if (String(topic) == "iot/node2/data") {
    Serial.println("Node 2 update received");
  }
}

// =========================
// MQTT CONNECT
// =========================
void connectMQTT() {
  while (!mqtt.connected()) {
    Serial.print("Connecting to MQTT...");
    if (mqtt.connect(NODE_ID, MQTT_USER, MQTT_PASS)) {  // credentials added
      Serial.println("connected");
      mqtt.subscribe(SUB_COMMAND);
      mqtt.subscribe(SUB_NODE2);
      mqtt.publish(PUB_STATUS, "{\"node\":\"node1\",\"status\":\"online\"}");
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

  // Build JSON
  StaticJsonDocument<256> doc;
  doc["node"]     = "node1";
  doc["temp"]     = temp;
  doc["humidity"] = humidity;
  doc["ozone"]    = ozone;
  doc["ozone_v"]  = round(volt * 100) / 100.0;
  doc["ip"]       = WiFi.localIP().toString();
  doc["rssi"]     = WiFi.RSSI();

  char buffer[256];
  serializeJson(doc, buffer);

  mqtt.publish(PUB_DATA, buffer);
  Serial.println("Published: " + String(buffer));

  delay(2000);
}