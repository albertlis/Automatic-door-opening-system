#include "Led.hpp"

void cLed::blink(uint16_t time) {
    if (millis() - previousTime > time) {
        previousTime = millis();
        digitalWrite(pin, !digitalRead(pin));
    }
}