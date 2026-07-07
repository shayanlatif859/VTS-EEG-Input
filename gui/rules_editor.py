from PySide6.QtWidgets import QWidget, QVBoxLayout, QListWidget
from PySide6.QtCore import Signal, QFile
from PySide6.QtUiTools import QUiLoader
import os

BANDS   = ["delta", "theta", "alpha", "beta", "gamma", "faa", "valence", "arousal"]
SENSORS = ["mean", "AF7", "AF8", "TP9", "TP10"]
OPS     = ["<", ">", "<=", ">=", "=="]

UI_DIR = os.path.dirname(__file__)


# =========================
# CONDITION ROW
# =========================
class ConditionRow(QWidget):
    delete_requested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)

        loader = QUiLoader()
        file = QFile(os.path.join(UI_DIR, "condition_row.ui"))
        file.open(QFile.OpenModeFlag.ReadOnly)
        self.ui = loader.load(file, self)
        file.close()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.ui)

        self.ui.bandCombo.addItems(BANDS)
        self.ui.sensorCombo.addItems(SENSORS)
        self.ui.opCombo.addItems(OPS)
        self.ui.valueSpinbox.setRange(0.0, 1.0)
        self.ui.valueSpinbox.setSingleStep(0.05)

        self.ui.deleteBtn.clicked.connect(lambda: self.delete_requested.emit(self))

    def to_dict(self) -> dict:
        d = {
            "band":  self.ui.bandCombo.currentText(),
            "op":    self.ui.opCombo.currentText(),
            "value": round(self.ui.valueSpinbox.value(), 4),
        }
        if self.ui.sensorCombo.currentText() != "mean":
            d["sensor"] = self.ui.sensorCombo.currentText()
        return d

    def from_dict(self, d: dict):
        _set_combo(self.ui.bandCombo,   d.get("band",   "alpha"))
        _set_combo(self.ui.sensorCombo, d.get("sensor", "mean"))
        _set_combo(self.ui.opCombo,     d.get("op",     ">"))
        self.ui.valueSpinbox.setValue(float(d.get("value", 0.5)))


