#include "Led.hpp"

void cLed::blink(uint16_t time) {
    if (millis() - previousTime > time) {
        previousTime = millis();
        // Toggle pin state
        bool newState = !digitalRead(pin);
        digitalWrite(pin, newState);
        // During blinking we don't treat transitional HIGH as steady on,
        // keep isTurnedOn false so stopBlinking() can restore intended steady state.
    }
}