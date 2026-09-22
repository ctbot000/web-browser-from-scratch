"""The pages the browser serves itself."""

from __future__ import annotations

_STYLE = """
body { font-family: sans-serif; margin: 0; background: #f6f7f9; color: #1b1f23 }
header { background: #1b1f23; color: #ffffff; padding: 24px 32px }
header h1 { margin: 0; font-size: 1.6em }
header p { margin: 6px 0 0; color: #b8bdc4; font-size: 0.9em }
main { padding: 24px 32px; max-width: 760px }
h2 { font-size: 1.1em; margin-top: 28px; border-bottom: 1px solid #dcdfe4;
     padding-bottom: 6px }
table { border-collapse: collapse; width: 100% }
td, th { text-align: left; padding: 6px 10px; border-bottom: 1px solid #e4e7eb;
         vertical-align: top }
th { width: 180px; color: #57606a; font-weight: normal }
code { background: #eceff3; padding: 1px 5px; font-size: 0.92em }
ul { padding-left: 20px } li { margin: 4px 0 }
a { color: #0b5cd5 }
"""


def about_page(name):
    if name == "mote":
        return _wrap("Mote", "A web browser written from scratch", """
<h2>What this is</h2>
<p>Mote is a small web browser built from the socket up: it speaks HTTP/1.1
itself, parses HTML and CSS itself, lays the result out itself, and paints it
itself. There is no rendering engine underneath.</p>
<h2>Pages to try</h2>
<ul>
  <li><a href="about:features">about:features</a> &mdash; what is and is not
      supported</li>
  <li><a href="http://example.com/">example.com</a></li>
  <li><a href="https://info.cern.ch/hypertext/WWW/TheProject.html">The first
      web page</a></li>
</ul>
<h2>Keys</h2>
<table>
<tr><th>Ctrl+L</th><td>focus the address bar</td></tr>
<tr><th>Alt+Left / Alt+Right</th><td>back and forward</td></tr>
<tr><th>Ctrl+R</th><td>reload</td></tr>
<tr><th>Ctrl+T / Ctrl+W</th><td>new tab, close tab</td></tr>
<tr><th>Ctrl+U</th><td>view source</td></tr>
<tr><th>Space / Page Down</th><td>scroll</td></tr>
</table>
""")
    if name == "features":
        return _wrap("Features", "What the engine supports today", """
<h2>Network</h2>
<ul>
<li>HTTP/1.1 written directly onto a socket, TLS through the platform's
    trust store</li>
<li>Keep-alive connection pooling, chunked transfer coding, gzip and
    deflate</li>
<li>Redirects, cookies, and a cache that honours <code>max-age</code>,
    <code>ETag</code> and <code>Last-Modified</code></li>
<li>Schemes: <code>http</code>, <code>https</code>, <code>file</code>,
    <code>data</code>, <code>about</code></li>
</ul>
<h2>HTML</h2>
<ul>
<li>Tokenizer and tree builder with the usual recovery rules: implied end
    tags, head and body scaffolding, implicit <code>tbody</code>, raw text
    elements</li>
<li>All 2231 named character references</li>
<li>Encoding sniffing from the BOM, the Content-Type header and
    <code>&lt;meta charset&gt;</code></li>
</ul>
<h2>CSS</h2>
<ul>
<li>Full selector syntax: combinators, attribute selectors, structural
    pseudo-classes, <code>:not()</code></li>
<li>The cascade with specificity, origins, <code>!important</code> and
    inheritance</li>
<li><code>@media</code> and <code>@import</code>, shorthands,
    <code>calc()</code>, and colours in every notation</li>
</ul>
<h2>Layout</h2>
<ul>
<li>Block and inline formatting, line breaking, collapsing margins</li>
<li>Floats, shrink-to-fit, inline-block, automatic table layout</li>
<li>List markers, relative and absolute positioning</li>
</ul>
<h2>Not supported</h2>
<ul>
<li>JavaScript. Pages render as their markup describes them.</li>
<li>Flexbox and grid fall back to block layout.</li>
<li>Transforms, animations, and shadows are parsed and ignored.</li>
</ul>
""")
    return None


def _wrap(title, subtitle, body):
    return ("<!doctype html><html><head><title>%s</title><style>%s</style>"
            "</head><body><header><h1>%s</h1><p>%s</p></header><main>%s</main>"
            "</body></html>" % (title, _STYLE, title, subtitle, body))
