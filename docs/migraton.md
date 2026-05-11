# Smart Kurnik V2 — Architektura Migracji

## 1. Kontekst i Cel

Trzy główne problemy v1 (ATmega328P / C++ / Arduino):
- DS1307 dryfuje — obejście przez ręczną korektę o północy
- MG995 trzyma drzwi prądem stojącym 24/7 — dominujące zużycie energii
- Brak USB, brak zdalnego wglądu — ślepe w produkcji

V2 wymienia MCU i napęd, zachowuje czujniki i fizyczne sterowanie, dodaje web UI.

Skrzynka sterownicza: wewnątrz pomieszczenia (5–25°C, sucho).

---

## 2. Hardware BOM

| Komponent | Part | Uwagi |
|-----------|------|-------|
| MCU | **ESP32-C3** | USB natywne, WiFi, 3.3V, tanie |
| Napęd | **JGY-370** (DC, 6V, przekładnia ślimakowa) | Samohamowny = 0 mA w spoczynku, zastępuje MG995 |
| Sterownik silnika | **DRV8833** (nie MX1508) | Ograniczenie prądu, tryb uśpienia, 1.5A peak — **PRZED MONTAŻEM: zmierz stall prąd JGY-370 przy 6V; jeśli >1.5A → potrzebna inna decyzja (oba kanały DRV8833 równolegle ~3A peak, lub wymiana chipa — brak prostego drop-in)** |
| GPIO expander | **PCF8574** | I2C 0x20 (A0=A1=A2=GND), LEDs + Motor IN1/IN2 |
| RTC | **DS3231** (płytka ZS-042) | TCXO ±2ppm, backup CR2032 przez pin VBAT |
| Czujnik światła | BH1750 (GY-302) | Reuse z v1, 3.3V-compatible, I2C 0x23 |
| Krańcówki | 2× mikroswitch | Reuse (GÓRA / DÓŁ) |
| Przyciski ręczne | 2× push-button | Reuse (open / close) |
| LEDs | 3× (red / yellow / green) | Reuse, duże rezystory (<1mA każda) |

### Modyfikacja DS3231 (ZS-042) — OBOWIĄZKOWE
Wylutować: diodę LED zasilania + rezystor obwodu ładowania CR2032. Bez tego układ ładuje nieładowalną baterię → uszkodzenie / niebezpieczeństwo.

### Topologia zasilania (gwiazda)

```
Zasilacz 5–6V
├── VMOT (DRV8833) — bezpośrednio, kondensator ~1000µF dla inrush JGY-370
├── 5V/USB ESP32-C3 — bezpośrednio
│   └── pin 3V3 → BH1750 VCC + DS3231 VCC + PCF8574 VCC
└── GND wspólny dla wszystkich
```

**ZAKAZ zasilania silnika przez piny ESP32.**

### Piny ESP32-C3 Super Mini

ESP32-C3 Super Mini: 13 GPIO fizycznych. Strapping: GPIO2/8/9 — nie używać.
GPIO18/19 = USB D-/D+ — wolne (LED_GREEN/YELLOW przeniesione na PCF8574, USB działa normalnie).

| Sygnał | Pin | Uwagi |
|--------|-----|-------|
| I2C SDA | GPIO4 | BH1750 + DS3231 + PCF8574 |
| I2C SCL | GPIO5 | |
| LIMIT_TOP | GPIO0 | INPUT_PULLUP, polling co 20ms w asyncio |
| LIMIT_BOTTOM | GPIO1 | INPUT_PULLUP, polling co 20ms w asyncio |
| VBAT_SENSE | GPIO3 | ADC1_CH3, direct do VBAT pin DS3231 ZS-042 |
| LED_RED | GPIO6 | OUTPUT, active-low, direct — działa gdy I2C dead |
| BTN_OPEN | GPIO7 | INPUT_PULLUP |
| BTN_CLOSE | GPIO10 | INPUT_PULLUP |
| DRV8833 nFAULT | GPIO20 | INPUT, active-low — direct, wykrycie faultu bez I2C |
| DRV8833 nSLEEP | GPIO21 | OUTPUT, 0=sleep (~2µA), 1=active |
| USB D- | GPIO18 | wolny |
| USB D+ | GPIO19 | wolny |

### Piny PCF8574 (I2C 0x20)

LEDs active-low: anoda → rezystor → 3.3V, katoda → PCF8574 pin. Pin=0 → LED ON.

| Sygnał | Pin | Uwagi |
|--------|-----|-------|
| LED_GREEN | P0 | output, active-low |
| LED_YELLOW | P1 | output, active-low |
| Motor IN1 | P2 | output |
| Motor IN2 | P3 | output |
| wolne | P4–P7 | — |

---

## 3. Feature Parity — co musi przeżyć z v1

| Feature | Źródło w C++ | v2 podejście |
|---------|-------------|-------------|
| 5 próbek światła — muszą być jednogłośne | `Gate.hpp:41-53` | `light_sensor_loop` wypełnia `ctrl.lux_buffer[5]` przez `LightSensor.read_lux()` co 300ms; `tick()` sprawdza czy wszystkie 5 > próg |
| Histereza progów | `lightOpen=8`, `lightClose=3` lx | Config, domyślne wartości zachowane |
| Okno czasowe (dynamiczne lub stałe) | `Gate.hpp:44` | Warunek w state machine — tryb `legacy`: stałe godziny; tryb `sun_position`: obliczone wschód/zachód + offset |
| Absolutny override czasu | `absoluteHourOpen=8`, `absoluteHourClose=22` | Osobny warunek, nadpisuje blokadę manualną |
| Blokada manualna do absolutnego triggera | `main.cpp:94-122` | Stan `MANUAL_HOLD` |
| Safety stop przy przekroczeniu czasu ruchu (21s) | `constants.hpp:9` | licznik `_move_start_ms` w `tick()`; przekroczenie `move_timeout_s` → `_safety_stop()` |
| Sygnały LED | pętla v1 | `led_loop()` async task |
| Korekcja dryftu RTC | `functions.cpp:240` | **Porzucić** — DS3231 eliminuje problem |
| Monitoring napięcia baterii RTC | `functions.hpp:49` | GPIO3 ADC1_CH3 → VBAT DS3231, próg <2.7V → LED_YELLOW blink |

---

## 4. Architektura Oprogramowania

### 4.1 Środowisko

- **Język:** MicroPython
- **Paradygmat:** pełna asynchroniczność przez `uasyncio`. Zero `time.sleep()`.
- **Web framework:** `microdot >= 2.0.6` (async) — zweryfikować SSE support w MicroPython porcie przed wgraniem; fallback: polling HTMX `hx-trigger="every 2s"` na `/api/status`
- **WiFi:** Pure AP mode — `Kurnik_Control`. Bez routera, bez internetu. Czas z DS3231.

### 4.2 Struktura plików na urządzeniu

```
/boot.py        — init HW, machine.freq(40_000_000), first-boot detection → WiFi AP lub off, WebREPL
/main.py        — uasyncio entry point, spawn wszystkich tasków
/state.py       — GateState enum + logika przejść
/hardware.py    — klasy: Motor, RTC, LightSensor, Button, LED
/compat.py      — MicroPython/CPython shims (ticks_ms, ticks_diff, sleep_ms) — import w hardware.py i state.py
/web.py         — microdot: REST + SSE + pliki statyczne
/config.py      — load/save config.json + domyślne wartości
/config.json    — ustawienia użytkownika (progi, godziny) — trwałe
/www/
  index.html    — live dashboard statusu
  config.html   — edytor progów i godzin
  logs.html     — podgląd ring buffera
  debug.html    — WebREPL link, surowe wartości, reboot
```

### 4.3 Maszyna Stanów

Zastępuje 7 booli z v1 (które dopuszczają sprzeczne stany) jednym enum:

