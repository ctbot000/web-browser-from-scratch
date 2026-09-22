"""The layout engine: turning a styled DOM into positioned boxes.

Implements the parts of CSS 2.1 normal flow that ordinary pages depend on:
the block box model with collapsing margins, inline formatting with line
breaking and vertical alignment, floats, shrink-to-fit sizing, automatic
table layout, list markers, and relative and absolute positioning.
"""

from __future__ import annotations

from .dom import Comment, Element, Text
from .fonts import Font, font_for_style
from .values import Lengths, parse_color, parse_length

BLOCK_DISPLAYS = {"block", "list-item", "flow-root", "table", "flex", "grid",
                  "table-row-group", "table-header-group", "table-footer-group",
                  "table-row", "table-cell", "table-caption", "table-column",
                  "table-column-group"}
INLINE_LEVEL = {"inline", "inline-block", "inline-table", "inline-flex"}
REPLACED_TAGS = {"img", "input", "textarea", "select", "button", "iframe",
                 "video", "canvas", "svg", "embed", "object", "audio"}
TABLE_INTERNAL = {"table-row-group", "table-header-group", "table-footer-group",
                  "table-row", "table-cell", "table-column",
                  "table-column-group", "table-caption"}

MARKERS = {"disc": "•", "circle": "◦", "square": "▪",
           "none": "", "": "•"}


class LayoutContext:
    def __init__(self, metrics, viewport_width=800.0, viewport_height=600.0,
                 image_loader=None, root_font_size=16.0):
        self.metrics = metrics
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height
        self.image_loader = image_loader
        self.root_font_size = root_font_size
        self.deferred_absolutes = []

    def lengths(self, style, percent_base=None):
        return Lengths(style.font_size, self.root_font_size,
                       self.viewport_width, self.viewport_height, percent_base)

    def load_image(self, url):
        if self.image_loader is None or not url:
            return None
        try:
            return self.image_loader(url)
        except Exception:
            return None


class Edges:
    __slots__ = ("top", "right", "bottom", "left")

    def __init__(self, top=0.0, right=0.0, bottom=0.0, left=0.0):
        self.top, self.right, self.bottom, self.left = top, right, bottom, left

    @property
    def horizontal(self):
        return self.left + self.right

    @property
    def vertical(self):
        return self.top + self.bottom

    def __repr__(self):
        return "Edges(%g,%g,%g,%g)" % (self.top, self.right, self.bottom,
                                       self.left)


class Box:
    """Base class: a rectangle in the document, with CSS edges around it."""

    def __init__(self, style, element=None):
        self.style = style
        self.element = element
        self.children = []
        self.parent = None
        self.x = 0.0
        self.y = 0.0
        self.width = 0.0
        self.height = 0.0
        self.margin = Edges()
        self.border = Edges()
        self.padding = Edges()
        self.lines = []
        self.relative_offset = (0.0, 0.0)
        if element is not None:
            element.layout_box = self

    # -- geometry ---------------------------------------------------------

    @property
    def content_box(self):
        dx, dy = self.relative_offset
        return (self.x + dx, self.y + dy, self.width, self.height)

    @property
    def padding_box(self):
        x, y, w, h = self.content_box
        return (x - self.padding.left, y - self.padding.top,
                w + self.padding.horizontal, h + self.padding.vertical)

    @property
    def border_box(self):
        x, y, w, h = self.padding_box
        return (x - self.border.left, y - self.border.top,
                w + self.border.horizontal, h + self.border.vertical)

    @property
    def margin_box(self):
        x, y, w, h = self.border_box
        return (x - self.margin.left, y - self.margin.top,
                w + self.margin.horizontal, h + self.margin.vertical)

    @property
    def outer_height(self):
        return (self.height + self.padding.vertical + self.border.vertical +
                self.margin.vertical)

    def add(self, child):
        child.parent = self
        self.children.append(child)
        return child

    def descendants(self):
        for child in self.children:
            yield child
            for box in child.descendants():
                yield box

    def __repr__(self):
        tag = self.element.tag if self.element is not None else "anon"
        return "<%s %s %gx%g at %g,%g>" % (type(self).__name__, tag,
                                           self.width, self.height,
                                           self.x, self.y)

    # -- shared box-model resolution --------------------------------------

    def resolve_edges(self, context, containing_width):
        style = self.style
        lengths = context.lengths(style, containing_width)
        self.margin = Edges(
            _margin(style, "top", lengths), _margin(style, "right", lengths),
            _margin(style, "bottom", lengths), _margin(style, "left", lengths))
        self.padding = Edges(
            max(0.0, style.length("padding-top", lengths)),
            max(0.0, style.length("padding-right", lengths)),
            max(0.0, style.length("padding-bottom", lengths)),
            max(0.0, style.length("padding-left", lengths)))
        self.border = Edges(
            style.border_width("top", lengths),
            style.border_width("right", lengths),
            style.border_width("bottom", lengths),
            style.border_width("left", lengths))
        return lengths

    def apply_relative(self, context, containing_width):
        if self.style.keyword("position") != "relative":
            return
        lengths = context.lengths(self.style, containing_width)
        left = parse_length(self.style.get("left"), lengths)
        right = parse_length(self.style.get("right"), lengths)
        top = parse_length(self.style.get("top"), lengths)
        bottom = parse_length(self.style.get("bottom"), lengths)
        dx = left if left is not None else (-right if right is not None else 0.0)
        dy = top if top is not None else (-bottom if bottom is not None else 0.0)
        self.relative_offset = (dx, dy)


def _margin(style, side, lengths):
    raw = (style.get("margin-" + side) or "0").strip().lower()
    if raw == "auto":
        return 0.0
    value = parse_length(raw, lengths)
    return 0.0 if value is None else value


def _is_auto(style, name):
    return (style.get(name) or "auto").strip().lower() == "auto"


