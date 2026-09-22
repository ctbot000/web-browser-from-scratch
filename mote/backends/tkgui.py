"""The browser window: Tk chrome around the engine.

Tk is used only as a drawing surface and an event source.  Everything on the
page -- every rectangle, every run of text, every image -- comes from the
engine's display list.
"""

from __future__ import annotations

import base64
import tkinter
from tkinter import font as tkfont

from ..browser import Engine, History, resolve
from ..fonts import TkMetrics
from ..paint import DrawBorder, DrawImage, DrawLine, DrawRect, DrawText
from ..url import URL, URLError
from ..values import to_hex

SCROLL_STEP = 60
CHROME_BG = "#f2f3f5"
CHROME_LINE = "#d6d9de"
HOME = "about:mote"


class Tab:
    def __init__(self, engine):
        self.engine = engine
        self.history = History()
        self.page = None
        self.scroll = 0.0
        self.zoom = 1.0

    @property
    def title(self):
        if self.page is None:
            return "New Tab"
        title = self.page.document.title or str(self.page.url)
        return title[:28] + ("…" if len(title) > 28 else "")

    def load(self, url, referrer=None, record=True):
        self.page = self.engine.load(url, referrer=referrer)
        if record:
            self.history.visit(self.page.url)
        self.scroll = 0.0
        return self.page


