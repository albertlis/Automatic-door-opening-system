# Automatic door open system
> Low-power consumption system of releasing and closing animals on small farm depending on the time of day and time.

## Table of contents
* [General info](#general-info)
* [Technologies](#technologies)
* [Libraries](#libraries)
* [Setup](#setup)
* [Features](#features)
* [Development](#development)
* [Status](#status)
* [Inspiration](#inspiration)
* [Contact](#contact)

## General info
The system was created for small farm specialized on breeding chickens. System is designed to work all year round. During the year the sunrise and sunset time is changing. To solve this problem I decided to measure outdoor light intensivity. System is comparing it with current time and decides to open or close the door.

## Technologies
* C++/Arduino - std11
* PlatformIO

## Hardware

| Component | Part | Notes |
|-----------|------|-------|
| MCU | Arduino Pro Mini 328 (5V/16MHz) | No USB — program via TTL converter, manual RST pin trick required |
| Servo | MG995 continuous rotation (360°) | Rope wound on ~30×30mm drum; stalls under load = mechanical brake holds door up |
| RTC | DS1307 (GY-302 breakout) | Drifts significantly; firmware compensates at midnight |
| Light sensor | BH1750FVI (GY-302 breakout) | Dusk/dawn detection; time windows prevent false triggers from clouds/storms |
| Limit switches | 2× microswitch | End-of-travel detection, interrupt-driven |
| Manual buttons | 2× push-button | Open/close override |
| LEDs | 3mm red/yellow/green | Large series resistors → <1mA each for power saving |
| Power | microUSB adapter board → VCC/GND | Bulk cap (~100µF) for supply stabilization |

Electronics enclosure placed in dark location to isolate sensor from ambient light.

## Libraries
* Wire (Arduino Standard Libraries)
* EEPROM (Arduino Standard Libraries)
* Math (avr-libc)- version 2.0.0
* stdint (avr-libc) - version 2.0.0
* RTClib - version 1.11.0
* BH1750FVI - version 1.2.6
* Servo - version 1.1.6
* Streaming - version 5.0.0

## Setup
* Clone this repository
* Install PlatformIO IDE or Platformio Core (IDE is recomended): [PlatformIO](https://platformio.org)
* Install necessary [libraries](#libraries) (It's highly recommended by using PlatformIO IDE)
* If you use PlatformIO IDE then import project. If you use Platformio Core then go to project folder
* If you use PlatformIO IDE then using graphical interface choose Project Tasks -> Build. If you use Platformio Core type `pio run` into terminal inside project folder.
* Connect your microcontroller using FDTI. To use other programmers modify `upload_protocol` inside `platformio.ini` file. Check: [Upload options](https://docs.platformio.org/en/latest/projectconf/section_env_upload.html)
* If you use PlatformIO IDE then using graphical interface choose Project Tasks -> Upload. If you use Platformio Core type `pio run --target upload` into terminal inside project folder.

## Features
- Measure and filtering light intensivity
- Time measurement using DS1307 RTC timer
- Timer drift compensation
- Internal status signaling by LEDs
- Automatic door opening/closing depending on time/light intensity
- Manual control mode
- Optimized power consumption

To-do list:
- Improve manual control mode
- Slow down CPU to 1MHz

## Development
Want to contribute? Great!

To fix a bug or enhance an existing module, follow these steps:

* Fork the repo
* Create a new branch (`git checkout -b improve-feature`)
* Make the appropriate changes in the files
* Verify if they are correct
* Add changes to reflect the changes made
* Commit changes
* Push to the branch (`git push origin improve-feature`)
* Create a Pull Request

## Status
Project is: _finished_

## Inspiration
Living on farm with animals it's not easy. Farmers have to wake up early and take care of this animals. Chickens are also going to sleep at specific time. If farmer has regular shift job, it's additional physical and mental stress for him. To help them in this situation I've decided for pro bono to design and develop this low-budget and low-power consumtion project. Total cost of this system is less than 20$.

## Contact
albert.lis.1996@gmail.com - feel free to contact me!
