#ifndef LOGLIGHT_HPP
#define LOGLIGHT_HPP
#include <Arduino.h>
#include "switches.hpp"

#ifdef LOG_LIGHT
struct LogLight {
    uint16_t hour : 5, minute : 6, lightDividedBy2 : 5;
};
#endif

#endif