class BrowserWindow:
    def __init__(self, start_url=HOME, width=1100, height=760):
        self.root = tkinter.Tk()
        self.root.title("Mote")
        self.root.geometry("%dx%d" % (width, height))
        self.metrics = TkMetrics(self.root)
        self.engine = Engine(metrics=self.metrics, width=width,
                             height=height, on_status=self.set_status)
        self.tabs = []
        self.active = 0
        self._photos = []
        self._hovered = None
        self._last_size = (0, 0)
        self._in_status = False
        self._build_chrome()
        self.new_tab(start_url)

    # -- chrome -----------------------------------------------------------

    def _build_chrome(self):
        ui = tkfont.Font(family="Helvetica", size=12)
        self.tabbar = tkinter.Frame(self.root, bg=CHROME_BG, height=30)
        self.tabbar.pack(fill="x", side="top")

        toolbar = tkinter.Frame(self.root, bg=CHROME_BG)
        toolbar.pack(fill="x", side="top")
        self.back_button = tkinter.Button(toolbar, text="←", width=2,
                                          command=self.go_back,
                                          highlightthickness=0)
        self.back_button.pack(side="left", padx=(8, 2), pady=6)
        self.forward_button = tkinter.Button(toolbar, text="→", width=2,
                                             command=self.go_forward,
                                             highlightthickness=0)
        self.forward_button.pack(side="left", padx=2, pady=6)
        tkinter.Button(toolbar, text="↻", width=2, command=self.reload,
                       highlightthickness=0).pack(side="left", padx=2, pady=6)

        self.address = tkinter.Entry(toolbar, font=ui, relief="solid",
                                     borderwidth=1)
        self.address.pack(side="left", fill="x", expand=True, padx=8, pady=6,
                          ipady=3)
        self.address.bind("<Return>", self.on_address_enter)
        tkinter.Button(toolbar, text="+", width=2, command=lambda:
                       self.new_tab(HOME), highlightthickness=0).pack(
                           side="left", padx=(0, 8), pady=6)

        tkinter.Frame(self.root, bg=CHROME_LINE, height=1).pack(fill="x")

        body = tkinter.Frame(self.root)
        body.pack(fill="both", expand=True)
        self.canvas = tkinter.Canvas(body, bg="white", highlightthickness=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar = tkinter.Scrollbar(body, orient="vertical",
                                           command=self.on_scrollbar)
        self.scrollbar.pack(side="right", fill="y")

        self.status = tkinter.Label(self.root, text="", anchor="w", bg=CHROME_BG,
                                    font=("Helvetica", 10), fg="#54585d")
        self.status.pack(fill="x", side="bottom")

        self.canvas.bind("<Configure>", self.on_resize)
        self.canvas.bind("<Button-1>", self.on_click)
        self.canvas.bind("<Motion>", self.on_motion)
        self.canvas.bind("<MouseWheel>", self.on_wheel)
        self.canvas.bind("<Button-4>", lambda e: self.scroll_by(-SCROLL_STEP))
        self.canvas.bind("<Button-5>", lambda e: self.scroll_by(SCROLL_STEP))

        bindings = {
            "<Down>": lambda e: self.scroll_by(SCROLL_STEP),
            "<Up>": lambda e: self.scroll_by(-SCROLL_STEP),
            "<Next>": lambda e: self.scroll_by(self.viewport_height() * 0.9),
            "<Prior>": lambda e: self.scroll_by(-self.viewport_height() * 0.9),
            "<space>": lambda e: self.scroll_by(self.viewport_height() * 0.9),
            "<Home>": lambda e: self.scroll_to(0),
            "<End>": lambda e: self.scroll_to(1e9),
            "<Control-l>": self.focus_address,
            "<Control-r>": lambda e: self.reload(),
            "<F5>": lambda e: self.reload(),
            "<Control-t>": lambda e: self.new_tab(HOME),
            "<Control-w>": lambda e: self.close_tab(),
            "<Control-u>": lambda e: self.view_source(),
            "<Alt-Left>": lambda e: self.go_back(),
            "<Alt-Right>": lambda e: self.go_forward(),
            "<Control-plus>": lambda e: self.change_zoom(1.1),
            "<Control-equal>": lambda e: self.change_zoom(1.1),
            "<Control-minus>": lambda e: self.change_zoom(1 / 1.1),
            "<Control-0>": lambda e: self.change_zoom(0),
        }
        for sequence, handler in bindings.items():
            self.root.bind_all(sequence, handler)

    # -- tabs -------------------------------------------------------------

    @property
    def tab(self):
        return self.tabs[self.active] if self.tabs else None

    def new_tab(self, url):
        tab = Tab(self.engine)
        self.tabs.append(tab)
        self.active = len(self.tabs) - 1
        self.navigate(url)

    def close_tab(self):
        if len(self.tabs) <= 1:
            self.root.destroy()
            return
        del self.tabs[self.active]
        self.active = min(self.active, len(self.tabs) - 1)
        self.refresh_tabs()
        self.render()

    def select_tab(self, index):
        self.active = index
        self.refresh_tabs()
        self.render()

    def refresh_tabs(self):
        for child in self.tabbar.winfo_children():
            child.destroy()
        for index, tab in enumerate(self.tabs):
            active = index == self.active
            button = tkinter.Label(
                self.tabbar, text=" %s " % tab.title,
                bg="#ffffff" if active else CHROME_BG,
                fg="#1b1f23" if active else "#57606a",
                font=("Helvetica", 11, "bold" if active else "normal"),
                padx=8, pady=5)
            button.pack(side="left", padx=(2, 0), pady=(3, 0))
            button.bind("<Button-1>",
                        lambda e, i=index: self.select_tab(i))

    # -- navigation -------------------------------------------------------

    def navigate(self, url, referrer=None, record=True):
        tab = self.tab
        try:
            if isinstance(url, str):
                url = URL.from_user_input(
                    url, "https://duckduckgo.com/html/?q=%s")
        except URLError as exc:
            self.set_status(str(exc))
            return
        self.engine.width = self.viewport_width() / tab.zoom
        self.engine.height = self.viewport_height() / tab.zoom
        try:
            tab.load(url, referrer=referrer, record=record)
        except Exception as exc:                       # keep the window alive
            self.set_status("%s: %s" % (type(exc).__name__, exc))
            return
        self.address.delete(0, "end")
        self.address.insert(0, str(tab.page.url))
        self.root.title("%s — Mote" % tab.page.title)
        self.refresh_tabs()
        self.render()

    def on_address_enter(self, event=None):
        self.navigate(self.address.get())
        self.canvas.focus_set()

    def focus_address(self, event=None):
        self.address.focus_set()
        self.address.select_range(0, "end")
        return "break"

    def go_back(self):
        url = self.tab.history.back()
        if url is not None:
            self.navigate(url, record=False)

    def go_forward(self):
        url = self.tab.history.forward()
        if url is not None:
            self.navigate(url, record=False)

    def reload(self):
        if self.tab.page is not None:
            self.engine.loader.client.cache.clear()
            self.navigate(self.tab.page.url, record=False)

    def view_source(self):
        if self.tab.page is not None:
            self.navigate("view-source:" + str(self.tab.page.url))

    def change_zoom(self, factor):
        tab = self.tab
        tab.zoom = 1.0 if not factor else max(0.4, min(4.0, tab.zoom * factor))
        self.set_status("Zoom %d%%" % round(tab.zoom * 100))
        if tab.page is not None:
            self.navigate(tab.page.url, record=False)

    # -- geometry ---------------------------------------------------------

    def viewport_width(self):
        return max(200, self.canvas.winfo_width())

    def viewport_height(self):
        return max(200, self.canvas.winfo_height())

    def on_resize(self, event):
        # Re-laying out fires more Configure events; without this guard the
        # window never finishes processing its own resize.
        if (event.width, event.height) == self._last_size:
            return
        self._last_size = (event.width, event.height)
        tab = self.tab
        if tab is None or tab.page is None:
            return
        self.engine.render(tab.page, event.width / tab.zoom,
                           event.height / tab.zoom)
        self.render()

    def on_scrollbar(self, *args):
        if not args:
            return
        if args[0] == "moveto":
            self.scroll_to(float(args[1]) * self.document_height())
        elif args[0] == "scroll":
            amount = int(args[1])
            unit = args[2]
            step = SCROLL_STEP if unit == "units" else self.viewport_height()
            self.scroll_by(amount * step)

    def document_height(self):
        tab = self.tab
        if tab is None or tab.page is None:
            return 1.0
        return max(tab.page.height * tab.zoom, self.viewport_height())

    def scroll_by(self, delta):
        self.scroll_to(self.tab.scroll + delta)

    def scroll_to(self, position):
        tab = self.tab
        limit = max(0.0, self.document_height() - self.viewport_height())
        tab.scroll = max(0.0, min(position, limit))
        self.render()

    def on_wheel(self, event):
        self.scroll_by(-event.delta * 3)

    # -- input ------------------------------------------------------------

    def _document_point(self, event):
        tab = self.tab
        return (event.x / tab.zoom, (event.y + tab.scroll) / tab.zoom)

    def on_click(self, event):
        tab = self.tab
        if tab.page is None:
            return
        self.canvas.focus_set()
        x, y = self._document_point(event)
        anchor = tab.page.link_at(x, y)
        if anchor is None:
            return
        target = resolve(tab.page.url, anchor.get("href"))
        if target is None:
            return
        if target.fragment is not None and \
                str(target.without_fragment()) == \
                str(tab.page.url.without_fragment()):
            self.scroll_to_fragment(target.fragment)
            return
        self.navigate(target, referrer=tab.page.url)

    def scroll_to_fragment(self, name):
        tab = self.tab
        for element in tab.page.document.elements():
            if element.get("id") == name or element.get("name") == name:
                box = element.layout_box
                if box is not None:
                    self.scroll_to(box.margin_box[1] * tab.zoom)
                return
        self.set_status("No anchor named %r" % name)

    def on_motion(self, event):
        tab = self.tab
        if tab.page is None:
            return
        x, y = self._document_point(event)
        anchor = tab.page.link_at(x, y)
        if anchor is self._hovered:
            return
        self._hovered = anchor
        if anchor is None:
            self.canvas.config(cursor="")
            self.set_status("")
        else:
            self.canvas.config(cursor="hand2")
            target = resolve(tab.page.url, anchor.get("href"))
            self.set_status(str(target) if target else anchor.get("href"))

    def set_status(self, message):
        # No update_idletasks() here: this is called from inside event
        # handlers, and pumping the loop from there re-enters them.
        self.status.config(text=message)

    # -- drawing ----------------------------------------------------------

    def render(self):
        tab = self.tab
        self.canvas.delete("all")
        self._photos = []
        if tab is None or tab.page is None:
            return
        zoom = tab.zoom
        top = tab.scroll / zoom
        bottom = top + self.viewport_height() / zoom

        for command in tab.page.display_list:
            if command.bottom < top or command.top > bottom:
                continue
            self._draw(command, -tab.scroll, zoom)

        self.back_button.config(
            state="normal" if tab.history.can_go_back() else "disabled")
        self.forward_button.config(
            state="normal" if tab.history.can_go_forward() else "disabled")
        height = self.document_height()
        visible = self.viewport_height()
        self.scrollbar.set(tab.scroll / height,
                           min(1.0, (tab.scroll + visible) / height))

    def _draw(self, command, offset, zoom):
        x, y, width, height = command.rect
        x, y = x * zoom, y * zoom + offset
        width, height = width * zoom, height * zoom

        if isinstance(command, DrawRect):
            if width <= 0 or height <= 0:
                return
            self.canvas.create_rectangle(x, y, x + width, y + height,
                                         fill=to_hex(command.color),
                                         outline="")
        elif isinstance(command, DrawLine):
            self.canvas.create_rectangle(
                x, y, x + width, y + max(1.0, command.thickness * zoom),
                fill=to_hex(command.color), outline="")
        elif isinstance(command, DrawBorder):
            self._draw_border(command, x, y, width, height, zoom)
        elif isinstance(command, DrawImage):
            self._draw_image(command, x, y, width, height)
        elif isinstance(command, DrawText):
            font = self.metrics.tk_font(_scaled(command.font, zoom))
            baseline = command.baseline * zoom + offset
            self.canvas.create_text(
                x, baseline - self.metrics.ascent(_scaled(command.font, zoom)),
                text=command.text, font=font, fill=to_hex(command.color),
                anchor="nw")

    def _draw_border(self, command, x, y, width, height, zoom):
        top, right, bottom, left = [w * zoom for w in command.widths]
        edges = ((top, command.colors[0], command.styles[0],
                  (x, y, x + width, y + top)),
                 (right, command.colors[1], command.styles[1],
                  (x + width - right, y, x + width, y + height)),
                 (bottom, command.colors[2], command.styles[2],
                  (x, y + height - bottom, x + width, y + height)),
                 (left, command.colors[3], command.styles[3],
                  (x, y, x + left, y + height)))
        for size, color, style, rect in edges:
            if size <= 0 or style in ("none", "hidden") or color is None:
                continue
            self.canvas.create_rectangle(*rect, fill=to_hex(color), outline="")

    def _draw_image(self, command, x, y, width, height):
        image = command.image
        if image is None or width < 1 or height < 1:
            self.canvas.create_rectangle(x, y, x + width, y + height,
                                         outline="#c8c8c8", fill="#f4f4f4")
            if command.alt:
                self.canvas.create_text(x + 4, y + 4, text=command.alt,
                                        anchor="nw", fill="#666666",
                                        font=("Helvetica", 10))
            return
        scaled = image.resized(int(width), int(height))
        try:
            photo = tkinter.PhotoImage(
                data=base64.b64encode(scaled.to_ppm()).decode("ascii"),
                master=self.root)
        except tkinter.TclError:
            return
        self._photos.append(photo)
        self.canvas.create_image(x, y, image=photo, anchor="nw")

    def run(self):
        self.canvas.focus_set()
        self.root.mainloop()
        self.engine.close()


def _scaled(font, zoom):
    from ..fonts import Font
    if zoom == 1.0:
        return font
    return Font(font.family, font.size * zoom, font.bold, font.italic)


def main(url=HOME):
    BrowserWindow(url).run()
