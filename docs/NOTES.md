# LCR-P1 firmware notes

Stock image: `APP_LCR-P1-EN_V1.1.0_260324.BIN`, 59276 bytes,
sha256 `5ccce71cec5798e9a88befe5b29613f2c38cd3f10fca85a0e64c5bed4ded85a5`.

## Hardware / memory map

- Cortex-M0 (almost no Thumb-2). The peripheral layout matches Nations N32G03x (ADC at 0x40020800,
  GPIO with slew-rate / drive-strength registers); not confirmed from a chip marking.
- Flash 64 KB, 512-byte pages:
  - `0x08000000–0x080017FF` FNIRSI IAP bootloader (not in the download)
  - `0x08001800–0x0800FF8B` application (the .BIN is linked here: the reset vector is 0x080018D9)
  - `0x0800FFD0` calibration record: `"dEnK"` (0x4B6E4564) + 32 bytes, loaded at boot by 0x080035C0.
    Without it the meter asks for the short-connector calibration.
- RAM: initialised data 0x20000000–0x2000070C, zeroed data + stack to 0x200018C8 (initial SP).
  0x20001900+ is unused by the firmware (the patch uses it).
- USART1 115200 8N1 on the CH340 (USB-C); the application only transmits.

## Update protocol (FNIRSI `BootTool.exe`, .NET, recovered from its IL)

1. Open the port at 115200 (DTR/RTS off), send `AC 55 02 03`, close, wait 10 ms, reopen.
2. Send the image in 512-byte blocks, no acknowledgements. The last block is padded with `FF` and its
   last 4 bytes are `78 56 34 12`; if the image ends at or near a block end, an extra all-`FF` block with
   that marker follows. There is no checksum.
3. Wait up to 20 s for the reply: byte 1 = `55` and byte 2 = `03` means success (seen: `ac 55 03 04`).

The meter enters update mode when ZENER is held while pressing TEST. Since the bootloader handles that,
a broken application can always be replaced.

## Useful firmware addresses (application, v1.1.0)

