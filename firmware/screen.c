/* LCR-P1 v1.1.0 patch: screen look.
 *
 * splash()     replaces the boot logo (FNIRSI bitmap, 222 bytes at 0x0800EECA, drawn once by the
 *              splash at 0x0800DE50): "LCR-P1" at 3x size in the accent colour, drawn from the
 *              firmware's own 6x12 font with one 3x3 fill per pixel. The freed bitmap bytes hold
 *              this code.
 * color_char() called by the draw hooks (uart_mirror.S) for every character and the ohm glyph;
 *              recolours white text only (the firmware's own colours stay):
 *                values (number routines, units, 0x.. codes, ohm) -> accent
 *                labels (strings ending in ':')                    -> soft grey
 *                titles and other text                             -> unchanged (white)
 */
#include <stdint.h>

#define ACCENT  0x5DDF          /* #5CB8FF, the meter's bezel blue */
#define LABEL   0x9D16          /* #9AA3B2 */
#define WHITE   0xFFFF
#define FONT    ((const uint8_t *)0x0800F01A)   /* 12 bytes per char from ' ', bit 0 = left pixel */
typedef void (*fill_t)(int, int, int, int, int);
#define FILL ((fill_t)0x08005741)      /* (x0, y0, x1, y1 exclusive, colour) */
#define SCALE 3

void splash(void)
{
    const char *t = "LCR-P1";          /* 6 chars x 6 px x 3 = 108 px wide, 36 px high */
    for (int x = (128 - 108) / 2; *t; t++, x += 6 * SCALE) {
        const uint8_t *g = FONT + (*t - ' ') * 12;
        for (int y = 0x38 - 9; y < 0x38 - 9 + 12 * SCALE; y += SCALE, g++)
            for (unsigned row = *g, xx = x; row; row >>= 1, xx += SCALE)
                if (row & 1)
                    FILL(xx, y, xx + SCALE, y + SCALE, ACCENT);
    }
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
