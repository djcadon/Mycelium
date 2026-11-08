#include <ArduinoGraphics.h>
#include <Arduino_LED_Matrix.h>
#include <Arduino_RouterBridge.h>

Arduino_LED_Matrix matrix;
String pendingText;

bool queueChat(String text) {
  if (text.length() == 0 || text.length() > 13) {
    return false;
  }

  pendingText = text;
  return true;
}

void setup() {
  matrix.begin();
  matrix.textFont(Font_4x6);
  matrix.textScrollSpeed(80);

  Bridge.begin();
  Bridge.provide_safe("mycelium_show_chat", queueChat);
}

void loop() {
  if (pendingText.length() == 0) {
    return;
  }

  String text = pendingText;
  pendingText = "";

  matrix.beginText(0, 1, 127, 0, 0);
  matrix.print("    ");
  matrix.print(text);
  matrix.print("    ");
  matrix.endText(SCROLL_LEFT);
  matrix.clear();
}