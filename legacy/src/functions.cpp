#include <EEPROM.h>
#include <Streaming.h>
#include <Servo.h>
#include "functions.hpp"
#include "objects.hpp"
#include "variables.hpp"
#include "pins.hpp"

// Lightweight ISR (debounce + flag only)
volatile bool openingEdgeFlag{false};
volatile bool closingEdgeFlag{false};
// Non-blocking state machine internals
static bool rtcDriftInProgress{false};
static unsigned long rtcDriftStartMillis{0};
static bool lightSamplingInProgress{false};
static uint8_t lightSampleIndex{0};
static unsigned long lastLightSampleMillis{0};
#ifdef LOG_LIGHT
static bool lightLoggingInProgress{false};
static uint8_t logLightSampleIndex{0};
static unsigned long lastLogLightSampleMillis{0};
static float logAccumulatedLight{0.0f};
#endif

void openingEdgeISR() {
    unsigned long now = millis();
    if (now - lastOpeningEdgeMillis > edgeDebounceMs) {
        lastOpeningEdgeMillis = now;
        openingEdgeFlag = true;
    }
}

void closingEdgeISR() {
    unsigned long now = millis();
    if (now - lastClosingEdgeMillis > edgeDebounceMs) {
        lastClosingEdgeMillis = now;
        closingEdgeFlag = true;
    }
}

void handleOpeningEdge() {
    if (openingEdgeFlag) {
        openingEdgeFlag = false;
        if (gate.isOpening) {
            gate.isOpening = false;
            servo.detach();
            gate.isOpened = true;
            gate.isClosed = false;
            ledGreen.shouldBlink = false;
            ledRed.shouldBlink = false;
            ledGreen.turnOn();
            ledRed.turnOff();
            #ifdef DEBUG
            Serial.println(F("opening edge interrupt"));
            #endif
        }
    }
}

void handleClosingEdge() {
    if (closingEdgeFlag) {
        closingEdgeFlag = false;
        if (gate.isClosing) {
            gate.isClosing = false;
            servo.detach();
            gate.isClosed = true;
            gate.isOpened = false;
            ledGreen.shouldBlink = false;
            ledRed.shouldBlink = false;
            ledGreen.turnOff();
            ledRed.turnOn();
            #ifdef DEBUG
            Serial.println(F("closing edge interrupt"));
            #endif
        }
    }
}

#ifdef PRINT
void printDate() {
    date = RTC.now();
    Serial.print(date.month(), DEC);
    Serial.print('/');
    Serial.print(date.day(), DEC);
    Serial.print('/');
    Serial.print(date.year(), DEC);
    Serial.print(' ');
    Serial.print(date.hour(), DEC);
    Serial.print(':');
    Serial.print(date.minute(), DEC);
    Serial.print(':');
    Serial.print(date.second(), DEC);
    Serial.print('\t');
}

void printLightIntensivity() {
    light = myBH1750.readLightLevel();
    Serial.print(light);
    Serial.println(F(" lx"));
}
#endif
#ifdef LOG_MOVES
void logInfo(GateMove move) {
    date = RTC.now();
    logs.hour = date.hour();
    logs.minute = date.minute();
    logs.move = move;
}

void writeLogIntoEeprom() {
    EEPROM.update(eepromCounterAddress, eepromCounter++);
    EEPROM.put(eepromAddress, logs);
    eepromAddress += addressStep;
    if(eepromAddress > ( EEPROM.length() - addressStep)) {
        eepromAddress = 0;
        eepromCounter = 0;
    }
}
#endif
#ifdef PRINT_LOG_MOVES
void printLogMovesFromEeprom() {
Serial.print("EEPROM counter: ");
    eepromCounter = EEPROM.read(eepromCounterAddress);
    Serial.println(eepromCounter + 1);
    for(uint16_t i{0}; i < EEPROM.read(eepromCounterAddress); i += 2) {
        sLog tempLog, tempLog1;
        EEPROM.get(eepromAddress, tempLog);
        eepromAddress += addressStep;
        EEPROM.get(eepromAddress, tempLog1);
        eepromAddress += addressStep;
        Serial << "Godzina: " << tempLog.hour << " Minuta: " << tempLog.minute << " Ruch: " << gateMoveToChar(tempLog.move) << '\t'
               << "Godzina: " << tempLog1.hour << " Minuta: " << tempLog1.minute << " Ruch: " << gateMoveToChar(tempLog1.move) << endl;
    }
}
#endif
#ifdef LOG_LIGHT
void logLightInfo() {
    date = RTC.now();
    float measuredLight = 0.0f;
    for(uint8_t i{0}; i < 5; i++) {
        measuredLight += myBH1750.readLightLevel();
        delay(500);
    }
    measuredLight /= 5;
    uint8_t lightInt = round(measuredLight/2);
    if(lightInt >= 31)
        lightInt = 31;
    logLight.hour = date.hour();
    logLight.minute = date.minute();
    logLight.lightDividedBy2 = lightInt;
}

