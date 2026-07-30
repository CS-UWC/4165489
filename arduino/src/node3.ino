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
#define MQTT_PASS     "yourpassword"  // Replace with the password you set

#define TRIG_PIN     5
#define ECHO_PIN     18
#define REED_DO      19
#define REED_AO      32

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
const char* SUB_NODE1   = "iot/node1/data";
const char* SUB_NODE2   = "iot/node2/data";
const char* SUB_NETWORK = "iot/network/message";

// =========================
// SHARED STATE FROM OTHER NODES
// =========================
float node1_temp     = 0;
float node1_humidity = 0;
int   node1_ozone    = 0;
int   node2_sound    = 0;
bool  node1_online   = false;
bool  node2_online   = false;
int   network_alerts = 0;

// =========================
// HC-SR04 DISTANCE
// =========================
float getDistance() {
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);

  long duration = pulseIn(ECHO_PIN, HIGH, 30000);

  if (duration == 0) return -1;
  return duration * 0.034 / 2;
}

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
  if (String(topic) == "iot/node3/command") {
    if (msg == "PING") {
      mqtt.publish(PUB_STATUS, "{\"node\":\"node3\",\"status\":\"alive\"}");
      Serial.println("PING — responded alive");
    }

    // Dashboard requests full network summary from node3
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
      doc["network_alerts"] = network_alerts;
      char buf[400];
      serializeJson(doc, buf);
      mqtt.publish("iot/network/message", buf);
      Serial.println("Network summary published");
    }

    // Dashboard requests current sensor status
    if (msg == "STATUS") {
      float dist   = getDistance();
      int   reed   = digitalRead(REED_DO);
      int   reed_a = analogRead(REED_AO);
      StaticJsonDocument<300> doc;
      doc["node"]      = "node3";
      doc["distance"]  = dist;
      doc["reed_do"]   = (reed == LOW) ? "magnet" : "clear";
      doc["reed_ao"]   = reed_a;
      doc["requested"] = true;
      char buf[300];
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
      Serial.println("Node1 → temp:" + String(node1_temp) +
                     " humidity:" + String(node1_humidity) +
                     " ozone:" + String(node1_ozone));
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

      // If node2 detects loud sound alert the network
      if (node2_sound > 3000) {
        String alert = "{\"from\":\"node3\",\"alert\":\"Node2 loud sound via node3\",\"value\":"
                       + String(node2_sound) + "}";
        mqtt.publish("iot/network/message", alert.c_str());
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
      mqtt.subscribe(SUB_NODE1);
      mqtt.subscribe(SUB_NODE2);
      mqtt.subscribe(SUB_NETWORK);
      mqtt.publish(PUB_STATUS, "{\"node\":\"node3\",\"status\":\"online\"}");
      mqtt.publish("iot/network/message",
                   "{\"from\":\"node3\",\"event\":\"joined\"}");
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
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  pinMode(REED_DO,  INPUT);

  Serial.println("=== NODE 3 ===");
  Serial.println("HC-SR04 + KY-025 Reed Switch");

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

  // ── HC-SR04 ──
  float distance = getDistance();

  // ── KY-025 Reed Switch ──
  int reed_do  = digitalRead(REED_DO);
  int reed_ao  = analogRead(REED_AO);
  float reed_v = reed_ao * (3.3 / 4095.0);

  bool  magnet   = (reed_do == LOW);
  String prox    = "unknown";

  if (distance < 0) {
    prox = "out_of_range";
  } else if (distance < 10) {
    prox = "very_close";
    // Alert if something very close
    String alert = "{\"from\":\"node3\",\"alert\":\"Object very close\",\"value\":"
                   + String(distance) + "}";
    mqtt.publish("iot/network/message", alert.c_str());
  } else if (distance < 50) {
    prox = "near";
  } else if (distance < 200) {
    prox = "far";
  } else {
    prox = "very_far";
  }

  // Alert if magnet detected
  if (magnet) {
    mqtt.publish("iot/network/message",
                 "{\"from\":\"node3\",\"alert\":\"Magnetic field detected\"}");
  }

  Serial.println("------------------------------");
  Serial.print("Distance:     ");
  if (distance < 0) Serial.println("OUT OF RANGE");
  else { Serial.print(distance); Serial.println(" cm"); }
  Serial.println("Proximity:    " + prox);
  Serial.print("Reed:         ");
  Serial.println(magnet ? "MAGNET DETECTED" : "clear");
  Serial.print("Reed raw:     ");
  Serial.println(reed_ao);

  // Build JSON
  StaticJsonDocument<350> doc;
  doc["node"]           = "node3";
  doc["distance"]       = distance < 0 ? 0 : distance;
  doc["proximity"]      = prox;
  doc["reed_do"]        = magnet ? "magnet" : "clear";
  doc["reed_ao"]        = reed_ao;
  doc["reed_v"]         = round(reed_v * 100) / 100.0;
  doc["ip"]             = WiFi.localIP().toString();
  doc["rssi"]           = WiFi.RSSI();
  doc["node1_online"]   = node1_online;
  doc["node2_online"]   = node2_online;
  doc["node1_temp"]     = node1_temp;
  doc["node2_sound"]    = node2_sound;
  doc["network_alerts"] = network_alerts;

  char buffer[350];
  serializeJson(doc, buffer);

  mqtt.publish(PUB_DATA, buffer);
  Serial.println("Published: " + String(buffer));

  delay(2000);
}
