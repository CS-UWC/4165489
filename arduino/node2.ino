#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

// =========================
// CONFIG
// =========================
#define WIFI_SSID     "WifiNAme" // Replace with name of your Wifi
#define WIFI_PASS     "WifiPass"  // Replace with the password of your Wifi
#define MQTT_BROKER   "xxx.xxx.X.X"  // Replace with Rock Pi IP
#define MQTT_PORT     xxxx
#define MQTT_USER     "esp32user"  // Replace with MQTT user name for ESP32

#define MIC_PIN      35   // microphone analog out
#define RED_PIN      27   // KY-016 red
#define GREEN_PIN    26   // KY-016 green
#define BLUE_PIN     25   // KY-016 blue

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
const char* SUB_NETWORK = "iot/network/message";

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
// LED HELPER
// KY-016 is common anode — values are inverted
// =========================
void setColor(int r, int g, int b) {
  analogWrite(RED_PIN,   255 - r);
  analogWrite(GREEN_PIN, 255 - g);
  analogWrite(BLUE_PIN,  255 - b);
}

void ledOff() {
  setColor(0, 0, 0);
}

// =========================
// SOUND LEVEL → LED COLOR
// Green  = quiet      (raw < 1500)
// Yellow = medium     (raw 1500–3000)
// Red    = loud       (raw > 3000)
// Blue   = alert from another node
// Purple = MQTT disconnected
// =========================
void updateLED(int sound) {
  if (sound > 3000) {
    setColor(255, 0, 0);    // red — loud
  } else if (sound > 1500) {
    setColor(255, 165, 0);  // yellow — medium
  } else {
    setColor(0, 255, 0);    // green — quiet
  }
}

// =========================
// WIFI
// =========================
void connectWiFi() {
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("Connecting to WiFi");
  setColor(0, 0, 255);  // blue while connecting
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nWiFi connected: " + WiFi.localIP().toString());
  setColor(0, 255, 0);  // green when connected
  delay(500);
  ledOff();
}

// =========================
// MQTT RECEIVE
// =========================
void onMessage(char* topic, byte* payload, unsigned int length) {
  String msg = "";
  for (int i = 0; i < length; i++) msg += (char)payload[i];

  Serial.println("MSG from [" + String(topic) + "]: " + msg);

  // Commands from dashboard
  if (String(topic) == "iot/node2/command") {
    if (msg == "PING") {
      mqtt.publish(PUB_STATUS, "{\"node\":\"node2\",\"status\":\"alive\"}");
      // Flash white on PING
      setColor(255, 255, 255);
      delay(200);
      ledOff();
    }
    if (msg == "STATUS") {
      int sound = analogRead(MIC_PIN);
      StaticJsonDocument<200> doc;
      doc["node"]      = "node2";
      doc["sound"]     = sound;
      doc["requested"] = true;
      char buf[200];
      serializeJson(doc, buf);
      mqtt.publish(PUB_DATA, buf);
    }
  }

  // Data from Node 1
  if (String(topic) == "iot/node1/data") {
    StaticJsonDocument<300> doc;
    DeserializationError err = deserializeJson(doc, msg);
    if (!err) {
      node1_temp     = doc["temp"]     | 0.0;
      node1_humidity = doc["humidity"] | 0.0;
      node1_ozone    = doc["ozone"]    | 0;
      node1_online   = true;

      Serial.println("Node1 → temp:" + String(node1_temp) +
                     " humidity:" + String(node1_humidity) +
                     " ozone:" + String(node1_ozone));

      // Flash blue briefly when node1 data arrives
      setColor(0, 0, 255);
      delay(100);

      // Alert if node1 temp is high
      if (node1_temp > 35.0) {
        String alert = "{\"from\":\"node2\",\"alert\":\"Node1 high temperature\",\"value\":"
                       + String(node1_temp) + "}";
        mqtt.publish("iot/network/message", alert.c_str());
        Serial.println("Alert sent: Node1 high temp");
        // Flash red rapidly for temp alert
        for (int i = 0; i < 3; i++) {
          setColor(255, 0, 0);
          delay(150);
          ledOff();
          delay(150);
        }
      }

      // Alert if node1 ozone is high
      if (node1_ozone > 800) {
        String alert = "{\"from\":\"node2\",\"alert\":\"Node1 high ozone\",\"value\":"
                       + String(node1_ozone) + "}";
        mqtt.publish("iot/network/message", alert.c_str());
        Serial.println("Alert sent: Node1 high ozone");
        // Flash purple for ozone alert
        for (int i = 0; i < 3; i++) {
          setColor(128, 0, 128);
          delay(150);
          ledOff();
          delay(150);
        }
      }
    }
  }

  // Data from Node 3
  if (String(topic) == "iot/node3/data") {
    StaticJsonDocument<256> doc;
    DeserializationError err = deserializeJson(doc, msg);
    if (!err) {
      node3_uptime = doc["uptime"] | 0;
      node3_online = true;
      Serial.println("Node3 → uptime:" + String(node3_uptime) + "s");
    }
  }

  // Network-wide messages
  if (String(topic) == "iot/network/message") {
    Serial.println("NETWORK MSG: " + msg);
    // Flash cyan on any network message
    setColor(0, 255, 255);
    delay(150);
  }
}

// =========================
// MQTT CONNECT
// =========================
void connectMQTT() {
  setColor(128, 0, 128);  // purple while connecting to MQTT
  while (!mqtt.connected()) {
    Serial.print("Connecting to MQTT...");
    if (mqtt.connect(NODE_ID, MQTT_USER, MQTT_PASS)) {
      Serial.println("connected");
      mqtt.subscribe(SUB_COMMAND);
      mqtt.subscribe(SUB_NODE1);
      mqtt.subscribe(SUB_NODE3);
      mqtt.subscribe(SUB_NETWORK);
      mqtt.publish(PUB_STATUS, "{\"node\":\"node2\",\"status\":\"online\"}");
      mqtt.publish("iot/network/message", "{\"from\":\"node2\",\"event\":\"joined\"}");
      setColor(0, 255, 0);  // green when connected
      delay(500);
      ledOff();
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
  pinMode(RED_PIN,   OUTPUT);
  pinMode(GREEN_PIN, OUTPUT);
  pinMode(BLUE_PIN,  OUTPUT);
  ledOff();

  // Startup flash — white
  setColor(255, 255, 255);
  delay(300);
  ledOff();

  connectWiFi();
  mqtt.setServer(MQTT_BROKER, MQTT_PORT);
  mqtt.setCallback(onMessage);
  connectMQTT();
}

// =========================
// LOOP
// =========================
void loop() {
  if (!mqtt.connected()) {
    setColor(128, 0, 128);  // purple when disconnected
    connectMQTT();
  }
  mqtt.loop();

  int   sound   = analogRead(MIC_PIN);
  float sound_v = sound * (3.3 / 4095.0);

  // Update LED based on sound level
  updateLED(sound);

  // Build JSON
  StaticJsonDocument<300> doc;
  doc["node"]         = "node2";
  doc["sound"]        = sound;
  doc["sound_v"]      = round(sound_v * 100) / 100.0;
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