void startLightLogging() {
    lightLoggingInProgress = true;
    logLightSampleIndex = 0;
    lastLogLightSampleMillis = 0;
    logAccumulatedLight = 0.0f;
    date = RTC.now();
}

void processLightLogging() {
    if(!lightLoggingInProgress) return;
    unsigned long now = millis();
    if(logLightSampleIndex == 0 || (now - lastLogLightSampleMillis) >= 500) {
        logAccumulatedLight += myBH1750.readLightLevel();
        lastLogLightSampleMillis = now;
        ++logLightSampleIndex;
        if(logLightSampleIndex >= 5) {
            float avg = logAccumulatedLight / 5.0f;
            uint8_t lightInt = round(avg/2);
            if(lightInt >= 31) lightInt = 31;
            logLight.hour = date.hour();
            logLight.minute = date.minute();
            logLight.lightDividedBy2 = lightInt;
            writeLightLogIntoEeprom();
            lightLoggingInProgress = false;
        }
    }
}

bool isLightLoggingFinished() { return !lightLoggingInProgress; }

void writeLightLogIntoEeprom() {
    EEPROM.update(eepromCounterAddress, eepromCounter++);
    EEPROM.put(eepromAddress, logLight);
    eepromAddress += addressStep;
    if(eepromAddress > ( EEPROM.length() - addressStep)) {
        eepromAddress = 0;
        eepromCounter = 0;
    }
}
#endif
#ifdef PRINT_LOG_LIGHT
void printLightLogFromEeprom() {
    Serial.print("EEPROM counter: ");
    eepromCounter = EEPROM.read(eepromCounterAddress);
    Serial.println(eepromCounter + 1);
    for(uint16_t i{0}; i < eepromCounter; i += 2) {
        LogLight tempLog, tempLog1;
        EEPROM.get(eepromAddress, tempLog);
        eepromAddress += addressStep;
        EEPROM.get(eepromAddress, tempLog1);
        eepromAddress += addressStep;
        Serial << "Godzina: " << tempLog.hour << " Minuta: " << tempLog.minute << " Ruch: " << tempLog.lightDividedBy2 * 2 << '\t'
               << "Godzina: " << tempLog1.hour << " Minuta: " << tempLog1.minute << " Ruch: " << tempLog1.lightDividedBy2 * 2 << endl;
    }
}
#endif

#ifdef DEBUG
void printRtcSqwMode() {
  Ds1307SqwPinMode mode = RTC.readSqwPinMode();

  Serial.print("Sqw Pin Mode: ");
  switch(mode) {
  case Ds1307SqwPinMode::DS1307_OFF:              Serial.println("OFF");       break;
  case Ds1307SqwPinMode::DS1307_ON:               Serial.println("ON");        break;
  case Ds1307SqwPinMode::DS1307_SquareWave1HZ:    Serial.println("1Hz");       break;
  case Ds1307SqwPinMode::DS1307_SquareWave4kHz:   Serial.println("4.096kHz");  break;
  case Ds1307SqwPinMode::DS1307_SquareWave8kHz:   Serial.println("8.192kHz");  break;
  case Ds1307SqwPinMode::DS1307_SquareWave32kHz:  Serial.println("32.768kHz"); break;
  default:                      Serial.println("UNKNOWN");   break;
  }
}
#endif

void setupPins() {
  pinMode(openingEdgePin, INPUT_PULLUP);
  pinMode(closingEdgePin, INPUT_PULLUP);
  pinMode(openButtonPin, INPUT_PULLUP);
  pinMode(closeButtonPin, INPUT_PULLUP);
  pinMode(servoPin, OUTPUT);
  pinMode(ledGreenPin, OUTPUT);
  pinMode(ledRedPin, OUTPUT);
  pinMode(ledYellowPin, OUTPUT);
}

