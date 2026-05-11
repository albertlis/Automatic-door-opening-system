#include <Arduino.h>
#include "switches.hpp"
#include "objects.hpp"
#include "variables.hpp"
#include "functions.hpp"
#include <Streaming.h>

/***********************************************************************
 * 
 *      Przy realizacji wyzerować licznik EEPROMu oraz ustawić datę
 *      !!!Aby wgrać program trzeba ręcznie resetować płytkę!!!
 *         Dodać kondensatory na wejścia przycisków bo szum potrafią złapać
 * 
 *      Sygnały:
 *          -zielona ciągły:    poprawnie otwarty
 *          -zielona miga:      w ruchu(obie strony)
 *          -zółta miga:        słaba bateria 
 *          -czerwona ciągły:    poprawnie zamknięty
 *          -czerwona miga:     safetyStop
 * 
 * W kolejnej wersji dodać timer opóźniający załączenie
 * *********************************************************************/
/*
  TODO add button reset after time
*/

/**************************************************************
 *                          Setup
 **************************************************************/
void setup() {
    // Slow down clocks to 1Mhz
    noInterrupts();
    // CLKPR = _BV(CLKPCE);  // enable change of the clock prescaler
    // CLKPR = _BV(CLKPS2);  // divide frequency by 16
    interrupts();

    #ifdef PRINT
    Serial.begin(9600);
    Serial << F("Waiting 1s") << endl;
    #endif
    delay(1000);
    #ifdef PRINT
    Serial << F("Waiting finished") << endl;
    #endif
    setupPins();
    setupInterrupts();

    Wire.begin();
    
    setupRTC();
    // date = RTC.now();
    // if(date.month() >= 4 and date.month() <= 10)
        // isSummerTime = true;

    setupLightSensor();

    readGatePosition();
    setupEEPROM();
    // wdt_enable(WDTO_8S);
    #ifdef PRINT_LOG_LIGHT
    printLightLogFromEeprom();
    #endif
    #ifdef PRINT_LOG_MOVES
    printLogMovesFromEeprom();
    #endif
}

/***************************************************************
 *                  Loop
 ***************************************************************/
void loop() {
    if (millis() - millisNow > checkDelay) {
        millisNow = millis();
        date = RTC.now();

        if(date.hour() == 0 && date.minute() == 0 && compensateRtcDriftSwitch == true) {
            compensateRtcDrift();
            compensateRtcDriftSwitch = false;
            gate.isCloseButtonClicked = false;
            gate.isOpenButtonClicked = false;
        }    
        else if (date.hour() != 0)
            compensateRtcDriftSwitch = true;

        // switchSummerWinterTime();

        readLight();
        
        #ifdef PRINT
        printInfo();
        #endif
        if (gate.shouldClose() && (!gate.isClosed)) {
            if ((!(gate.isOpening || gate.isClosing)) && (!gate.isSafetyStop)) {
                if(!gate.isOpenButtonClicked) {
                    gate.closeGate();
                }
            }
        }
        if (gate.shouldOpen() && (!gate.isOpened)) {
            if ((!(gate.isOpening || gate.isClosing)) && (!gate.isSafetyStop)) {
                if(!gate.isCloseButtonClicked) {
                    gate.openGate();
                }
            }
        }
       
        if (gate.shouldAbsoluteClose() && (!gate.isClosed)) {
            if ((!(gate.isOpening || gate.isClosing)) && (!gate.isSafetyStop)) {
                if(!gate.isOpenButtonClicked) {
                    gate.closeGate();
                    gate.isCloseButtonClicked = false;
                }
            }
        }
        if (gate.shouldAbsoluteOpen() && (!gate.isOpened)) {
            if ((!(gate.isOpening || gate.isClosing)) && (!gate.isSafetyStop)) {
                if(!gate.isCloseButtonClicked) {
                    gate.openGate();
                    gate.isOpenButtonClicked = false;
                }
            }
        }

        ledYellow.shouldBlink = (checkBatteryVoltage() < batteryDischargeVoltage) ? true : false;
    }
    if ((gate.isOpening || gate.isClosing) && (millis() - gate.startMovingTime > maxMovingTime))
        gate.safetyStop();

    if (digitalRead(openButtonPin) == LOW) {
        #ifdef DEBUG
        Serial.println(F("Manual open Pin detected"));
        #endif
        if ((!gate.isOpening)  && (!gate.isOpened)) {
            gate.openGate();
            gate.isSafetyStop = false;
            gate.isOpenButtonClicked = true;
            gate.isCloseButtonClicked = false;
            #ifdef DEBUG
            Serial.println(F("Manual open"));
            #endif
        }
    }
    if (digitalRead(closeButtonPin) == LOW) {
        #ifdef DEBUG
        Serial.println(F("Manual close Pin detected"));
        #endif
        if( (!gate.isClosing) && (!gate.isClosed)) {
            gate.closeGate();
            gate.isSafetyStop = false;
            gate.isCloseButtonClicked = true;
            gate.isOpenButtonClicked = false;
            #ifdef DEBUG
            Serial.println(F("Manual close"));
            #endif
        }
    }

    controlLeds();
    // delay(2000);
    // wdt_reset();
    #ifdef LOG_LIGHT
    if (millis() - millisNow > logLightInterval) {
        logLightInfo();
        writeLightLogIntoEeprom();
    }
    #endif
}