# --------------------------------------------------------------- box tree

class TextBox(Box):
    def __init__(self, style, text, element=None):
        super().__init__(style, None)
        self.text = text
        self.source = element


class InlineBox(Box):
    """An inline element; its geometry comes from the fragments it produced."""

    def __init__(self, style, element=None):
        super().__init__(style, element)
        self.fragments = []


class ReplacedBox(Box):
    """Something with intrinsic size that we do not lay out inside: an image,
    a form control, a plugin."""

    def __init__(self, style, element, intrinsic=(0.0, 0.0), image=None,
                 label=""):
        super().__init__(style, element)
        self.intrinsic = intrinsic
        self.image = image
        self.label = label
        self.inline_level = style.display in INLINE_LEVEL

    def resolve_size(self, context, containing_width):
        lengths = context.lengths(self.style, containing_width)
        width = parse_length(self.style.get("width"), lengths) \
            if not _is_auto(self.style, "width") else None
        height = parse_length(self.style.get("height"), lengths) \
            if not _is_auto(self.style, "height") else None
        intrinsic_width, intrinsic_height = self.intrinsic
        if width is None and height is None:
            width, height = intrinsic_width, intrinsic_height
        elif width is None:
            ratio = (intrinsic_width / intrinsic_height) if intrinsic_height else 0
            width = height * ratio if ratio else intrinsic_width
        elif height is None:
            ratio = (intrinsic_height / intrinsic_width) if intrinsic_width else 0
            height = width * ratio if ratio else intrinsic_height
        self.width, self.height = max(0.0, width), max(0.0, height)


class LineBox:
    __slots__ = ("x", "y", "width", "height", "baseline", "fragments")

    def __init__(self):
        self.x = self.y = self.width = self.height = self.baseline = 0.0
        self.fragments = []


class Fragment:
    """One piece of inline content placed on a line."""

    __slots__ = ("kind", "x", "y", "width", "height", "baseline", "text",
                 "style", "font", "box", "element")

    def __init__(self, kind, width, height, baseline, style, font=None,
                 text="", box=None, element=None):
        self.kind = kind            # text | atomic | marker
        self.x = self.y = 0.0
        self.width, self.height = width, height
        self.baseline = baseline
        self.text = text
        self.style = style
        self.font = font
        self.box = box
        self.element = element

    def __repr__(self):
        return "Fragment(%s %r %gx%g @%g,%g)" % (self.kind, self.text[:16],
                                                 self.width, self.height,
                                                 self.x, self.y)


class BlockBox(Box):
    """A block container: stacks block children, or formats inline content."""

    def __init__(self, style, element=None, anonymous=False):
        super().__init__(style, element if not anonymous else None)
        self.anonymous = anonymous
        self.source_element = element
        self.marker = None
        self.floats = None

    # -- helpers ----------------------------------------------------------

    @property
    def establishes_bfc(self):
        style = self.style
        if style.keyword("float", "none") != "none":
            return True
        if style.keyword("overflow", "visible") not in ("visible", ""):
            return True
        if style.display in ("inline-block", "table-cell", "flow-root",
                             "table", "flex", "grid"):
            return True
        return self.parent is None

    def has_block_children(self):
        return any(isinstance(c, BlockBox) or isinstance(c, TableBox)
                   for c in self.children)

    # -- layout -----------------------------------------------------------

    def layout(self, context, containing_width, x, y, float_context=None):
        style = self.style
        lengths = self.resolve_edges(context, containing_width)

        if self.establishes_bfc or float_context is None:
            float_context = FloatContext()
        self.floats = float_context

        self._resolve_width(context, containing_width, lengths)

        self.x = x + self.margin.left + self.border.left + self.padding.left
        self.y = y + self.margin.top + self.border.top + self.padding.top

        content_height = self._layout_contents(context, float_context)

        explicit = None
        if not _is_auto(style, "height"):
            explicit = parse_length(style.get("height"),
                                    context.lengths(style, None))
            if explicit is None:
                percent = parse_length(style.get("height"),
                                       context.lengths(style, containing_width))
                explicit = percent
        self.height = explicit if explicit is not None else content_height

        minimum = parse_length(style.get("min-height"), lengths)
        if minimum is not None and self.height < minimum:
            self.height = minimum
        maximum = parse_length(style.get("max-height"), lengths) \
            if (style.get("max-height") or "none").lower() != "none" else None
        if maximum is not None and self.height > maximum:
            self.height = maximum

        if self.establishes_bfc:
            bottom = float_context.lowest_bottom()
            if bottom > self.y + self.height:
                self.height = bottom - self.y

        self.apply_relative(context, containing_width)
        return self.y + self.height + self.padding.bottom + \
            self.border.bottom + self.margin.bottom

    def _resolve_width(self, context, containing_width, lengths):
        style = self.style
        extra = (self.padding.horizontal + self.border.horizontal)
        available = containing_width - self.margin.horizontal - extra

        if _is_auto(style, "width"):
            self.width = max(0.0, available)
        else:
            declared = parse_length(style.get("width"), lengths)
            if declared is None:
                self.width = max(0.0, available)
            else:
                if style.keyword("box-sizing") == "border-box":
                    declared -= extra
                self.width = max(0.0, declared)
                # "margin: 0 auto" centres a box with a known width.
                left_auto = (style.get("margin-left") or "").strip() == "auto"
                right_auto = (style.get("margin-right") or "").strip() == "auto"
                slack = containing_width - self.width - extra - \
                    self.margin.horizontal
                if left_auto and right_auto and slack > 0:
                    self.margin.left = self.margin.right = slack / 2.0
                elif left_auto and slack > 0:
                    self.margin.left = slack
                elif right_auto and slack > 0:
                    self.margin.right = slack

        maximum = style.get("max-width")
        if maximum and maximum.strip().lower() != "none":
            limit = parse_length(maximum, lengths)
            if limit is not None:
                if style.keyword("box-sizing") == "border-box":
                    limit -= extra
                if self.width > limit:
                    slack = self.width - limit
                    self.width = max(0.0, limit)
                    if (style.get("margin-left") or "").strip() == "auto" and \
                            (style.get("margin-right") or "").strip() == "auto":
                        self.margin.left += slack / 2.0
                        self.margin.right += slack / 2.0
        minimum = parse_length(style.get("min-width"), lengths)
        if minimum is not None and self.width < minimum:
            self.width = minimum

    def _layout_contents(self, context, float_context):
        if self.has_block_children():
            return self._layout_block_children(context, float_context)
        return layout_inline_content(self, context, float_context)

    def _layout_block_children(self, context, float_context):
        cursor = self.y
        previous_margin = 0.0
        first = True
        for child in self.children:
            if isinstance(child, BlockBox) and child.is_absolute():
                context.deferred_absolutes.append((child, self))
                continue
            if isinstance(child, BlockBox) and \
                    child.style.keyword("float", "none") != "none":
                self._place_float(child, context, float_context, cursor)
                continue
            if isinstance(child, (TextBox, InlineBox, ReplacedBox)):
                continue

            clear = child.style.keyword("clear", "none")
            if clear in ("left", "right", "both"):
                cleared = float_context.clear_to(clear)
                if cleared > cursor:
                    cursor = cleared
                    previous_margin = 0.0

            child.resolve_edges(context, self.width)
            # Adjacent vertical margins collapse to the larger of the two.
            if not first:
                collapsed = max(previous_margin, child.margin.top)
                cursor += collapsed - previous_margin - child.margin.top
            bottom = child.layout(context, self.width, self.x, cursor,
                                  float_context)
            previous_margin = child.margin.bottom
            cursor = bottom
            first = False
        return max(0.0, cursor - self.y)

    def _place_float(self, child, context, float_context, cursor):
        child.resolve_edges(context, self.width)
        preferred = shrink_to_fit(child, context, self.width)
        child.layout(context, preferred + child.margin.horizontal +
                     child.padding.horizontal + child.border.horizontal,
                     self.x, cursor, None)
        outer_width = child.margin_box[2]
        outer_height = child.margin_box[3]
        side = child.style.keyword("float")
        top, left = float_context.place(cursor, outer_width, outer_height,
                                        self.x, self.x + self.width, side)
        dx = left - child.margin_box[0]
        dy = top - child.margin_box[1]
        _translate(child, dx, dy)

    def is_absolute(self):
        return self.style.keyword("position") in ("absolute", "fixed")


