/* LCR-P1 v1.1.0 patch: commands over the USB serial port, auto-repeat test, auto power-off.
 *
 * key_hook replaces the main loop's call to the key-queue read (0x080051E0, called at 0x0800B964).
 * It returns the real key if there is one; otherwise it reads USART1 (receive enabled by the patch,
 * RX = PA10) and can return the TEST short-press code 0x21, which the main loop handles exactly like
 * the button (and ignores on the IR page, as for the button).
 *
 * Commands: '#' + letter [+ decimal number] + CR/LF. Reply: "#<letter> R=<s> A=<s> P=<page>".
 * Every received byte is echoed with bit 7 set (screen text is ASCII, so echoes are unambiguous):
 * the USART buffers only one byte and this runs once per main-loop
 * pass, so the sender must wait for each echo before sending the next byte.
 *   #T    run a test (as pressing TEST)
 *   #R<s> repeat a test every s seconds after the previous one finished, #R0 = off
 *   #A<s> auto power-off after s seconds idle, #A0 = off (the timer ISR still powers off after 60 min)
 *   #O    power off now
 *   #?    status only
 * Settings live in RAM: after power-on, repeat is off and auto power-off is the build default.
 */
#include <stdint.h>

#ifndef AUTOOFF_DEFAULT_S
#define AUTOOFF_DEFAULT_S 300
#endif
#define TICK_HZ   200                                   /* timer ISR IRQ20, 0x080060F4 */
#define TICKS     (*(volatile uint32_t *)0x200006F4)    /* +1 every tick, always */
#define IDLE      (*(volatile uint32_t *)0x200006F0)    /* idle ticks, cleared on activity */
#define PWROFF    (*(volatile uint8_t *)0x200006ED)     /* main loop powers off when set */
#define IRF       (*(volatile uint8_t *)0x20000802)     /* bit2: IR page */
#define U_STS     (*(volatile uint16_t *)0x40013800)    /* USART1: bit5 RX not empty */
#define U_DAT     (*(volatile uint16_t *)0x40013804)
#define KEY_TEST  0x21
#define MAGIC     0x4C435231u                           /* RAM is not cleared at boot */

struct ctl {
    uint32_t magic;
    uint32_t tdone;           /* tick when the last test (or key) finished */
    uint32_t n;               /* number being received */
    uint16_t rep, aoff;       /* seconds; 0 = off */
    uint8_t st, cmd, wait;    /* st: 0 idle, 1 after '#', 2 after the letter */
};
#define CTL ((struct ctl *)0x20001F04)   /* 0x20001F04..0x20001F1B; main loop only, so not volatile */

typedef int (*keyscan_t)(void);
typedef int (*printf_t)(const char *, ...);
#define KEYSCAN ((keyscan_t)0x080051E1)
#define PRINTF  ((printf_t)0x08008FA5)
typedef int (*putc_t)(int);
#define PUTC    ((putc_t)0x0800B2DD)    /* fputc -> USART1 */

int key_hook(void)
{
    struct ctl *c = CTL;
    if (c->magic != MAGIC) {
        c->magic = MAGIC;
        c->rep = 0;
        c->aoff = AUTOOFF_DEFAULT_S;
        c->st = 0;
        c->wait = 0;
        c->tdone = TICKS;
    }
    int k = KEYSCAN();
    uint32_t now = TICKS;
    if (c->wait || k) {       /* a test just finished, or a real key: restart the repeat wait */
        c->wait = 0;
        c->tdone = now;
    }
    while (U_STS & 0x20) {
        /* The USART holds one byte and the main loop polls slowly, so the PC sends a byte only
           after the previous one is echoed (lcrp1/port.py). */
        unsigned ch = U_DAT & 0xFF;
        PUTC(ch | 0x80);      /* echo, marked: screen text is plain ASCII */
        unsigned st = c->st;
        c->st = 0;            /* anything unexpected drops the line */
        if (ch == '#') {
            c->st = 1;
            c->n = 0;
        } else if (st == 1) {
            c->cmd = ch;
            c->st = 2;
        } else if (st == 2 && ch - '0' < 10 && c->n < 100000) {
            c->n = c->n * 10 + ch - '0';
            c->st = 2;
        } else if (st == 2 && (ch == '\n' || ch == '\r') && c->n < 100000) {
            unsigned n = c->n > 3600 ? 3600 : c->n;
            unsigned cmd = c->cmd;
            if (cmd == 'T' && !k)
                k = KEY_TEST;
            else if (cmd == 'R')
                c->rep = n;
            else if (cmd == 'A')
                c->aoff = n;
            else if (cmd == 'O')
                PWROFF = 1;
            else if (cmd != '?')
                cmd = '!';
            PRINTF("\n#%c R=%d A=%d P=%d\n", cmd, c->rep, c->aoff, (IRF >> 2) & 1);
        }
    }
    if (!k && c->rep && !(IRF & 4) && now - c->tdone >= (uint32_t)c->rep * TICK_HZ)
        k = KEY_TEST;
    if (c->aoff && IDLE >= (uint32_t)c->aoff * TICK_HZ)
        PWROFF = 1;
    if (k == KEY_TEST)
        c->wait = 1;
    return k;
}
