#ifndef GATE_HPP
#define GATE_HPP
#include <Arduino.h>
#include "objects.hpp"
#include "constants.hpp"
#include "variables.hpp"

struct sGate {
    volatile bool isOpening{false};
    volatile bool isClosing{false};
    volatile bool isOpened{false};
    volatile bool isClosed{false};
    volatile bool isSafetyStop{false};
    volatile bool isOpenButtonClicked{false};
    volatile bool isCloseButtonClicked{false};
    unsigned long startMovingTime{0};

    // Configuration constants (timing/light thresholds)
    static constexpr uint8_t hourOpen{6};
    static constexpr uint8_t hourClose{18};
    static constexpr uint8_t absoluteHourClose{22};
    static constexpr uint8_t absoluteHourOpen{8};
    static constexpr uint8_t lightClose{3};
    static constexpr uint8_t lightOpen{8};
    static constexpr uint8_t closeSpeed{120};
    static constexpr uint8_t openSpeed{60};

    void openGate();
    void closeGate();
    void safetyStop();
    void printInternalState() const;
};

extern sGate gate;

namespace GateLogic {
    bool shouldOpen();
    bool shouldClose();
    bool shouldAbsoluteOpen();
    bool shouldAbsoluteClose();
}
#endif