// Main application sketch. Excluded from unit test builds so that
// test sources can provide their own setup()/loop() without conflicts.
#ifndef UNIT_TEST
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
    // Start first non-blocking light sampling cycle
    startLightSampling();

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
    // Handle debounced edge events outside ISR
    handleOpeningEdge();
    handleClosingEdge();
    // Continuous sampling independent of checkDelay window
    processLightSampling();
    if (millis() - millisNow > checkDelay) {
        millisNow = millis();
        date = RTC.now();

        if(date.hour() == 0 && date.minute() == 0 && compensateRtcDriftSwitch == true) {
            compensateRtcDrift(); // start non-blocking drift
            compensateRtcDriftSwitch = false;
            gate.isCloseButtonClicked = false;
            gate.isOpenButtonClicked = false;
        }    
        else if (date.hour() != 0)
            compensateRtcDriftSwitch = true;

        processRtcDrift();

        // switchSummerWinterTime();

        bool lightReady = isLightSamplingFinished();
        
        #ifdef PRINT
        printInfo();
        #endif
    bool wantClose = false;
    bool wantOpen  = false;
        if(lightReady) {
            wantClose = GateLogic::shouldClose() && (!gate.isClosed);
            wantOpen  = GateLogic::shouldOpen()  && (!gate.isOpened);
        }
        // Prevent conflicting decisions in the same cycle
        if (wantClose && wantOpen) {
            // Give priority: safety -> absolute -> skip (here just skip both)
            wantClose = true;
            wantOpen = false;
        }

        if (wantClose) {
            if ((!(gate.isOpening || gate.isClosing)) && (!gate.isSafetyStop)) {
                if(!gate.isOpenButtonClicked) {
                    gate.closeGate();
                }
            }
        }
        if (wantOpen) {
            if ((!(gate.isOpening || gate.isClosing)) && (!gate.isSafetyStop)) {
                if(!gate.isCloseButtonClicked) {
                    gate.openGate();
                }
            }
        }
       
    bool wantAbsClose = false;
    bool wantAbsOpen  = false;
        if(lightReady) {
            wantAbsClose = GateLogic::shouldAbsoluteClose() && (!gate.isClosed);
            wantAbsOpen  = GateLogic::shouldAbsoluteOpen()  && (!gate.isOpened);
        }
        if (wantAbsClose && wantAbsOpen) {
            // If both absolute states conflict (edge hour), ignore to avoid oscillation
            wantAbsClose = false;
            wantAbsOpen = false;
        }
        if (wantAbsClose) {
            if ((!(gate.isOpening || gate.isClosing)) && (!gate.isSafetyStop)) {
                if(!gate.isOpenButtonClicked) {
                    gate.closeGate();
                    gate.isCloseButtonClicked = false;
                }
            }
        }
        if (wantAbsOpen) {
            if ((!(gate.isOpening || gate.isClosing)) && (!gate.isSafetyStop)) {
                if(!gate.isCloseButtonClicked) {
                    gate.openGate();
                    gate.isOpenButtonClicked = false;
                }
            }
        }

        ledYellow.shouldBlink = (checkBatteryVoltage() < batteryDischargeVoltage) ? true : false;
        // Start next sampling cycle after using the completed buffer
        if(lightReady) {
            startLightSampling();
        }
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
    if (millis() - lastLightLogMillis > logLightInterval) {
        lastLightLogMillis = millis();
        startLightLogging();
    }
    processLightLogging();
    #endif
}
#endif // UNIT_TEST