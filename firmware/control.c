/* LCR-P1 v1.1.0 patch: commands over the USB serial port, auto-repeat test, auto power-off.
 *
 * key_hook replaces the main loop's call to the key-queue read (0x080051E0, called at 0x0800B964).
 * It returns the real key if there is one; otherwise it reads USART1 (receive enabled by the patch,
 * RX = PA10) and can return the TEST short-press code 0x21, which the main loop handles exactly like
 * the button (and ignores on the IR page, as for the button).
 *
 * Commands: '#' + letter [+ decimal number] + CR/LF. Reply: "#<letter> R=<s> A=<s> P=<page>".
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
    uint16_t rep, aoff;       /* seconds; 0 = off */
    uint8_t len, wait;
    char buf[10];
};
#define CTL ((struct ctl *)0x20001F04)   /* 0x20001F04..0x20001F1F; main loop only, so not volatile */

typedef int (*keyscan_t)(void);
typedef int (*printf_t)(const char *, ...);
#define KEYSCAN ((keyscan_t)0x080051E1)
#define PRINTF  ((printf_t)0x08008FA5)

int key_hook(void)
{
    struct ctl *c = CTL;
    if (c->magic != MAGIC || c->rep > 3600 || c->aoff > 3600) {
        c->magic = MAGIC;
        c->rep = 0;
        c->aoff = AUTOOFF_DEFAULT_S;
        c->len = 0;
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
        char ch = U_DAT;
        if (ch == '#') {
            c->len = 1;
        } else if (c->len && (ch == '\n' || ch == '\r')) {
            unsigned n = 0;
            for (int i = 2; i < c->len; i++)
                n = n * 10 + (c->buf[i] - '0');
            char cmd = c->buf[1];
            if (n > 3600)
                n = 3600;
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
            c->len = 0;
        } else if (c->len) {
            if (c->len < (int)sizeof c->buf && (c->len < 2 || (ch >= '0' && ch <= '9')))
                c->buf[c->len++] = ch;
            else
                c->len = 0;   /* malformed: drop the line */
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
