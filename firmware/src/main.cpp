// ESP32 garden node: soil moisture + MQ-135 air quality -> OLED + web dashboard.
//
// First boot: the ESP32 opens a WiFi hotspot "GardenMonitor-Setup". Connect to it with
// your phone, pick your WiFi, and fill in the server URL (e.g. http://192.168.1.10:8000)
// and the device API key (DEVICE_API_KEY in the server's .env).
// To re-open setup later: power on, then hold the BOOT button within 3 s.
// (Holding BOOT *while* pressing EN/reset enters flash mode instead; that's normal.)

#include <Arduino.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <Preferences.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <WiFiManager.h>
#include <Wire.h>

// ---------------------------------------------------------------- wiring
// Analog sensors must use ADC1 pins (GPIO 32-39): ADC2 stops working while WiFi is on.
constexpr int PIN_SOIL = 34;
constexpr int PIN_MQ135 = 35;  // through a voltage divider! MQ-135 AO can reach 5 V.
constexpr int PIN_SDA = 21;
constexpr int PIN_SCL = 22;
constexpr int PIN_BOOT_BTN = 0;
constexpr uint8_t OLED_ADDR = 0x3C;  // some modules use 0x3D

// ---------------------------------------------------------------- calibration
// Open the serial monitor (mise run fw:monitor) and note the "soil raw" value with the
// sensor in dry air, then in a glass of water. Capacitive sensors read HIGHER when dry.
constexpr int SOIL_RAW_DRY = 3000;
constexpr int SOIL_RAW_WET = 1200;
// MQ-135 heater warm-up after every power-on. (Brand-new sensors also need a one-time
// 24-48 h burn-in before readings settle.)
constexpr uint32_t MQ135_WARMUP_MS = 60UL * 1000;

constexpr uint32_t DISPLAY_EVERY_MS = 2000;
constexpr const char* SETUP_AP_NAME = "GardenMonitor-Setup";

// ---------------------------------------------------------------- state
Adafruit_SSD1306 display(128, 64, &Wire, -1);
bool displayOk = false;
Preferences prefs;

String serverUrl;  // e.g. http://192.168.1.10:8000
String apiKey;
String deviceId;

uint32_t reportEveryMs = 60UL * 1000;  // overwritten by the server's "interval"
uint32_t nextReportAt = 0;
uint32_t nextDisplayAt = 0;

int soilRaw = 0;
int soilPct = 0;
int airRaw = 0;
String soilStatus = "...";  // labels come from the server so thresholds live in one place
String airLevel = "...";
int lastHttpCode = 0;

bool shouldSaveConfig = false;

// ---------------------------------------------------------------- helpers

int readAveraged(int pin, int samples = 16) {
  long sum = 0;
  for (int i = 0; i < samples; i++) {
    sum += analogRead(pin);
    delay(2);
  }
  return sum / samples;
}

bool airWarmingUp() { return millis() < MQ135_WARMUP_MS; }

void showMessage(const char* line1, const char* line2 = "", const char* line3 = "") {
  Serial.printf("%s %s %s\n", line1, line2, line3);
  if (!displayOk) return;
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0, 0);
  display.println(line1);
  display.setCursor(0, 20);
  display.println(line2);
  display.setCursor(0, 40);
  display.println(line3);
  display.display();
}

void readSensors() {
  soilRaw = readAveraged(PIN_SOIL);
  soilPct = constrain(map(soilRaw, SOIL_RAW_DRY, SOIL_RAW_WET, 0, 100), 0, 100);
  airRaw = readAveraged(PIN_MQ135);
  Serial.printf("soil raw=%d pct=%d | air raw=%d%s\n", soilRaw, soilPct, airRaw,
                airWarmingUp() ? " (warming up)" : "");
}

void drawDashboard() {
  if (!displayOk) return;
  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);

  display.setTextSize(1);
  display.setCursor(0, 0);
  display.print("Soil moisture");

  display.setTextSize(2);
  display.setCursor(0, 11);
  display.printf("%3d%%", soilPct);
  display.setTextSize(1);
  display.setCursor(64, 15);
  display.print(soilStatus);

  display.setCursor(0, 34);
  if (airWarmingUp()) {
    display.printf("Air: warming %lus", (MQ135_WARMUP_MS - millis()) / 1000);
  } else {
    display.printf("Air: %d  %s", airRaw, airLevel.c_str());
  }

  display.drawFastHLine(0, 52, 128, SSD1306_WHITE);
  display.setCursor(0, 55);
  if (WiFi.status() != WL_CONNECTED) {
    display.print("WiFi: reconnecting");
  } else if (lastHttpCode == 200) {
    display.printf("WiFi %ddBm  sent", WiFi.RSSI());
  } else if (lastHttpCode == 401) {
    display.print("Server: bad API key");
  } else if (lastHttpCode == 0) {
    display.printf("WiFi %ddBm", WiFi.RSSI());
  } else {
    display.printf("Server error %d", lastHttpCode);
  }
  display.display();
}

