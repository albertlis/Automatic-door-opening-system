#ifndef FUNCTIONS_HPP
#define FUNCTIONS_HPP
#include <Arduino.h>
#include "variables.hpp"
#include "Gate.hpp"
#include "Log.hpp"
#include "pins.hpp"
#include "switches.hpp"

void openingEdgeISR();
void closingEdgeISR();
void handleOpeningEdge();
void handleClosingEdge();
float checkBatteryVoltage();
void setupPins();
void setupInterrupts();
void setupRTC();
void setupLightSensor();
void readGatePosition();
void setupEEPROM();
void readLight();
void processLightSampling(); // non-blocking replacement for readLight
bool isLightSamplingFinished();
void startLightSampling();
void printInfo();
void compensateRtcDrift();
void processRtcDrift(); // non-blocking drift compensation step
bool isRtcDriftInProgress();
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
void logInfo(GateMove move);
void writeLogIntoEeprom();
#endif
#ifdef LOG_LIGHT
void logLightInfo();
void writeLightLogIntoEeprom();
void startLightLogging();
void processLightLogging();
bool isLightLoggingFinished();
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
    int raw = analogRead(batteryVoltagePin); // single read (could be averaged if needed)
    float v = mapfloat(raw, 0, 1023, 0, 5);
    #ifdef DEBUG
    Serial.print(F("Battery voltage: "));
    Serial.println(v);
    #endif
    return v;
}

#endif