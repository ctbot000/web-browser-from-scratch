"""Render a display list to SVG: a screenshot that needs no window."""

from __future__ import annotations

from ..paint import DrawBorder, DrawImage, DrawLine, DrawRect, DrawText
from ..values import to_hex

_FAMILY = {"serif": "Georgia, 'Times New Roman', Times, serif",
           "sans-serif": "Helvetica, Arial, sans-serif",
           "monospace": "'SF Mono', Menlo, Consolas, monospace"}


def escape(text):
    return (text.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))


def render_svg(commands, width, height, background="#ffffff", title=""):
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
           'viewBox="0 0 %d %d">' % (int(width), int(height), int(width),
                                     int(height))]
    if title:
        out.append("<title>%s</title>" % escape(title))
    out.append('<rect width="100%%" height="100%%" fill="%s"/>' % background)

    for command in commands:
        out.append(_render(command))
    out.append("</svg>")
    return "\n".join(part for part in out if part)


def _render(command):
    x, y, width, height = command.rect
    if isinstance(command, DrawRect):
        if width <= 0 or height <= 0:
            return ""
        return ('<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f" '
                'fill="%s"%s/>' % (x, y, width, height, to_hex(command.color),
                                   _opacity(command.color)))
    if isinstance(command, DrawBorder):
        return _border(command)
    if isinstance(command, DrawLine):
        return ('<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f" '
                'fill="%s"/>' % (x, y, width, max(1.0, command.thickness),
                                 to_hex(command.color)))
    if isinstance(command, DrawImage):
        data = getattr(command.image, "data_uri", None)
        if data:
            return ('<image x="%.2f" y="%.2f" width="%.2f" height="%.2f" '
                    'href="%s" preserveAspectRatio="none"/>'
                    % (x, y, width, height, data))
        return ('<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f" '
                'fill="#f0f0f0" stroke="#c8c8c8"/>' % (x, y, width, height))
    if isinstance(command, DrawText):
        font = command.font
        weight = ' font-weight="bold"' if font.bold else ""
        italic = ' font-style="italic"' if font.italic else ""
        return ('<text x="%.2f" y="%.2f" font-family="%s" font-size="%.2f"%s%s'
                ' fill="%s" xml:space="preserve">%s</text>'
                % (x, command.baseline, _FAMILY[font.generic], font.size,
                   weight, italic, to_hex(command.color),
                   escape(command.text)))
    return ""


def _opacity(color):
    return "" if color[3] >= 1.0 else ' fill-opacity="%.3f"' % color[3]


def _border(command):
    x, y, width, height = command.rect
    top, right, bottom, left = command.widths
    parts = []
    edges = (
        (top, command.colors[0], command.styles[0], (x, y, width, top)),
        (right, command.colors[1], command.styles[1],
         (x + width - right, y, right, height)),
        (bottom, command.colors[2], command.styles[2],
         (x, y + height - bottom, width, bottom)),
        (left, command.colors[3], command.styles[3], (x, y, left, height)),
    )
    for size, color, style, rect in edges:
        if size <= 0 or style in ("none", "hidden") or color is None:
            continue
        dash = ""
        if style == "dashed":
            dash = ' stroke-dasharray="%g %g"' % (size * 3, size * 2)
        if style in ("dashed", "dotted"):
            parts.append(_dashed_edge(rect, color, size, style))
            continue
        parts.append('<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f" '
                     'fill="%s"%s/>' % (rect[0], rect[1], max(rect[2], 0),
                                        max(rect[3], 0), to_hex(color), dash))
    return "".join(parts)


def _dashed_edge(rect, color, size, style):
    x, y, width, height = rect
    horizontal = width >= height
    length = width if horizontal else height
    period = size * (2 if style == "dotted" else 3)
    on = size if style == "dotted" else size * 2
    parts = []
    position = 0.0
    while position < length:
        run = min(on, length - position)
        if horizontal:
            parts.append('<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f" '
                         'fill="%s"/>' % (x + position, y, run, height,
                                          to_hex(color)))
        else:
            parts.append('<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f" '
                         'fill="%s"/>' % (x, y + position, width, run,
                                          to_hex(color)))
        position += period
    return "".join(parts)