```python
class GateState(Enum):
    INIT
    IDLE_OPEN
    IDLE_CLOSED
    MOVING_OPEN
    MOVING_CLOSE
    MANUAL_HOLD_OPEN
    MANUAL_HOLD_CLOSED
    SAFETY_STOP
    ERROR

class MovementTrigger(Enum):
    AUTO     = 0  # automatyczna decyzja (światło / czas)
    MANUAL   = 1  # przycisk w normalnym stanie → po ruchu MANUAL_HOLD_*
    RECOVERY = 2  # przycisk po SAFETY_STOP → po ruchu IDLE_* (auto-logika wraca)
```

`CoopController` przechowuje `self._trigger: MovementTrigger = MovementTrigger.AUTO`.
Przy starcie ruchu ustawiany na właściwą wartość. Po osiągnięciu limit switcha:
- `MANUAL` → `MANUAL_HOLD_*`
- `AUTO` lub `RECOVERY` → `IDLE_*`

```
INIT
  → IDLE_OPEN                        (LIMIT_TOP aktywny, LIMIT_BOTTOM nieaktywny)
  → IDLE_CLOSED                      (LIMIT_BOTTOM aktywny, LIMIT_TOP nieaktywny)
  → MOVING_OPEN | MOVING_CLOSE       (oba nieaktywne → is_daytime() → dzień=open, noc=close) [trigger=AUTO → po dotarciu → IDLE_*]
  → ERROR                            (oba aktywne jednocześnie → hardware fault)

IDLE_CLOSED
  → MOVING_OPEN                      (sensor_open:  window_open ≤ now < window_close  AND all_lux > lux_open
                                      LUB abs_open: abs_open_local ≤ now < window_close_local)
                                     [trigger=AUTO]

IDLE_OPEN
  → MOVING_CLOSE                     (sensor_close: (now ≥ window_close OR now < window_open)  AND all_lux < lux_close
                                      LUB abs_close: now ≥ abs_close_local  OR  now < window_open_local)
                                     [trigger=AUTO]

MOVING_OPEN  ──timeout 21s──→  SAFETY_STOP
  → IDLE_OPEN                        (LIMIT_TOP hit, trigger=AUTO lub RECOVERY)
  → MANUAL_HOLD_OPEN                 (LIMIT_TOP hit, trigger=MANUAL)

MOVING_CLOSE ──timeout 21s──→  SAFETY_STOP
  → IDLE_CLOSED                      (LIMIT_BOTTOM hit, trigger=AUTO lub RECOVERY)
  → MANUAL_HOLD_CLOSED               (LIMIT_BOTTOM hit, trigger=MANUAL)

IDLE_OPEN   + BTN_CLOSE → MOVING_CLOSE [trigger=MANUAL] → MANUAL_HOLD_CLOSED
IDLE_CLOSED + BTN_OPEN  → MOVING_OPEN  [trigger=MANUAL] → MANUAL_HOLD_OPEN

MANUAL_HOLD_OPEN   + abs_close → MOVING_CLOSE [trigger=AUTO]   (now ≥ abs_close_local OR now < window_open)
MANUAL_HOLD_CLOSED + abs_open  → MOVING_OPEN  [trigger=AUTO]   (abs_open_local ≤ now < window_close)
MANUAL_HOLD_OPEN   + BTN_CLOSE → MOVING_CLOSE [trigger=MANUAL]
MANUAL_HOLD_CLOSED + BTN_OPEN  → MOVING_OPEN  [trigger=MANUAL]
MANUAL_HOLD_OPEN   + BTN_OPEN  → no-op (już otwarte)
MANUAL_HOLD_CLOSED + BTN_CLOSE → no-op (już zamknięte)

SAFETY_STOP
  → MOVING_OPEN | MOVING_CLOSE       (tylko przycisk manualny, [trigger=RECOVERY] → NIE MANUAL_HOLD)
                                     (web UI nie odblokowuje — AP lokalny, zasięg = fizyczna bliskość)

ERROR                                  (brak I2C, brak czujnika, obie krańcówki aktywne)
  Wejście: z INIT (hardware fault przy starcie) LUB z dowolnego stanu gdy I2C przestaje odpowiadać
  Wyjście: tylko reboot (POST /api/reboot lub fizyczny reset)
  LED: LED_RED miga **4Hz** (odróżnia od SAFETY_STOP: 1Hz; LED_YELLOW na PCF8574 niedostępna gdy I2C dead)
```

### Sygnały LED

| Stan | LED | Wzorzec |
|------|-----|---------|
| `IDLE_OPEN` / `MANUAL_HOLD_OPEN` | zielona | ciągła |
| `IDLE_CLOSED` / `MANUAL_HOLD_CLOSED` | czerwona | ciągła |
| `MOVING_OPEN` / `MOVING_CLOSE` | zielona | miga 1Hz |
| `SAFETY_STOP` | czerwona | miga 1Hz |
| `ERROR` | czerwona | **4Hz** |
| Bateria słaba (każdy stan normalny) | żółta | miga 1Hz (nakłada się z innymi) |

`ERROR` odróżnialny od `SAFETY_STOP` przez szybkość (4Hz vs 1Hz).

Zatrzymanie silnika = IN1 + IN2 LOW (DRV8833 coast). Brak trzymania prądem — przekładnia ślimakowa samohamuje.
DRV8833 nSLEEP (GPIO21): 0 = hardware sleep (~2µA), 1 = active. Włącz przed ruchem, wyłącz po zatrzymaniu.
DRV8833 nFAULT (GPIO20): LOW = thermal shutdown / overcurrent → przejście w SAFETY_STOP bez czekania na timeout.

`CoopController` eksponuje synchroniczną metodę `tick()` zawierającą całą logikę decyzyjną. `control_loop` wywołuje `tick()` co 2s:

```python
_last_midnight_day = -1

async def control_loop():
    global _last_midnight_day
    while True:
        y, mo, d, h, minute, *_ = rtc.datetime()
        if y < 2020:           # DS3231 nigdy nie zsync
            await asyncio.sleep(2)
            continue

        if h == 0 and minute == 0 and d != _last_midnight_day:
            ctrl._on_midnight(y, mo, d, h, minute)
            _last_midnight_day = d

        prev_state = ctrl.state
        ctrl.tick()
        new_state = ctrl.state

        if new_state == GateState.MOVING_OPEN  and prev_state != GateState.MOVING_OPEN:
            asyncio.create_task(ctrl._run_move('open'))
        elif new_state == GateState.MOVING_CLOSE and prev_state != GateState.MOVING_CLOSE:
            asyncio.create_task(ctrl._run_move('close'))

        await asyncio.sleep(2)
```

```python
class CoopController:
    def __init__(self, motor, rtc, light_sensor, pcf, limit_top, limit_bottom,
                 btn_open, btn_close, leds, config):
        self._move_start_ms: int = 0
        self._i2c_fail_count: int = 0
        self._vbat_low: bool = False
        self._abort_move: bool = False   # set by manual_move() to interrupt _run_move
        self._config_warning: str = ""   # set by _resolve_times() gdy invariant clamped; eksponowany w /api/status
        self._today_times: tuple = (0, 0, 0, 0)  # (window_open, window_close, abs_open, abs_close) min od północy
        ...
        # Wylicz czasy triggerów dla dzisiaj (gdy RTC zsync)
        y, m, d, h, minute, *_ = self.rtc.datetime()
        if y >= 2020:
            self._resolve_times(y, m, d, h, minute)

    def tick(self):
        """Jeden krok state machine. Synchroniczny — testowalny bez asyncio."""
        if self.state in (GateState.MOVING_OPEN, GateState.MOVING_CLOSE):
            elapsed = time.ticks_diff(time.ticks_ms(), self._move_start_ms)
            if elapsed > self.config.safety.move_timeout_s * 1000:
                self._safety_stop()
                return
        # ... reszta logiki state machine (shouldOpen/shouldClose, absolutne triggery)

    def simulate_timeout(self):
        """Test hook — cofa _move_start_ms poza granicę timeout."""
        self._move_start_ms = time.ticks_ms() - (self.config.safety.move_timeout_s * 1000 + 1)

    def is_daytime(self) -> bool:
        """True gdy czas lokalny mieści się w aktywnym oknie sensora (z config)."""
        y, m, d, h, minute, *_ = self.rtc.datetime()
        now = local_minutes(y, m, d, h, minute)
        # oblicza window_open_local / window_close_local zgodnie z §5.4
        ...
        return window_open_local <= now < window_close_local

    def _i2c_call(self, fn):
        """Wrapper I2C odporny na błędy przejściowe. Po 3 kolejnych OSError → GateState.ERROR."""
        try:
            result = fn()
            self._i2c_fail_count = 0
            return result
        except OSError:
            self._i2c_fail_count += 1
            if self._i2c_fail_count >= 3:
                self._enter_error()
            return None
```

