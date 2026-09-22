"""Rendering a page into a character grid, for terminals and for tests.

Laying out with a metrics object whose glyphs are exactly one cell wide makes
the display list map onto a grid of characters without any resampling, so the
terminal shows the same layout the window does, at lower resolution.
"""

from __future__ import annotations

from ..fonts import FontMetrics, is_wide
from ..paint import DrawBorder, DrawImage, DrawLine, DrawRect, DrawText

CELL_WIDTH = 8.0
# One cell is exactly one default line box (font-size 16px, line-height 1.2),
# so ordinary text maps one line to one row with no rounding drift.
CELL_HEIGHT = 19.2


class GridMetrics(FontMetrics):
    """Every glyph is one cell wide; East Asian glyphs are two."""

    def __init__(self, cell_width=CELL_WIDTH, cell_height=CELL_HEIGHT):
        self.cell_width = cell_width
        self.cell_height = cell_height

    def measure(self, text, font):
        cells = 0
        for char in text:
            if char == "\t":
                cells += 8
            elif is_wide(char):
                cells += 2
            elif ord(char) >= 0x300 and ord(char) <= 0x36F:
                continue
            else:
                cells += 1
        return cells * self.cell_width

    def ascent(self, font):
        return self.cell_height * 0.8

    def descent(self, font):
        return self.cell_height * 0.2

    def line_spacing(self, font):
        return self.cell_height


class Canvas:
    def __init__(self, columns, rows):
        self.columns = columns
        self.rows = rows
        self.cells = [[" "] * columns for _ in range(rows)]
        self.colors = [[None] * columns for _ in range(rows)]
        self.backgrounds = [[None] * columns for _ in range(rows)]
        self.underlines = [[False] * columns for _ in range(rows)]

    def put(self, row, column, char, color=None):
        if 0 <= row < self.rows and 0 <= column < self.columns:
            self.cells[row][column] = char
            if color is not None:
                self.colors[row][column] = color

    def underline(self, row, left, width):
        if not 0 <= row < self.rows:
            return
        for column in range(max(0, left), min(self.columns, left + width)):
            self.underlines[row][column] = True

    def fill(self, top, left, height, width, color):
        for row in range(max(0, top), min(self.rows, top + height)):
            for column in range(max(0, left), min(self.columns, left + width)):
                self.backgrounds[row][column] = color

    def to_text(self, color=False, trim=True):
        lines = []
        for row in range(self.rows):
            if color:
                lines.append(self._colored_row(row))
            else:
                lines.append("".join(self.cells[row]).rstrip())
        if trim:
            while lines and not lines[-1].strip():
                lines.pop()
        return "\n".join(lines)

    def _colored_row(self, row):
        out = []
        current = None
        for column in range(self.columns):
            state = (self.colors[row][column], self.backgrounds[row][column],
                     self.underlines[row][column])
            if state != current:
                foreground, background, underlined = state
                out.append("\x1b[0m")
                if background is not None:
                    out.append("\x1b[48;2;%d;%d;%dm" % background[:3])
                if foreground is not None:
                    out.append("\x1b[38;2;%d;%d;%dm" % foreground[:3])
                if underlined:
                    out.append("\x1b[4m")
                current = state
            out.append(self.cells[row][column])
        out.append("\x1b[0m")
        return "".join(out).rstrip()


_BORDER_CHARS = {"horizontal": "─", "vertical": "│"}


def render_grid(commands, width, height, cell_width=CELL_WIDTH,
                cell_height=CELL_HEIGHT, draw_boxes=True):
    columns = max(1, int(round(width / cell_width)))
    rows = max(1, int(round(height / cell_height)) + 1)
    canvas = Canvas(columns, rows)

    for command in commands:
        x, y, box_width, box_height = command.rect
        left = int(round(x / cell_width))
        top = int(round(y / cell_height))

        if isinstance(command, DrawRect):
            if command.color[3] <= 0:
                continue
            canvas.fill(top, left, max(1, int(round(box_height / cell_height))),
                        max(1, int(round(box_width / cell_width))),
                        command.color)
        elif isinstance(command, DrawBorder) and draw_boxes:
            _draw_border(canvas, command, cell_width, cell_height)
        elif isinstance(command, DrawLine):
            # An underline sits a pixel or two below its own baseline, which
            # is still inside that text's row: floor rather than round, or the
            # rule lands on the next line and overwrites it.
            canvas.underline(int(y // cell_height), left,
                             max(1, int(round(box_width / cell_width))))
        elif isinstance(command, DrawImage):
            _draw_image(canvas, command, cell_width, cell_height)
        elif isinstance(command, DrawText):
            row = int(round((command.baseline - cell_height * 0.8) /
                            cell_height))
            column = left
            for char in command.text:
                if char == "\t":
                    column += 8
                    continue
                canvas.put(row, column, char, command.color)
                column += 2 if is_wide(char) else 1
    return canvas


def _draw_border(canvas, command, cell_width, cell_height):
    x, y, width, height = command.rect
    left = int(round(x / cell_width))
    top = int(round(y / cell_height))
    right = left + max(1, int(round(width / cell_width))) - 1
    bottom = top + max(1, int(round(height / cell_height))) - 1
    top_w, right_w, bottom_w, left_w = command.widths
    color = command.colors[0]
    if top_w:
        for column in range(left, right + 1):
            canvas.put(top, column, "─", color)
    if bottom_w:
        for column in range(left, right + 1):
            canvas.put(bottom, column, "─", color)
    if left_w:
        for row in range(top, bottom + 1):
            canvas.put(row, left, "│", color)
    if right_w:
        for row in range(top, bottom + 1):
            canvas.put(row, right, "│", color)
    if top_w and left_w:
        canvas.put(top, left, "┌", color)
    if top_w and right_w:
        canvas.put(top, right, "┐", color)
    if bottom_w and left_w:
        canvas.put(bottom, left, "└", color)
    if bottom_w and right_w:
        canvas.put(bottom, right, "┘", color)


_SHADES = " ░▒▓█"


def _draw_image(canvas, command, cell_width, cell_height):
    x, y, width, height = command.rect
    left = int(round(x / cell_width))
    top = int(round(y / cell_height))
    columns = max(1, int(round(width / cell_width)))
    rows = max(1, int(round(height / cell_height)))
    image = command.image
    if image is None:
        for row in range(top, top + rows):
            for column in range(left, left + columns):
                canvas.put(row, column, "·")
        return
    small = image.resized(columns, rows)
    for row in range(rows):
        for column in range(columns):
            red, green, blue, alpha = small.pixel(column, row)
            if alpha < 32:
                continue
            luminance = (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255.0
            index = min(4, int((1.0 - luminance) * 4.99))
            canvas.put(top + row, left + column, _SHADES[index],
                       (red, green, blue))


def render_text(page, columns=100, color=False, cell_width=CELL_WIDTH,
                cell_height=CELL_HEIGHT):
    canvas = render_grid(page.display_list, columns * cell_width, page.height,
                         cell_width, cell_height)
    return canvas.to_text(color=color)