class ListItemBox(BlockBox):
    def __init__(self, style, element=None):
        super().__init__(style, element)
        self.marker_text = ""


class TableBox(BlockBox):
    """Automatic table layout: columns sized from their content."""

    def __init__(self, style, element=None):
        super().__init__(style, element)
        self.rows = []
        self.column_widths = []

    def _layout_contents(self, context, float_context):
        rows = self.collect_rows()
        if not rows:
            return 0.0
        lengths = context.lengths(self.style, self.width)
        spacing = 0.0
        if self.style.keyword("border-collapse", "separate") != "collapse":
            spacing = max(0.0, self.style.length("border-spacing", lengths, 2.0))

        columns = 0
        for row in rows:
            columns = max(columns, sum(_colspan(cell) for cell in row.cells))
        if columns == 0:
            return 0.0

        minimums = [0.0] * columns
        maximums = [0.0] * columns
        for row in rows:
            index = 0
            for cell in row.cells:
                span = _colspan(cell)
                cell.resolve_edges(context, self.width)
                extra = (cell.padding.horizontal + cell.border.horizontal)
                low = min_content_width(cell, context) + extra
                high = max_content_width(cell, context) + extra
                declared = None
                if not _is_auto(cell.style, "width"):
                    declared = parse_length(cell.style.get("width"),
                                            context.lengths(cell.style, self.width))
                if declared is not None:
                    low = max(low, declared)
                    high = max(high, declared)
                if span == 1 and index < columns:
                    minimums[index] = max(minimums[index], low)
                    maximums[index] = max(maximums[index], high)
                index += span

        available = self.width - spacing * (columns + 1)
        widths = _distribute_columns(minimums, maximums, available)
        self.column_widths = widths

        cursor = self.y + spacing
        for row in rows:
            row_top = cursor
            offset = self.x + spacing
            index = 0
            tallest = 0.0
            for cell in row.cells:
                span = _colspan(cell)
                cell_width = sum(widths[index:index + span]) + \
                    spacing * (span - 1)
                inner = cell_width - cell.padding.horizontal - \
                    cell.border.horizontal
                cell.width = max(0.0, inner)
                bottom = cell.layout(context, cell_width, offset, row_top)
                tallest = max(tallest, bottom - row_top)
                offset += cell_width + spacing
                index += span
            for cell in row.cells:
                _align_cell(cell, row_top, tallest)
            row.box.x = self.x + spacing
            row.box.y = row_top
            row.box.width = max(0.0, self.width - spacing * 2)
            row.box.height = max(0.0, tallest - spacing)
            cursor = row_top + tallest + spacing
        self.rows = rows
        return max(0.0, cursor - self.y)

    def collect_rows(self):
        rows = []

        def walk(box):
            for child in box.children:
                display = child.style.display
                if display == "table-row":
                    cells = [c for c in child.children
                             if c.style.display == "table-cell"]
                    if cells:
                        rows.append(_Row(child, cells))
                elif display in ("table-row-group", "table-header-group",
                                 "table-footer-group"):
                    walk(child)
        walk(self)
        return rows


class _Row:
    __slots__ = ("box", "cells")

    def __init__(self, box, cells):
        self.box, self.cells = box, cells


