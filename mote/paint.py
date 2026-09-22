"""Painting: walking the box tree to produce a display list.

The display list is a flat, backend-independent sequence of drawing commands
in paint order.  Every backend -- the Tk window, the SVG writer, the terminal
renderer -- consumes the same list.
"""

from __future__ import annotations

from .fonts import font_for_style
from .layout import (BlockBox, InlineBox, ListItemBox, ReplacedBox, TableBox,
                     TextBox)
from .values import TRANSPARENT, parse_color


class Command:
    __slots__ = ("rect",)

    def __init__(self, rect):
        self.rect = rect            # (x, y, width, height)

    @property
    def top(self):
        return self.rect[1]

    @property
    def bottom(self):
        return self.rect[1] + self.rect[3]


class DrawRect(Command):
    __slots__ = ("color",)

    def __init__(self, rect, color):
        super().__init__(rect)
        self.color = color

    def __repr__(self):
        return "DrawRect(%s, %s)" % (_fmt(self.rect), self.color)


class DrawBorder(Command):
    __slots__ = ("widths", "colors", "styles")

    def __init__(self, rect, widths, colors, styles):
        super().__init__(rect)
        self.widths = widths        # (top, right, bottom, left)
        self.colors = colors
        self.styles = styles

    def __repr__(self):
        return "DrawBorder(%s, %s)" % (_fmt(self.rect), self.widths)


class DrawText(Command):
    __slots__ = ("text", "font", "color", "baseline", "decoration", "element")

    def __init__(self, rect, text, font, color, baseline, decoration="none",
                 element=None):
        super().__init__(rect)
        self.text = text
        self.font = font
        self.color = color
        self.baseline = baseline    # absolute y of the text baseline
        self.decoration = decoration
        self.element = element

    def __repr__(self):
        return "DrawText(%s, %r)" % (_fmt(self.rect), self.text)


class DrawImage(Command):
    __slots__ = ("image", "alt", "element")

    def __init__(self, rect, image, alt="", element=None):
        super().__init__(rect)
        self.image = image
        self.alt = alt
        self.element = element

    def __repr__(self):
        return "DrawImage(%s)" % (_fmt(self.rect),)


class DrawLine(Command):
    __slots__ = ("color", "thickness")

    def __init__(self, rect, color, thickness=1.0):
        super().__init__(rect)
        self.color = color
        self.thickness = thickness


def _fmt(rect):
    return "%g,%g %gx%g" % rect


class Painter:
    def __init__(self):
        self.commands = []

    def paint(self, box):
        self._paint_box(box)
        self.commands.sort(key=lambda c: 0)   # stable: keep paint order
        return self.commands

    # -- per box ----------------------------------------------------------

    def _paint_box(self, box):
        style = box.style
        if style.keyword("visibility", "visible") == "hidden":
            return
        if isinstance(box, (BlockBox, TableBox, ReplacedBox)):
            self._paint_background(box)
            self._paint_borders(box)

        if isinstance(box, ReplacedBox):
            self._paint_replaced(box)
            return

        for line in box.lines:
            for fragment in line.fragments:
                self._paint_fragment(fragment)

        for child in box.children:
            if isinstance(child, (TextBox, InlineBox)):
                continue                     # painted through line fragments
            self._paint_box(child)

    def _paint_background(self, box):
        color = box.style.color_of("background-color", TRANSPARENT)
        if color is None or color[3] <= 0:
            return
        x, y, width, height = box.padding_box
        if width <= 0 or height <= 0:
            return
        self.commands.append(DrawRect((x, y, width, height), color))

    def _paint_borders(self, box):
        widths = (box.border.top, box.border.right, box.border.bottom,
                  box.border.left)
        if not any(widths):
            return
        style = box.style
        colors = tuple(style.color_of("border-%s-color" % side, style.color)
                       for side in ("top", "right", "bottom", "left"))
        styles = tuple(style.keyword("border-%s-style" % side, "none")
                       for side in ("top", "right", "bottom", "left"))
        self.commands.append(DrawBorder(box.border_box, widths, colors, styles))

    def _paint_replaced(self, box):
        x, y, width, height = box.content_box
        element = box.element
        tag = element.tag if element is not None else ""
        if box.image is not None:
            self.commands.append(DrawImage((x, y, width, height), box.image,
                                           element.get("alt", "") if element
                                           else "", element))
            return
        if tag in ("input", "select", "textarea", "button") and box.label:
            font = font_for_style(box.style)
            self.commands.append(DrawText(
                (x + 3, y + 2, width, height), box.label, font,
                box.style.color, y + height * 0.72, element=element))
        elif tag in ("iframe", "video", "canvas", "embed", "object", "svg"):
            font = font_for_style(box.style)
            self.commands.append(DrawText(
                (x + 4, y + 4, width, height), "[%s]" % tag, font,
                box.style.color, y + 16, element=element))

    def _paint_fragment(self, fragment):
        style = fragment.style
        if fragment.kind == "atomic":
            if fragment.box is not None:
                self._paint_box(fragment.box)
            return
        if not fragment.text.strip():
            self._paint_inline_background(fragment)
            return
        self._paint_inline_background(fragment)
        color = style.color
        baseline = fragment.y + fragment.baseline
        decoration = style.keyword("text-decoration", "none")
        self.commands.append(DrawText(
            (fragment.x, fragment.y, fragment.width, fragment.height),
            fragment.text, fragment.font, color, baseline, decoration,
            fragment.element))
        if decoration in ("underline", "line-through", "overline"):
            offset = {"underline": fragment.baseline + 1.5,
                      "line-through": fragment.baseline * 0.68,
                      "overline": 0.0}[decoration]
            thickness = max(1.0, fragment.font.size / 14.0)
            self.commands.append(DrawLine(
                (fragment.x, fragment.y + offset, fragment.width, thickness),
                color, thickness))

    def _paint_inline_background(self, fragment):
        color = fragment.style.color_of("background-color", TRANSPARENT)
        if color is None or color[3] <= 0:
            return
        self.commands.append(DrawRect(
            (fragment.x, fragment.y, fragment.width, fragment.height), color))


def build_display_list(root_box):
    return Painter().paint(root_box)


def document_height(commands, root_box=None):
    bottom = root_box.margin_box[1] + root_box.margin_box[3] if root_box else 0.0
    for command in commands:
        bottom = max(bottom, command.bottom)
    return bottom
