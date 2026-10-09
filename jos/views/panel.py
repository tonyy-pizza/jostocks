r"""jos/views/panel.py - the panel frame, and the dropdown that titles one.

Every module on screen sits in a Panel. Two things it standardises:

  - The tag. A panel drawn by Qt widgets is tagged "native" and bordered with
    a solid line; one drawn by a web view is tagged "web" and bordered with a
    dashed amber line. Section 5 says to keep these, and they are genuinely
    useful: the two kinds behave differently when the window resizes and when
    data reloads, so knowing which is which while using the app is not
    decoration.
  - The title. Some panels are titled by a dropdown that switches what the
    panel shows (section 6). The dropdown IS the title - styled as the
    heading, not as a form field next to one - so the panel keeps one visual
    anchor whichever view is up.

A Panel is a QFrame with a vertical layout; `body` is where content goes, and
`set_body` swaps it. The header can also carry a right-hand widget: a filter
chip, a tab row, a count.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel,
                             QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from .. import theme


def _refresh_style(widget):
    """Re-apply the stylesheet after a dynamic property changed.

    Qt does not re-evaluate property selectors on its own, so a panel that
    becomes a web panel - or a chip that becomes active - keeps its old look
    until it is unpolished and polished again.
    """
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class Chip(QPushButton):
    """A small flat button: filters, tabs, toggles, the clear button.

    Checkable chips carry `toggled=true` so the stylesheet can tint them,
    which keeps the "on" state readable without relying on colour alone - the
    border changes as well as the text.
    """

    def __init__(self, text, checkable=False, parent=None):
        super().__init__(text, parent)
        self.setProperty("chip", True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setCheckable(checkable)
        if checkable:
            self.toggled.connect(self._sync)
        self._sync(self.isChecked())

    def _sync(self, on):
        self.setProperty("toggled", bool(on))
        _refresh_style(self)


class Panel(QFrame):
    """A titled panel. `body_layout` is where a view puts its widgets."""

    def __init__(self, title, tag="native", parent=None):
        super().__init__(parent)
        self.setObjectName("panel")
        self.setProperty("webview", tag == "web")
        _refresh_style(self)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 10)
        outer.setSpacing(8)

        # ── header ──────────────────────────────────────────────────
        self.header = QHBoxLayout()
        self.header.setSpacing(8)

        self.title_label = QLabel(str(title).lower())
        self.title_label.setObjectName("panelTitle")
        self.header.addWidget(self.title_label)

        self.header.addStretch(1)

        self._extra = QHBoxLayout()
        self._extra.setSpacing(6)
        self.header.addLayout(self._extra)

        self.tag_label = QLabel(tag)
        self.tag_label.setObjectName("panelTag")
        self.tag_label.setProperty("webview", tag == "web")
        _refresh_style(self.tag_label)
        self.tag_label.setToolTip(
            "drawn with Qt widgets" if tag == "native"
            else "drawn in a web view with Plotly, from a bundled local copy "
                 "of plotly.js - no network")
        self.header.addWidget(self.tag_label, 0, Qt.AlignmentFlag.AlignTop)

        outer.addLayout(self.header)

        # ── body ────────────────────────────────────────────────────
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(6)
        outer.addWidget(self.body, 1)

        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)

    # ── header extras ───────────────────────────────────────────────
    def add_header_widget(self, widget):
        """Put a widget in the header, left of the native/web tag."""
        self._extra.addWidget(widget)
        return widget

    def set_title(self, text):
        self.title_label.setText(str(text).lower())

    # ── body ────────────────────────────────────────────────────────
    def set_body(self, widget):
        """Replace the body with one widget."""
        while self.body_layout.count():
            item = self.body_layout.takeAt(0)
            existing = item.widget()
            if existing is not None:
                existing.setParent(None)
        self.body_layout.addWidget(widget, 1)
        return widget

    def add(self, widget, stretch=0):
        self.body_layout.addWidget(widget, stretch)
        return widget


class DropdownPanel(Panel):
    """A panel whose title is a dropdown that switches its view.

    `views` is [(key, label)]. The chosen key is remembered by the caller in
    settings.json, so the choice survives a restart - section 6 asks for that
    per dropdown.

    Switching is instant because every view is built once and kept in a stack;
    the dropdown only changes which is visible. Rebuilding on each change
    would lose scroll position and selection, which section 7 forbids.
    """

    view_changed = pyqtSignal(str)

    def __init__(self, views, tag="native", parent=None):
        first_label = views[0][1] if views else ""
        super().__init__(first_label, tag=tag, parent=parent)

        self.title_label.hide()

        self._keys = [key for key, _label in views]
        self.combo = QComboBox()
        self.combo.setObjectName("titleCombo")
        self.combo.setCursor(Qt.CursorShape.PointingHandCursor)
        for key, label in views:
            self.combo.addItem(str(label).lower(), key)
        self.combo.setToolTip("switch what this panel shows")
        self.combo.currentIndexChanged.connect(self._on_change)

        # Into the header where the title was, so it reads as the heading.
        self.header.insertWidget(0, self.combo)

    def _on_change(self, index):
        if 0 <= index < len(self._keys):
            self.view_changed.emit(self._keys[index])

    def current_view(self) -> str:
        return self.combo.currentData() or (self._keys[0] if self._keys else "")

    def set_view(self, key, emit=True):
        """Select a view by key. Unknown keys are ignored, not an error."""
        if key not in self._keys:
            return False
        index = self._keys.index(key)
        if index == self.combo.currentIndex():
            if emit:
                self.view_changed.emit(key)
            return True
        if emit:
            self.combo.setCurrentIndex(index)
        else:
            self.combo.blockSignals(True)
            self.combo.setCurrentIndex(index)
            self.combo.blockSignals(False)
        return True


class Message(QLabel):
    """The "nothing to show yet" state.

    Section 4: every file being absent or stale must read as a sentence, not
    an error. This is that sentence, and it is deliberately a plain label -
    never a modal dialog, which section 9 rules out at startup.
    """

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setObjectName("caption")
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.setContentsMargins(2, 6, 2, 6)


class HeldTag(QLabel):
    """The "held" marker. Cyan, and the word - never the colour alone."""

    def __init__(self, parent=None):
        super().__init__("held", parent)
        self.setObjectName("panelTag")
        self.setStyleSheet(f"color: {theme.CYAN}; border-color: {theme.CYAN};")
        self.setToolTip("already in holdings.json - shown, but not sized "
                        "as a new position")
