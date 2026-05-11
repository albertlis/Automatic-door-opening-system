#ifndef CONSTANTS_HPP
#define CONSTANTS_HPP
#include <Arduino.h>
#include "switches.hpp"
#include "Log.hpp"
#include "LogLight.hpp"

const constexpr uint16_t checkDelay{2000};
const constexpr uint16_t maxMovingTime{21000};
const constexpr float batteryDischargeVoltage{2.05};
const constexpr uint8_t eepromCounterAddress{0};
const constexpr uint8_t lightTableSize{5};
const constexpr uint32_t logLightInterval{300000};
const constexpr uint8_t secondsDriftOffset{4};
#ifdef LOG_MOVES
const constexpr uint8_t addressStep{sizeof(sLog)};
#endif
#ifdef LOG_LIGHT
const constexpr uint8_t addressStep{sizeof(LogLight)};
#endif

#endif