"""
rules_editor.py ~ Visual editor for the MuseBridge rules JSON.

Features:
~ Rule list on the left (mirrors the QListWidget already in the .ui file)
~ Condition rows: band / sensor / op / value ~ each a compact widget
~ Output rows: type switcher that shows the right fields per type
  - parameter: param name + source band/sensor + scale + offset  (or fixed value)
  - expression: expression file picker (populated from VTS)
  - hotkey:     hotkey ID picker + cooldown spinbox
~ get_rules() returns a list ready to pass to VTSBridge or write to JSON
~ load_rules() populates the editor from an existing list
~ set_vts_assets() feeds expression/hotkey lists from a live VTS fetch

Most of this program will definitely have to change at some point.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QComboBox, QDoubleSpinBox, QScrollArea,
    QFrame, QSizePolicy, QSpacerItem
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui  import QFont


# =========================
# SCHEMA CONSTANTS ~ Single source of truth for every dropdown's options.
# Changing these here is all that's needed if the bridge gains new bands or ops.
# =========================
BANDS      = ["delta", "theta", "alpha", "beta", "gamma", "faa", "taa", "valence", "arousal"]
SENSORS    = ["mean", "AF7", "AF8", "TP9", "TP10"]
OPS        = ["<", ">", "<=", ">=", "=="]
OUT_TYPES  = ["parameter", "expression", "hotkey"]


# =========================
# STYLE CONSTANTS ~ Shared style strings so all rows look consistent.
# Dark theme to match BrainDisplay — they will sit side by side.
# =========================
ROW_STYLE = """
    QWidget#conditionRow, QWidget#outputRow {
        background: #13131f;
        border: 1px solid #2a2a3e;
        border-radius: 4px;
    }
