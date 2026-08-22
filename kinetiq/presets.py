

ONE_THIRD = 1.0 / 3.0
TWO_THIRDS = 2.0 / 3.0

LINEAR = ("Linear", (ONE_THIRD, ONE_THIRD), (TWO_THIRDS, TWO_THIRDS))

# easeOutBack-style overshoot / easeInBack-style anticipation
OVERSHOOT = ("Overshoot", (0.34, 1.56), (0.64, 1.0))
ANTICIPATE = ("Anticipate", (0.36, 0.0), (0.66, -0.56))


def _ease_in(p):
    return ("In %d%%" % int(p * 100), (p, 0.0), (1.0, 1.0))


def _ease_out(p):
    return ("Out %d%%" % int(p * 100), (0.0, 0.0), (1.0 - p, 1.0))


def _ease_in_out(p):
    return ("In-Out %d%%" % int(p * 100), (p, 0.0), (1.0 - p, 1.0))


_STRENGTHS = (0.25, 0.5, 0.75, 1.0)

# Rows of presets, laid out as rows of buttons in the UI.
PRESET_ROWS = [
    [LINEAR, OVERSHOOT, ANTICIPATE],
    [_ease_in(p) for p in _STRENGTHS],
    [_ease_out(p) for p in _STRENGTHS],
    [_ease_in_out(p) for p in _STRENGTHS],
]

ALL_PRESETS = [p for row in PRESET_ROWS for p in row]
