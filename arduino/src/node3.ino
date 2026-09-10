#include <DHT.h>

// =========================
// NODE 3 — Arduino Nano
// HC-SR04 + KY-025 Reed Switch
// Communicates via USB Serial to Rock Pi
// =========================

#define TRIG_PIN   5
#define ECHO_PIN   6
#define REED_DO    7
#define REED_AO    A0

void setup() {
  Serial.begin(9600);
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  pinMode(REED_DO,  INPUT);
  Serial.println("NODE3 READY");
}

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

void loop() {
  float distance = getDistance();
  int   reed_do  = digitalRead(REED_DO);
  int   reed_ao  = analogRead(REED_AO);
  bool magnet = (reed_do == HIGH);;

  String prox = "unknown";
  if (distance < 0)        prox = "out_of_range";
  else if (distance < 10)  prox = "very_close";
  else if (distance < 50)  prox = "near";
  else if (distance < 200) prox = "far";
  else                     prox = "very_far";

  // Serial output format
  Serial.print("NODE=3;");
  Serial.print("DIST=");   Serial.print(distance);
  Serial.print(";PROX=");  Serial.print(prox);
  Serial.print(";REED=");  Serial.print(magnet ? "magnet" : "clear");
  Serial.print(";REEDAO="); Serial.print(reed_ao);
  Serial.println(";");

  delay(2000);
}