def _colspan(cell):
    if cell.element is None:
        return 1
    try:
        return max(1, min(64, int(cell.element.get("colspan", "1"))))
    except (TypeError, ValueError):
        return 1


def _align_cell(cell, row_top, row_height):
    alignment = cell.style.keyword("vertical-align", "middle")
    used = cell.height + cell.padding.vertical + cell.border.vertical
    slack = row_height - used
    if slack <= 0:
        cell.height = max(cell.height,
                          row_height - cell.padding.vertical -
                          cell.border.vertical)
        return
    if alignment in ("middle", "center"):
        _translate(cell, 0.0, slack / 2.0)
    elif alignment in ("bottom", "baseline") and alignment == "bottom":
        _translate(cell, 0.0, slack)


def _distribute_columns(minimums, maximums, available):
    total_min = sum(minimums)
    total_max = sum(maximums)
    count = len(minimums)
    if count == 0:
        return []
    if total_max <= available:
        widths = list(maximums)
        slack = available - total_max
        if slack > 0 and total_max > 0:
            widths = [w + slack * (w / total_max) for w in widths]
        elif slack > 0:
            widths = [available / count] * count
        return widths
    if total_min >= available:
        return list(minimums)
    room = available - total_min
    spread = total_max - total_min
    return [minimums[i] + room * ((maximums[i] - minimums[i]) / spread
                                  if spread else 1.0 / count)
            for i in range(count)]


def _translate(box, dx, dy):
    if dx == 0 and dy == 0:
        return
    box.x += dx
    box.y += dy
    for line in box.lines:
        line.x += dx
        line.y += dy
        for fragment in line.fragments:
            fragment.x += dx
            fragment.y += dy
            if fragment.box is not None:
                _translate(fragment.box, dx, dy)
    for child in box.children:
        if isinstance(child, (BlockBox, ReplacedBox, TableBox)):
            _translate(child, dx, dy)


# ------------------------------------------------------------------ floats

class FloatContext:
    """Where floats sit in a block formatting context, and how they shorten
    the lines that flow beside them."""

    def __init__(self):
        self.left = []          # (top, bottom, right_edge)
        self.right = []         # (top, bottom, left_edge)

    def place(self, y, width, height, container_left, container_right, side):
        top = y
        while True:
            left_edge, right_edge = self.edges_at(top, height,
                                                  container_left,
                                                  container_right)
            if right_edge - left_edge >= width or \
                    (not self.left and not self.right):
                break
            nxt = self.next_boundary(top)
            if nxt is None:
                left_edge, right_edge = container_left, container_right
                break
            top = nxt
        if side == "left":
            x = left_edge
            self.left.append((top, top + height, x + width))
        else:
            x = right_edge - width
            self.right.append((top, top + height, x))
        return top, x

    def edges_at(self, y, height, container_left, container_right):
        left_edge, right_edge = container_left, container_right
        bottom = y + max(height, 0.001)
        for top, float_bottom, edge in self.left:
            if top < bottom and float_bottom > y:
                left_edge = max(left_edge, edge)
        for top, float_bottom, edge in self.right:
            if top < bottom and float_bottom > y:
                right_edge = min(right_edge, edge)
        if right_edge < left_edge:
            right_edge = left_edge
        return left_edge, right_edge

    def next_boundary(self, y):
        candidates = [b for _, b, _ in self.left + self.right if b > y]
        return min(candidates) if candidates else None

    def clear_to(self, mode):
        bottoms = []
        if mode in ("left", "both"):
            bottoms += [b for _, b, _ in self.left]
        if mode in ("right", "both"):
            bottoms += [b for _, b, _ in self.right]
        return max(bottoms) if bottoms else 0.0

    def lowest_bottom(self):
        bottoms = [b for _, b, _ in self.left + self.right]
        return max(bottoms) if bottoms else 0.0


# -------------------------------------------------------- inline formatting

class _InlineItem:
    __slots__ = ("kind", "text", "style", "font", "box", "width", "element",
                 "collapsible")

    def __init__(self, kind, text="", style=None, font=None, box=None,
                 width=0.0, element=None, collapsible=True):
        self.kind = kind        # word | space | atomic | break
        self.text = text
        self.style = style
        self.font = font
        self.box = box
        self.width = width
        self.element = element
        self.collapsible = collapsible


def _transform(text, style):
    mode = style.keyword("text-transform", "none")
    if mode == "uppercase":
        return text.upper()
    if mode == "lowercase":
        return text.lower()
    if mode == "capitalize":
        return " ".join(w[:1].upper() + w[1:] for w in text.split(" "))
    return text


def collect_inline_items(box, context, items=None, owner=None):
    if items is None:
        items = []
    for child in box.children:
        style = child.style
        if style.keyword("display") == "none":
            continue
        if isinstance(child, TextBox):
            _collect_text(child, context, items, owner)
        elif isinstance(child, ReplacedBox):
            if child.element is not None and child.element.tag == "br":
                items.append(_InlineItem("break", style=style))
                continue
            child.resolve_edges(context, box.width)
            child.resolve_size(context, box.width)
            items.append(_InlineItem(
                "atomic", box=child, style=style,
                width=child.margin_box[2], element=child.element))
        elif isinstance(child, InlineBox):
            collect_inline_items(child, context, items, child)
        elif isinstance(child, BlockBox):
            if child.style.display in ("inline-block", "inline-table"):
                child.resolve_edges(context, box.width)
                available = box.width
                preferred = shrink_to_fit(child, context, available)
                child.layout(context,
                             preferred + child.margin.horizontal +
                             child.padding.horizontal + child.border.horizontal,
                             0.0, 0.0, None)
                items.append(_InlineItem("atomic", box=child, style=child.style,
                                         width=child.margin_box[2],
                                         element=child.element))
            else:
                items.append(_InlineItem("atomic", box=child,
                                         style=child.style,
                                         width=child.margin_box[2],
                                         element=child.element))
    return items