# =========================
# OUTPUT ROW
# All columns live in one flat QHBoxLayout so every label and control  shares the same vertical baseline.
# Type-switching caused misalignment issues earlier.
# =========================
class OutputRow(QWidget):
    delete_requested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)

        loader = QUiLoader()
        file = QFile(os.path.join(UI_DIR, "output_row.ui"))
        file.open(QFile.OpenModeFlag.ReadOnly)
        self.ui = loader.load(file, self)
        file.close()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.ui)

        self.ui.typeCombo.addItems(["parameter", "expression", "hotkey"])
        self.ui.paramMode.addItems(["source", "fixed"])
        self.ui.sourceBand.addItems(BANDS)
        self.ui.sourceSensor.addItems(SENSORS)
        self.ui.scaleSpinbox.setRange(-5.0, 5.0)
        self.ui.scaleSpinbox.setValue(1.0)
        self.ui.offsetSpinbox.setRange(-1.0, 1.0)
        self.ui.fixedValue.setRange(-1.0, 1.0)
        self.ui.cooldownSpinbox.setRange(0.0, 30.0)
        self.ui.cooldownSpinbox.setValue(3.0)

        self.ui.typeCombo.currentTextChanged.connect(self._on_type_changed)
        self.ui.paramMode.currentTextChanged.connect(self._on_mode_changed)
        self.ui.deleteBtn.clicked.connect(lambda: self.delete_requested.emit(self))

        self._hotkey_ids: list[str] = []

        self._on_type_changed("parameter")
        self._on_mode_changed("source")

    # =========================
    # Column groups per type.
    # Each returns the widgets that should be visible for that state.
    # Hiding a widget in a flat HBoxLayout collapses its space cleanly.
    # =========================

    @property
    def _param_cols(self):
        # Always visible when type == parameter
        return [self.ui.paramLbl, self.ui.paramName,
                self.ui.modeLbl, self.ui.paramMode]

    @property
    def _fixed_cols(self):
        return [self.ui.fixedLbl, self.ui.fixedValue]

    @property
    def _source_cols(self):
        return [self.ui.srcBandLbl, self.ui.sourceBand,
                self.ui.srcSensorLbl, self.ui.sourceSensor,
                self.ui.scaleLbl, self.ui.scaleSpinbox,
                self.ui.offsetLbl, self.ui.offsetSpinbox]

    @property
    def _expr_cols(self):
        return [self.ui.exprLbl, self.ui.exprCombo]

    @property
    def _hotkey_cols(self):
        return [self.ui.hotkeyLbl, self.ui.hotkeyCombo,
                self.ui.cooldownLbl, self.ui.cooldownSpinbox]

    @property
    def _all_type_cols(self):
        return self._param_cols + self._fixed_cols + self._source_cols \
             + self._expr_cols + self._hotkey_cols

    def _on_type_changed(self, t: str):
        # Hide everything then show only what belongs to this type
        for w in self._all_type_cols:
            w.setVisible(False)

        if t == "parameter":
            for w in self._param_cols:
                w.setVisible(True)
            # Also apply current mode visibility
            self._on_mode_changed(self.ui.paramMode.currentText())

        elif t == "expression":
            for w in self._expr_cols:
                w.setVisible(True)

        elif t == "hotkey":
            for w in self._hotkey_cols:
                w.setVisible(True)

    def _on_mode_changed(self, mode: str):
        # Only relevant when type == parameter; harmless to call otherwise
        for w in self._fixed_cols:
            w.setVisible(mode == "fixed")
        for w in self._source_cols:
            w.setVisible(mode == "source")

    def set_vts_assets(self, expressions: list[str], hotkeys: list[dict]):
        cur_expr = self.ui.exprCombo.currentText()
        cur_hid  = None
        idx = self.ui.hotkeyCombo.currentIndex()
        if 0 <= idx < len(self._hotkey_ids):
            cur_hid = self._hotkey_ids[idx]

        self.ui.exprCombo.clear()
        self.ui.exprCombo.addItems(expressions)
        if cur_expr:
            self.ui.exprCombo.setCurrentText(cur_expr)

        self._hotkey_ids = [h["hotkeyID"] for h in hotkeys]
        self.ui.hotkeyCombo.clear()
        self.ui.hotkeyCombo.addItems(
            [f"{h['name']} ({h['hotkeyID']})" for h in hotkeys]
        )
        if cur_hid and cur_hid in self._hotkey_ids:
            self.ui.hotkeyCombo.setCurrentIndex(self._hotkey_ids.index(cur_hid))

    def to_dict(self) -> dict:
        t = self.ui.typeCombo.currentText()

        if t == "parameter":
            d: dict = {"type": "parameter", "param": self.ui.paramName.text().strip()}
            if self.ui.paramMode.currentText() == "fixed":
                d["value"] = round(self.ui.fixedValue.value(), 4)
            else:
                d["source"] = self.ui.sourceBand.currentText()
                if self.ui.sourceSensor.currentText() != "mean":
                    d["sensor"] = self.ui.sourceSensor.currentText()
                d["scale"]  = round(self.ui.scaleSpinbox.value(), 4)
                d["offset"] = round(self.ui.offsetSpinbox.value(), 4)
            return d

        if t == "expression":
            return {
                "type":       "expression",
                "expression": self.ui.exprCombo.currentText().strip(),
            }

        if t == "hotkey":
            idx = self.ui.hotkeyCombo.currentIndex()
            hid = (
                self._hotkey_ids[idx]
                if 0 <= idx < len(self._hotkey_ids)
                else self.ui.hotkeyCombo.currentText().strip()
            )
            return {
                "type":      "hotkey",
                "hotkey_id": hid,
                "cooldown":  round(self.ui.cooldownSpinbox.value(), 1),
            }

        return {}

    def from_dict(self, d: dict):
        t = d.get("type", "parameter")
        _set_combo(self.ui.typeCombo, t)
        self._on_type_changed(t)

        if t == "parameter":
            self.ui.paramName.setText(d.get("param", ""))
            if "value" in d:
                _set_combo(self.ui.paramMode, "fixed")
                self.ui.fixedValue.setValue(float(d["value"]))
                self._on_mode_changed("fixed")
            else:
                _set_combo(self.ui.paramMode, "source")
                _set_combo(self.ui.sourceBand,   d.get("source", "alpha"))
                _set_combo(self.ui.sourceSensor, d.get("sensor", "mean"))
                self.ui.scaleSpinbox.setValue(float(d.get("scale",  1.0)))
                self.ui.offsetSpinbox.setValue(float(d.get("offset", 0.0)))
                self._on_mode_changed("source")

        elif t == "expression":
            self.ui.exprCombo.setCurrentText(d.get("expression", ""))

        elif t == "hotkey":
            hid = d.get("hotkey_id", "")
            if hid in self._hotkey_ids:
                self.ui.hotkeyCombo.setCurrentIndex(self._hotkey_ids.index(hid))
            else:
                self.ui.hotkeyCombo.setCurrentText(hid)
            self.ui.cooldownSpinbox.setValue(float(d.get("cooldown", 3.0)))


