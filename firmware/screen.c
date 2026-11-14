/* LCR-P1 v1.1.0 patch: screen look.
 *
 * The boot logo itself is data: make_patch.py writes a 32x32 skull (art/skull.py) over the
 * FNIRSI bitmap and recolours / recentres it; the rest of the old bitmap holds pixel_hook.
 * pixel_hook() replaces every call of lcd_write_pixel: see remap().
 * color_char() called by the draw hooks (uart_mirror.S) for every character and the ohm glyph;
 *              recolours white text only (the firmware's own colours stay):
 *                values (number routines, units, 0x.. codes, ohm) -> accent
 *                labels (strings ending in ':')                    -> soft grey
 *                titles and other text                             -> unchanged (white)
 */
#include <stdint.h>

#define ACCENT  0xB47F          /* #B48CFF purple */
#define LABEL   0x8C33          /* #8E879E purple-grey */
#define DIM     0x4A2A          /* #4B4655 dim grey for lines and leads */
#define WHITE   0xFFFF
#define FONT    ((const uint8_t *)0x0800F01A)   /* 12 bytes per char from ' ', bit 0 = left pixel */
typedef void (*pixel_t)(uint32_t);
#define PIXEL ((pixel_t)0x08006061)    /* lcd_write_pixel(colour): the sink of text, lines, bitmaps */

/* Minimal palette for everything drawn pixel by pixel (component symbols, icons, IR bars, text):
   black / white / our two text colours stay, reds stay (warnings), greys -> DIM, any other
   colour -> ACCENT. Idempotent, so already-mapped colours pass unchanged. */
static uint32_t remap(uint32_t c)
{
    c &= 0xFFFF;
    if (c == 0 || c == WHITE || c == ACCENT || c == LABEL)
        return c;
    unsigned r = c >> 11, g = c >> 6 & 31, b = c & 31;
    unsigned hi = r > g ? r : g, lo = r < g ? r : g;
    hi = b > hi ? b : hi;
    lo = b < lo ? b : lo;
    if (hi - lo < 5)
        return DIM;
    if (r >= 20 && g < 10 && b < 10)
        return c;
    return ACCENT;
}

void pixel_hook(uint32_t c)
{
    PIXEL(remap(c));
}

/* f = the hook's saved frame: r0 x, r1 y, r2 char, r3 colour, r4, lr (return into the draw routine).
   For draw_string (the only caller at 0x080060xx) the string start is at f[11]. */
void color_char(uint32_t *f)
{
    if ((f[3] & 0xFFFF) != WHITE)
        return;
    uint32_t c = ACCENT;
    if ((f[5] >> 12 & 15) == 6) {      /* from draw_string: decide by the whole string */
        const char *s = (const char *)(uintptr_t)f[11], *e = s;
        while (*e)
            e++;
        if (e[-1] == ':')
            c = LABEL;
        else if (e - s > 3 && s[1] != 'x')
            return;                    /* titles, messages: stay white */
    }
    f[3] = (f[3] & ~0xFFFFu) | c;
}
