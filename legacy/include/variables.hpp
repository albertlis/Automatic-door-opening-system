#ifndef VARIABLES_HPP
#define VARIABLES_HPP

#include <Arduino.h>
#include <RTClib.h>
#include "constants.hpp"
#include "switches.hpp"

extern float light;
extern float lights[];
extern DateTime date;
extern unsigned long millisNow;
extern uint16_t eepromAddress;
extern uint16_t eepromCounter;
extern bool compensateRtcDriftSwitch;
// extern bool isSummerTime;

#endif