| Address | What |
|---|---|
| 0x0800B8B4 | `main` |
| 0x08007824 | result dispatcher (Keil `__ARM_common_switch8` at 0x080077F2 with inline tables; Ghidra can't follow these) |
| 0x08005C20 | `draw_char(x, y, ch, fg, [bg, size, mode])` 6×12 font |
| 0x080060C0 | `draw_string(x, y, str, fg, [bg, size, mode])` |
| 0x08005D24 | draw 12×12 GBK glyph; the EN firmware only uses 欧 (ohm) |
| 0x08005740 | fill rectangle `(x0, y0, x1, y1, [colour])` |
| 0x0800B2DC | `fputc` → USART1; printf at 0x08008FA4 |
| 0x0800D460 | IR result page; shows 4 bytes at 0x20000890 |
| 0x08002454 | pin probe; prints `=== Check Pin ===` (see below) |
| 0x080060F4 | 200 Hz timer ISR (IRQ20): idle counter 0x200006F0, power-off at 12000 ticks |
| 0x0800B986 | low-battery compare: `2·ADC ≤ 3300 mV` (battery screen shows reading + 200 mV) |

`docs/SeedFuncs.java` creates functions at every `bl` target after a headless import
(`-loader BinaryLoader -loader-baseAddr 0x08001800 -processor ARM:LE:32:Cortex`).

## Patch points

| What | Where | How |
|---|---|---|
| serial mirror | 7 BLs to `draw_char`, 2 to the glyph routine | → hooks in `uart_mirror.S`: send the character, newline when the text row changes, space at gaps, `ohm` for the glyph; then continue into the original routine with registers and stack untouched |
| IR decoder | BL at 0x0800BBAA (main loop → stock NEC decoder) | → `ir_poll` |
| IR capture | vector IRQ8 (EXTI line 11), image offset 0x60 | → `ir_edge_isr`: 760-edge buffer at 0x20001900 instead of 68 |
| IR frame end | TIM period literal 0x08004A24 | 15 ms → 50 ms of quiet (AC remotes have 20/40 ms gaps); gaps > 12 ms split a capture into segments |
| auto power-off | literal 0x0800615C | 12000 → 720000 (60 min hard limit); the limit itself is applied by `key_hook` (`AUTOOFF_S` at power-on, `#A` over USB) |
| serial commands | BL at 0x0800B964 (main loop → key-queue read 0x080051E0) | → `key_hook` (`control.c`): real key, else USART commands; returns TEST (0x21) for `#T` / auto-repeat |
| USART1 receive | 0x08008F5C, 0x08008F80 in the USART init | pins 0x200 → 0x600 (PA9 + PA10, unused by the firmware), mode TX → TX\|RX |
| low battery | literal 0x0800BCE0 | 3300 → `LOWBAT_MV − 200` |
| text colours | hook tail → `color_char` (`screen.c`) | white values/units/`0x` codes → purple, `…:` labels → grey, titles white |
| graphics colours | 5 BLs to 0x08006060 that send pixels (bitmap ×2, plot, char, glyph) | → `pixel_hook`: colours → purple, greys → dim; black, white, red kept |
| boot screen | logo bitmap 0x0800EECA, its colour/position, version strings | 32×32 picture in purple, centred; only a grey `1.1.0` |
| pin legend | BL 0x0800DF0C (panel), literal 0x0800E110 | panel not drawn, digits grey |

Code space (the image is full):

- A also starts in the ASCII font: glyphs `{|}~`, and E (`XYZ[\\]^`) and F (`;<=>?@`) are font glyphs no
  string ever draws; the build checks every reused glyph is in that set.
- A, B: unused slots of the GBK glyph table at 0x0800F48E (58 × 26 bytes; only entry 33, ohm, is used).
  The lookup scans every key, so the build checks no other entry starts with `c5 b7`.
- C, D: the stock NEC decoder (0x08004848–0x08004978) and stock IR edge ISR (0x08003C4C–0x08003CB0),
  dead once their only callers are redirected.
- The linker runs with `--orphan-handling=error` and fails on any code outside A–D (this caught
  hidden `memset` / libgcc division calls that do not exist in the firmware).

## Lessons

- **0x08006060 is not "write pixel".** It sends any 16-bit word to the LCD, and 7 of its 12 callers send
  window coordinates. Remapping all 12 as colours scrambled the whole screen on the meter. The screen test
  now runs the real window/pixel code against a model LCD that decodes the command stream.

- **Don't remove the `Check Pin` debug prints.** Each takes ~4 ms on the UART inside the pin probe. With
  them removed, a 1 MΩ resistor read as 2 pF: the delay is de-facto settling time.
- RAM is not cleared at boot above the firmware's own data: the IR buffer is only trusted after a
  marker word written when capture is armed.

## Keys

The key scanner (timer ISR) queues `(bit + 2) × 10 + event`: event 3 short press, 7 held 1 s, 8 released after
holding, 5 two keys. TEST = bit 1: `0x21` short (starts a test), `0x25` held (power off). ZENER = bit 2 (`0x2B`).
The main loop reads the queue at 0x0800B964 (`0x080051E0`).

## IR formats seen

- NEC: 9 ms / 4.5 ms leader, 32 bits pulse distance (560 / 1690 µs), LSB first.
- Samsung32: as NEC with a 4.5 ms / 4.5 ms leader.
- Gree / Kelvinator AC: 3 messages 40 ms apart, each 9 ms / 4.5 ms leader + 35 bits (4 bytes + `010`)
  + 20 ms gap + 32 bits. Checksum = (10 + Σ low nibbles of bytes 0–3 + Σ high nibbles of bytes 4–6)
  & 15, in the high nibble of byte 7.
