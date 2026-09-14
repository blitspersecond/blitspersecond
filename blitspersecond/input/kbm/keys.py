"""The keyboard's vocabularies: what a key is, what an edit is, what was held.

Three enums, and only ONE of them is a thing a game asks about.

    Key       a control, with a boundary. Queryable, so it carries the
              condition operators.
    Motion    an editing intent. Instantaneous -- no boundary, no held
              state, nothing to ask.
    Modifier  a BITFIELD describing one event, where `|` is union and means
              it arithmetically.

**Only `Key` is composable, and the other two are not for different reasons.**
A `Modifier` mask must keep `|` as union, because `CTRL | SHIFT` meaning one
mask for both is the arithmetic being right. A `Motion` is not composable
because there is nothing to compose it against: motions arrive as facts and
never enter a scan, so a `Motion` predicate could only ever answer False --
except where it answered True by ACCIDENT, since eight motions share a value
with the arrow and editing KEYS. `Motion.LEFT` and `key.LEFT` are both
0xff51, and a set holding the key would satisfy a predicate about the motion.

That is the exact failure this file exists to prevent, one vocabulary over.
See `condition.py`.

**The values are pyglet's own**, so nothing is translated at ingress: a scan
holding raw symbols answers membership against these directly, because an
`IntEnum` member hashes equal to its int. What is added here is names that
carry operators -- not a second numbering, which would be a table of a hundred
and seventy entries mapping values to themselves.

**The symbol vocabulary is complete rather than curated, and `Button`'s is
not.** A pad has a converged FORM and ten members is that form written down;
a keyboard has whatever keys it has, no convergence to appeal to, and refusing
a symbol would only make a key unbindable for nothing. Members are ordered by
value, so the ASCII range reads first.

Design record: `bps/reference/input/keyboard.md` and `input.md`,
*Asking about controls*.
"""

from __future__ import annotations

from enum import IntEnum, IntFlag

from blitspersecond.input.condition import Composable


class Key(Composable, IntEnum):
    """One key symbol, layout-translated by the platform before it arrives.

    **`Composable` comes FIRST so its operators shadow `int`\'s.** Listed the
    other way round, `key.LCTRL & key.LALT` is arithmetic and evaluates to
    `LSHIFT` -- a real symbol, so nothing downstream could tell. That is the
    whole reason this type exists rather than a bare re-export.

    There are no scancodes. Symbols are layout-translated, so WASD on an
    AZERTY keyboard is literally ZQSD, and rebinding is the escape hatch.
    """

    SPACE = 0x0020
    EXCLAMATION = 0x0021
    DOUBLEQUOTE = 0x0022
    HASH = 0x0023
    POUND = 0x0023
    DOLLAR = 0x0024
    PERCENT = 0x0025
    AMPERSAND = 0x0026
    APOSTROPHE = 0x0027
    PARENLEFT = 0x0028
    PARENRIGHT = 0x0029
    ASTERISK = 0x002a
    PLUS = 0x002b
    COMMA = 0x002c
    MINUS = 0x002d
    PERIOD = 0x002e
    SLASH = 0x002f
    COLON = 0x003a
    SEMICOLON = 0x003b
    LESS = 0x003c
    EQUAL = 0x003d
    GREATER = 0x003e
    QUESTION = 0x003f
    AT = 0x0040
    BRACKETLEFT = 0x005b
    BACKSLASH = 0x005c
    BRACKETRIGHT = 0x005d
    ASCIICIRCUM = 0x005e
    UNDERSCORE = 0x005f
    GRAVE = 0x0060
    QUOTELEFT = 0x0060
    A = 0x0061
    B = 0x0062
    C = 0x0063
    D = 0x0064
    E = 0x0065
    F = 0x0066
    G = 0x0067
    H = 0x0068
    I = 0x0069
    J = 0x006a
    K = 0x006b
    L = 0x006c
    M = 0x006d
    N = 0x006e
    O = 0x006f
    P = 0x0070
    Q = 0x0071
    R = 0x0072
    S = 0x0073
    T = 0x0074
    U = 0x0075
    V = 0x0076
    W = 0x0077
    X = 0x0078
    Y = 0x0079
    Z = 0x007a
    BRACELEFT = 0x007b
    BAR = 0x007c
    BRACERIGHT = 0x007d
    ASCIITILDE = 0x007e
    BACKSPACE = 0xff08
    TAB = 0xff09
    LINEFEED = 0xff0a
    CLEAR = 0xff0b
    RETURN = 0xff0d
    ENTER = 0xff0d
    PAUSE = 0xff13
    SCROLLLOCK = 0xff14
    SYSREQ = 0xff15
    ESCAPE = 0xff1b
    HOME = 0xff50
    LEFT = 0xff51
    UP = 0xff52
    RIGHT = 0xff53
    DOWN = 0xff54
    PAGEUP = 0xff55
    PAGEDOWN = 0xff56
    END = 0xff57
    BEGIN = 0xff58
    SELECT = 0xff60
    PRINT = 0xff61
    EXECUTE = 0xff62
    INSERT = 0xff63
    UNDO = 0xff65
    REDO = 0xff66
    MENU = 0xff67
    FIND = 0xff68
    CANCEL = 0xff69
    HELP = 0xff6a
    BREAK = 0xff6b
    MODESWITCH = 0xff7e
    SCRIPTSWITCH = 0xff7e
    NUMLOCK = 0xff7f
    NUM_SPACE = 0xff80
    NUM_TAB = 0xff89
    NUM_ENTER = 0xff8d
    NUM_F1 = 0xff91
    NUM_F2 = 0xff92
    NUM_F3 = 0xff93
    NUM_F4 = 0xff94
    NUM_HOME = 0xff95
    NUM_LEFT = 0xff96
    NUM_UP = 0xff97
    NUM_RIGHT = 0xff98
    NUM_DOWN = 0xff99
    NUM_PAGE_UP = 0xff9a
    NUM_PRIOR = 0xff9a
    NUM_PAGE_DOWN = 0xff9b
    NUM_NEXT = 0xff9b
    NUM_END = 0xff9c
    NUM_BEGIN = 0xff9d
    NUM_INSERT = 0xff9e
    NUM_DELETE = 0xff9f
    NUM_MULTIPLY = 0xffaa
    NUM_ADD = 0xffab
    NUM_SEPARATOR = 0xffac
    NUM_SUBTRACT = 0xffad
    NUM_DECIMAL = 0xffae
    NUM_DIVIDE = 0xffaf
    NUM_0 = 0xffb0
    NUM_1 = 0xffb1
    NUM_2 = 0xffb2
    NUM_3 = 0xffb3
    NUM_4 = 0xffb4
    NUM_5 = 0xffb5
    NUM_6 = 0xffb6
    NUM_7 = 0xffb7
    NUM_8 = 0xffb8
    NUM_9 = 0xffb9
    NUM_EQUAL = 0xffbd
    F1 = 0xffbe
    F2 = 0xffbf
    F3 = 0xffc0
    F4 = 0xffc1
    F5 = 0xffc2
    F6 = 0xffc3
    F7 = 0xffc4
    F8 = 0xffc5
    F9 = 0xffc6
    F10 = 0xffc7
    F11 = 0xffc8
    F12 = 0xffc9
    F13 = 0xffca
    F14 = 0xffcb
    F15 = 0xffcc
    F16 = 0xffcd
    F17 = 0xffce
    F18 = 0xffcf
    F19 = 0xffd0
    F20 = 0xffd1
    F21 = 0xffd2
    FUNCTION = 0xffd2
    F22 = 0xffd3
    F23 = 0xffd4
    F24 = 0xffd5
    LSHIFT = 0xffe1
    RSHIFT = 0xffe2
    LCTRL = 0xffe3
    RCTRL = 0xffe4
    CAPSLOCK = 0xffe5
    LMETA = 0xffe7
    RMETA = 0xffe8
    LALT = 0xffe9
    RALT = 0xffea
    LWINDOWS = 0xffeb
    RWINDOWS = 0xffec
    LCOMMAND = 0xffed
    RCOMMAND = 0xffee
    LOPTION = 0xffef
    ROPTION = 0xfff0
    DELETE = 0xffff

