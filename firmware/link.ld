/* Free space in the stock image:
   A, B: unused GBK glyph slots (EN firmware only uses entry 33, "ohm", at 0x0800F7E8)
         A = entries 0..32 (0x0800F48E..0x0800F7E8), B = entries 34..57 (0x0800F802..0x0800FA72)
   C, D: stock code made dead by this patch (only callers redirected):
         C = stock NEC decoder 0x08004848..0x08004978 (main-loop BL now -> ir_poll)
         D = stock IR edge ISR 0x08003C4C..0x08003CB0 (IRQ8 vector now -> ir_edge_isr) */
SECTIONS {
  .patchA 0x0800F490 : {
    uart_mirror.o(.text)
    ir_decode.o(.text.rc5* .text.sony* .text.ir_edge_isr .text.ac_print)
    ir_decode.o(.rodata.ir_poll* .rodata.ac_print*)
  }
  ASSERT(. <= 0x0800F7E8, "region A overflow")
  .patchB 0x0800F804 : { ir_decode.o(.text.ir_poll) }
  ASSERT(. <= 0x0800FA72, "region B overflow")
  .patchC 0x08004848 : { ir_decode.o(.text.gree* .text.rearm .text.pulse_distance*) }
  ASSERT(. <= 0x08004978, "region C overflow")
  .patchD 0x08003C4C : { ir_decode.o(.text.pd_bits*) }
  ASSERT(. <= 0x08003CB0, "region D overflow")
  .putc 0x0800B2DC (NOLOAD) : { *(.putc) }
  .empty 0 (NOLOAD) : { *(.text) *(.data) *(.bss) *(COMMON) }
  ASSERT(SIZEOF(.empty) == 0, "unexpected code/data outside the placed sections")
  /DISCARD/ : { *(.comment) *(.ARM.attributes) *(.glue_7) *(.glue_7t) *(.vfp11_veneer) *(.v4_bx)
                *(.iplt) *(.rel.iplt) *(.igot.plt) }
}
