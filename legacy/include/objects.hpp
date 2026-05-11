#ifndef OBJECTS_HPP
#define OBJECTS_HPP

#include <Arduino.h>
#include <BH1750FVI.h>
#include <Servo.h>
#include <RTClib.h>
#include "Led.hpp"
#include "Log.hpp"
#include "LogLight.hpp"
#include "Gate.hpp"
#include "switches.hpp"

extern BH1750FVI myBH1750;
extern RTC_DS1307 RTC;
extern Servo servo;
// extern sGate gate;
extern cLed ledGreen, ledRed, ledYellow;
#ifdef LOG_MOVES
extern sLog logs;
#endif
#ifdef LOG_LIGHT
extern LogLight logLight;
#endif

#endif