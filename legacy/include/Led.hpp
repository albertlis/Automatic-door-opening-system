#ifndef LED_HPP
#define LED_HPP
#include <Arduino.h>

class cLed {
    private:
        const uint8_t pin;
        uint32_t previousTime;
        bool isTurnedOn;
    public:
        bool shouldBlink;

        cLed(uint8_t p) : pin(p), isTurnedOn(false), shouldBlink(false) {}
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
    if(!isTurnedOn)
        digitalWrite(pin, LOW);
}

#endif