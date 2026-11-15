/* LCR-P1 diagnostic build (FLASH_DUMP=1): dump the FNIRSI bootloader over USB serial.
 *
 * Replaces the main-loop key hook (no serial commands, no IR patch in this build). Send 'D':
 *   #V <VTOR> <CPUID> <SP> <xPSR-free: PRIMASK>
 *   #X <addr> <32 bytes hex>        for 0x08000000 .. 0x080017FF (bootloader), 192 lines
 *   #X end
 * Used to find out how the bootloader starts the app (vector table, validity checks), which a
 * from-scratch firmware must satisfy.
 */
#include <stdint.h>

typedef int (*keyscan_t)(void);
typedef int (*putc_t)(int);
#define KEYSCAN ((keyscan_t)0x080051E1)
#define PUTC    ((putc_t)0x0800B2DD)            /* fputc -> USART1 (waits for TX empty) */
#define U_STS   (*(volatile uint16_t *)0x40013800)
#define U_DAT   (*(volatile uint16_t *)0x40013804)
#define VTOR    (*(volatile uint32_t *)0xE000ED08)
#define CPUID   (*(volatile uint32_t *)0xE000ED00)

static void hex(uint32_t v, int digits)
{
    while (digits--)
        PUTC("0123456789abcdef"[v >> (4 * digits) & 15]);
}

static void str(const char *s)
{
    while (*s)
        PUTC(*s++);
}

int key_hook(void)
{
    int k = KEYSCAN();
    if ((U_STS & 0x20) && (U_DAT & 0xFF) == 'D') {
        uint32_t sp, pm;
        __asm__ volatile("mov %0, sp" : "=r"(sp));
        __asm__ volatile("mrs %0, primask" : "=r"(pm));
        str("\n#V "); hex(VTOR, 8); PUTC(' '); hex(CPUID, 8); PUTC(' '); hex(sp, 8); PUTC(' '); hex(pm, 1);
        for (uint32_t a = 0x08000000; a < 0x08001800; a += 32) {
            str("\n#X "); hex(a, 8); PUTC(' ');
            for (int i = 0; i < 32; i++)
                hex(*(volatile uint8_t *)(a + i), 2);
        }
        str("\n#X end\n");
    }
    return k;
}
