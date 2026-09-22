"""Command line entry point: ``python3 -m mote``."""

from __future__ import annotations

import argparse
import os
import sys

from . import __version__

DEFAULT_URL = "about:mote"


def build_parser():
    parser = argparse.ArgumentParser(
        prog="mote",
        description="Mote: a web browser written from scratch.",
        epilog="With no options, opens the graphical browser window.")
    parser.add_argument("url", nargs="?", default=DEFAULT_URL,
                        help="the page to open (default: %s)" % DEFAULT_URL)
    parser.add_argument("-t", "--text", action="store_true",
                        help="render the page in the terminal instead")
    parser.add_argument("-c", "--color", action="store_true",
                        help="use colour in terminal rendering")
    parser.add_argument("-s", "--svg", metavar="FILE",
                        help="render the page to an SVG file")
    parser.add_argument("--dump", choices=["dom", "layout", "display", "css",
                                           "headers", "source", "errors"],
                        help="print an intermediate representation and exit")
    parser.add_argument("--columns", type=int, default=100,
                        help="terminal width in characters (default: 100)")
    parser.add_argument("--width", type=int, default=1100,
                        help="viewport width in CSS pixels")
    parser.add_argument("--height", type=int, default=800,
                        help="viewport height in CSS pixels")
    parser.add_argument("--no-images", action="store_true",
                        help="skip loading images")
    parser.add_argument("--full-height", action="store_true",
                        help="make the SVG as tall as the whole document")
    parser.add_argument("--version", action="version",
                        version="mote %s" % __version__)
    return parser


def resolve_argument(text):
    """Accept a URL, a local path, or something to hand to a search engine."""
    from .url import URL
    if os.path.exists(text):
        return "file://" + os.path.abspath(text)
    return str(URL.from_user_input(text, "https://duckduckgo.com/html/?q=%s"))


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.url = resolve_argument(args.url)

    if args.dump:
        return dump(args)
    if args.text:
        return render_terminal(args)
    if args.svg:
        return render_svg_file(args)
    return open_window(args)


def open_window(args):
    try:
        from .backends.tkgui import BrowserWindow
    except ImportError as exc:
        print("The graphical browser needs tkinter, which this Python does "
              "not have (%s).\nTry: python3 -m mote --text %s"
              % (exc, args.url), file=sys.stderr)
        return 2
    BrowserWindow(args.url, args.width, args.height).run()
    return 0


def _engine(args, metrics=None, width=None, height=None):
    from .browser import Engine
    return Engine(metrics=metrics, width=width or args.width,
                  height=height or args.height,
                  load_images=not args.no_images)


def render_terminal(args):
    from .backends.textout import CELL_HEIGHT, CELL_WIDTH, GridMetrics, render_text
    metrics = GridMetrics()
    engine = _engine(args, metrics, args.columns * CELL_WIDTH, 40 * CELL_HEIGHT)
    try:
        page = engine.load(args.url)
        sys.stdout.write(render_text(page, args.columns, color=args.color))
        sys.stdout.write("\n")
    finally:
        engine.close()
    return 0


def render_svg_file(args):
    from .backends.svgout import render_svg
    from .values import to_hex
    engine = _engine(args)
    try:
        page = engine.load(args.url)
        height = page.height if args.full_height else args.height
        background = to_hex(page.canvas_color) if page.canvas_color \
            else "#ffffff"
        svg = render_svg(page.display_list, args.width, height, background,
                         page.title)
        with open(args.svg, "w", encoding="utf-8") as handle:
            handle.write(svg)
        print("Wrote %s (%d x %d, %d drawing commands)"
              % (args.svg, args.width, height, len(page.display_list)))
    finally:
        engine.close()
    return 0


def dump(args):
    from .browser import Engine
    from .dom import dump as dump_dom

    engine = _engine(args)
    try:
        if args.dump == "headers":
            resource = engine.loader.fetch(args.url)
            print("%s %s" % (resource.status, resource.url))
            if resource.headers:
                for name, value in resource.headers:
                    print("%s: %s" % (name, value))
            elif resource.error:
                print("error: %s" % resource.error)
            return 0
        if args.dump == "source":
            resource = engine.loader.fetch(args.url)
            sys.stdout.write(resource.text())
            return 0

        page = engine.load(args.url)
        if args.dump == "dom":
            print("\n".join(dump_dom(page.document)))
        elif args.dump == "errors":
            if not page.document.errors:
                print("No parse errors.")
            for offset, message in page.document.errors:
                print("%8d  %s" % (offset, message))
        elif args.dump == "css":
            for element in page.document.elements():
                style = page.styles.get(id(element))
                if style is None:
                    continue
                print("%s {" % _selector_for(element))
                for name in sorted(style.props):
                    print("    %s: %s;" % (name, style.props[name]))
                print("}")
        elif args.dump == "layout":
            _print_boxes(page.root_box)
        elif args.dump == "display":
            for command in page.display_list:
                print(command)
    finally:
        engine.close()
    return 0


def _selector_for(element):
    out = element.tag
    if element.get("id"):
        out += "#" + element.get("id")
    for name in element.classes[:3]:
        out += "." + name
    return out


def _print_boxes(box, depth=0):
    if box is None:
        print("(empty document)")
        return
    pad = "  " * depth
    print("%s%s" % (pad, box))
    for line in box.lines:
        texts = " ".join(repr(f.text) for f in line.fragments
                         if f.kind in ("text", "marker") and f.text.strip())
        print("%s  line y=%.1f h=%.1f %s" % (pad, line.y, line.height,
                                             texts[:90]))
    for child in box.children:
        _print_boxes(child, depth + 1)


if __name__ == "__main__":
    sys.exit(main())
