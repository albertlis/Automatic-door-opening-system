#include "objects.hpp"
#include "pins.hpp"

BH1750FVI myBH1750(BH1750_DEFAULT_I2CADDR, BH1750_ONE_TIME_HIGH_RES_MODE_2, 0.5, BH1750_ACCURACY_DEFAULT);
RTC_DS1307 RTC;
Servo servo;
// sGate gate;
cLed ledGreen(ledGreenPin), ledRed(ledRedPin), ledYellow(ledYellowPin);
#ifdef LOG_MOVES
sLog logs;
#endif
#ifdef LOG_LIGHT
LogLight logLight;
#endif