`is_daytime()` używane w INIT gdy obie krańcówki nieaktywne (drzwi w połowie drogi po resecie) — decyduje o kierunku pierwszego ruchu.

### 4.4 Taski async

```python
async def main():
    asyncio.create_task(light_sensor_loop())  # 5 próbek × 300ms = 1.5s cykl
    asyncio.create_task(control_loop())       # sprawdza co 2s, napędza state machine
    asyncio.create_task(led_loop())           # mig co 1s
    asyncio.create_task(button_monitor(btn_open,  'open',  ctrl))
    asyncio.create_task(button_monitor(btn_close, 'close', ctrl))

    y, mo, d, *_ = rtc.datetime()
    if y == 2000 and mo == 1 and d == 1:      # DS3231 default = first boot
        await start_ap_session()              # auto AP przy pierwszym uruchomieniu

    while True:
        await asyncio.sleep(3600)

asyncio.run(main())
# web server startuje wyłącznie przez start_ap_session() (multi-click lub first-boot)
```

`control_loop` pomija logikę decyzyjną gdy RTC nie jest zsynchronizowany (y < 2020 = DS3231 nigdy nie zsync). Pełna implementacja `control_loop` i `tick()` w §4.3.

Endpoint SSE w `web.py` (każdy klient ma własną pętlę — max 2 klientów jednocześnie na AP):

```python
@app.route('/api/events')
async def events(request):
    async def stream():
        while True:
            yield f"data: {ctrl.status_json()}\n\n"
            await asyncio.sleep(2)
    return Response(stream(), headers={'Content-Type': 'text/event-stream',
                                       'Cache-Control': 'no-cache'})
```

### 4.5 Shared Light Buffer

`CoopController` przechowuje:
```python
self.lux_buffer: list[float] = [0.0] * 5
self.lux_ready: bool = False  # set once after first full buffer, never reset
```
`light_sensor_loop` pobiera pojedynczy odczyt przez `LightSensor.read_lux()` co 300ms i rotuje po buforze. Po zapełnieniu 5 pozycji ustawia `lux_ready = True`. `tick()` sprawdza `lux_buffer` gdy `lux_ready`. Brak race conditions — single-threaded asyncio.

```python
async def light_sensor_loop():
    i = 0
    while True:
        val = ctrl._i2c_call(lambda: ctrl.light.read_lux())
        if val is not None:
            ctrl.lux_buffer[i] = val
            i = (i + 1) % 5
            if i == 0:
                ctrl.lux_ready = True
        await asyncio.sleep_ms(300)
```

`LightSensor.read_lux()` zwraca jeden odczyt BH1750 (float lux). Brak `read_unanimous()` — jednomyślność sprawdzana w `tick()` przez `all(v > threshold for v in lux_buffer)`.

### 4.6 Logika napędu

- Przed ruchem: `_move_start_ms = ticks_ms()`, nSLEEP=HIGH, delay 1ms (czas wybudzenia DRV8833)
- Ruch w górę: IN1=HIGH, IN2=LOW → poll LIMIT_TOP co 20ms → IN1=IN2=LOW → nSLEEP=LOW
- Ruch w dół: IN1=LOW, IN2=HIGH → poll LIMIT_BOTTOM co 20ms → IN1=IN2=LOW → nSLEEP=LOW
- Safety stop (timeout): `tick()` sprawdza `ticks_diff(now, _move_start_ms) > timeout_ms` → `_safety_stop()`
- Safety stop (nFAULT): polling GPIO20 w `_run_move` → LOW → `_safety_stop()`, powrót z corutyny

```python
async def _run_move(self, direction):
    self._move_start_ms = time.ticks_ms()
    self.nsleep.value(1)
    await asyncio.sleep_ms(1)
    self.motor.forward() if direction == 'open' else self.motor.backward()
    limit_pin = self.limit_top if direction == 'open' else self.limit_bottom
    while limit_pin.value() == 1:           # HIGH = nieaktywna (pull-up)
        if self._abort_move:                 # przerwane przez manual_move()
            self._abort_move = False
            return
        if self.nfault.value() == 0:                         # nFAULT LOW, active-low direct GPIO20
            self._safety_stop()
            return
        if self.state == GateState.SAFETY_STOP:  # timeout wykryty przez tick()
            return
        await asyncio.sleep_ms(20)
    self.motor.stop()
    self.nsleep.value(0)
    self._on_limit_reached()               # przejście stanu zależy od _trigger
```

```python
def _safety_stop(self):
    self.nsleep.value(0)        # nSLEEP=LOW (~2µA) — zawsze, niezależnie od I2C
    self.motor.stop()           # IN1=IN2=LOW (coast) — best-effort przez PCF8574
    if self.state != GateState.SAFETY_STOP:   # guard: nie podwajaj error_count
        self._today_rec_error_count += 1
    self.state = GateState.SAFETY_STOP

def manual_move(self, action: str):
    """Najwyższy priorytet — działa z każdego stanu. Przerywa ruch przeciwny."""
    if action == 'open'  and self.state in (GateState.IDLE_OPEN,
                                            GateState.MANUAL_HOLD_OPEN,
                                            GateState.MOVING_OPEN):
        return  # już otwarte lub otwiera się
    if action == 'close' and self.state in (GateState.IDLE_CLOSED,
                                            GateState.MANUAL_HOLD_CLOSED,
                                            GateState.MOVING_CLOSE):
        return  # już zamknięte lub zamyka się
    if self.state in (GateState.MOVING_OPEN, GateState.MOVING_CLOSE):
        self._abort_move = True   # _run_move wykryje przy kolejnym ticku 20ms
        self.motor.stop()
        self.nsleep.value(0)
    self._trigger = MovementTrigger.MANUAL
    self.state = GateState.MOVING_OPEN if action == 'open' else GateState.MOVING_CLOSE
    asyncio.create_task(self._run_move(action))

def _on_midnight(self, y, m, d, h, minute):
    """Zapis dziennego rekordu do flash, reset na nowy dzień, przelicz czasy triggerów."""
    _write_record(self._today_rec)
    self._today_rec = _empty_record()
    self._resolve_times(y, m, d, h, minute)

def _resolve_times(self, y, m, d, h, minute):
    """Przelicza czasy triggerów dla danego dnia. Jeśli invariant złamany — clamp + warning.
    Wywołać przy starcie (init) i przy każdym _on_midnight."""
    rise, sset = sun_times_cet(y, m, d)
    dst = 60 if is_dst(y, m, d, h) else 0

    wo = _window_open_local(self.config, rise, dst)
    wc = _window_close_local(self.config, sset, dst)
    ao = _abs_open_local(self.config, rise, dst)
    ac = _abs_close_local(self.config, sset, dst)

    warnings = []
    if ao < wo:
        warnings.append(f"abs_open {ao//60:02d}:{ao%60:02d} < window_open {wo//60:02d}:{wo%60:02d} — clamped")
        ao = wo
    if ac < wc:
        warnings.append(f"abs_close {ac//60:02d}:{ac%60:02d} < window_close {wc//60:02d}:{wc%60:02d} — clamped")
        ac = wc
    if ao >= ac:
        warnings.append(f"abs_open >= abs_close — backstop conflict, verify config")

    self._config_warning = "; ".join(warnings)
    if self._config_warning:
        _log_warning(self._config_warning)
    self._today_times = (wo, wc, ao, ac)
```

