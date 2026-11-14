"""Original minimal 32x32 horned mask for the boot screen (alternative to skull.py), stock bitmap
format: u16 (unused), u16 width, u16 height, rows MSB first, a 0 bit is drawn in the colour."""
HALF = """
##..............
###.............
.###............
..###...........
...####.........
....#####.......
.....###########
....############
...#############
...#############
..##############
..##############
..###......#####
..####......####
..#####......###
..##############
..##############
...#############
...#############
....############
.....###########
......##########
.......#########
........########
.........#######
...........#####
................
................
................
................
................
................
"""
# left half; the right half is its mirror image
ROWS = [h + h[::-1] for h in HALF.strip('\n').split('\n')]
W, H = 32, 32
assert len(ROWS) == H and all(len(r) == W for r in ROWS)


def bitmap():
    out = bytearray([0, 1, W, 0, H, 0])
    for r in ROWS:
        for i in range(0, W, 8):
            out.append(sum((r[i + b] != '#') << (7 - b) for b in range(8)))
    return bytes(out)


if __name__ == '__main__':
    for r in ROWS:
        print(''.join('██' if p == '#' else '  ' for p in r))
    print(len(bitmap()), 'bytes')