def _collect_text(text_box, context, items, owner):
    style = text_box.style
    whitespace = style.keyword("white-space", "normal")
    font = font_for_style(style)
    metrics = context.metrics
    raw = _transform(text_box.text, style)
    element = text_box.source

    if whitespace in ("pre", "pre-wrap", "break-spaces"):
        lines = raw.split("\n")
        for index, line in enumerate(lines):
            if index:
                items.append(_InlineItem("break", style=style))
            if not line:
                continue
            if whitespace == "pre":
                items.append(_InlineItem(
                    "word", line.replace("\t", "        "), style, font,
                    width=metrics.measure(line.replace("\t", "        "), font),
                    element=element, collapsible=False))
            else:
                for piece in _split_keeping_spaces(line):
                    items.append(_InlineItem(
                        "word" if piece.strip() else "space", piece, style,
                        font, width=metrics.measure(piece, font),
                        element=element, collapsible=False))
        return

    for index, chunk in enumerate(raw.split()):
        if index or (raw[:1].isspace() and items):
            items.append(_InlineItem("space", " ", style, font,
                                     width=metrics.measure(" ", font),
                                     element=element))
        items.append(_InlineItem("word", chunk, style, font,
                                 width=metrics.measure(chunk, font),
                                 element=element))
    if raw[-1:].isspace() and raw.strip():
        items.append(_InlineItem("space", " ", style, font,
                                 width=metrics.measure(" ", font),
                                 element=element))
    elif raw.strip() == "" and raw and items:
        items.append(_InlineItem("space", " ", style, font,
                                 width=metrics.measure(" ", font),
                                 element=element))


def _split_keeping_spaces(text):
    out, current, in_space = [], [], None
    for char in text:
        space = char.isspace()
        if in_space is None or space == in_space:
            current.append(char)
        else:
            out.append("".join(current))
            current = [char]
        in_space = space
    if current:
        out.append("".join(current))
    return out


def layout_inline_content(box, context, float_context):
    """Break the inline content of *box* into line boxes."""
    items = collect_inline_items(box, context)
    marker = getattr(box, "marker_text", "")
    style = box.style
    metrics = context.metrics
    lengths = context.lengths(style, box.width)

    box.lines = []
    if not items and not marker:
        return 0.0

    nowrap = style.keyword("white-space") in ("nowrap", "pre")
    indent = style.length("text-indent", lengths, 0.0)
    align = style.keyword("text-align", "start")
    if align in ("start", ""):
        align = "left"
    if align == "end":
        align = "right"

    cursor_y = box.y
    line = LineBox()
    line_items = []
    used = indent
    pending_space = None

    def available_at(y, height):
        left_edge, right_edge = float_context.edges_at(
            y, height, box.x, box.x + box.width)
        return max(0.0, right_edge - left_edge), left_edge

    def flush(force_align=None):
        nonlocal cursor_y, line, line_items, used, pending_space
        if not line_items:
            return
        fragments = []
        for item in line_items:
            fragments.append(item)
        height, baseline = _line_metrics(fragments, metrics, style, context)
        width_available, left_edge = available_at(cursor_y, height)
        total = sum(f.width for f in fragments)
        offset = 0.0
        chosen = force_align or align
        if chosen == "center":
            offset = max(0.0, (width_available - total) / 2.0)
        elif chosen == "right":
            offset = max(0.0, width_available - total)
        x = left_edge + offset + (indent if not box.lines else 0.0)
        extra_gap = 0.0
        if chosen == "justify" and force_align is None:
            gaps = sum(1 for f in fragments if f.kind == "space")
            if gaps and width_available > total:
                extra_gap = (width_available - total) / gaps
        for fragment in fragments:
            fragment.x = x
            fragment.y = cursor_y + baseline - fragment.baseline
            if fragment.box is not None:
                _translate(fragment.box,
                           fragment.x - fragment.box.margin_box[0],
                           fragment.y - fragment.box.margin_box[1])
            x += fragment.width
            if fragment.kind == "space":
                x += extra_gap
        line.x, line.y = left_edge, cursor_y
        line.width, line.height, line.baseline = total, height, baseline
        line.fragments = fragments
        box.lines.append(line)
        cursor_y += height
        line = LineBox()
        line_items = []
        used = 0.0
        pending_space = None

    if marker and style.keyword("list-style-position", "outside") == "inside":
        font = font_for_style(style)
        width = metrics.measure(marker + " ", font)
        line_items.append(Fragment("marker", width,
                                   metrics.line_spacing(font),
                                   metrics.ascent(font), style, font,
                                   marker + " "))
        used += width

    for item in items:
        if item.kind == "break":
            if not line_items:
                font = font_for_style(item.style)
                line_items.append(Fragment("text", 0.0,
                                           metrics.line_spacing(font),
                                           metrics.ascent(font), item.style,
                                           font, ""))
            flush(force_align="left" if align == "justify" else None)
            continue

        if item.kind == "space":
            if not line_items and item.collapsible:
                continue
            pending_space = item
            continue

        width = item.width
        space_width = pending_space.width if pending_space is not None else 0.0
        probe_height = _probe_height(item, metrics)
        room, _ = available_at(cursor_y, probe_height)
        if line_items and not nowrap and used + space_width + width > room + 0.01:
            flush(force_align="left" if align == "justify" and
                  item is items[-1] else None)
            space_width = 0.0
            pending_space = None

        if pending_space is not None:
            font = pending_space.font
            line_items.append(Fragment("space", pending_space.width,
                                       metrics.line_spacing(font),
                                       metrics.ascent(font),
                                       pending_space.style, font, " ",
                                       element=pending_space.element))
            used += pending_space.width
            pending_space = None

        if item.kind == "atomic":
            child = item.box
            height = child.margin_box[3]
            baseline = _atomic_baseline(child, metrics)
            line_items.append(Fragment("atomic", item.width, height, baseline,
                                       item.style, box=child,
                                       element=item.element))
        else:
            font = item.font
            line_items.append(Fragment("text", width,
                                       metrics.line_spacing(font),
                                       metrics.ascent(font), item.style, font,
                                       item.text, element=item.element))
        used += width

    flush(force_align="left" if align == "justify" else None)

    if marker and style.keyword("list-style-position", "outside") != "inside" \
            and box.lines:
        font = font_for_style(style)
        width = metrics.measure(marker + " ", font)
        first = box.lines[0]
        fragment = Fragment("marker", width, first.height,
                            metrics.ascent(font), style, font, marker + " ")
        fragment.x = box.x - width
        fragment.y = first.y + first.baseline - metrics.ascent(font)
        first.fragments.insert(0, fragment)

    return max(0.0, cursor_y - box.y)


