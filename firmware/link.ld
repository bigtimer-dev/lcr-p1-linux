/* Free space in the stock v1.1.0 image (it is full, so the patch reuses):
   A  font glyphs 'z{|}~' (never drawn) + GBK glyph slots 0..32: 0x0800F454..0x0800F7E8
   B  GBK glyph slots 34..57:                                  0x0800F804..0x0800FA72
      (the EN firmware only uses glyph 33, "ohm", at 0x0800F7E8)
   C  stock NEC decoder, dead once its only caller is redirected: 0x08004848..0x08004978
   D  stock IR edge ISR, dead once its vector is redirected:       0x08003C4C..0x08003CB0
   E  font glyphs 'XYZ[\]^' (never drawn):                       0x0800F2BC..0x0800F30E
   F  font glyphs ';<=>?@' (never drawn):                        0x0800F160..0x0800F1A6
   G  the FNIRSI boot logo bitmap (replaced by screen.c: splash): 0x0800EECC..0x0800EFA8
   C and D are only written when the IR patch is on (make_patch.py). */
SECTIONS {
  .patchA 0x0800F454 : {
    uart_mirror.o(.text)
    control.o(.text.key_hook)
    ir_decode.o(.text.rc5* .text.sony* .text.ir_edge_isr)
  }
  ASSERT(. <= 0x0800F7E8, "region A overflow")
  .patchB 0x0800F804 : { ir_decode.o(.text.ir_poll) control.o(.rodata*) }
  ASSERT(. <= 0x0800FA72, "region B overflow")
  .patchC 0x08004848 : { ir_decode.o(.text.gree* .text.pulse_distance* .text.ac_print .rodata.ac_print*) }
  ASSERT(. <= 0x08004978, "region C overflow")
  .patchD 0x08003C4C : { ir_decode.o(.text.pd_bits*) }
  ASSERT(. <= 0x08003CB0, "region D overflow")
  .patchE 0x0800F2BC : { ir_decode.o(.rodata.ir_poll*) }
  ASSERT(. <= 0x0800F30E, "region E overflow")
  .patchF 0x0800F160 : { ir_decode.o(.text.rearm) }
  ASSERT(. <= 0x0800F1A6, "region F overflow")
  .patchG 0x0800EECC : { screen.o(.text.splash .text.color_char .rodata*) }
  ASSERT(. <= 0x0800EFA8, "region G overflow")
  .putc 0x0800B2DC (NOLOAD) : { *(.putc) }
  .empty 0 (NOLOAD) : { *(.text) *(.data) *(.bss) *(COMMON) }
  ASSERT(SIZEOF(.empty) == 0, "unexpected code/data outside the placed sections")
  /DISCARD/ : { *(.comment) *(.ARM.attributes) *(.glue_7) *(.glue_7t) *(.vfp11_veneer) *(.v4_bx)
                *(.iplt) *(.rel.iplt) *(.igot.plt) }
}