"""

LABEL_STYLE  = "color: #666; font-size: 10px; font-family: monospace;"
COMBO_STYLE  = """
    QComboBox {
        background: #1a1a2e; color: #ccc;
        border: 1px solid #2a2a3e; border-radius: 3px;
        padding: 2px 6px; font-family: monospace; font-size: 11px;
    }
    QComboBox::drop-down { border: none; }
    QComboBox QAbstractItemView { background: #1a1a2e; color: #ccc; }
"""
SPIN_STYLE   = """
    QDoubleSpinBox {
        background: #1a1a2e; color: #ccc;
        border: 1px solid #2a2a3e; border-radius: 3px;
        padding: 2px 4px; font-family: monospace; font-size: 11px;
    }
"""
EDIT_STYLE   = """
    QLineEdit {
        background: #1a1a2e; color: #ccc;
        border: 1px solid #2a2a3e; border-radius: 3px;
        padding: 2px 6px; font-family: monospace; font-size: 11px;
    }
"""
DELETE_STYLE = """
    QPushButton {
        background: transparent; color: #552222;
        border: none; font-size: 14px; padding: 0 4px;
    }
    QPushButton:hover { color: #e05c5c; }
"""
ADD_STYLE    = """
    QPushButton {
        background: #1a1a2e; color: #5c8ae0;
        border: 1px solid #2a2a3e; border-radius: 3px;
        padding: 4px 10px; font-family: monospace; font-size: 11px;
    }
    QPushButton:hover { border-color: #5c8ae0; }
"""


# =========================
# HELPERS ~ Small reusable factories to keep row constructors readable.
# =========================

def _label(text: str) -> QLabel:
    # Dim caption label used above each field
    lbl = QLabel(text)
    lbl.setStyleSheet(LABEL_STYLE)
    return lbl

def _combo(options: list, width: int = 90) -> QComboBox:
    cb = QComboBox()
    cb.addItems(options)
    cb.setFixedWidth(width)
    cb.setStyleSheet(COMBO_STYLE)
    return cb

def _spin(lo: float = 0.0, hi: float = 1.0, step: float = 0.05,
          decimals: int = 2, width: int = 70) -> QDoubleSpinBox:
    sp = QDoubleSpinBox()
    sp.setRange(lo, hi)
    sp.setSingleStep(step)
    sp.setDecimals(decimals)
    sp.setFixedWidth(width)
    sp.setStyleSheet(SPIN_STYLE)
    return sp

def _edit(placeholder: str = "", width: int = 140) -> QLineEdit:
    ed = QLineEdit()
    ed.setPlaceholderText(placeholder)
    ed.setFixedWidth(width)
    ed.setStyleSheet(EDIT_STYLE)
    return ed

def _delete_button() -> QPushButton:
    btn = QPushButton("✕")
    btn.setFixedSize(22, 22)
    btn.setStyleSheet(DELETE_STYLE)
    return btn

def _section_line() -> QFrame:
    # Thin horizontal rule between sections
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setStyleSheet("color: #2a2a3e; margin: 4px 0;")
    return line


# =========================
# CONDITION ROW ~ One row representing a single condition dict.
# Produces: {"band": ..., "op": ..., "value": ..., "sensor": ...}
# Sensor field is omitted from the dict if set to "mean" (bridge default).
# =========================
class ConditionRow(QWidget):

    # Signal emitted when the user clicks the delete button
    delete_requested = Signal(object)   # passes self so the parent can remove it

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("conditionRow")
        self.setStyleSheet(ROW_STYLE)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(8)

        # =========================
        # FIELDS ~ band / sensor / op / value
        # Arranged left-to-right in the order a human would read the condition:
        # "alpha / AF7  >  0.60"
        # =========================

        # Band dropdown
        band_col = QVBoxLayout()
        band_col.setSpacing(2)
        band_col.addWidget(_label("band"))
        self._band = _combo(BANDS, width=80)
        band_col.addWidget(self._band)
        layout.addLayout(band_col)

        # Sensor dropdown ~ "mean" means use the averaged key (backward-compatible)
        sensor_col = QVBoxLayout()
        sensor_col.setSpacing(2)
        sensor_col.addWidget(_label("sensor"))
        self._sensor = _combo(SENSORS, width=70)
        sensor_col.addWidget(self._sensor)
        layout.addLayout(sensor_col)

        # Operator dropdown
        op_col = QVBoxLayout()
        op_col.setSpacing(2)
        op_col.addWidget(_label("op"))
        self._op = _combo(OPS, width=55)
        op_col.addWidget(self._op)
        layout.addLayout(op_col)

        # Threshold value ~ 0.0 to 1.0, two decimal places
        val_col = QVBoxLayout()
        val_col.setSpacing(2)
        val_col.addWidget(_label("value"))
        self._value = _spin(0.0, 1.0, 0.05, 2, width=70)
        val_col.addWidget(self._value)
        layout.addLayout(val_col)

        layout.addStretch()

        # Delete button ~ emits signal rather than deleting itself
        del_btn = _delete_button()
        del_btn.clicked.connect(lambda: self.delete_requested.emit(self))
        layout.addWidget(del_btn, alignment=Qt.AlignTop)

    # =========================
    # TO DICT ~ Serialises this row into the dict the bridge expects.
    # Sensor is excluded when "mean" so existing rules without sensor field still work.
    # =========================
    def to_dict(self) -> dict:
        d = {
            "band":  self._band.currentText(),
            "op":    self._op.currentText(),
            "value": round(self._value.value(), 4),
        }
        sensor = self._sensor.currentText()
        if sensor != "mean":
            d["sensor"] = sensor
        return d

    # =========================
    # FROM DICT ~ Populates this row from an existing condition dict.
    # Missing keys fall back to widget defaults silently.
    # =========================
    def from_dict(self, d: dict):
        _set_combo(self._band,   d.get("band",   "alpha"))
        _set_combo(self._sensor, d.get("sensor", "mean"))
        _set_combo(self._op,     d.get("op",     ">"))
        self._value.setValue(float(d.get("value", 0.5)))


# =========================
# OUTPUT ROW ~ One row for a single output dict.
# The type combo switches which fields are shown — only the relevant
# fields are visible at any time to keep the row compact.
#
# Parameter output fields:
#   param (name), mode (fixed/source), value or source+sensor+scale+offset
# Expression output fields:
#   expression file (combo populated from VTS)
# Hotkey output fields:
#   hotkey_id (combo populated from VTS), cooldown
# =========================
class OutputRow(QWidget):

    delete_requested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("outputRow")
        self.setStyleSheet(ROW_STYLE)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(6)

        # =========================
        # TYPE ROW ~ Type combo + delete button always visible
        # =========================
        type_row = QHBoxLayout()
        type_row.setSpacing(8)

        type_col = QVBoxLayout()
        type_col.setSpacing(2)
        type_col.addWidget(_label("type"))
        self._type = _combo(OUT_TYPES, width=100)
        self._type.currentTextChanged.connect(self._on_type_changed)
        type_col.addWidget(self._type)
        type_row.addLayout(type_col)
        type_row.addStretch()

        del_btn = _delete_button()
        del_btn.clicked.connect(lambda: self.delete_requested.emit(self))
        type_row.addWidget(del_btn, alignment=Qt.AlignTop)
        outer.addLayout(type_row)

        # =========================
        # PARAMETER FIELDS ~ param name + mode switcher + value or source fields
        # =========================
        self._param_widget = QWidget()
        param_layout = QHBoxLayout(self._param_widget)
        param_layout.setContentsMargins(0, 0, 0, 0)
        param_layout.setSpacing(8)

        # VTS parameter name (e.g. "EyeOpenLeft")
        param_name_col = QVBoxLayout()
        param_name_col.setSpacing(2)
        param_name_col.addWidget(_label("param"))
        self._param_name = _edit("EyeOpenLeft", width=120)
        param_name_col.addWidget(self._param_name)
        param_layout.addLayout(param_name_col)

        # Mode: fixed value or driven by a brain source
        mode_col = QVBoxLayout()
        mode_col.setSpacing(2)
        mode_col.addWidget(_label("mode"))
        self._param_mode = _combo(["source", "fixed"], width=70)
        self._param_mode.currentTextChanged.connect(self._on_param_mode_changed)
        mode_col.addWidget(self._param_mode)
        param_layout.addLayout(mode_col)

        # Fixed value spinbox: only shown when mode = "fixed"
        self._fixed_widget = QWidget()
        fixed_layout = QVBoxLayout(self._fixed_widget)
        fixed_layout.setContentsMargins(0, 0, 0, 0)
        fixed_layout.setSpacing(2)
        fixed_layout.addWidget(_label("value"))
        self._fixed_value = _spin(-1.0, 1.0, 0.05, 2, width=70)
        fixed_layout.addWidget(self._fixed_value)
        param_layout.addWidget(self._fixed_widget)

        # Source fields: only shown when mode = "source"
        self._source_widget = QWidget()
        source_layout = QHBoxLayout(self._source_widget)
        source_layout.setContentsMargins(0, 0, 0, 0)
        source_layout.setSpacing(8)

        src_col = QVBoxLayout()
        src_col.setSpacing(2)
        src_col.addWidget(_label("source band"))
        self._source_band = _combo(BANDS, width=80)
        src_col.addWidget(self._source_band)
        source_layout.addLayout(src_col)

        src_sensor_col = QVBoxLayout()
        src_sensor_col.setSpacing(2)
        src_sensor_col.addWidget(_label("sensor"))
        self._source_sensor = _combo(SENSORS, width=70)
        src_sensor_col.addWidget(self._source_sensor)
        source_layout.addLayout(src_sensor_col)

        scale_col = QVBoxLayout()
        scale_col.setSpacing(2)
        scale_col.addWidget(_label("scale"))
        self._scale = _spin(-5.0, 5.0, 0.1, 2, width=65)
        self._scale.setValue(1.0)
        scale_col.addWidget(self._scale)
        source_layout.addLayout(scale_col)

        offset_col = QVBoxLayout()
        offset_col.setSpacing(2)
        offset_col.addWidget(_label("offset"))
        self._offset = _spin(-1.0, 1.0, 0.05, 2, width=65)
        self._offset.setValue(0.0)
        offset_col.addWidget(self._offset)
        source_layout.addLayout(offset_col)

        param_layout.addWidget(self._source_widget)
        param_layout.addStretch()
        outer.addWidget(self._param_widget)

        # =========================
        # EXPRESSION FIELDS ~ expression file combo, populated from VTS
        # =========================
        self._expr_widget = QWidget()
        expr_layout = QHBoxLayout(self._expr_widget)
        expr_layout.setContentsMargins(0, 0, 0, 0)
        expr_layout.setSpacing(8)

        expr_col = QVBoxLayout()
        expr_col.setSpacing(2)
        expr_col.addWidget(_label("expression file"))
        self._expr_combo = _combo([], width=220)
        self._expr_combo.setEditable(True)      # allow typing if VTS not fetched yet
        expr_col.addWidget(self._expr_combo)
        expr_layout.addLayout(expr_col)
        expr_layout.addStretch()
        outer.addWidget(self._expr_widget)

        # =========================
        # HOTKEY FIELDS ~ hotkey_id combo + cooldown spinbox
        # =========================
        self._hotkey_widget = QWidget()
        hotkey_layout = QHBoxLayout(self._hotkey_widget)
        hotkey_layout.setContentsMargins(0, 0, 0, 0)
        hotkey_layout.setSpacing(8)

        hk_col = QVBoxLayout()
        hk_col.setSpacing(2)
        hk_col.addWidget(_label("hotkey ID"))
        self._hotkey_combo = _combo([], width=220)
        self._hotkey_combo.setEditable(True)    # allow typing if VTS not fetched yet
        hk_col.addWidget(self._hotkey_combo)
        hotkey_layout.addLayout(hk_col)

        cd_col = QVBoxLayout()
        cd_col.setSpacing(2)
        cd_col.addWidget(_label("cooldown (s)"))
        self._cooldown = _spin(0.0, 30.0, 0.5, 1, width=70)
        self._cooldown.setValue(3.0)
        cd_col.addWidget(self._cooldown)
        hotkey_layout.addLayout(cd_col)
        hotkey_layout.addStretch()
        outer.addWidget(self._hotkey_widget)

        # =========================
        # INITIAL VISIBILITY ~ Show parameter fields by default,
        # hide the others. _on_type_changed manages this going forward.
        # =========================
        self._on_type_changed("parameter")

    # =========================
    # ON TYPE CHANGED ~ Shows the right field group for the selected output type.
    # =========================
    def _on_type_changed(self, out_type: str):
        self._param_widget.setVisible(out_type == "parameter")
        self._expr_widget.setVisible(out_type == "expression")
        self._hotkey_widget.setVisible(out_type == "hotkey")

    # =========================
    # ON PARAM MODE CHANGED ~ For parameter outputs, switches between
    # a fixed value and a brain-sourced value with scale/offset.
    # =========================
    def _on_param_mode_changed(self, mode: str):
        self._fixed_widget.setVisible(mode == "fixed")
        self._source_widget.setVisible(mode == "source")

    # =========================
    # SET VTS ASSETS ~ Called when the main window fetches expressions and
    # hotkeys from a live VTS. Populates the combo boxes and preserves
    # any value the user already typed.
    # =========================
    def set_vts_assets(self, expressions: list[str], hotkeys: list[dict]):
        # Preserve current text before repopulating
        current_expr   = self._expr_combo.currentText()
        current_hotkey = self._hotkey_combo.currentText()

        self._expr_combo.clear()
        self._expr_combo.addItems(expressions)
        if current_expr:
            _set_combo(self._expr_combo, current_expr)

        self._hotkey_combo.clear()
        # hotkeys is a list of dicts: {"hotkeyID": ..., "name": ...}
        # Show "name (id)" in the combo so it's human-readable
        self._hotkey_ids = [h["hotkeyID"] for h in hotkeys]
        labels = [f"{h['name']} ({h['hotkeyID']})" for h in hotkeys]
        self._hotkey_combo.addItems(labels)
        if current_hotkey:
            _set_combo(self._hotkey_combo, current_hotkey)

    # =========================
    # TO DICT ~ Serialises this row into the dict the bridge expects.
    # Only includes fields relevant to the current output type.
    # =========================
    def to_dict(self) -> dict:
        out_type = self._type.currentText()

        if out_type == "parameter":
            d = {"type": "parameter", "param": self._param_name.text().strip()}
            if self._param_mode.currentText() == "fixed":
                d["value"] = round(self._fixed_value.value(), 4)
            else:
                d["source"] = self._source_band.currentText()
                sensor = self._source_sensor.currentText()
                if sensor != "mean":
                    d["sensor"] = sensor
                d["scale"]  = round(self._scale.value(), 4)
                d["offset"] = round(self._offset.value(), 4)
            return d

        elif out_type == "expression":
            return {
                "type":       "expression",
                "expression": self._expr_combo.currentText().strip(),
            }

        elif out_type == "hotkey":
            # Resolve label back to raw hotkey ID if VTS assets were loaded
            idx = self._hotkey_combo.currentIndex()
            if hasattr(self, "_hotkey_ids") and 0 <= idx < len(self._hotkey_ids):
                hid = self._hotkey_ids[idx]
            else:
                hid = self._hotkey_combo.currentText().strip()
            return {
                "type":      "hotkey",
                "hotkey_id": hid,
                "cooldown":  round(self._cooldown.value(), 1),
            }

        return {}

    # =========================
    # FROM DICT ~ Populates this row from an existing output dict.
    # =========================
    def from_dict(self, d: dict):
        out_type = d.get("type", "parameter")
        _set_combo(self._type, out_type)
        self._on_type_changed(out_type)

        if out_type == "parameter":
            self._param_name.setText(d.get("param", ""))
            if "value" in d:
                _set_combo(self._param_mode, "fixed")
                self._fixed_value.setValue(float(d["value"]))
                self._on_param_mode_changed("fixed")
            else:
                _set_combo(self._param_mode, "source")
                _set_combo(self._source_band,   d.get("source", "alpha"))
                _set_combo(self._source_sensor, d.get("sensor", "mean"))
                self._scale.setValue(float(d.get("scale",  1.0)))
                self._offset.setValue(float(d.get("offset", 0.0)))
                self._on_param_mode_changed("source")

        elif out_type == "expression":
            # Try to set combo; if not found, editable combo accepts the text
            self._expr_combo.setCurrentText(d.get("expression", ""))

        elif out_type == "hotkey":
            self._hotkey_combo.setCurrentText(d.get("hotkey_id", ""))
            self._cooldown.setValue(float(d.get("cooldown", 3.0)))


# =========================
# RULE EDITOR PANEL ~ The right-hand side of the window.
# Shows the name field, conditions group, and outputs group for one rule.
# Plugged into the layout slots that already exist in the .ui file.
# =========================
class RuleEditorPanel(QWidget):

    # Emitted whenever any field changes; main window listens to auto-save
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        # =========================
        # RULE NAME ~ Single text field at the top
        # =========================
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Rule name:"))
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("e.g. high_alpha")
        self._name_edit.setStyleSheet(EDIT_STYLE)
        self._name_edit.textChanged.connect(self.changed)
        name_row.addWidget(self._name_edit)
        outer.addLayout(name_row)

        outer.addWidget(_section_line())

        # =========================
        # CONDITIONS ~ Label + scroll area + add button
        # Conditions are added into a VBoxLayout inside a scroll area
        # so a rule with many conditions doesn't overflow the panel.
        # =========================
        cond_label = QLabel("Conditions  (ALL must be true)")
        cond_label.setStyleSheet("color: #888; font-size: 11px;")
        outer.addWidget(cond_label)

        cond_scroll = QScrollArea()
        cond_scroll.setWidgetResizable(True)
        cond_scroll.setMaximumHeight(220)
        cond_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self._cond_container = QWidget()
        self._cond_layout    = QVBoxLayout(self._cond_container)
        self._cond_layout.setContentsMargins(0, 0, 0, 0)
        self._cond_layout.setSpacing(4)
        self._cond_layout.addStretch()   # keeps rows pinned to top
        cond_scroll.setWidget(self._cond_container)
        outer.addWidget(cond_scroll)

        add_cond_btn = QPushButton("+ Add Condition")
        add_cond_btn.setStyleSheet(ADD_STYLE)
        add_cond_btn.clicked.connect(self._add_condition)
        outer.addWidget(add_cond_btn, alignment=Qt.AlignLeft)

        outer.addWidget(_section_line())

        # =========================
        # OUTPUTS ~ Label + scroll area + add button
        # =========================
        out_label = QLabel("Outputs")
        out_label.setStyleSheet("color: #888; font-size: 11px;")
        outer.addWidget(out_label)

        out_scroll = QScrollArea()
        out_scroll.setWidgetResizable(True)
        out_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        out_scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._out_container = QWidget()
        self._out_layout = QVBoxLayout(self._out_container)
        self._out_layout.setContentsMargins(0, 0, 0, 0)
        self._out_layout.setSpacing(4)
        self._out_layout.addStretch()    # keeps rows pinned to top
        out_scroll.setWidget(self._out_container)
        outer.addWidget(out_scroll)

        add_out_btn = QPushButton("+ Add Output")
        add_out_btn.setStyleSheet(ADD_STYLE)
        add_out_btn.clicked.connect(self._add_output)
        outer.addWidget(add_out_btn, alignment=Qt.AlignLeft)

        # =========================
        # INTERNAL STATE ~ Track all live row widgets so to_rule() can iterate them.
        # =========================
        self._condition_rows: list[ConditionRow] = []
        self._output_rows:    list[OutputRow]    = []

        # VTS asset lists — empty until set_vts_assets() is called
        self._vts_expressions: list[str]  = []
        self._vts_hotkeys:     list[dict] = []

    # =========================
    # ADD CONDITION ~ Creates a new blank ConditionRow and inserts it
    # before the stretch at the end of the layout.
    # =========================
    def _add_condition(self, d: dict = None):
        row = ConditionRow()
        if d:
            row.from_dict(d)
        row.delete_requested.connect(self._remove_condition)

        # Insert before the trailing stretch (always last item)
        insert_at = self._cond_layout.count() - 1
        self._cond_layout.insertWidget(insert_at, row)
        self._condition_rows.append(row)
        self.changed.emit()

    # =========================
    # REMOVE CONDITION ~ Deletes the row widget and removes it from tracking list.
    # =========================
    def _remove_condition(self, row: ConditionRow):
        self._cond_layout.removeWidget(row)
        row.deleteLater()
        self._condition_rows.remove(row)
        self.changed.emit()

    # =========================
    # ADD OUTPUT ~ Creates a new blank OutputRow, seeds it with any VTS assets
    # already fetched, and inserts it before the stretch.
    # =========================
    def _add_output(self, d: dict = None):
        row = OutputRow()
        row.set_vts_assets(self._vts_expressions, self._vts_hotkeys)
        if d:
            row.from_dict(d)
        row.delete_requested.connect(self._remove_output)

        insert_at = self._out_layout.count() - 1
        self._out_layout.insertWidget(insert_at, row)
        self._output_rows.append(row)
        self.changed.emit()

    # =========================
    # REMOVE OUTPUT ~ Deletes the row widget and removes it from tracking list.
    # =========================
    def _remove_output(self, row: OutputRow):
        self._out_layout.removeWidget(row)
        row.deleteLater()
        self._output_rows.remove(row)
        self.changed.emit()

    # =========================
    # SET VTS ASSETS ~ Passes asset lists down to every existing output row
    # and stores them so future rows created by _add_output() get them too.
    # =========================
    def set_vts_assets(self, expressions: list[str], hotkeys: list[dict]):
        self._vts_expressions = expressions
        self._vts_hotkeys     = hotkeys
        for row in self._output_rows:
            row.set_vts_assets(expressions, hotkeys)

    # =========================
    # CLEAR ~ Removes all condition and output rows.
    # Called before loading a new rule into the panel.
    # =========================
    def clear(self):
        for row in list(self._condition_rows):
            self._remove_condition(row)
        for row in list(self._output_rows):
            self._remove_output(row)
        self._name_edit.clear()

    # =========================
    # LOAD RULE ~ Populates the panel from a rule dict.
    # Clears existing rows first so there's no bleed between rules.
    # =========================
    def load_rule(self, rule: dict):
        self.clear()
        self._name_edit.setText(rule.get("name", ""))
        for cond in rule.get("conditions", []):
            self._add_condition(cond)
        for out in rule.get("outputs", []):
            self._add_output(out)

    # =========================
    # TO RULE ~ Serialises the current panel state into a rule dict.
    # The result is ready to append to the rules list and pass to the bridge.
    # =========================
    def to_rule(self) -> dict:
        return {
            "name":       self._name_edit.text().strip() or "unnamed",
            "conditions": [row.to_dict() for row in self._condition_rows],
            "outputs":    [row.to_dict() for row in self._output_rows],
        }


# =========================
# RULES EDITOR
# Owns the rule list (a QListWidget passed in from the .ui file) and
# a RuleEditorPanel that shows the selected rule's details.
#
# The main window passes its existing QListWidget from the .ui file
# in rather than creating a new one, so the layout stays under
# Designer's control.
# =========================
class RulesEditor(QWidget):

    # Emitted whenever rules change — main window uses this to mark unsaved changes
    rules_changed = Signal(list)

    def __init__(self, rules_list_widget, parent=None):
        super().__init__(parent)

        # =========================
        # LIST WIDGET ~ Passed in from the .ui file.
        # We connect its signals but don't reparent it, allowing to stay in its
        # designer-assigned position on the left panel.
        # =========================
        self._list = rules_list_widget
        self._list.currentRowChanged.connect(self._on_rule_selected)

        # =========================
        # EDITOR PANEL ~ Lives on the right side of the splitter.
        # The main window inserts this into editorOuterLayout.
        # =========================
        self._editor = RuleEditorPanel()
        self._editor.changed.connect(self._on_editor_changed)

        # =========================
        # RULE DATA ~ Parallel list to the QListWidget items.
        # Index in _rules matches index in the QListWidget.
        # =========================
        self._rules:        list[dict] = []
        self._current_idx:  int        = -1
        self._loading:      bool       = False  # suppresses change signals while loading

    # =========================
    # EDITOR PANEL WIDGET ~ Property so main window can insert it into the layout.
    # =========================
    @property
    def editor_widget(self) -> RuleEditorPanel:
        return self._editor

    # =========================
    # LOAD RULES ~ Replaces the current rule set with a new list.
    # Rebuilds the QListWidget and selects the first rule.
    # =========================
    def load_rules(self, rules: list):
        self._loading = True
        self._rules   = [dict(r) for r in rules]   # shallow copy

        self._list.clear()
        for rule in self._rules:
            self._list.addItem(rule.get("name", "unnamed"))

        self._loading = False

        # Select the first rule if any exist
        if self._rules:
            self._list.setCurrentRow(0)
        else:
            self._editor.clear()

    # =========================
    # GET RULES ~ Returns the full rules list with any unsaved edits from
    # the currently selected rule flushed in first.
    # =========================
    def get_rules(self) -> list:
        self._flush_current()
        return list(self._rules)

    # =========================
    # ADD RULE ~ Appends a blank rule, adds it to the list, and selects it.
    # =========================
    def add_rule(self):
        new_rule = {"name": "new_rule", "conditions": [], "outputs": []}
        self._rules.append(new_rule)
        self._list.addItem(new_rule["name"])
        self._list.setCurrentRow(len(self._rules) - 1)
        self.rules_changed.emit(self.get_rules())

    # =========================
    # DELETE RULE ~ Removes the currently selected rule.
    # =========================
    def delete_rule(self):
        idx = self._list.currentRow()
        if idx < 0 or idx >= len(self._rules):
            return

        self._rules.pop(idx)
        self._list.takeItem(idx)
        self._current_idx = -1

        # Select adjacent rule after deletion
        new_count = len(self._rules)
        if new_count > 0:
            self._list.setCurrentRow(min(idx, new_count - 1))
        else:
            self._editor.clear()

        self.rules_changed.emit(self.get_rules())

    # =========================
    # SET VTS ASSETS ~ Passes fetched VTS data to the editor panel.
    # =========================
    def set_vts_assets(self, expressions: list[str], hotkeys: list[dict]):
        self._editor.set_vts_assets(expressions, hotkeys)

    # =========================
    # ON RULE SELECTED ~ Flushes the previous rule's edits then loads the new one.
    # _loading flag prevents the flush from emitting a spurious rules_changed.
    # =========================
    def _on_rule_selected(self, idx: int):
        if self._loading or idx < 0:
            return

        # Save edits from the rule we're leaving
        self._flush_current()

        # Load the newly selected rule into the editor panel
        self._current_idx = idx
        self._editor.load_rule(self._rules[idx])

    # =========================
    # ON EDITOR CHANGED ~ Called whenever any field in the editor panel changes.
    # Flushes the current rule and emits rules_changed so the main window
    # knows the in-memory state is dirty (unsaved).
    # =========================
    def _on_editor_changed(self):
        if self._loading:
            return
        self._flush_current()
        self.rules_changed.emit(self.get_rules())

    # =========================
    # FLUSH CURRENT ~ Writes the editor panel's current state back into
    # self._rules at the current index, and updates the list item's label
    # if the rule name changed.
    # =========================
    def _flush_current(self):
        idx = self._current_idx
        if idx < 0 or idx >= len(self._rules):
            return

        updated = self._editor.to_rule()
        self._rules[idx] = updated

        # Keep the list item label in sync with the rule name field
        item = self._list.item(idx)
        if item and item.text() != updated["name"]:
            item.setText(updated["name"])


# =========================
# COMBO HELPER ~ Sets a QComboBox to a value by text.
# If the value isn't in the list, does nothing (avoids index errors on old JSON).
# =========================
def _set_combo(combo: QComboBox, value: str):
    idx = combo.findText(value)
    if idx >= 0:
        combo.setCurrentIndex(idx)
    else:
        # For editable combos (expression/hotkey), just set the text directly
        if combo.isEditable():
            combo.setCurrentText(value)