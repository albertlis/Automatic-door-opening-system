#ifndef LOG_HPP
#define LOG_HPP
#include <Arduino.h>
#include "switches.hpp"

#ifdef LOG_MOVES
enum class GateMove : char { Open = 'O', Close = 'C' };

inline char gateMoveToChar(GateMove m) { return static_cast<char>(m); }
inline GateMove charToGateMove(char c) { return (c == 'C') ? GateMove::Close : GateMove::Open; }

struct sLog {
    uint8_t hour{0};
    uint8_t minute{0};
    GateMove move{GateMove::Open};
};
#endif

#endif