---

## 5. Tryby Pracy i Logika Astronomiczna

### 5.1 Priorytety Decyzji

**Czujnik światła = decyzja główna. Czas = zabezpieczenie.**

W obu trybach:
- Drzwi otwierają się gdy `5× lux > lux_open` **ORAZ** aktualny czas jest w oknie dziennym (blokuje nocne otwarcie od sztucznego światła)
- Drzwi zamykają się gdy `5× lux < lux_close` **ORAZ** aktualny czas jest **poza** oknem dziennym (ochrona przed burzą = 5-próbkowa jednomyślność; warunek czasu blokuje zamykanie w środku dnia)
- Absolutny override (backstop): wymusza open/close gdy sensor nie zadziałał do granicznej godziny. abs są **zewnętrzną** granicą — sensor operuje wewnątrz. Przykład semantyki: "z jakiegoś powodu czujnik nie wykrył zachodu (latarnia, księżyc, awaria) → na pewno jest noc → bezwzględnie zamknij"

### 5.2 Tryby — trzy niezależne sekcje konfiguracji

Okno sensora, siłowe otwarcie i siłowe zamknięcie konfigurowane niezależnie — każde ma własny `mode`.

**Okno sensora (`window.mode`)** — kiedy czujnik światła jest aktywny:
- `legacy`: sztywne godziny `hour_open`–`hour_close` (domyślnie 06:00–18:00)
- `sun_position`: `[sunrise_cet + sunrise_offset_min, sunset_cet + sunset_offset_min]`

**Siłowe otwarcie (`override_open.mode`)** — zabezpieczenie gdy sensor nie otworzył (awaria, zasłonięcie):
- `fixed`: otwórz siłowo o `fixed_hour` (czas lokalny)
- `dynamic`: otwórz siłowo `after_sunrise_min` minut PO wschodzie słońca

**Siłowe zamknięcie (`override_close.mode`)** — zabezpieczenie gdy sensor nie zamknął (lampa, sztuczne światło):
- `fixed`: zamknij siłowo o `fixed_hour` (czas lokalny)
- `dynamic`: zamknij siłowo `after_sunset_min` minut PO zachodzie słońca

### Invariant konfiguracyjny

abs są **zewnętrzną** granicą — sensor operuje wewnątrz. Musi zachodzić:
```
window_open_local <= abs_open_local
abs_open_local < abs_close_local
abs_close_local >= window_close_local
```

**Przy load/save config** — twarda walidacja, `ConfigError` (użytkownik jest przy urządzeniu):
```python
if abs_open_local < window_open_local:
    raise ConfigError("abs_open musi być >= window_open (abs to backstop, nie pierwszy trigger)")
if abs_close_local < window_close_local:
    raise ConfigError("abs_close musi być >= window_close")
```

**Runtime (codziennie o północy i przy starcie)** — miękka walidacja przez `_resolve_times()`:
- Invariant może być **sezonowo wrażliwy** przy mieszaniu trybów (`legacy` okno + `dynamic` abs lub odwrotnie), bo jedna wartość jest stała a druga zmienia się z astronomią
- `_resolve_times()` liczy czasy dla nowego dnia: jeśli invariant złamany → clamp (`ao = max(ao, wo)`, `ac = max(ac, wc)`) + `_log_warning()` + `self._config_warning` (eksponowany w `/api/status` i UI dashboard)
- Urządzenie **nigdy nie zatrzymuje działania** z powodu naruszenia invariantu w runtime — degraduje łagodnie (backstop może odpalić razem z otwarciem okna sensora zamiast później)

Przykład — sztywne rano + dynamiczne wieczorem:
```json
"override_open":  { "mode": "fixed",   "fixed_hour": 7 },
"override_close": { "mode": "dynamic", "after_sunset_min": 120 }
```

### 5.3 Aproksymacja Astronomiczna (Tarnów, ~50°N, 21°E)

Bazowe stałe w czasie **standardowym CET (UTC+1)**, bez DST:

| Przesilenie | DOY | Wschód (min od północy) | Zachód (min od północy) |
|-------------|-----|------------------------|------------------------|
| Zimowe (21 gru) | 355 | 453 (07:33 CET) | 943 (15:43 CET) |
| Letnie (21 cze) | 172 | 206 (03:26 CET) | 1193 (19:53 CET) |

Kosinusoidalna aproksymacja (math.cos dostępny w MicroPython):

```python
import math

_RISE_MEAN  = (453 + 206) / 2   # = 329.5 min
_RISE_AMP   = (453 - 206) / 2   # = 123.5 min
_SET_MEAN   = (943 + 1193) / 2  # = 1068.0 min
_SET_AMP    = (1193 - 943) / 2  # = 125.0 min
_SUMMER_DOY = 172               # 21 czerwca, rok nieprzestępny; w latach przestępnych = 173 (błąd ±1d ≈ ±4min — mieści się w dokładności aproksymacji)

def day_of_year(y, m, d):
    DAYS_BEFORE = [0, 0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    doy = DAYS_BEFORE[m] + d
    if m > 2 and (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)):
        doy += 1
    return doy

def sun_times_cet(y, m, d):
    """Zwraca (wschód_min, zachód_min) w czasie CET (UTC+1), bez DST."""
    doy = day_of_year(y, m, d)
    angle = 2 * math.pi * (doy - _SUMMER_DOY) / 365
    rise = int(_RISE_MEAN - _RISE_AMP * math.cos(angle))
    sset = int(_SET_MEAN  + _SET_AMP  * math.cos(angle))
    return rise, sset
```

Weryfikacja:
- doy=172: `cos(0)=1` → rise=329.5−123.5=**206** ✓, set=1068+125=**1193** ✓
- doy=355: `cos≈−1` → rise≈329.5+123.5=**453** ✓, set≈1068−125=**943** ✓  (kąt 3.149 rad, aproksymacja ±kilka min)

### 5.4 DST (Czas Letni / Zimowy)

**Zasada:** DS3231 zawsze przechowuje czas **CET (UTC+1)**. Nigdy nie przestawiać zegara przy zmianie czasu.

DST offset (`+60 min`) dodawany wyłącznie w warstwie logicznej do:
- godziny wyświetlanej na dashboardzie
- obliczeń triggerów (`sun_times_cet()` zwraca CET → przy porównaniu z lokalnym czasem dodać offset)

Europejskie reguły DST:
- Start: ostatnia niedziela marca o **02:00 CET** (wg RTC)
- Koniec: ostatnia niedziela października o **02:00 CET** (wg RTC; odpowiada CEST 03:00)

```python
def _day_of_week(y, m, d):
    """Zwraca 0=Nd, 1=Pn, ..., 6=Sb (Tomohiko Sakamoto)."""
    t = [0, 3, 2, 5, 0, 3, 5, 1, 4, 6, 2, 4]
    if m < 3: y -= 1
    return (y + y//4 - y//100 + y//400 + t[m-1] + d) % 7

def _last_sunday(y, m):
    """Dzień miesiąca ostatniej niedzieli."""
    dim = [0,31,28,31,30,31,30,31,31,30,31,30,31][m]
    if m == 2 and (y%4==0 and (y%100!=0 or y%400==0)): dim = 29
    dow = _day_of_week(y, m, dim)  # 0=Nd
    return dim - dow               # cofnij o tyle dni ile trzeba (dim jeśli dim=Nd)

def is_dst(y, m, d, h):
    """True gdy CEST aktywny. RTC przechowuje CET."""
    if m < 3 or m > 10: return False
    if 3 < m < 10:       return True
    ls = _last_sunday(y, m)
    if m == 3:
        if d != ls: return d > ls
        return h >= 2   # DST start o 02:00 CET
    else:  # m == 10
        if d != ls: return d < ls
        return h < 2    # DST koniec o 02:00 CET

def local_minutes(y, m, d, h, minute):
    # Może zwrócić >1440 podczas CEST przy CET 23:xx (lokalna północ = CET 23:00).
    # Porównania z window_*/abs_* są poprawne — te wartości zawsze <1440.
    return h * 60 + minute + (60 if is_dst(y, m, d, h) else 0)
```

