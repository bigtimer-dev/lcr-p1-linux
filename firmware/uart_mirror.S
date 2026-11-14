@ LCR-P1 v1.1.0 patch: mirror every character drawn on the LCD to USART1 (115200 8N1).
@ Placed in unused GBK glyph slots 0..32 at 0x0800F490. Hooks are reached via patched BLs.
        .syntax unified
        .cpu cortex-m0
        .thumb
        .equ DRAWCHAR,  0x08005C20      @ draw_char(x, y, ch, fg, bg, [sp]size, mode)
        .equ DRAWGLYPH, 0x08005D24      @ draw 12x12 GBK glyph; EN firmware only uses "ohm"
        .equ STATE,     0x20001F00      @ u16 lastx, u16 lasty (RAM above 0x200018C8 is unused)

        .text
        .global hook_char, hook_glyph
        .thumb_func
hook_char:
        push    {r0-r4, lr}
        ldr     r3, =STATE
        ldrh    r4, [r3, #2]            @ last y
        strh    r1, [r3, #2]
        subs    r4, r1, r4
        adds    r4, #2
        cmp     r4, #4                  @ |dy| <= 2 -> same text row
        bhi     newline
        cmp     r2, #'.'                @ float formatter puts '.' 8 px after a digit
        beq     emit
        ldrh    r4, [r3]                @ last x
        subs    r4, r0, r4
        subs    r4, #1
        cmp     r4, #5                  @ 1 <= dx <= 6 -> adjacent char (digits use 2,4,6), no separator
        bls     emit
        movs    r0, #' '
        b       sep
newline:
        movs    r0, #10
sep:
        bl      PUTC
emit:
        ldr     r3, =STATE
        ldr     r0, [sp, #0]            @ x
        strh    r0, [r3]
        ldr     r0, [sp, #8]            @ ch
        bl      PUTC
        ldr     r4, =DRAWCHAR + 1
        b       tail

        .thumb_func
hook_glyph:
        push    {r0-r4, lr}
        movs    r0, #'o'
        bl      PUTC
        movs    r0, #'h'
        bl      PUTC
        movs    r0, #'m'
        bl      PUTC
        ldr     r4, =DRAWGLYPH + 1
tail:   @ restore caller's r0-r4 and lr, continue into the original routine
        ldr     r3, [sp, #20]
        mov     lr, r3
        str     r4, [sp, #20]
        pop     {r0-r4, pc}
        .ltorg

@ Stand-in for the firmware's fputc at 0x0800B2DC (section placed there by the link script,
@ not emitted) so the linker produces correct Thumb BL offsets.
        .section .putc, "ax"
        .thumb_func
PUTC:   @ fputc: send r0 on USART1, wait TXE; clobbers r0-r3
        nop
