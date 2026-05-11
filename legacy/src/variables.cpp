#include "variables.hpp"

float light;
float lights[lightTableSize];
DateTime date;
unsigned long millisNow;
uint16_t eepromAddress{eepromCounterAddress + sizeof(uint16_t)};
uint16_t eepromCounter{0};
bool compensateRtcDriftSwitch{false};
// bool isSummerTime{false};