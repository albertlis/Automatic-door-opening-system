#ifndef CONSTANTS_HPP
#define CONSTANTS_HPP
#include <Arduino.h>
#include "switches.hpp"
#include "Log.hpp"
#include "LogLight.hpp"

constexpr uint16_t checkDelay{2000};
constexpr uint16_t maxMovingTime{21000};
constexpr float batteryDischargeVoltage{2.05};
constexpr uint8_t eepromCounterAddress{0};
constexpr uint8_t lightTableSize{5};
constexpr uint32_t logLightInterval{300000};
constexpr uint8_t secondsDriftOffset{4};
constexpr uint16_t edgeDebounceMs{30};
#if defined(LOG_MOVES) && defined(LOG_LIGHT)
#error "LOG_MOVES and LOG_LIGHT cannot both be defined simultaneously (addressStep conflict)."
#endif
#ifdef LOG_MOVES
constexpr uint8_t addressStep{sizeof(sLog)};
#endif
#ifdef LOG_LIGHT
constexpr uint8_t addressStep{sizeof(LogLight)};
#endif

#endif