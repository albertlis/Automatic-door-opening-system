#ifndef FUNCTIONS_HPP
#define FUNCTIONS_HPP
#include <Arduino.h>
#include "variables.hpp"
#include "Gate.hpp"
#include "pins.hpp"
#include "switches.hpp"

void openingEdgeISR();
void closingEdgeISR();
float checkBatteryVoltage();
void setupPins();
void setupInterrupts();
void setupRTC();
void setupLightSensor();
void readGatePosition();
void setupEEPROM();
void readLight();
void printInfo();
void compensateRtcDrift();
// void switchSummerWinterTime();
void controlLeds();
#ifdef PRINT
void printDate();
void printLightIntensivity();
#endif
#ifdef DEBUG
void printRtcSqwMode();
#endif
#ifdef LOG_MOVES
void logInfo(char move);
void writeLogIntoEeprom();
#endif
#ifdef LOG_LIGHT
void logLightInfo();
void writeLightLogIntoEeprom();
#endif
#ifdef PRINT_LOG_LIGHT
void printLightLogFromEeprom();
#endif
#ifdef PRINT_LOG_MOVES
void printLogMovesFromEeprom();
#endif

inline float mapfloat(long x, long in_min, long in_max, long out_min, long out_max) {
    return (float)(x - in_min) * (out_max - out_min) / (float)(in_max - in_min) + out_min;
}

inline float checkBatteryVoltage() {
    #ifdef DEBUG
    Serial.print(F("Battery voltage: "));
    Serial.println(mapfloat(analogRead(batteryVoltagePin), 0, 1023, 0, 5));
    #endif
    return mapfloat(analogRead(batteryVoltagePin), 0, 1023, 0, 5);
}

#endif