def _probe_height(item, metrics):
    if item.kind == "atomic" and item.box is not None:
        return max(1.0, item.box.margin_box[3])
    if item.font is not None:
        return metrics.line_spacing(item.font)
    return 1.0


def _atomic_baseline(child, metrics):
    """An inline-block sits on the baseline of its last line box."""
    height = child.margin_box[3]
    alignment = child.style.keyword("vertical-align", "baseline")
    if alignment in ("top", "text-top", "middle", "bottom", "text-bottom"):
        return height
    if isinstance(child, BlockBox) and child.lines:
        last = child.lines[-1]
        return (last.y + last.baseline) - child.margin_box[1]
    return height


def _line_metrics(fragments, metrics, style, context):
    above = below = 0.0
    lengths = context.lengths(style, None)
    leading_height = style.line_height(lengths)
    for fragment in fragments:
        fragment_style = fragment.style
        if fragment.kind == "atomic":
            alignment = fragment_style.keyword("vertical-align", "baseline")
            if alignment in ("middle",):
                above = max(above, fragment.height * 0.5 + 0.25 *
                            fragment_style.font_size)
                below = max(below, fragment.height * 0.5 - 0.25 *
                            fragment_style.font_size)
                continue
            above = max(above, fragment.baseline)
            below = max(below, fragment.height - fragment.baseline)
            continue
        font = fragment.font
        own_lengths = context.lengths(fragment_style, None)
        line_height = fragment_style.line_height(own_lengths)
        content = metrics.line_spacing(font)
        half_leading = (line_height - content) / 2.0
        ascent = metrics.ascent(font) + half_leading
        descent = metrics.descent(font) + half_leading
        shift = _vertical_shift(fragment_style, font, metrics)
        above = max(above, ascent + shift)
        below = max(below, descent - shift)
        fragment.baseline = metrics.ascent(font) + shift
        fragment.height = content
    if not fragments:
        return leading_height, leading_height * 0.8
    height = above + below
    if height <= 0:
        height = leading_height
    return height, above


def _vertical_shift(style, font, metrics):
    alignment = style.keyword("vertical-align", "baseline")
    if alignment == "super":
        return font.size * 0.33
    if alignment == "sub":
        return -font.size * 0.2
    return 0.0


# ------------------------------------------------- intrinsic width measuring

def min_content_width(box, context):
    """Width of the widest unbreakable piece of content."""
    if isinstance(box, TextBox):
        font = font_for_style(box.style)
        words = _transform(box.text, box.style).split()
        return max([context.metrics.measure(w, font) for w in words] or [0.0])
    if isinstance(box, ReplacedBox):
        box.resolve_size(context, 0.0)
        return box.margin_box[2]
    if isinstance(box, (BlockBox, TableBox)):
        if not _is_auto(box.style, "width"):
            declared = parse_length(box.style.get("width"),
                                    context.lengths(box.style, None))
            if declared is not None:
                return declared
    widest = 0.0
    if isinstance(box, (BlockBox, TableBox)) and box.has_block_children():
        for child in box.children:
            widest = max(widest, min_content_width(child, context) +
                         _edge_width(child, context))
        return widest
    for child in box.children:
        widest = max(widest, min_content_width(child, context))
    return widest


def max_content_width(box, context):
    """Width the content would take if never wrapped."""
    if isinstance(box, TextBox):
        font = font_for_style(box.style)
        text = " ".join(_transform(box.text, box.style).split())
        return context.metrics.measure(text, font)
    if isinstance(box, ReplacedBox):
        box.resolve_size(context, 0.0)
        return box.margin_box[2]
    if isinstance(box, (BlockBox, TableBox)):
        if not _is_auto(box.style, "width"):
            declared = parse_length(box.style.get("width"),
                                    context.lengths(box.style, None))
            if declared is not None:
                return declared
        if box.has_block_children():
            return max([max_content_width(c, context) + _edge_width(c, context)
                        for c in box.children] or [0.0])
    total = 0.0
    for child in box.children:
        if isinstance(child, (BlockBox, TableBox)) and \
                child.style.display not in INLINE_LEVEL:
            total = max(total, max_content_width(child, context))
        else:
            total += max_content_width(child, context)
    return total


def _edge_width(box, context):
    style = box.style
    lengths = context.lengths(style, None)
    total = 0.0
    for side in ("left", "right"):
        value = parse_length(style.get("padding-" + side), lengths)
        total += value or 0.0
        value = parse_length(style.get("margin-" + side), lengths)
        total += value or 0.0
        total += style.border_width(side, lengths)
    return total


def shrink_to_fit(box, context, available):
    """CSS 2.1 shrink-to-fit: min(max(preferred minimum, available),
    preferred)."""
    if not _is_auto(box.style, "width"):
        declared = parse_length(box.style.get("width"),
                                context.lengths(box.style, available))
        if declared is not None:
            return max(0.0, declared)
    inner = available - box.margin.horizontal - box.padding.horizontal - \
        box.border.horizontal
    preferred = max_content_width(box, context)
    minimum = min_content_width(box, context)
    return max(0.0, min(max(minimum, min(preferred, inner)), max(preferred, 0.0)))


