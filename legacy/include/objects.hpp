#ifndef OBJECTS_HPP
#define OBJECTS_HPP

#include <Arduino.h>
#ifndef UNIT_TEST
#include <BH1750FVI.h>
#include <Servo.h>
#include <RTClib.h>
#else
// Lightweight stubs for unit testing logic (no hardware interaction)
class BH1750FVI {
public:
	BH1750FVI(uint8_t=0, uint8_t=0, float=0, uint8_t=0) {}
	bool begin() { return true; }
	float readLightLevel() { return 10.0f; } // default dummy value
};
class RTC_DS1307 {
	DateTime _now;
public:
	bool begin() { return true; }
	DateTime now() { return _now; }
	void adjust(const DateTime &dt) { _now = dt; }
	void writeSqwPinMode(int) {}
	int readSqwPinMode() { return 0; }
};
class Servo {
	bool _attached{false};
public:
	void attach(uint8_t){ _attached=true; }
	void detach(){ _attached=false; }
	bool attached() const { return _attached; }
	void write(uint8_t) {}
};
#endif
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