Przeliczenie czasów triggerów dla danego dnia (`_resolve_times()` — wywoływane raz dziennie):
```python
def _window_open_local(cfg, rise_cet, dst):
    if cfg.window.mode == 'sun_position':
        return rise_cet + dst + cfg.window.sun.sunrise_offset_min
    return cfg.window.legacy.hour_open * 60

def _window_close_local(cfg, set_cet, dst):
    if cfg.window.mode == 'sun_position':
        return set_cet + dst + cfg.window.sun.sunset_offset_min
    return cfg.window.legacy.hour_close * 60

def _abs_open_local(cfg, rise_cet, dst):
    if cfg.override_open.mode == 'dynamic':
        return rise_cet + dst + cfg.override_open.after_sunrise_min
    return cfg.override_open.fixed_hour * 60

def _abs_close_local(cfg, set_cet, dst):
    if cfg.override_close.mode == 'dynamic':
        return set_cet + dst + cfg.override_close.after_sunset_min
    return cfg.override_close.fixed_hour * 60
```

`tick()` używa `self._today_times` (przeliczone raz przy starcie i o północy przez `_resolve_times()`):
```python
wo, wc, ao, ac = self._today_times
now = local_minutes(y, m, d, h, minute)

sensor_open  = (wo <= now < wc) and all(v > cfg.light.lux_open  for v in self.lux_buffer)
sensor_close = (now >= wc or now < wo) and all(v < cfg.light.lux_close for v in self.lux_buffer)
abs_open     = ao <= now < wc    # backstop wewnątrz okna
abs_close    = now >= ac or now < wo  # backstop w strefie nocnej
```

### 5.5 Schemat config.json

```json
{
  "window": {
    "mode": "sun_position",
    "legacy": { "hour_open": 6, "hour_close": 18 },
    "sun":    { "sunrise_offset_min": -30, "sunset_offset_min": 30 }
  },
  "override_open": {
    "mode": "dynamic",
    "fixed_hour": 8,
    "after_sunrise_min": 120
  },
  "override_close": {
    "mode": "fixed",
    "fixed_hour": 22,
    "after_sunset_min": 120
  },
  "light": {
    "lux_open": 8.0,
    "lux_close": 3.0
  },
  "safety": {
    "move_timeout_s": 21
  }
}
```

Każda z trzech sekcji (`window`, `override_open`, `override_close`) ma własny `mode` — niezależne.
Nieużywane pola (np. `fixed_hour` gdy `mode=dynamic`) ignorowane — trzymane w JSON jako fallback.

Walidacja przy `config.load()` i `POST /api/config` (po przeliczeniu wartości lokalnych dla bieżącego dnia):
```python
def validate_config(cfg, now_local_minutes, rise_cet, set_cet, dst_offset):
    wo = window_open_local(cfg, rise_cet, dst_offset)
    wc = window_close_local(cfg, set_cet, dst_offset)
    ao = abs_open_local(cfg, rise_cet, dst_offset)
    ac = abs_close_local(cfg, set_cet, dst_offset)
    if ao < wo:
        raise ConfigError(f"abs_open ({ao//60:02d}:{ao%60:02d}) < window_open ({wo//60:02d}:{wo%60:02d})")
    if ac < wc:
        raise ConfigError(f"abs_close ({ac//60:02d}:{ac%60:02d}) < window_close ({wc//60:02d}:{wc%60:02d})")
    if ao >= ac:
        raise ConfigError(f"abs_open ({ao//60:02d}:{ao%60:02d}) >= abs_close ({ac//60:02d}:{ac%60:02d})")
```

---

## 6. Web UI

### Stack (bez narzędzi build, mieści się w flash ESP32)

- **Pico.css** (~10KB) — semantyczny HTML, zero class-spamu
- **HTMX** (~14KB) — deklaratywne AJAX, microdot zwraca fragmenty HTML
- **SSE** — server-sent events dla live statusu bez pollingu
- **Inline SVG** — wykres historii lux (ostatnie N odczytów z RAM)
- **Vanilla JS** — minimum, tylko przycisk sync czasu

### Strony

| Strona | URL | Zawartość |
|--------|-----|-----------|
| Status | `/` | Badge stanu, lux[5], czas RTC, wykres lux SVG, stany krańcówek, stan baterii, czas ostatniego otwarcia/zamknięcia, czas działania silnika; **panel "Harmonogram dziś"**; **wykres 30-dniowy** |
| Konfiguracja | `/config` | Progi lux, godziny okna, godziny absolutne, timeout safety. POST → config.json |
| Logi | `/logs` | RAM ring buffer: czas, typ zdarzenia, lux przy zdarzeniu |
| Debug | `/debug` | Surowe wartości I2C, link WebREPL, przycisk reboot, wersja firmware |

**Panel "Harmonogram dziś"** — wyświetlany na stronie Status:

| Pole | Wartość |
|------|---------|
| Okno sensora | `HH:MM – HH:MM` (wo → wc) |
| Backstop otwarcia | `HH:MM` (ao) |
| Backstop zamknięcia | `HH:MM` (ac) |
| Ostrzeżenie | jeśli `config_warning` niepusty → żółty baner z treścią |

W trybie `legacy` wartości stałe (nie zmieniają się z dniem) — panel nadal pokazuje ich wartość dla spójności UI.

**Wykres 30-dniowy** — SVG generowany server-side z danych `/api/forecast` (patrz §6.2).

### REST API

```
GET  /api/status    → {state, lux[5], time, limit_top, limit_bottom, vbat_v, config_warning}
GET  /api/events    → SSE stream (status JSON co 2s)
GET  /api/config    → config.json
POST /api/config    → zapis config.json
GET  /api/logs      → logs.bin jako JSON array (dekodowany binarny circular buffer)
GET  /api/forecast  → [[wo,wc,ao,ac], ...] — 30 wpisów (domyślnie), każdy = minuty od północy
POST /api/time      → {timestamp_ms: <Date.now()>} → konwersja UTC→CET/CEST → ustawia DS3231
POST /api/reboot    → miękki restart
```

`/api/forecast` — dane czysto obliczeniowe, zero storage. Parametr opcjonalny `?days=N` (max 90). Przykład odpowiedzi:
```json
[[360, 1080, 420, 1320], [358, 1082, 418, 1322], ...]
```
Każdy wpis: `[window_open_min, window_close_min, abs_open_min, abs_close_min]`. Wartości po clampie (identyczne jak `_resolve_times()`).

### 6.2 Wykres 30-dniowy (SVG server-side)

SVG generowany przez endpoint `/` bezpośrednio z danych forecast — brak client-side JS.

**Canvas:** `viewBox="0 0 300 160"`. Marginesy: lewy 30px (oś Y etykiety), dolny 16px (oś X etykiety), górny 4px.

**Mapowanie:**
- X: dzień 0–29 → px `30 + day * (270/29)`
- Y: minuty 0–1440 → px `4 + minutes * (140/1440)` (00:00 = góra, 24:00 = dół)

**Warstwy (kolejność rysowania):**

