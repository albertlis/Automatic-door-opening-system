#ifndef GATE_HPP
#define GATE_HPP
#include <Arduino.h>
#include "objects.hpp"
#include "constants.hpp"
#include "variables.hpp"

struct sGate {
    //variables
    volatile bool isOpening{false};
    volatile bool isClosing{false};
    volatile bool isOpened{false};
    volatile bool isClosed{false};
    volatile bool isSafetyStop{false};
    volatile bool isOpenButtonClicked{false};
    volatile bool isCloseButtonClicked{false};
    unsigned long startMovingTime{0};

    //constants
    const static uint8_t hourOpen{6}, hourClose{18};
    const static uint8_t absoluteHourClose{22}, absoluteHourOpen{8};
    const static uint8_t lightClose{3}, lightOpen{8};
    const static uint8_t closeSpeed{120}, openSpeed{60};

    //functions
    void openGate();
    void closeGate();
    bool shouldClose() const;
    bool shouldOpen() const;
    bool shouldAbsoluteClose() const;
    bool shouldAbsoluteOpen() const;
    void safetyStop();
    void printInternalState() const;
};

extern sGate gate;

inline bool sGate::shouldOpen() const {
    //Serial.println(F("Should open function"));
    uint8_t counter{0};
    for(uint8_t i{0}; i < lightTableSize; ++i)
        if (lights[i] > lightClose)
            ++counter;
    return ( (date.hour() < hourClose) && (date.hour() >= hourOpen) && (counter == lightTableSize)) ? true : false;
}

inline bool sGate::shouldClose() const {
    //Serial.println(F("Should close function"));
    uint8_t counter{0};
    for(uint8_t i{0}; i < lightTableSize; ++i)
        if (lights[i] < lightClose)
            ++counter;
    return ((date.hour() >= hourClose || date.hour() < hourOpen) && (counter == lightTableSize)) ? true : false;
}

inline bool sGate::shouldAbsoluteClose() const {
    //Serial.println(F("Should close function"));
    return ((date.hour() >= absoluteHourClose) || (date.hour() < hourOpen)) ? true : false;
}

inline bool sGate::shouldAbsoluteOpen() const {
    //Serial.println(F("Should open function"));
    return ((date.hour() < hourClose) && (date.hour() >= absoluteHourOpen)) ? true : false;
}
#endif