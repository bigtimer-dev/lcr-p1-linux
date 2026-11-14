/* LCR-P1 v1.1.0 patch: IR capture upgrade + multi-protocol decoder.
 *
 * Stock: EXTI11 edge ISR (0x08003C4C, vector IRQ8) stores the time since the previous
 * edge (TIM @0x40000800, 1 us/tick) into a 68-entry u16 buffer at 0x20000804; the
 * timer overflow ISR (15 ms) stops the timer and clears bit0 of 0x20000802 (= frame
 * done); the main loop (0x0800BBAA) then calls the NEC-only decoder.
 *
 * Patched:
 *  - ir_edge_isr replaces the IRQ8 vector: same logic, but a 760-entry buffer in free
 *    RAM (0x20001900..), so long air-conditioner frames fit.
 *  - timer period 15 ms -> 50 ms (literal at 0x08004A24), so gaps inside AC frames
 *    (~20-40 ms) are kept in one capture. Gaps > 12 ms split a capture into segments.
 *  - ir_poll (main-loop call) decodes Gree/Kelvinator AC frames (screen: bytes 0-3 +
 *    "Long code - see PC"; UART: "AC Gree <8 bytes>"), else the first segment as NEC / Samsung32 / Sony
 *    12/15/20 and shows it on the stock IR page (bytes at 0x20000890) with a
 *    protocol label; anything else is dumped to the UART as "IR raw <n>: <us> ...".
 */
#include <stdint.h>

#define IRF    (*(volatile uint8_t *)0x20000802)   /* bit0 capture armed, bit1 busy, bit2 IR page */
#define IRN    (*(volatile uint8_t *)0x20000803)   /* stock edge count (kept at 0) */
#define CODE   ((volatile uint8_t *)0x20000890)
#define BUF    ((volatile uint16_t *)0x20001900)   /* free RAM above the stack top 0x200018C8 */
#define BUFN   (*(volatile uint16_t *)0x20001EF8)
#define BUFOK  (*(volatile uint16_t *)0x20001EFA)   /* = MAGIC once armed by us (RAM is not zeroed) */
#define MAGIC  0x1A5C
#define BUFMAX 760                                  /* 0x20001900 + 2*760 = 0x20001EF0 */
#define TIM_CR1 (*(volatile uint32_t *)0x40000800)
#define TIM_CNT (*(volatile uint16_t *)0x40000824)
#define GAP    12000                                /* us: longer space = new segment */

typedef int (*exti_get_t)(uint32_t);
typedef void (*exti_clr_t)(uint32_t);
typedef void (*show_t)(int, int, int, int, int, int);
typedef void (*drawstr_t)(int, int, const char *, int, int, int, int);
typedef int (*printf_t)(const char *, ...);
typedef void (*fill_t)(int, int, int, int, int);
#define EXTI_GET ((exti_get_t)0x08003CBD)  /* stock: pending && enabled */
#define EXTI_CLR ((exti_clr_t)0x08003CB1)  /* stock: write PEND */
#define SHOW_IR  ((show_t)0x0800D461)      /* stock IR page, args as in stock decoder */
#define DRAWSTR  ((drawstr_t)0x080060C1)   /* (x, y, str, fg, bg, size, mode) */
#define PRINTF   ((printf_t)0x08008FA5)    /* Keil printf -> fputc -> USART1 */
#define FILL     ((fill_t)0x08005741)      /* stock fill rect (x0, y0, x1, y1, colour), as used by the IR page */

/* IRQ8 (EXTI line 11, IR receiver). Called by hardware: plain C is fine on Cortex-M0. */
void ir_edge_isr(void)
{
    if (!EXTI_GET(1 << 11))
        return;
    EXTI_CLR(1 << 11);
    if (!(IRF & 1))
        return;
    TIM_CR1 &= ~1u;
    uint16_t t = TIM_CNT;
    TIM_CNT = 0;
    TIM_CR1 |= 1;
    unsigned n = BUFN;
    if (n < BUFMAX)
        BUF[n++] = t;
    BUFN = n;
    if (n >= BUFMAX) {          /* full: end the capture like the stock 68-edge limit */
        TIM_CR1 &= ~1u;
        IRF &= ~1;
    }
}

static int in(unsigned v, unsigned lo, unsigned hi) { return v - lo <= hi - lo; }

/* All decoders get b = segment (b[0] = gap/stale count before its first edge), n = length. */

/* Pulse distance (NEC-style) bits, LSB first: bit k = mark b[i+2k], space b[i+2k+1]. */
__attribute__((noinline)) static int pd_bits(const volatile uint16_t *b, int i, int nbits, uint8_t *o)
{
    for (int k = 0; k < nbits; k++) {
        unsigned m = b[i + 2 * k], sp = b[i + 2 * k + 1];
        if (!in(m, 250, 900))
            return 0;
        if (in(sp, 1200, 2100))
            o[k >> 3] |= 1 << (k & 7);
        else if (!in(sp, 250, 900))
            return 0;
    }
    return 1;
}

/* NEC / Samsung32: leader at b[s], leader space s+1, 32 bits from s+2. */
static int pulse_distance(const volatile uint16_t *b, int s, int n, uint8_t *o)
{
    return s + 65 <= n - 1 && in(b[s + 1], 3500, 5500) && pd_bits(b, s + 2, 32, o);
}

