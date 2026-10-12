#include "objects.hpp"
#include "pins.hpp"

#ifndef UNIT_TEST
BH1750FVI myBH1750(BH1750_DEFAULT_I2CADDR, BH1750_ONE_TIME_HIGH_RES_MODE_2, 0.5, BH1750_ACCURACY_DEFAULT);
#else
BH1750FVI myBH1750; // use default ctor stub
#endif
RTC_DS1307 RTC;
Servo servo;
cLed ledGreen(ledGreenPin), ledRed(ledRedPin), ledYellow(ledYellowPin);
#ifdef LOG_MOVES
sLog logs;
#endif
#ifdef LOG_LIGHT
LogLight logLight;
#endif