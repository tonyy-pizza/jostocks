r"""jos/views/webpanel.py - a chart panel backed by a web view.

Plotly in a QWebEngineView, with three rules from section 2:

  - No CDN. plotly.js is injected from the local copy plotly ships
    (plotly.offline.get_plotlyjs()), so the charts work with no network. The
    bundle is ~4.8 MB of JavaScript, so it is fetched once per process and
    reused by every panel.
  - Updates happen in place, through Plotly.react, never by reloading the
    page. A reload would throw away the zoom, the hover state and about a
    quarter of a second, on every slider step.
  - The page is written once, when the view is first shown, and then only
    ever handed new data.

PyQt6-WebEngine is an optional import on purpose. It is a separate wheel from
PyQt6, and on a machine where it is missing - or where, as on a headless
Linux box, its libraries will not load - the app has to open and say so
rather than failing at startup. `AVAILABLE` is what the panels check.

The JavaScript bridge is deliberately tiny: one `render(figure)` function that
calls newPlot the first time and react afterwards, and a `setBusy` for the
"computing correlations..." state. Anything more and the chart logic would be
split across two languages.
"""

from __future__ import annotations

import json

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .. import theme

try:
    from PyQt6.QtWebEngineCore import QWebEngineSettings
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    AVAILABLE = True
    IMPORT_ERROR = None
except Exception as exc:                            # noqa: BLE001
    QWebEngineView = None                           # type: ignore
    QWebEngineSettings = None                       # type: ignore
    AVAILABLE = False
    IMPORT_ERROR = f"{type(exc).__name__}: {exc}"

_PLOTLY_JS = None


def plotly_js() -> str:
    """The bundled plotly.js, read once per process."""
    global _PLOTLY_JS
    if _PLOTLY_JS is None:
        try:
            from plotly.offline import get_plotlyjs
            _PLOTLY_JS = get_plotlyjs()
        except Exception:                           # noqa: BLE001
            _PLOTLY_JS = ""
    return _PLOTLY_JS


def page_html(body_css="") -> str:
    """The shell page. One div, one script, no network.

    Plotly's own modebar is switched off: it offers a toolbar of exports and
    zoom modes that do not belong in a panel this size, and the panel's own
    controls are in the Qt header above it.
    """
    families = theme.font_families()
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<style>
  html, body {{
    margin: 0; padding: 0; height: 100%;
    background: {theme.PANEL_BG};
    color: {theme.CREAM};
    font-family: {families['body']};
    font-size: 12px;
    overflow: hidden;
  }}
  #chart {{ width: 100%; height: 100%; }}
  #busy {{
    position: absolute; inset: 0;
    display: none; align-items: center; justify-content: center;
    background: {theme.PANEL_BG};
    color: {theme.MUTED};
    font-family: {families['mono']};
    font-size: 12px;
    text-align: center; padding: 0 16px;
  }}
  #busy.on {{ display: flex; }}
  .mono {{ font-family: {families['mono']}; }}
  {body_css}
</style>
<script>{plotly_js()}</script>
</head>
<body>
<div id="chart"></div>
<div id="busy"></div>
<script>
  var plotted = false;
  var target = document.getElementById('chart');
  var busy = document.getElementById('busy');

  // One entry point. newPlot the first time, react after - react diffs the
  // figure and keeps the view state, which is the whole reason the sliders
  // feel live rather than reloading a page.
  function render(figure) {{
    if (!window.Plotly) {{ return; }}
    var config = {{displayModeBar: false, responsive: true,
                   doubleClick: false, scrollZoom: false}};
    if (plotted) {{
      Plotly.react(target, figure.data, figure.layout, config);
    }} else {{
      Plotly.newPlot(target, figure.data, figure.layout, config);
      plotted = true;
    }}
    setBusy('');
  }}

  function setBusy(text) {{
    if (text) {{ busy.textContent = text; busy.classList.add('on'); }}
    else {{ busy.classList.remove('on'); }}
  }}

  // Plain HTML instead of a chart, for the clusters view: cards, not a plot.
  function renderHtml(html) {{
    plotted = false;
    target.innerHTML = html;
    setBusy('');
  }}

  window.addEventListener('resize', function () {{
    if (plotted && window.Plotly) {{ Plotly.Plots.resize(target); }}
  }});
</script>
</body>
</html>
"""


class WebPanelBody(QWidget):
    """A web view with a render queue, or a plain message when it cannot load.

    Calls arriving before the page has finished loading are kept and replayed,
    because the first figure is usually pushed while the HTML is still being
    parsed. Without that the panel comes up blank until the next slider move.
    """

    ready = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._loaded = False
        self._pending = None
        self._pending_busy = None
        self.view = None

        if not AVAILABLE:
            self._fallback = QLabel(
                "This panel needs PyQt6-WebEngine, which is not installed "
                "here.\n\n"
                "    pip install PyQt6-WebEngine\n\n"
                "Everything else in the window works without it."
                f"\n\n({IMPORT_ERROR})")
            self._fallback.setObjectName("caption")
            self._fallback.setWordWrap(True)
            layout.addWidget(self._fallback)
            return

        self.view = QWebEngineView(self)
        settings = self.view.settings()
        # Nothing in these pages is remote, and none of it should be able to
        # become remote: no plugins, no local-file reads, no clipboard.
        for attribute, value in (
            (QWebEngineSettings.WebAttribute.PluginsEnabled, False),
            (QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False),
            (QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, False),
            (QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, False),
            (QWebEngineSettings.WebAttribute.ShowScrollBars, False),
        ):
            try:
                settings.setAttribute(attribute, value)
            except Exception:                       # noqa: BLE001
                pass

        self.view.loadFinished.connect(self._on_loaded)
        self.view.setHtml(page_html())
        layout.addWidget(self.view)

    # ── lifecycle ───────────────────────────────────────────────────
    def _on_loaded(self, ok):
        self._loaded = bool(ok)
        if not self._loaded:
            return
        if self._pending_busy is not None:
            self.set_busy(self._pending_busy)
            self._pending_busy = None
        if self._pending is not None:
            figure, self._pending = self._pending, None
            self.render(figure)
        self.ready.emit()

    def _run(self, script):
        if self.view is None:
            return
        if not self._loaded:
            QTimer.singleShot(50, lambda: self._run(script))
            return
        self.view.page().runJavaScript(script)

    # ── the three things a panel can do ─────────────────────────────
    def render(self, figure):
        """Push a figure. Plotly.react after the first call."""
        if self.view is None:
            return
        if not self._loaded:
            self._pending = figure
            return
        payload = json.dumps(figure, default=str, allow_nan=False)
        self._run(f"render({payload});")

    def render_html(self, html):
        """Replace the chart with markup. For the clusters cards."""
        if self.view is None:
            return
        payload = json.dumps(html, default=str)
        self._run(f"renderHtml({payload});")

    def set_busy(self, text):
        """Show or clear a one-line working state."""
        if self.view is None:
            return
        if not self._loaded:
            self._pending_busy = text
            return
        self._run(f"setBusy({json.dumps(str(text or ''))});")