# ----------------------------------------------------------- box tree build

def _roman(number, upper=False):
    if not 0 < number < 4000:
        return str(number)
    numerals = (("m", 1000), ("cm", 900), ("d", 500), ("cd", 400), ("c", 100),
                ("xc", 90), ("l", 50), ("xl", 40), ("x", 10), ("ix", 9),
                ("v", 5), ("iv", 4), ("i", 1))
    out = []
    for symbol, value in numerals:
        while number >= value:
            out.append(symbol)
            number -= value
    text = "".join(out)
    return text.upper() if upper else text


def _alpha(number, upper=False):
    if number < 1:
        return str(number)
    letters = ""
    while number > 0:
        number, remainder = divmod(number - 1, 26)
        letters = chr(ord("a") + remainder) + letters
    return letters.upper() if upper else letters


def marker_for(element, style, index):
    kind = style.keyword("list-style-type", "disc")
    if kind in MARKERS:
        return MARKERS[kind]
    if kind == "decimal":
        return "%d." % index
    if kind == "decimal-leading-zero":
        return "%02d." % index
    if kind == "lower-roman":
        return _roman(index) + "."
    if kind == "upper-roman":
        return _roman(index, True) + "."
    if kind in ("lower-alpha", "lower-latin"):
        return _alpha(index) + "."
    if kind in ("upper-alpha", "upper-latin"):
        return _alpha(index, True) + "."
    return MARKERS["disc"]


def _list_index(element):
    if element.has("value"):
        try:
            return int(element.get("value"))
        except ValueError:
            pass
    index = 1
    parent = element.parent
    if parent is None:
        return index
    start = 1
    if isinstance(parent, Element) and parent.has("start"):
        try:
            start = int(parent.get("start"))
        except ValueError:
            start = 1
    for sibling in parent.children:
        if sibling is element:
            break
        if isinstance(sibling, Element) and sibling.tag == "li":
            index += 1
    return start + index - 1


def _replaced_for(element, style, context):
    tag = element.tag
    if tag == "br":
        return ReplacedBox(style, element, (0.0, 0.0))
    if tag == "img":
        source = element.get("src") or ""
        image = context.load_image(source)
        if image is not None:
            return ReplacedBox(style, element,
                               (float(image.width), float(image.height)),
                               image=image)
        alt = (element.get("alt") or "").strip()
        if alt:
            return None                # fall through to rendering the alt text
        return ReplacedBox(style, element, (0.0, 0.0), label="")
    if tag in ("input", "select"):
        kind = (element.get("type") or "text").lower()
        if kind in ("checkbox", "radio"):
            return ReplacedBox(style, element, (13.0, 13.0), label=kind)
        text = element.get("value") or ""
        if kind in ("submit", "reset", "button") and not text:
            text = {"submit": "Submit", "reset": "Reset"}.get(kind, "Button")
        font = font_for_style(style)
        try:
            size = int(element.get("size") or 20)
        except ValueError:
            size = 20
        width = max(context.metrics.measure(text or "x" * size, font) + 8.0,
                    30.0)
        height = context.metrics.line_spacing(font) + 6.0
        return ReplacedBox(style, element, (width, height), label=text)
    if tag == "textarea":
        font = font_for_style(style)
        try:
            columns = int(element.get("cols") or 30)
            rows = int(element.get("rows") or 3)
        except ValueError:
            columns, rows = 30, 3
        return ReplacedBox(style, element,
                           (context.metrics.measure("x" * columns, font) + 8.0,
                            context.metrics.line_spacing(font) * rows + 6.0),
                           label=element.text_content)
    if tag in ("iframe", "video", "canvas", "embed", "object", "svg"):
        return ReplacedBox(style, element, (300.0, 150.0), label=tag)
    return None


def _make_box(element, style, context):
    display = style.display
    if display == "list-item":
        box = ListItemBox(style, element)
        box.marker_text = marker_for(element, style, _list_index(element))
        return box
    if display in ("table", "inline-table"):
        return TableBox(style, element)
    if display in INLINE_LEVEL and display != "inline-block":
        return InlineBox(style, element)
    return BlockBox(style, element)


def build_box_tree(node, styles, context):
    """Create the box for *node*, recursing into its children."""
    if isinstance(node, Comment):
        return None
    if isinstance(node, Text):
        parent_style = getattr(node.parent, "_style_cache", None)
        if parent_style is None or not node.data:
            return None
        return TextBox(parent_style, node.data, node.parent)
    if not isinstance(node, Element):
        return None

    style = styles.get(id(node)) or node._style_cache
    if style is None or style.display == "none":
        return None
    if node.tag in ("head", "script", "style", "title", "meta", "link"):
        return None

    if node.tag in REPLACED_TAGS:
        replaced = _replaced_for(node, style, context)
        if replaced is not None:
            return replaced

    box = _make_box(node, style, context)
    for child in node.children:
        child_box = build_box_tree(child, styles, context)
        if child_box is not None:
            box.add(child_box)
    if isinstance(box, (BlockBox, TableBox)):
        _fix_up_children(box, style)
    return box


def _is_inline_level(box):
    if isinstance(box, (TextBox, InlineBox)):
        return True
    if isinstance(box, ReplacedBox):
        return box.style.display in INLINE_LEVEL
    return box.style.display in INLINE_LEVEL


