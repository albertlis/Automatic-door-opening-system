#ifndef PINS_HPP
#define PINS_HPP
#include <Arduino.h>

const constexpr uint8_t servoPin{9};
const constexpr uint8_t openingEdgePin{2}, closingEdgePin{3};
const constexpr uint8_t openButtonPin{7}, closeButtonPin{8};
const constexpr uint8_t batteryVoltagePin{A0};
const constexpr uint8_t ledGreenPin{4}, ledRedPin{12}, ledYellowPin{13};

#endif