#ifndef LOG_HPP
#define LOG_HPP
#include <Arduino.h>
#include "switches.hpp"

#ifdef LOG_MOVES
struct sLog
{
    uint8_t hour{0};
    uint8_t minute{0};
    char move{'\0'}; //O - open C - close
};
#endif

#endif