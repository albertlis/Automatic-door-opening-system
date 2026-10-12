// Gate logic tests (logic only, no hardware)
#include <Arduino.h>
#include <unity.h>
#include "constants.hpp"
#include "variables.hpp"
#include "Gate.hpp"
#include "GateLogic.cpp" // include implementation directly for UNIT_TEST build to avoid separate compile filtering

// Allocate globals (weak definitions for the linker)
float light = 0.0f;
float lights[lightTableSize];
DateTime date; 
volatile unsigned long millisNow = 0;
volatile unsigned long lastLightLogMillis = 0;
volatile unsigned long lastOpeningEdgeMillis = 0;
volatile unsigned long lastClosingEdgeMillis = 0;
uint16_t eepromAddress = 0;
uint16_t eepromCounter = 0;
bool compensateRtcDriftSwitch = false;
sGate gate; 

namespace GateLogic {
    bool shouldOpen();
    bool shouldClose();
    bool shouldAbsoluteOpen();
    bool shouldAbsoluteClose();
}

static void fillLightsAbove(uint8_t thr){ for(uint8_t i=0;i<lightTableSize;++i) lights[i] = thr + 1; }
static void fillLightsBelow(uint8_t thr){ for(uint8_t i=0;i<lightTableSize;++i) lights[i] = (thr == 0 ? 0 : thr - 1); }

void test_should_open_basic_daylight() {
    // Set time inside normal open window (e.g. 10:00) -> unixtime = 10*3600
    date = DateTime(10UL * 3600UL);
    fillLightsAbove(sGate::lightClose);
    TEST_ASSERT_TRUE(GateLogic::shouldOpen());
    TEST_ASSERT_FALSE(GateLogic::shouldClose());
}

void test_should_close_evening_dark() {
    // 19:00 outside open window (>= hourClose 18) -> 19 * 3600
    date = DateTime(19UL * 3600UL);
    fillLightsBelow(sGate::lightClose);
    TEST_ASSERT_TRUE(GateLogic::shouldClose());
    TEST_ASSERT_FALSE(GateLogic::shouldOpen());
}

void test_absolute_open_after_absolute_hour() {
    // absoluteHourOpen = 8; choose 9
    date = DateTime(9UL * 3600UL);
    fillLightsAbove(sGate::lightClose);
    TEST_ASSERT_TRUE(GateLogic::shouldAbsoluteOpen());
}

void test_absolute_close_after_absolute_close_hour() {
    // absoluteHourClose = 22; choose 23
    date = DateTime(23UL * 3600UL);
    fillLightsBelow(sGate::lightClose);
    TEST_ASSERT_TRUE(GateLogic::shouldAbsoluteClose());
}

void setUp() {}
void tearDown() {}

void setup() {
    UNITY_BEGIN();
    RUN_TEST(test_should_open_basic_daylight);
    RUN_TEST(test_should_close_evening_dark);
    RUN_TEST(test_absolute_open_after_absolute_hour);
    RUN_TEST(test_absolute_close_after_absolute_close_hour);
    UNITY_END();
}

void loop() {}