| Warstwa | Element SVG | Kolor | Znaczenie |
|---------|------------|-------|-----------|
| Tło nocne | `<polygon>` (dwa: górny + dolny pas) | `rgba(30,30,60,0.25)` | Strefy poza oknem sensora (noc) |
| Okno sensora | `<polygon>` (kontur wo→wc przez 30 dni) | `rgba(80,200,80,0.30)` | Aktywne okno czujnika światła |
| Backstop open | `<polyline>` przez ao | `#f90` stroke-width=1 stroke-dasharray=`3,2` | Granica siłowego otwarcia |
| Backstop close | `<polyline>` przez ac | `#f90` stroke-width=1 stroke-dasharray=`3,2` | Granica siłowego zamknięcia |
| Dziś | `<line>` pionowa x=30 | `#fff` opacity=0.4 | Bieżący dzień |
| Oś Y | `<text>` co 6h: 00/06/12/18/24 | `#888` font-size=8 | |
| Oś X | `<text>` co 7 dni: +0/+7/+14/+21/+28 | `#888` font-size=8 | |

**Polygon okna sensora** — budowany z danych forecast:
```python
# Górna krawędź (wo przez wszystkie dni)
top_pts = " ".join(f"{x(i):.0f},{y(d[0]):.0f}" for i, d in enumerate(forecast))
# Dolna krawędź (wc od końca)
bot_pts = " ".join(f"{x(i):.0f},{y(d[1]):.0f}" for i, d in reversed(list(enumerate(forecast))))
svg += f'<polygon points="{top_pts} {bot_pts}" fill="rgba(80,200,80,0.30)"/>'
```

**Polygon nocny** — dwa obszary: 00:00→wo i wc→24:00. Budowane analogicznie (górny: y=4 do wo, dolny: wc do y=144).

**Generowanie w MicroPython:** endpoint `/` wywołuje `ctrl.get_forecast(30)` (zwraca list of tuples po clampie), buduje SVG string przez string concatenation (brak f-string issue w MicroPython ≥1.19). ~2–4KB SVG dla 30 dni.

---

**POST /api/time — konwersja UTC→CET:**
`Date.now()` = UTC ms. DS3231 zawsze trzyma CET (UTC+1) — nigdy CEST. Handler dodaje zawsze +1h:
```python
offset_h = 1  # zawsze CET (UTC+1), DST stosowany tylko w warstwie logicznej
```

---

## 7. Debug / Development

### WebREPL

```python
# boot.py
import webrepl
webrepl.start(password='kurnik')
```

Połączenie: przeglądarka → `https://micropython.org/webrepl/` → host `192.168.4.1:8266`

Zastosowania: live REPL, upload plików podczas developmentu, inspekcja zmiennych.

### mpremote (z PC)

```bash
# Upload pliku
mpremote connect COM<N> fs cp hardware.py :hardware.py

# Uruchom skrypt testowy
mpremote connect COM<N> run test_motor.py

# REPL
mpremote connect COM<N> repl
```

### Strona /debug

Surowe odczyty I2C, nazwa stanu enum, uptime, VBAT — diagnostyka bez telefonu/REPL w terenie.
GPIO18/19 wolne (USB D-/D+) — USB działa normalnie przez cały cykl życia urządzenia.

---

## 8. Testowanie Offline (TDD / Mocking)

Główna logika i serwer WWW muszą działać na CPython (PC) bez fizycznego ESP32. Praca na sprzęcie tylko do weryfikacji end-to-end.

### 8.0 compat.py — MicroPython/CPython shims

`state.py` i `hardware.py` importują z `compat` zamiast bezpośrednio z `utime`/`uasyncio`:

```python
# compat.py
import sys
if sys.implementation.name == 'micropython':
    from utime import ticks_ms, ticks_diff
    from uasyncio import sleep_ms
else:
    import time as _time
    def ticks_ms(): return int(_time.time() * 1000)
    def ticks_diff(a, b): return a - b
    async def sleep_ms(ms):
        import asyncio
        await asyncio.sleep(ms / 1000)
```

### 8.1 Zasada — Dependency Injection wszędzie

`CoopController` (maszyna stanów) przyjmuje obiekty sprzętowe w konstruktorze. Żadnych globalnych importów `machine` w logice biznesowej.

```python
# state.py
class CoopController:
    def __init__(self, motor, rtc, light_sensor, buttons, leds, config):
        self.motor = motor
        self.rtc = rtc
        self.light = light_sensor
        ...
```

```python
# main.py (na ESP32)
from hardware import Motor, RTC, LightSensor, Button, LED
ctrl = CoopController(Motor(...), RTC(), LightSensor(), ...)

# tests/conftest.py (na PC)
from mock_hardware import MockMotor, MockRTC, MockLightSensor, MockButton, MockLED
ctrl = CoopController(MockMotor(), MockRTC(), MockLightSensor(), ...)
```

### 8.2 Plik `mock_hardware.py`

Implementacje bez importów `machine`/`uasyncio`. Programowalne z poziomu testów.

```python
class MockRTC:
    def __init__(self): self._dt = (2024, 6, 1, 6, 0, 0, 0, 0)
    def datetime(self): return self._dt
    def set_datetime(self, dt): self._dt = dt  # test hook

class MockLightSensor:
    def __init__(self): self._lux = 10.0
    def read_lux(self): return self._lux   # pojedynczy odczyt
    def set_lux(self, v): self._lux = v    # test hook

class MockMotor:
    def __init__(self): self.commands = []
    def forward(self): self.commands.append('forward')
    def backward(self): self.commands.append('backward')
    def stop(self): self.commands.append('stop')

class MockButton:
    def __init__(self): self._pressed = False
    def is_pressed(self): return self._pressed
    def press(self): self._pressed = True  # test hook

class MockLED:
    def __init__(self): self.state = 'off'; self.blinking = False
    def on(self): self.state = 'on'
    def off(self): self.state = 'off'
    def blink(self): self.blinking = True
    def stop_blink(self): self.blinking = False
```

### 8.3 Microdot na PC

`web.py` importuje `CoopController` przez DI — nie importuje `hardware.py` bezpośrednio. Uruchomienie na localhost:

```python
# run_local.py — tylko development
from mock_hardware import MockMotor, MockRTC, MockLightSensor, MockButton, MockLED
from state import CoopController
from web import create_app

ctrl = CoopController(MockMotor(), MockRTC(), MockLightSensor(), ...)
app = create_app(ctrl)
app.run(host='localhost', port=5000)  # microdot działa na CPython
```

Frontend testowalny w przeglądarce pod `http://localhost:5000` bez sprzętu.

### 8.4 Struktura testów

```
tests/
  conftest.py       — pytest fixtures (mock hardware + skonfigurowany ctrl)
  test_state.py     — logika maszyny stanów
  test_api.py       — endpointy microdot (pytest-asyncio)
  test_sensors.py   — warunki shouldOpen/shouldClose
mock_hardware.py    — fałszywe implementacje
run_local.py        — uruchomienie web UI lokalnie
```

Treść `tests/conftest.py`:

```python
import pytest
from mock_hardware import MockMotor, MockRTC, MockLightSensor, MockButton, MockLED, MockPCF
from state import CoopController, GateState
from web import create_app
from config import default_config

@pytest.fixture
def ctrl():
    c = CoopController(
        motor=MockMotor(), rtc=MockRTC(), light_sensor=MockLightSensor(),
        pcf=MockPCF(), limit_top=MockButton(), limit_bottom=MockButton(),
        btn_open=MockButton(), btn_close=MockButton(),
        leds=(MockLED(), MockLED(), MockLED()), config=default_config()
    )
    c.state = GateState.IDLE_CLOSED
    c.lux_buffer = [10.0] * 5
    c.lux_ready = True
    return c

@pytest.fixture
def client(ctrl):
    app = create_app(ctrl)
    return app.test_client()
```

### 8.5 Scenariusze testowe (pytest)