class Motion(IntEnum):
    """An editing intent, resolved from the platform\'s own key bindings.

    Backspace on a Mac and backspace on Linux arrive as the same motion
    whatever key produced it, which is why these are carried separately and
    are not recoverable from key symbols.

    **Deliberately not composable.** A motion has no boundary to cross and no
    held state, so it never enters a scan -- it is read off a fact by whatever
    consumes one, which today is the line reducer. Giving it the operators
    would produce predicates that answer False forever, or worse answer True
    because eight of these share a value with a key.
    """

    NEXT_WORD = 0x0001
    PREVIOUS_WORD = 0x0002
    BEGINNING_OF_LINE = 0x0003
    END_OF_LINE = 0x0004
    BEGINNING_OF_FILE = 0x0005
    END_OF_FILE = 0x0006
    COPY = 0x0007
    PASTE = 0x0008
    BACKSPACE = 0xff08
    LEFT = 0xff51
    UP = 0xff52
    RIGHT = 0xff53
    DOWN = 0xff54
    PREVIOUS_PAGE = 0xff55
    NEXT_PAGE = 0xff56
    DELETE = 0xffff

class Modifier(IntFlag):
    """What was held when an event arrived, as the platform reported it.

    **Deliberately NOT composable.** This is a bitfield and `|` is union --
    `Modifier.CTRL | Modifier.SHIFT` is one mask meaning both, which is the
    arithmetic being right rather than a hazard.

    It is a record of what the platform said about one event, and the query
    vocabulary never reads it. *Is Ctrl down* is answered from physical truth,
    by `CTRL` below, so that one question has one answer instead of two that
    can disagree.
    """

    SHIFT = 0x00001
    ACCEL = 0x00002
    CTRL = 0x00002
    ALT = 0x00004
    CAPSLOCK = 0x00008
    NUMLOCK = 0x00010
    WINDOWS = 0x00020
    COMMAND = 0x00040
    OPTION = 0x00080
    SCROLLLOCK = 0x00100
    FUNCTION = 0x00200

#: Either side counts. A player holding the right Ctrl is holding Ctrl, and no
#: game means "the left one specifically" -- so these are the spelling that
#: should be reached for, and the sided symbols stay available beneath them.
#:
#: Predicates rather than members, for the reason `Button.ACCEPT` is: a name
#: that stands for more than one control is not itself a control.
Key.CTRL = Key.LCTRL | Key.RCTRL
Key.ALT = Key.LALT | Key.RALT
Key.SHIFT = Key.LSHIFT | Key.RSHIFT
Key.META = Key.LMETA | Key.RMETA
Key.WINDOWS = Key.LWINDOWS | Key.RWINDOWS
Key.COMMAND = Key.LCOMMAND | Key.RCOMMAND
Key.OPTION = Key.LOPTION | Key.ROPTION