void postReading() {
  if (WiFi.status() != WL_CONNECTED || serverUrl.isEmpty()) return;

  JsonDocument doc;
  doc["device_id"] = deviceId;
  doc["soil_raw"] = soilRaw;
  doc["soil_pct"] = soilPct;
  if (airWarmingUp()) {
    doc["air_raw"] = nullptr;  // dashboard shows "warming up"
  } else {
    doc["air_raw"] = airRaw;
  }
  doc["rssi"] = WiFi.RSSI();
  String body;
  serializeJson(doc, body);

  String url = serverUrl;
  if (url.endsWith("/")) url.remove(url.length() - 1);
  url += "/api/readings";

  HTTPClient http;
  WiFiClient plain;
  WiFiClientSecure tls;
  bool ok;
  if (url.startsWith("https://")) {
    tls.setInsecure();  // no certificate pinning; fine for a hobby node, not for production
    ok = http.begin(tls, url);
  } else {
    ok = http.begin(plain, url);
  }
  if (!ok) {
    lastHttpCode = -1;
    return;
  }
  http.setTimeout(8000);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-API-Key", apiKey);

  lastHttpCode = http.POST(body);
  Serial.printf("POST %s -> %d\n", url.c_str(), lastHttpCode);

  if (lastHttpCode == 200) {
    JsonDocument resp;
    if (deserializeJson(resp, http.getString()) == DeserializationError::Ok) {
      uint32_t interval = resp["interval"] | 60;
      reportEveryMs = constrain(interval, 10u, 3600u) * 1000UL;
      soilStatus = resp["soil_status"] | "?";
      airLevel = resp["air_level"] | "?";
    }
  }
  http.end();
}

// ---------------------------------------------------------------- WiFi + config portal

bool bootButtonHeld() {
  pinMode(PIN_BOOT_BTN, INPUT_PULLUP);
  showMessage("Garden Monitor", "Hold BOOT now to", "re-run WiFi setup");
  uint32_t heldSince = 0;
  for (uint32_t start = millis(); millis() - start < 3000; delay(20)) {
    if (digitalRead(PIN_BOOT_BTN) == LOW) {
      if (!heldSince) heldSince = millis();
      if (millis() - heldSince > 1000) return true;
    } else {
      heldSince = 0;
    }
  }
  return false;
}

void connectWiFi(bool forcePortal) {
  WiFiManager wm;
  WiFiManagerParameter pServer("server", "Server URL (http://IP:8000)", serverUrl.c_str(), 120);
  WiFiManagerParameter pKey("apikey", "Device API key", apiKey.c_str(), 80);
  wm.addParameter(&pServer);
  wm.addParameter(&pKey);
  wm.setSaveConfigCallback([] { shouldSaveConfig = true; });
  wm.setConfigPortalTimeout(300);

  showMessage("WiFi setup:", "connect phone to", SETUP_AP_NAME);
  bool connected = forcePortal ? wm.startConfigPortal(SETUP_AP_NAME) : wm.autoConnect(SETUP_AP_NAME);
  if (!connected) {
    showMessage("WiFi failed", "restarting...");
    delay(3000);
    ESP.restart();
  }

  if (shouldSaveConfig) {
    serverUrl = pServer.getValue();
    apiKey = pKey.getValue();
    serverUrl.trim();
    apiKey.trim();
    prefs.putString("server", serverUrl);
    prefs.putString("apikey", apiKey);
  }
  WiFi.setAutoReconnect(true);
  showMessage("WiFi connected", WiFi.localIP().toString().c_str(), serverUrl.c_str());
  delay(1500);
}

// ---------------------------------------------------------------- Arduino entry points

void setup() {
  Serial.begin(115200);
  delay(200);

  Wire.begin(PIN_SDA, PIN_SCL);
  displayOk = display.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR);
  if (!displayOk) Serial.println("OLED not found - check wiring/address (0x3C or 0x3D)");

  analogReadResolution(12);

  uint8_t mac[6];
  WiFi.macAddress(mac);
  char id[24];
  snprintf(id, sizeof(id), "esp32-%02x%02x%02x", mac[3], mac[4], mac[5]);
  deviceId = id;

  prefs.begin("webiot", false);
  serverUrl = prefs.getString("server", "");
  apiKey = prefs.getString("apikey", "");

  bool forcePortal = bootButtonHeld() || serverUrl.isEmpty();
  connectWiFi(forcePortal);
  Serial.printf("device %s -> %s\n", deviceId.c_str(), serverUrl.c_str());
}

void loop() {
  uint32_t now = millis();

  if ((int32_t)(now - nextDisplayAt) >= 0) {
    readSensors();
    drawDashboard();
    nextDisplayAt = now + DISPLAY_EVERY_MS;
  }

  if ((int32_t)(now - nextReportAt) >= 0) {
    postReading();
    drawDashboard();
    nextReportAt = millis() + reportEveryMs;
  }

  delay(20);
}