```python
# tests/test_state.py
# Testy ustawiają ctrl.lux_buffer bezpośrednio — light_sensor_loop nie działa w testach.

def test_morning_open(ctrl):
    ctrl.rtc.set_datetime((2024, 6, 1, 8, 1, 0, 0, 0))  # 08:01 CET
    ctrl.lux_buffer = [10.0] * 5                          # jasno
    ctrl.tick()
    assert ctrl.state == GateState.MOVING_OPEN

def test_evening_close(ctrl):
    ctrl.rtc.set_datetime((2024, 6, 1, 18, 1, 0, 0, 0))  # 18:01 CET
    ctrl.lux_buffer = [1.0] * 5                           # ciemno
    ctrl.state = GateState.IDLE_OPEN
    ctrl.tick()
    assert ctrl.state == GateState.MOVING_CLOSE

def test_cloud_no_premature_close(ctrl):
    """Tylko 3/5 próbek poniżej progu — brak zamknięcia."""
    ctrl.rtc.set_datetime((2024, 6, 1, 14, 0, 0, 0, 0))  # 14:00
    ctrl.state = GateState.IDLE_OPEN
    ctrl.lux_buffer = [10.0, 10.0, 1.0, 1.0, 10.0]      # chmura — 3/5 poniżej
    ctrl.tick()
    assert ctrl.state == GateState.IDLE_OPEN  # bez zmian

def test_absolute_close_overrides_light(ctrl):
    """22:00 → zamknij bez względu na lux."""
    ctrl.rtc.set_datetime((2024, 6, 1, 22, 0, 0, 0, 0))
    ctrl.lux_buffer = [50.0] * 5  # bardzo jasno
    ctrl.state = GateState.IDLE_OPEN
    ctrl.tick()
    assert ctrl.state == GateState.MOVING_CLOSE

def test_manual_hold_open_blocks_auto_close(ctrl):
    """Po ręcznym otwarciu: logika auto nie zamknie przed absolutnym triggerem."""
    ctrl.state = GateState.MANUAL_HOLD_OPEN
    ctrl.rtc.set_datetime((2024, 6, 1, 19, 0, 0, 0, 0))  # po hourClose
    ctrl.lux_buffer = [1.0] * 5
    ctrl.tick()
    assert ctrl.state == GateState.MANUAL_HOLD_OPEN

def test_manual_hold_closed_blocks_auto_open(ctrl):
    """Po ręcznym zamknięciu: logika auto nie otworzy przed absolutnym triggerem."""
    ctrl.state = GateState.MANUAL_HOLD_CLOSED
    ctrl.rtc.set_datetime((2024, 6, 1, 9, 0, 0, 0, 0))   # w oknie dziennym
    ctrl.lux_buffer = [50.0] * 5
    ctrl.tick()
    assert ctrl.state == GateState.MANUAL_HOLD_CLOSED

def test_safety_stop_on_timeout(ctrl):
    """Silnik nie osiąga krańcówki → tick() wykrywa timeout → SAFETY_STOP."""
    ctrl.state = GateState.MOVING_OPEN
    ctrl.simulate_timeout()   # cofa _move_start_ms poza granicę timeout
    ctrl.tick()
    assert ctrl.state == GateState.SAFETY_STOP
    assert 'stop' in ctrl.motor.commands

# tests/test_api.py

@pytest.mark.asyncio
async def test_api_status(client):
    res = await client.get('/api/status')
    data = res.json
    assert 'state' in data
    assert 'lux' in data
    assert len(data['lux']) == 5

@pytest.mark.asyncio
async def test_api_time_sync(client, ctrl):
    ts = 1717228800000  # 2024-06-01 08:00:00 UTC w ms
    res = await client.post('/api/time', json={'timestamp_ms': ts})
    assert res.status_code == 200
    dt = ctrl.rtc.datetime()
    assert dt[3] == 9   # RTC zawsze CET (UTC+1): 08:00 UTC + 1h = 09:00 CET
```

### 8.6 Uruchamianie

```bash
# Instalacja (jednorazowo)
pip install microdot pytest pytest-asyncio

# Testy jednostkowe
pytest tests/ -v

# Web UI lokalnie (frontend dev)
python run_local.py
# → http://localhost:5000

# Upload na ESP32 po weryfikacji na PC
mpremote connect COM<N> fs cp compat.py :compat.py
mpremote connect COM<N> fs cp hardware.py :hardware.py
mpremote connect COM<N> fs cp state.py :state.py
mpremote connect COM<N> fs cp main.py :main.py
```

---

## 9. Zarządzanie Energią i Interfejs Web

**Architektura:** asyncio ciągły (nie lightsleep). WiFi AP domyślnie OFF — ~8-10mA baseline przy 40MHz.
DRV8833 nSLEEP (GPIO21): sleep gdy silnik stoi (~2µA vs ~3mA active) — 1000× redukcja quiescent DRV8833.

**Taktowanie:** `machine.freq(40_000_000)` w `boot.py`. WiFi wymaga min 80MHz — `start_ap_session()` musi przełączyć przed aktywacją AP:

```python
async def start_ap_session():
    machine.freq(80_000_000)   # wymagane przez WiFi
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    # ... serwer ...
    ap.active(False)
    machine.freq(40_000_000)   # powrót
```

```python
import network
ap = network.WLAN(network.AP_IF)
ap.active(False)  # domyślnie off
```

### 9.1 Interfejs Web (AP On-Demand) — Multi-Click

**Przypisanie kliknięć** (każdy przycisk niezależnie):

| Kliknięcia | BTN_OPEN (GPIO7) | BTN_CLOSE (GPIO10) |
|-----------|----------------------|---------------------|
| 1× | `ctrl.manual_move('open')` | `ctrl.manual_move('close')` |
| ≥2× | Uruchom WiFi AP + serwer microdot | Uruchom WiFi AP + serwer microdot |

Dwa osobne taski — ten sam `button_monitor`, różna akcja:
```python
asyncio.create_task(button_monitor(btn_open,  'open',  ctrl))
asyncio.create_task(button_monitor(btn_close, 'close', ctrl))
```

**Implementacja multi-click debounce:**

```python
CLICK_WINDOW_MS = 500   # okno zliczania kliknięć
DEBOUNCE_MS     = 50    # filtr drgań styków

async def button_monitor(btn, action, ctrl):
    click_count = 0
    deadline    = 0

    while True:
        if btn.value() == 0:                          # naciśnięty (pull-up)
            await asyncio.sleep_ms(DEBOUNCE_MS)
            if btn.value() != 0:                      # fałszywy impuls
                continue
            click_count += 1
            deadline = time.ticks_ms() + CLICK_WINDOW_MS
            while btn.value() == 0:                   # czekaj na zwolnienie
                await asyncio.sleep_ms(10)

        if click_count > 0 and time.ticks_diff(deadline, time.ticks_ms()) <= 0:
            if click_count == 1:
                ctrl.manual_move(action)              # 'open' lub 'close' — sync, tworzy task wewnętrznie
            else:
                asyncio.create_task(start_ap_session())  # ≥2 kliknięcia → AP
            click_count = 0

        await asyncio.sleep_ms(20)
```

**Watchdog AP — wyłączenie po 10 minutach bezczynności:**

```python
AP_TIMEOUT_MS = 10 * 60 * 1000
_last_http_ms  = 0

# Middleware microdot — aktualizuj timestamp każdego żądania
@app.before_request
async def _touch(req):
    global _last_http_ms
    _last_http_ms = time.ticks_ms()

async def ap_watchdog(ap, server):
    global _last_http_ms
    _last_http_ms = time.ticks_ms()
    while True:
        await asyncio.sleep(60)
        idle = time.ticks_diff(time.ticks_ms(), _last_http_ms)
        if idle > AP_TIMEOUT_MS:
            server.shutdown()
            ap.active(False)
            return               # task kończy się, system wraca do sleep cyklu
```

**Sekwencja uruchomienia AP:**

```python
_ap_active = False

async def start_ap_session():
    global _ap_active
    if _ap_active: return             # guard: nie startuj drugiej sesji
    _ap_active = True
    machine.freq(80_000_000)          # WiFi wymaga min 80MHz
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(ssid='Kurnik_Control', password='kurnik123')
    app = create_app(ctrl)
    asyncio.create_task(ap_watchdog(ap, app))
    await app.start_server(port=80)   # blokuje do shutdown()
    ap.active(False)
    machine.freq(40_000_000)          # powrót do trybu oszczędnego
    _ap_active = False
```

