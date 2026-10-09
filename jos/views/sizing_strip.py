r"""jos/views/sizing_strip.py - the always-visible sizing controls.

Four controls, and between them they re-size every not-held candidate without
touching the network: the correlation threshold, the reduction factor, which
basis the threshold applies to, and whether the reduction is proportional or
flat. Defaults come from `params` in sized_candidates.json, so the strip opens
showing what the scan was actually run with.

Debounce: `changed` fires on a ~50 ms timer (section 2). Dragging a Qt slider
emits valueChanged for every pixel, and each one would otherwise re-sort a
table and push new data into three charts. The timer collapses a drag into
one update per frame or so. `changed_now` fires immediately for the cases
where latency is worse than work - releasing the slider, clicking a toggle.

The basis toggle is worth a word, because it is easy to misread as cosmetic.
"raw" is the sample correlation; "cleaned" is the same matrix after the
Marchenko-Pastur filter, which pulls each pair toward the factor structure.
position_sizer's comment measures the difference: on a 27-name panel the banks
sat at 0.71-0.76 raw and 0.65-0.69 cleaned, so a 0.70 bar on cleaned values
quietly behaves like a 0.75 bar. The pipeline defaults to raw because
"correlated above 0.70" conventionally means the sample correlation.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel,
                             QSlider, QVBoxLayout, QWidget)

from .. import theme
from .panel import Chip

#: Section 2: debounce slider input by about this long.
DEBOUNCE_MS = 50

#: Sliders are integers, so the real values are scaled by these.
THRESHOLD_STEPS = 100       # 0.00 - 1.00 in hundredths
FACTOR_STEPS = 100


class SizingStrip(QFrame):
    """Threshold, reduction factor, basis and mode - the live sizing controls."""

    changed = pyqtSignal()        # debounced
    changed_now = pyqtSignal()    # immediate

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("panel")

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(DEBOUNCE_MS)
        self._timer.timeout.connect(self.changed.emit)

        self._basis = "raw"
        self._flat = False
        self._defaults = {}

        outer = QHBoxLayout(self)
        outer.setContentsMargins(12, 8, 12, 8)
        outer.setSpacing(18)

        title = QLabel("sizing")
        title.setObjectName("panelTitle")
        outer.addWidget(title, 0, Qt.AlignmentFlag.AlignVCenter)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(2)

        # ── correlation threshold ───────────────────────────────────
        self.threshold_slider = QSlider(Qt.Orientation.Horizontal)
        self.threshold_slider.setRange(0, THRESHOLD_STEPS)
        self.threshold_slider.setMinimumWidth(150)
        self.threshold_slider.setToolTip(
            "Correlation with a holding above which a candidate is treated as "
            "partly the same position, and its size cut.")
        self.threshold_value = QLabel("0.70")
        self.threshold_value.setObjectName("mono")

        grid.addWidget(self._caption("correlation threshold"), 0, 0)
        grid.addWidget(self.threshold_slider, 1, 0)
        grid.addWidget(self.threshold_value, 1, 1)

        # ── reduction factor ────────────────────────────────────────
        self.factor_slider = QSlider(Qt.Orientation.Horizontal)
        self.factor_slider.setRange(0, FACTOR_STEPS)
        self.factor_slider.setMinimumWidth(150)
        self.factor_slider.setToolTip(
            "How much of the base size a fully correlated (1.0) duplicate "
            "loses. Proportional mode scales between the threshold and 1.0.")
        self.factor_value = QLabel("0.50")
        self.factor_value.setObjectName("mono")

        grid.addWidget(self._caption("reduction factor"), 0, 2)
        grid.addWidget(self.factor_slider, 1, 2)
        grid.addWidget(self.factor_value, 1, 3)

        outer.addLayout(grid)

        # ── basis ───────────────────────────────────────────────────
        outer.addWidget(self._toggle_group(
            "basis",
            [("raw", "raw"), ("cleaned", "cleaned")],
            self._set_basis,
            "raw is the sample correlation; cleaned is the same matrix after "
            "the Marchenko-Pastur noise filter. The pipeline defaults to raw, "
            "because “correlated above 0.70” conventionally means "
            "the sample correlation.",
            attribute="_basis_chips"))

        # ── reduction mode ──────────────────────────────────────────
        outer.addWidget(self._toggle_group(
            "mode",
            [("proportional", "proportional"), ("flat", "flat")],
            self._set_mode,
            "proportional takes nothing away at the threshold and the full "
            "factor at 1.0, so a name just over the line is barely touched. "
            "flat applies the whole factor the moment the threshold is "
            "crossed.",
            attribute="_mode_chips"))

        outer.addStretch(1)

        self.summary = QLabel("")
        self.summary.setObjectName("caption")
        self.summary.setToolTip("how many candidates the current controls cut")
        outer.addWidget(self.summary, 0, Qt.AlignmentFlag.AlignVCenter)

        self.reset_chip = Chip("reset to scan")
        self.reset_chip.setToolTip("put the controls back to what the scan ran with")
        self.reset_chip.clicked.connect(self.reset)
        outer.addWidget(self.reset_chip, 0, Qt.AlignmentFlag.AlignVCenter)

        self.threshold_slider.valueChanged.connect(self._on_slide)
        self.factor_slider.valueChanged.connect(self._on_slide)
        self.threshold_slider.sliderReleased.connect(self._on_release)
        self.factor_slider.sliderReleased.connect(self._on_release)

    # ── construction helpers ────────────────────────────────────────
    @staticmethod
    def _caption(text):
        label = QLabel(text)
        label.setObjectName("caption")
        return label

    def _toggle_group(self, caption, options, handler, tooltip, attribute):
        holder = QWidget()
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        column.addWidget(self._caption(caption))

        row = QHBoxLayout()
        row.setSpacing(4)
        chips = {}
        for key, label in options:
            chip = Chip(label, checkable=True)
            chip.setToolTip(tooltip)
            chip.clicked.connect(lambda _checked, k=key: handler(k))
            chips[key] = chip
            row.addWidget(chip)
        row.addStretch(1)
        column.addLayout(row)

        setattr(self, attribute, chips)
        return holder

    # ── state ───────────────────────────────────────────────────────
    def values(self) -> dict:
        """The controls, as position_sizer's own argument names."""
        return {
            "threshold": self.threshold_slider.value() / THRESHOLD_STEPS,
            "reduction_factor": self.factor_slider.value() / FACTOR_STEPS,
            "basis": self._basis,
            "flat": self._flat,
        }

    def set_values(self, threshold=None, reduction_factor=None, basis=None,
                   flat=None, emit=False):
        """Set the controls without firing a storm of updates."""
        for widget in (self.threshold_slider, self.factor_slider):
            widget.blockSignals(True)

        if threshold is not None:
            self.threshold_slider.setValue(int(round(float(threshold) * THRESHOLD_STEPS)))
        if reduction_factor is not None:
            self.factor_slider.setValue(int(round(float(reduction_factor) * FACTOR_STEPS)))

        for widget in (self.threshold_slider, self.factor_slider):
            widget.blockSignals(False)

        if basis in ("raw", "cleaned"):
            self._basis = basis
        if flat is not None:
            self._flat = bool(flat)

        self._sync_labels()
        if emit:
            self.changed_now.emit()

    def apply_defaults(self, defaults, remembered=None):
        """Start from the scan's parameters, then any remembered override.

        The scan's own params are the baseline because they are what produced
        the numbers on screen. A remembered position from settings.json wins,
        so a person who moved the sliders last time finds them where they left
        them - but "reset to scan" always goes back to the baseline.
        """
        self._defaults = dict(defaults or {})
        merged = dict(self._defaults)
        if isinstance(remembered, dict):
            for key in ("threshold", "reduction_factor", "basis", "flat"):
                if remembered.get(key) is not None:
                    merged[key] = remembered[key]
        self.set_values(**{k: merged.get(k) for k in
                           ("threshold", "reduction_factor", "basis", "flat")})

    def reset(self):
        """Back to what the scan was run with."""
        if self._defaults:
            self.set_values(**self._defaults, emit=True)

    def is_default(self) -> bool:
        """Whether the controls still match the scan's parameters."""
        if not self._defaults:
            return True
        current = self.values()
        return (abs(current["threshold"] - self._defaults.get("threshold", 0)) < 1e-9
                and abs(current["reduction_factor"]
                        - self._defaults.get("reduction_factor", 0)) < 1e-9
                and current["basis"] == self._defaults.get("basis")
                and current["flat"] == bool(self._defaults.get("flat")))

    def set_summary(self, text):
        self.summary.setText(text)

    # ── signals ─────────────────────────────────────────────────────
    def _set_basis(self, key):
        self._basis = key
        self._sync_labels()
        self.changed_now.emit()

    def _set_mode(self, key):
        self._flat = (key == "flat")
        self._sync_labels()
        self.changed_now.emit()

    def _on_slide(self):
        self._sync_labels()
        self._timer.start()

    def _on_release(self):
        self._timer.stop()
        self.changed_now.emit()

    def _sync_labels(self):
        values = self.values()
        self.threshold_value.setText(f"{values['threshold']:.2f}")
        self.factor_value.setText(f"{values['reduction_factor']:.2f}")

        for key, chip in getattr(self, "_basis_chips", {}).items():
            chip.setChecked(key == self._basis)
        for key, chip in getattr(self, "_mode_chips", {}).items():
            chip.setChecked((key == "flat") == self._flat)

        # An amber label when the controls no longer match the scan, so a
        # simulated number is never mistaken for the pipeline's own.
        drifted = not self.is_default()
        tint = theme.AMBER if drifted else theme.CREAM
        for label in (self.threshold_value, self.factor_value):
            label.setStyleSheet(f"color: {tint};")
        self.reset_chip.setEnabled(drifted)
