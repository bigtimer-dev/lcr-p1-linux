/* LCR-P1 diagnostic build (PIN_DEBUG=1): log GPIO activity over USB serial to map the board.
 *
 * pin_isr wraps the stock 200 Hz timer ISR (IRQ20, 0x080060F4). Each tick it samples the input
 * (PID) and output (POD) registers of ports A, B, C, F, so it also sees states that exist only
 * while a measurement runs and the main loop is blocked (relay, HV boost, ...). A changed bit is
 * queued as an event unless it belongs to the probe network / UART / IR / LCD SPI, or it is "busy"
 * (changed twice within the current or previous second, e.g. LCD control lines).
 * key_hook (main loop) prints the queue and, once a second, a snapshot:
 *   #E <tick> <reg> <value> <changed>    reg 0-3 = PID A,B,C,F  4-7 = POD A,B,C,F
 *   #S <PID A B C F> <POD A B C F> <PMODE A B C F> <dropped events>
 *   #B <busy masks reg 0-7>              only when something is busy
 * In this build the serial commands are not available (key_hook is replaced).
 */
#include <stdint.h>

#define TICKS     (*(volatile uint32_t *)0x200006F4)    /* +1 every tick (stock ISR) */
#define MAGIC     0x50494E44u                           /* RAM is not cleared at boot */
#define NEV       32

struct ev { uint16_t t; uint8_t w, _p; uint16_t v, x; };
struct pd {
    uint32_t magic, last, roll;
    uint16_t cur[8], seen1[8], seen2[8], busy[8];
    uint8_t head, tail, drops, _p;
    struct ev ev[NEV];
};
#define PD ((struct pd *)0x20002000)    /* free: stock RAM use ends at 0x200018C8, chip has 16 KB */

typedef int (*keyscan_t)(void);
typedef int (*printf_t)(const char *, ...);
typedef void (*isr_t)(void);
#define KEYSCAN   ((keyscan_t)0x080051E1)
#define PRINTF    ((printf_t)0x08008FA5)
#define STOCK_ISR ((isr_t)0x080060F5)

/* sampled registers: PID A, B, C, F then POD A, B, C, F */
static volatile uint16_t * const reg[8] = {
    (volatile uint16_t *)0x40010810, (volatile uint16_t *)0x40010C10,
    (volatile uint16_t *)0x40011010, (volatile uint16_t *)0x40011C10,
    (volatile uint16_t *)0x40010814, (volatile uint16_t *)0x40010C14,
    (volatile uint16_t *)0x40011014, (volatile uint16_t *)0x40011C14};
/* bits never reported as events: A: PA1,3-7 probes, PA9/10 UART, PA11 IR;
   B: PB1/PB2/PB10 probes, PB13-15 LCD SPI */
static const uint16_t excl[8] = {0x0EFA, 0xE406, 0, 0, 0x0EFA, 0xE406, 0, 0};

void pin_isr(void)
{
    struct pd *p = PD;
    if (p->magic == MAGIC) {
        uint32_t now = TICKS;
        int roll = now - p->roll >= 200;
        if (roll)
            p->roll = now;
        for (int i = 0; i < 8; i++) {
            unsigned v = *reg[i], x = v ^ p->cur[i];
            p->cur[i] = v;
            p->seen2[i] |= p->seen1[i] & x;
            p->seen1[i] |= x;
            if (x & ~excl[i] & ~p->busy[i] & ~p->seen2[i]) {
                unsigned h = p->head, n = (h + 1) & (NEV - 1);
                if (n == p->tail) {
                    p->drops++;
                } else {
                    struct ev *e = &p->ev[h];
                    e->t = now; e->w = i; e->v = v; e->x = x;
                    p->head = n;
                }
            }
            if (roll) {
                p->busy[i] = p->seen2[i];
                p->seen1[i] = p->seen2[i] = 0;
            }
        }
    }
    STOCK_ISR();
}

static void line(int tag, const uint32_t *v, int n)
{
    PRINTF("\n#%c", tag);
    while (n--)
        PRINTF(" %x", *v++);
    PRINTF("\n");
}

int key_hook(void)
{
    struct pd *p = PD;
    if (p->magic != MAGIC) {
        uint8_t *b = (uint8_t *)p;
        for (unsigned i = 0; i < sizeof *p; i++)
            b[i] = 0;
        p->magic = MAGIC;        /* ISR starts sampling now (cur = 0: first tick reports all set bits) */
    }
    int k = KEYSCAN();
    while (p->tail != p->head) {
        struct ev *e = &p->ev[p->tail];
        PRINTF("\n#E %d %d %x %x\n", e->t, e->w, e->v, e->x);
        p->tail = (p->tail + 1) & (NEV - 1);
    }
    uint32_t now = TICKS;
    if (now - p->last >= 200) {
        uint32_t v[13], busy = 0;
        p->last = now;
        for (int i = 0; i < 8; i++)
            v[i] = p->cur[i];
        for (int i = 0; i < 4; i++)                    /* PMODE A, B, C, F */
            v[8 + i] = *(volatile uint32_t *)((uint32_t)reg[i] - 0x10);
        v[12] = p->drops;
        line('S', v, 13);
        for (int i = 0; i < 8; i++)
            busy |= v[i] = p->busy[i];
        if (busy)
            line('B', v, 8);
    }
    return k;
}