# =========================
# RULES EDITOR PANEL
# =========================
class RulesEditorPanel(QWidget):

    rules_changed = Signal(list)

    def __init__(self, rules_list_widget: QListWidget, parent=None):
        super().__init__(parent)

        loader = QUiLoader()
        file = QFile(os.path.join(UI_DIR, "rules_editor.ui"))
        file.open(QFile.OpenModeFlag.ReadOnly)
        self.ui = loader.load(file, self)
        file.close()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.ui)

        self._list = rules_list_widget
        self._list.currentRowChanged.connect(self._on_row_selected)

        self._rules:           list[dict] = []
        self._current_idx:     int        = -1
        self._loading:         bool       = False

        self._condition_rows:  list[ConditionRow] = []
        self._output_rows:     list[OutputRow]    = []
        self._vts_expressions: list[str]          = []
        self._vts_hotkeys:     list[dict]         = []

        self.ui.addConditionBtn.clicked.connect(self._add_condition)
        self.ui.addOutputBtn.clicked.connect(self._add_output)
        self.ui.ruleName.textChanged.connect(self._on_editor_changed)

    @property
    def editor_widget(self) -> QWidget:
        return self

    def load_rules(self, rules: list):
        self._loading = True
        self._rules   = [dict(r) for r in rules]

        self._list.clear()
        for rule in self._rules:
            self._list.addItem(rule.get("name", "unnamed"))

        self._loading = False

        if self._rules:
            self._list.setCurrentRow(0)
        else:
            self._clear_editor()

    def get_rules(self) -> list:
        self._flush_current()
        return list(self._rules)

    def add_rule(self):
        new_rule = {"name": "new_rule", "conditions": [], "outputs": []}
        self._rules.append(new_rule)
        self._list.addItem(new_rule["name"])
        self._list.setCurrentRow(len(self._rules) - 1)
        self.rules_changed.emit(self.get_rules())

    def delete_rule(self):
        idx = self._list.currentRow()
        if idx < 0 or idx >= len(self._rules):
            return

        self._rules.pop(idx)
        self._list.takeItem(idx)
        self._current_idx = -1

        new_count = len(self._rules)
        if new_count > 0:
            self._list.setCurrentRow(min(idx, new_count - 1))
        else:
            self._clear_editor()

        self.rules_changed.emit(self.get_rules())

    def set_vts_assets(self, expressions: list[str], hotkeys: list[dict]):
        self._vts_expressions = expressions
        self._vts_hotkeys     = hotkeys
        for row in self._output_rows:
            row.set_vts_assets(expressions, hotkeys)

    def _on_row_selected(self, idx: int):
        if self._loading or idx < 0:
            return
        self._flush_current()
        self._current_idx = idx
        self._load_rule_into_editor(self._rules[idx])

    def _on_editor_changed(self):
        if self._loading:
            return
        self._flush_current()
        self.rules_changed.emit(list(self._rules))

    def _flush_current(self):
        idx = self._current_idx
        if idx < 0 or idx >= len(self._rules):
            return

        updated = {
            "name":       self.ui.ruleName.text().strip() or "unnamed",
            "conditions": [r.to_dict() for r in self._condition_rows],
            "outputs":    [r.to_dict() for r in self._output_rows],
        }
        self._rules[idx] = updated

        item = self._list.item(idx)
        if item and item.text() != updated["name"]:
            item.setText(updated["name"])

    def _load_rule_into_editor(self, rule: dict):
        self._clear_editor()
        self.ui.ruleName.setText(rule.get("name", ""))
        for c in rule.get("conditions", []):
            self._add_condition(c)
        for o in rule.get("outputs", []):
            self._add_output(o)

    def _clear_editor(self):
        for row in list(self._condition_rows):
            self._remove_condition(row)
        for row in list(self._output_rows):
            self._remove_output(row)
        self.ui.ruleName.clear()

    def _add_condition(self, d: dict = None):
        row = ConditionRow()
        if d:
            row.from_dict(d)
        row.delete_requested.connect(self._remove_condition)
        layout = self.ui.conditionsLayout
        layout.insertWidget(layout.count() - 1, row)
        self._condition_rows.append(row)
        self._on_editor_changed()

    def _remove_condition(self, row: ConditionRow):
        self.ui.conditionsLayout.removeWidget(row)
        row.deleteLater()
        self._condition_rows.remove(row)
        self._on_editor_changed()

    def _add_output(self, d: dict = None):
        row = OutputRow()
        row.set_vts_assets(self._vts_expressions, self._vts_hotkeys)
        if d:
            row.from_dict(d)
        row.delete_requested.connect(self._remove_output)
        layout = self.ui.outputsLayout
        layout.insertWidget(layout.count() - 1, row)
        self._output_rows.append(row)
        self._on_editor_changed()

    def _remove_output(self, row: OutputRow):
        self.ui.outputsLayout.removeWidget(row)
        row.deleteLater()
        self._output_rows.remove(row)
        self._on_editor_changed()


# =========================
# HELPER
# =========================
def _set_combo(combo, value: str):
    idx = combo.findText(value)
    if idx >= 0:
        combo.setCurrentIndex(idx)
    elif combo.isEditable():
        combo.setCurrentText(value)