void setupInterrupts() {
  attachInterrupt(digitalPinToInterrupt(openingEdgePin), openingEdgeISR, CHANGE);
  attachInterrupt(digitalPinToInterrupt(closingEdgePin), closingEdgeISR, CHANGE);
}

void setupRTC() {
  while(RTC.begin() != true) {
      delay(5000);
  }
  #ifdef ADJUST_TIME
  RTC.adjust(DateTime(F(__DATE__), F(__TIME__)));
  #endif
  RTC.writeSqwPinMode(Ds1307SqwPinMode::DS1307_OFF);
  #ifdef DEBUG
  printRtcSqwMode();
  #endif
}

void setupLightSensor() {
  while (myBH1750.begin() != true) {
      #ifdef DEBUG
      Serial.println(F("ROHM BH1750FVI is not present")); //(F()) saves string to flash & keeps dynamic memory free
      #endif
      delay(5000);
  }
  #ifdef DEBUG
  Serial.println(F("Conected with light sensor"));
  #endif
}

void readGatePosition() {
  if (digitalRead(openingEdgePin) == LOW) {
      gate.isOpened = true; 
      gate.isClosed = false;
      ledGreen.turnOn();
      ledRed.turnOff();
  }
  else if (digitalRead(closingEdgePin) == LOW) {
      gate.isClosed = true;
      gate.isOpened = false;
      ledRed.turnOn();
      ledGreen.turnOff();
  }
}

void setupEEPROM() {
  #ifdef RESET_EEPROM_COUNTER
  EEPROM.update(eepromCounterAddress, 0);
  #endif
  eepromCounter = EEPROM.read(eepromCounterAddress);
}

void readLight() {
  for (uint8_t i{0}; i < lightTableSize; ++i) {
      lights[i] = myBH1750.readLightLevel();
      delay(300);
  }
}

void startLightSampling() {
    lightSamplingInProgress = true;
    lightSampleIndex = 0;
    lights[0] = myBH1750.readLightLevel(); // immediate first sample
    lightSampleIndex = 1;
    lastLightSampleMillis = millis();
}

void processLightSampling() {
    if(!lightSamplingInProgress) return;
    unsigned long now = millis();
    if((now - lastLightSampleMillis) >= 300) {
        lights[lightSampleIndex] = myBH1750.readLightLevel();
        lastLightSampleMillis = now;
        ++lightSampleIndex;
        if(lightSampleIndex >= lightTableSize) {
            lightSamplingInProgress = false;
        }
    }
}

bool isLightSamplingFinished() { return !lightSamplingInProgress; }

void printInfo() {
  printDate();
  printLightIntensivity();
  gate.printInternalState();
//   Serial << F("Is summer time: ") << isSummerTime << endl;
}

void compensateRtcDrift() {
    if(!rtcDriftInProgress) {
        rtcDriftInProgress = true;
        rtcDriftStartMillis = millis();
    }
}

void processRtcDrift() {
    if(!rtcDriftInProgress) return;
    if(millis() - rtcDriftStartMillis >= (secondsDriftOffset * 1000UL)) {
        date = RTC.now();
        DateTime tempDate = DateTime(date.unixtime() - secondsDriftOffset);
        RTC.adjust(tempDate);
        rtcDriftInProgress = false;
    }
}

bool isRtcDriftInProgress() { return rtcDriftInProgress; }

// void switchSummerWinterTime() {
//     if(isSummerTime == true && (date.month() <= 3 or date.month() >= 11)){
//         if(date.hour() > 2) {
//             date = RTC.now();
//             DateTime tempDate = DateTime(date.unixtime() - 3600);
//             RTC.adjust(tempDate);
//             isSummerTime = false;
//         }
//     }
//     else if(isSummerTime == false && (date.month() >= 4 and date.month() <= 10)) {
//         if(date.hour() > 2) {
//             date = RTC.now();
//             DateTime tempDate = DateTime(date.unixtime() + 3600);
//             RTC.adjust(tempDate);
//             isSummerTime = true;
//         }
//     }
// }

void controlLeds(){
  if (ledGreen.shouldBlink) ledGreen.blink(1000);
  else ledGreen.stopBlinking();
  if (ledRed.shouldBlink) ledRed.blink(1000);
  else ledRed.stopBlinking();
  if (ledYellow.shouldBlink) ledYellow.blink(1000);
  else  ledYellow.stopBlinking();
}