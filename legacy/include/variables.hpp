#ifndef VARIABLES_HPP
#define VARIABLES_HPP

#include <Arduino.h>
#ifndef UNIT_TEST
#include <RTClib.h>
#else
// Minimal DateTime stub for unit tests (enough for logic evaluation)
class DateTime {
public:
	DateTime(): _t(0) {}
	explicit DateTime(uint32_t t): _t(t) {}
	uint8_t hour() const { return (uint8_t)((_t / 3600UL) % 24UL); }
	uint8_t minute() const { return (uint8_t)((_t / 60UL) % 60UL); }
	uint8_t second() const { return (uint8_t)(_t % 60UL); }
	uint8_t month() const { return 1; }
	uint8_t day() const { return 1; }
	uint16_t year() const { return 2024; }
	uint32_t unixtime() const { return _t; }
private:
	uint32_t _t; // seconds from epoch for tests
};
#endif
#include "constants.hpp"
#include "switches.hpp"

extern float light;
extern float lights[];
extern DateTime date;
extern volatile unsigned long millisNow;
extern volatile unsigned long lastLightLogMillis;
extern volatile unsigned long lastOpeningEdgeMillis;
extern volatile unsigned long lastClosingEdgeMillis;
extern uint16_t eepromAddress;
extern uint16_t eepromCounter;
extern bool compensateRtcDriftSwitch;
// extern bool isSummerTime;

#endif