---

## 10. Procedura Pierwszego Uruchomienia

1. Flash MicroPython na ESP32-C3 przez `esptool` (USB)
2. Upload plików przez `mpremote` via USB — `main.py` uploaduj **ostatni**
3. Zasilić → `boot.py` wykrywa first-boot (DS3231 domyślna data `2000-01-01`) → AP `Kurnik_Control` startuje automatycznie
4. Podłączyć telefon/laptop do AP → otworzyć `192.168.4.1`
5. Kliknąć "Synchronizuj czas z przeglądarki" → POST `/api/time` → konwersja UTC→CET → ustawia DS3231
   ⚠️ **Do synchronizacji czasu automatyczne otwieranie/zamykanie nieaktywne** (`control_loop` pomija logikę gdy y < 2020). Manualne przyciski działają normalnie.
6. Zweryfikować detekcję krańcówek na stronie statusu
7. Przetestować ręczne open/close przez web UI
8. Dostosować progi na stronie Config jeśli trzeba
9. Kolejne uruchomienia: WiFi OFF domyślnie, AP przez multi-click (≥2×) na przycisku

---

## 11. PCF8574 — Driver i Okablowanie

### PCF8574 Driver (minimal, `hardware.py`)

```python
class PCF8574:
    def __init__(self, i2c, addr=0x20):
        self._i2c = i2c
        self._addr = addr
        self._out = 0xFF  # wszystkie HIGH: LEDs off, inputs float high

    def _write(self):
        self._i2c.writeto(self._addr, bytes([self._out]))

    def set_pin(self, pin, val):
        if val: self._out |=  (1 << pin)
        else:   self._out &= ~(1 << pin)
        self._write()

    def get_pin(self, pin):
        return bool(self._i2c.readfrom(self._addr, 1)[0] & (1 << pin))

    def read_all(self):
        return self._i2c.readfrom(self._addr, 1)[0]
```

### LED API (active-low przez PCF8574)

```python
# P0=LED_GREEN, P1=LED_YELLOW, P2=Motor IN1, P3=Motor IN2
def led_on(pcf, pin):  pcf.set_pin(pin, 0)  # active-low
def led_off(pcf, pin): pcf.set_pin(pin, 1)
```

Polling w `button_monitor`: `asyncio.sleep_ms(20)` (wymagane dla multi-click detection w oknie 500ms).
BTN_OPEN (GPIO7) i BTN_CLOSE (GPIO10) oba direct GPIO — działają niezależnie od I2C.

---

## 12. Logi Zdarzeń — Format Binarny (LittleFS)

### Format rekordu (8 bajtów)

```python
import struct
RECORD_FMT  = '>HHHBB'  # big-endian: 2+2+2+1+1 = 8 bajtów
RECORD_SIZE = 8

# pola:
#   days_since_2025 : uint16  (0 = 2025-01-01, wystarczy ~179 lat)
#   open_min        : uint16  (minuty od północy; 0xFFFF = brak otwarcia tego dnia)
#   close_min       : uint16  (minuty od północy; 0xFFFF = brak zamknięcia)
#   manual_count    : uint8   (ręczne interwencje w ciągu dnia)
#   error_count     : uint8   (safety_stop incydenty)
```

### Plik `logs.bin`

```
Offset  Rozmiar  Zawartość
0       4 B      total_written (uint32, big-endian) — łączna liczba zapisanych rekordów
4       N×8 B    rekordy (circular buffer, rośnie do 365×8=2920 B organicznie)
Łącznie docelowo: 2924 bajtów
```

Pochodne:
- `write_pos = total_written % MAX_RECORDS`
- `valid_count = min(total_written, MAX_RECORDS)`
- `bufor_pełny = total_written >= MAX_RECORDS`

### Strategia

- **W RAM:** bieżący dzienny rekord (`_today_rec`) aktualizowany przy każdym zdarzeniu
  - pierwsze otwarcie dnia → `open_min`
  - ostatnie zamknięcie dnia → `close_min`
  - każde ręczne → `manual_count++`
  - każdy safety_stop → `error_count++`
- **Zapis do flash:** 1× na dobę o północy (ten sam punkt co midnight task)
- **Circular buffer:** `write_index = (write_index + 1) % 365` po każdym zapisie

```python
LOG_PATH     = '/logs.bin'
MAX_RECORDS  = 365
HEADER_SIZE  = 4

def _init_log():
    """Wywołać przy starcie (boot.py). Tworzy plik tylko jeśli nie istnieje — 4 bajty."""
    try:
        open(LOG_PATH, 'rb').close()
    except OSError:
        with open(LOG_PATH, 'wb') as f:
            f.write(struct.pack('>I', 0))  # total_written = 0

def _write_record(rec):
    with open(LOG_PATH, 'r+b') as f:
        total = struct.unpack('>I', f.read(4))[0]
        idx = total % MAX_RECORDS
        f.seek(HEADER_SIZE + idx * RECORD_SIZE)
        f.write(struct.pack(RECORD_FMT, *rec))
        f.seek(0)
        f.write(struct.pack('>I', total + 1))

def read_records():
    with open(LOG_PATH, 'rb') as f:
        total = struct.unpack('>I', f.read(4))[0]
        count = min(total, MAX_RECORDS)
        start = total % MAX_RECORDS if total >= MAX_RECORDS else 0
        records = []
        for i in range(count):
            idx = (start + i) % MAX_RECORDS
            f.seek(HEADER_SIZE + idx * RECORD_SIZE)
            records.append(struct.unpack(RECORD_FMT, f.read(RECORD_SIZE)))
    return records
```

Plik rośnie organicznie: 4 B przy init → +8 B dziennie → 2924 B po 365 dniach. Zero pre-alokacji, zero fill.

---

## 13. Monitoring Napięcia Baterii RTC

DS3231 ZS-042: pin VBAT wyprowadzony na padzie. CR2032 pod obciążeniem: ~3.0V nominalne.
Brak dzielnika napięcia — direct connect do GPIO3 (ADC1_CH3, max 3.6V).

```python
from machine import ADC, Pin

vbat_adc = ADC(Pin(3), atten=ADC.ATTN_11DB)  # zakres 0–3.6V

def read_vbat_v():
    return vbat_adc.read_uv() / 1_000_000

# Próg w control_loop:
# read_vbat_v() < 2.7  →  LED_YELLOW blink
# DS3231 VBAT spec minimum: ~2.3V
# Odczyt: co 2s razem z control_loop (brak osobnego throttlingu).
```

---

## 14. Checklist Weryfikacji

- [ ] PCF8574 odpowiada na I2C (0x20) — skan bus po starcie
- [ ] Fizyczne przyciski wyzwalają przejścia stanów
- [ ] Test 5 próbek: latarka na czujnik → OPEN; zasłonić 5× → CLOSE
- [ ] Histereza: brak fluktuacji przy granicy 3 lx
- [ ] Absolutny close o 22:00: mock czasu RTC, MANUAL_HOLD nadpisany
- [ ] Safety stop: zablokować silnik mechanicznie, SAFETY_STOP po 21s
- [ ] Sygnały LED zgodne ze stanem w każdym przejściu
- [ ] RAM log rejestruje zdarzenia z timestampem
- [ ] SSE live update w przeglądarce bez odświeżania
- [ ] WebREPL łączy się i akceptuje polecenia
- [ ] Config zapis → reboot → config trwa (z config.json)
- [ ] DS3231 trzyma czas po odcięciu zasilania sieciowego (CR2032)
- [ ] VBAT_SENSE: ADC odczytuje ~3.0V przy świeżej CR2032
- [ ] DRV8833 nSLEEP: ~2µA gdy silnik stoi, wybudza się przed ruchem
- [ ] nFAULT: zablokuj silnik mechanicznie → GPIO20 LOW → SAFETY_STOP
- [ ] Logi binarne: zdarzenia persist po restarcie, circular buffer nie przekracza 365 wpisów
