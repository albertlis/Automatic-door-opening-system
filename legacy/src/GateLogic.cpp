#include "Gate.hpp"
#include "variables.hpp"

namespace GateLogic {
    static uint8_t countAbove(uint8_t threshold) {
        uint8_t c=0; for(uint8_t i=0;i<lightTableSize;++i) if(lights[i] > threshold) ++c; return c; }
    static uint8_t countBelow(uint8_t threshold) {
        uint8_t c=0; for(uint8_t i=0;i<lightTableSize;++i) if(lights[i] < threshold) ++c; return c; }

    bool shouldOpen() {
        // All samples high enough and within daytime window
        return (date.hour() < sGate::hourClose) && (date.hour() >= sGate::hourOpen) && (countAbove(sGate::lightClose) == lightTableSize);
    }
    bool shouldClose() {
        // All samples low or outside open window
        return ((date.hour() >= sGate::hourClose) || (date.hour() < sGate::hourOpen)) && (countBelow(sGate::lightClose) == lightTableSize);
    }
    bool shouldAbsoluteClose() {
        return (date.hour() >= sGate::absoluteHourClose) || (date.hour() < sGate::hourOpen);
    }
    bool shouldAbsoluteOpen() {
        return (date.hour() < sGate::hourClose) && (date.hour() >= sGate::absoluteHourOpen);
    }
}
