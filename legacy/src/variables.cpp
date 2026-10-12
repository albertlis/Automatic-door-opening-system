#include "variables.hpp"

float light;
float lights[lightTableSize];
DateTime date;
volatile unsigned long millisNow;
volatile unsigned long lastLightLogMillis{0};
volatile unsigned long lastOpeningEdgeMillis{0};
volatile unsigned long lastClosingEdgeMillis{0};
uint16_t eepromAddress{eepromCounterAddress + sizeof(uint16_t)};
uint16_t eepromCounter{0};
bool compensateRtcDriftSwitch{false};
// bool isSummerTime{false};