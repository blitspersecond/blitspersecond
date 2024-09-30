from numpy import array, uint8

BPS_DEFAULT_PALETTE = array(
    [
        (0x00, 0x00, 0x00, 0x00),
        (0x00, 0x00, 0x00, 0xFF),
        (0x44, 0x44, 0x44, 0xFF),
        (0x88, 0x88, 0x88, 0xFF),
        (0xBB, 0xBB, 0xBB, 0xFF),
        (0xFF, 0xFF, 0xFF, 0xFF),
        (0x44, 0x00, 0x00, 0xFF),
        (0x88, 0x00, 0x00, 0xFF),
        (0xBB, 0x00, 0x00, 0xFF),
        (0xFF, 0x00, 0x00, 0xFF),
        (0x00, 0x44, 0x00, 0xFF),
        (0x00, 0x88, 0x00, 0xFF),
        (0x00, 0xBB, 0x00, 0xFF),
        (0x00, 0xFF, 0x00, 0xFF),
        (0x00, 0x00, 0x44, 0xFF),
        (0x00, 0x00, 0x88, 0xFF),
        (0x00, 0x00, 0xBB, 0xFF),
        (0x00, 0x00, 0xFF, 0xFF),
        (0x44, 0x44, 0x00, 0xFF),
        (0x88, 0x88, 0x00, 0xFF),
        (0xBB, 0xBB, 0x00, 0xFF),
        (0xFF, 0xFF, 0x00, 0xFF),
        (0x00, 0x44, 0x44, 0xFF),
        (0x00, 0x88, 0x88, 0xFF),
        (0x00, 0xBB, 0xBB, 0xFF),
        (0x00, 0xFF, 0xFF, 0xFF),
        (0x44, 0x00, 0x44, 0xFF),
        (0x88, 0x00, 0x88, 0xFF),
        (0xBB, 0x00, 0xBB, 0xFF),
        (0xFF, 0x00, 0xFF, 0xFF),
        (0xFF, 0x88, 0x00, 0xFF),
        (0x88, 0x88, 0xFF, 0xFF),
    ],
    dtype=uint8,
)


BPS_COLOR_TRANSPARENT = 0
BPS_COLOR_BLACK = 1
BPS_COLOR_DARK_GRAY = 2
BPS_COLOR_GRAY = 3
BPS_COLOR_LIGHT_GRAY = 4
BPS_COLOR_WHITE = 5
BPS_COLOR_DARK_RED = 6
BPS_COLOR_CRIMSON = 7
BPS_COLOR_FIREBRICK = 8
BPS_COLOR_RED = 9
BPS_COLOR_DARK_GREEN = 10
BPS_COLOR_GREEN = 11
BPS_COLOR_LIME_GREEN = 12
BPS_COLOR_BRIGHT_GREEN = 13
BPS_COLOR_DARK_BLUE = 14
BPS_COLOR_MEDIUM_BLUE = 15
BPS_COLOR_DODGER_BLUE = 16
BPS_COLOR_BLUE = 17
BPS_COLOR_OLIVE = 18
BPS_COLOR_YELLOW_GREEN = 19
BPS_COLOR_YELLOW_OLIVE = 20
BPS_COLOR_YELLOW = 21
BPS_COLOR_TEAL = 22
BPS_COLOR_DARK_CYAN = 23
BPS_COLOR_LIGHT_CYAN = 24
BPS_COLOR_CYAN = 25
BPS_COLOR_PURPLE = 26
BPS_COLOR_MEDIUM_PURPLE = 27
BPS_COLOR_MAGENTA = 28
BPS_COLOR_FUCHSIA = 29
BPS_COLOR_ORANGE = 30
BPS_COLOR_LIGHT_BLUE = 31
