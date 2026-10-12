#ifndef LED_HPP
#define LED_HPP
#include <Arduino.h>

class cLed {
    private:
        const uint8_t pin;
        uint32_t previousTime; // ms timestamp of last toggle
        bool isTurnedOn;       // logical steady state (used outside blinking)
    public:
        bool shouldBlink;

        cLed(uint8_t p) : pin(p), previousTime(0), isTurnedOn(false), shouldBlink(false) {}
        void turnOn();
        void turnOff();
        void blink(uint16_t time);
        void stopBlinking();
};

inline void cLed::turnOn() {
    digitalWrite(pin, HIGH);
    isTurnedOn = true;
}

inline void cLed::turnOff() {
    digitalWrite(pin, LOW);
    isTurnedOn = false;
}

inline void cLed::stopBlinking() {
    // Ensure LED returns to its last commanded steady state (on/off)
    digitalWrite(pin, isTurnedOn ? HIGH : LOW);
}

#endif