/* Gree / Kelvinator AC, one message (from leader mark at b[i]):
 * 9000/4500, 35 bits (bytes 0-3 + "010"), mark + ~20 ms gap, 32 bits (bytes 4-7), mark.
 * = 139 entries. Checksum: high nibble of byte 7. */
__attribute__((noinline)) static int gree(const volatile uint16_t *b, int i, int total, uint8_t *o)
{
    if (i + 139 > total || !in(b[i], 7500, 10500) || !in(b[i + 1], 3500, 5500) ||
        !in(b[i + 73], 15000, 25000))
        return 0;
    uint32_t xw[2] = {0, 0};    /* word-cleared: no memset call */
    uint8_t *x = (uint8_t *)xw;
    if (!pd_bits(b, i + 2, 35, x) || ((x[4] & 7) != 2) || !pd_bits(b, i + 74, 32, o + 4))
        return 0;
    unsigned sum = 10;
    for (int k = 0; k < 4; k++) {
        o[k] = x[k];
        sum += (o[k] & 15) + (o[k + 4] >> 4);
    }
    sum -= o[7] >> 4;
    return (sum & 15) == (o[7] >> 4u);
}

/* Sony SIRC: leader 2.4 ms, then per bit space 0.6 + mark 0.6 (0) / 1.2 ms (1), LSB first. */
__attribute__((noinline)) static int sony(const volatile uint16_t *b, int s, int n, uint32_t *v)
{
    int nb = (n - 1 - s) >> 1;
    if (((n - 1 - s) & 1) || !(nb == 12 || nb == 15 || nb == 20))
        return 0;
    uint32_t x = 0;
    for (int k = 0; k < nb; k++) {
        unsigned sp = b[s + 1 + 2 * k], m = b[s + 2 + 2 * k];
        if (!in(sp, 300, 1000))
            return 0;
        if (in(m, 900, 1600))
            x |= 1u << k;
        else if (!in(m, 300, 900))
            return 0;
    }
    *v = x;
    return nb;
}

/* UART line with the full 8-byte AC code (message 1). */
__attribute__((noinline)) static void ac_print(const uint8_t *ac)
{
    PRINTF("\nAC Gree");
    for (int k = 0; k < 8; k++)
        PRINTF(" %02X", ac[k]);
    PRINTF("\n");
}

static void rearm(void)
{
    BUFN = 0;
    BUFOK = MAGIC;
    IRN = 0;
    IRF = (IRF & ~2) | 1;
}

void ir_poll(void)
{
    if (IRF & 1)
        return;                 /* still capturing */
    if (BUFOK != MAGIC) {       /* first poll after power-on: buffer holds random RAM */
        rearm();
        return;
    }
    int total = BUFN;
    if (total > BUFMAX)         /* RAM is not zeroed at boot: never trust a stale count */
        total = BUFMAX;
    int n = 1;                  /* first segment = BUF[0 .. n-1] */
    while (n < total && BUF[n] <= GAP)
        n++;
    const volatile uint16_t *b = BUF;
    uint32_t ow = 0;
    uint8_t *o = (uint8_t *)&ow;
    const char *name = 0;
    uint32_t v;

    uint32_t acw[2] = {0, 0};
    uint8_t *ac = (uint8_t *)acw;
    int is_ac = gree(b, 1, total, ac);
    if (is_ac) {
        /* screen shows bytes 0-3 only; the full 8-byte code goes to the UART */
        name = "Long code - see PC";
        for (int k = 0; k < 4; k++)
            o[k] = ac[k];
        ac_print(ac);
    }
    for (int s = 1; s <= 2 && !name; s++) {
        unsigned lead = b[s];
        if (n >= 60 && in(lead, 7500, 10500) && pulse_distance(b, s, n, o))
            name = "NEC";
        else if (n >= 60 && in(lead, 3500, 5500) && pulse_distance(b, s, n, o))
            name = "Samsung";
        else if (in(lead, 1900, 2900)) {
            int nb = sony(b, s, n, &v);
            if (nb) {
                uint32_t addr = v >> 7;
                o[0] = addr >> 8; o[1] = addr; o[3] = v & 0x7f;
                name = nb == 12 ? "Sony12" : nb == 15 ? "Sony15" : "Sony20";
            }
        }
    }
    if (!name || (total > 100 && !is_ac)) {
        /* unknown protocol, or a long transmission (AC remotes): dump the whole capture,
           us, mark space ... */
        if (total >= 6) {
            PRINTF("\nIR raw %d:", total - 1);
            for (int i = 1; i < total; i++)
                PRINTF(" %d", BUF[i]);
            PRINTF("\n");
        }
        if (!name) {
            rearm();
            return;
        }
    }
    for (int i = 0; i < 4; i++)
        CODE[i] = o[i];
    rearm();
    *(volatile uint8_t *)0x200006FF = 0;
    SHOW_IR(0x16, 0x19, 0x18, 0x28, 0xFFE0, 4);
    *(volatile uint32_t *)0x200006F0 = 0;   /* idle / auto-off timer */
    *(volatile uint8_t *)0x200006EC = 1;
    FILL(0, 0x72, 0x80, 0x7F, 0);           /* clear the label row (labels differ in length) */
    DRAWSTR(8, 0x72, name, 0xFFFF, 0, 12, 0);
}