def _fix_up_children(box, style):
    """Insert the anonymous boxes CSS requires around mixed content."""
    children = box.children
    if not children:
        return

    if isinstance(box, TableBox):
        _fix_up_table(box)
        return
    if style.display in ("table-row-group", "table-header-group",
                         "table-footer-group"):
        _wrap_cells_in_rows(box)
        return
    if style.display == "table-row":
        return

    floats_and_blocks = any(
        not _is_inline_level(c) and
        c.style.keyword("position") not in ("absolute", "fixed")
        for c in children)
    if not floats_and_blocks:
        return

    rebuilt = []
    run = []

    def flush_run():
        if not run:
            return
        if all(isinstance(c, TextBox) and not c.text.strip() for c in run):
            run.clear()
            return
        anonymous = BlockBox(style, None, anonymous=True)
        for item in run:
            anonymous.add(item)
        rebuilt.append(anonymous)
        anonymous.parent = box
        run.clear()

    for child in children:
        if _is_inline_level(child):
            run.append(child)
        else:
            flush_run()
            rebuilt.append(child)
    flush_run()
    box.children = rebuilt


def _wrap_cells_in_rows(box):
    rebuilt, run = [], []

    def flush():
        if not run:
            return
        row = BlockBox(run[0].style, None, anonymous=True)
        row.style = _synthetic_style(box.style, "table-row")
        for cell in run:
            row.add(cell)
        row.parent = box
        rebuilt.append(row)
        run.clear()

    for child in box.children:
        if child.style.display == "table-cell":
            run.append(child)
        elif child.style.display == "table-row":
            flush()
            rebuilt.append(child)
        elif isinstance(child, TextBox) and not child.text.strip():
            continue
        else:
            run.append(child)
    flush()
    box.children = rebuilt


def _fix_up_table(box):
    rebuilt, loose = [], []

    def flush():
        if not loose:
            return
        group = BlockBox(_synthetic_style(box.style, "table-row-group"), None,
                         anonymous=True)
        for item in loose:
            group.add(item)
        group.parent = box
        _wrap_cells_in_rows(group)
        rebuilt.append(group)
        loose.clear()

    for child in box.children:
        display = child.style.display
        if display in ("table-row-group", "table-header-group",
                       "table-footer-group"):
            flush()
            _wrap_cells_in_rows(child)
            rebuilt.append(child)
        elif display in ("table-row", "table-cell"):
            loose.append(child)
        elif isinstance(child, TextBox) and not child.text.strip():
            continue
        elif display in ("table-column", "table-column-group", "table-caption"):
            continue
        else:
            loose.append(child)
    flush()
    box.children = rebuilt


class _SyntheticStyle:
    """A style object for an anonymous box: inherited values, fixed display."""

    __slots__ = ("props", "font_size", "color", "element")

    def __init__(self, parent, display):
        self.props = dict(parent.props)
        self.props["display"] = display
        for name in ("margin-top", "margin-right", "margin-bottom",
                     "margin-left", "padding-top", "padding-right",
                     "padding-bottom", "padding-left", "background-color",
                     "width", "height"):
            self.props[name] = "0" if name.startswith(("margin", "padding")) \
                else ("auto" if name in ("width", "height") else "transparent")
        self.font_size = parent.font_size
        self.color = parent.color
        self.element = None


def _synthetic_style(parent, display):
    from .style import Style
    synthetic = _SyntheticStyle(parent, display)
    return Style(synthetic.props, synthetic.font_size, synthetic.color, None)


# --------------------------------------------------------- document layout

def layout_document(document, styles, context):
    """Lay out a whole document and return the root box."""
    root_element = document.document_element
    if root_element is None:
        return None
    context.deferred_absolutes = []
    root_box = build_box_tree(root_element, styles, context)
    if root_box is None:
        return None
    if not isinstance(root_box, BlockBox):
        wrapper = BlockBox(root_box.style, None, anonymous=True)
        wrapper.add(root_box)
        root_box = wrapper
    root_box.layout(context, context.viewport_width, 0.0, 0.0)
    _layout_absolutes(context, root_box)
    return root_box


def _layout_absolutes(context, root_box):
    pending = list(context.deferred_absolutes)
    context.deferred_absolutes = []
    guard = 0
    while pending and guard < 64:
        guard += 1
        for box, static_parent in pending:
            containing = _containing_block(box, root_box)
            _layout_absolute(box, containing, static_parent, context)
        pending = list(context.deferred_absolutes)
        context.deferred_absolutes = []


def _containing_block(box, root_box):
    ancestor = box.parent
    while ancestor is not None:
        if ancestor.style.keyword("position") in ("relative", "absolute",
                                                  "fixed", "sticky"):
            return ancestor
        ancestor = ancestor.parent
    return root_box


def _layout_absolute(box, containing, static_parent, context):
    cx, cy, cw, ch = containing.padding_box
    style = box.style
    box.resolve_edges(context, cw)
    lengths = context.lengths(style, cw)
    left = parse_length(style.get("left"), lengths)
    right = parse_length(style.get("right"), lengths)
    top = parse_length(style.get("top"), lengths)
    bottom = parse_length(style.get("bottom"), lengths)

    if _is_auto(style, "width"):
        if left is not None and right is not None:
            width = max(0.0, cw - left - right - box.margin.horizontal -
                        box.padding.horizontal - box.border.horizontal)
        else:
            width = shrink_to_fit(box, context, cw)
    else:
        width = parse_length(style.get("width"), lengths) or 0.0
        if style.keyword("box-sizing") == "border-box":
            width -= box.padding.horizontal + box.border.horizontal

    outer = width + box.padding.horizontal + box.border.horizontal
    if left is not None:
        x = cx + left
    elif right is not None:
        x = cx + cw - right - outer - box.margin.horizontal
    else:
        x = static_parent.x if static_parent is not None else cx
    y = cy + top if top is not None else (
        static_parent.y if static_parent is not None and bottom is None else cy)

    box.layout(context, outer + box.margin.horizontal,
               x - box.margin.left, y - box.margin.top, None)

    if bottom is not None and top is None:
        target = cy + ch - bottom - box.margin_box[3]
        _translate(box, 0.0, target - box.margin_box[1])
