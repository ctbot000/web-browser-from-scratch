"""Mote: a web browser written from scratch in pure Python.

The engine has no third-party dependencies and no rendering library beneath
it: HTTP, HTML, CSS, layout, painting and image decoding are all implemented
in this package.
"""

__version__ = "0.1.0"
__all__ = ["Engine", "Page", "URL"]


def __getattr__(name):
    if name in ("Engine", "Page"):
        from .browser import Engine, Page
        return {"Engine": Engine, "Page": Page}[name]
    if name == "URL":
        from .url import URL
        return URL
    raise AttributeError(name)
