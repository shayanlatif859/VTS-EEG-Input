"""
main_window.py ~ Main GUI window.

What it does:
~ Load a .ui file for easy GUI editing
~ Starts and stops pipeline and bridge lifecycle
~ Applies state relay from pipeline thread to GUI thread (NOTE: Inspect how you are doing this later.)
~ Rules file load/save/hot-reload into running bridge
~ CSV, synthetic, and live path toggle
~ Unsaved-changes marker in window title with *

What is does not:
~ Run the QApplication (main.py)
~ Rules editing UI (rules_editor.py) [NOT DONE!]
~ Band visualization (brain_display.py) [ALSO NOT DONE!]
"""

import json

from PySide6.QtWidgets import QMainWindow, QFileDialog
from PySide6.QtCore    import Qt, QObject, Signal
from PySide6.QtUiTools import QUiLoader

from core.pipeline     import EEGPipeline
from core.bridge       import VTSBridge
from gui.rules_editor  import RulesEditorPanel
# TODO: from gui.brain_display import BrainDisplay !



# =========================
# STATE RELAY ~ Lightweight QObject whose only job is carrying the
# state dict from a background thread to the main (GUI) thread safely.
#
# Qt signal/slot connections that cross threads are automatically queued with
# the slot runs in the receiver's thread, not the sender's.
# Without this, calling display.update_state() directly from the pipeline
# thread would update Qt widgets off the main thread, which is undefined behavior.
# =========================
class _StateRelay(QObject):
    state_received = Signal(dict)


# =========================
# MAIN WINDOW ~ Loads the .ui file and owns the application lifecycle.
# =========================
class MuseBridgeWindow(QMainWindow):

    def __init__(self):
        super().__init__()

        # =========================
        # LOAD UI ~ QUiLoader reads the .ui XML and builds the widget tree.
        #
        # self.ui holds all the named widget references (self.ui.loadButton etc.)
        # =========================
        loader  = QUiLoader()
        self.ui = loader.load("main_window.ui", None)

        # ⚑
        if self.ui is None:
            raise RuntimeError(
                "QUiLoader failed to load main_window.ui... "
                "check the file exists next to main.py"
            )

        # Adopt the central widget, menu bar, and status bar from the loaded
        # QMainWindow into this one so they render correctly
        self.setCentralWidget(self.ui.centralWidget())
        self.setMenuBar(self.ui.menuBar())
        self.setStatusBar(self.ui.statusBar())

        self.setWindowTitle("MuseBridge")
        self.resize(1100, 800)

        # =========================
        # CORE INSTANCES ~ Pipeline and bridge start as None.
        # They are created fresh on each Start click so settings are re-read and set back to None on Stop so the GC can clean them up.
        # =========================
        self._pipeline: EEGPipeline | None = None
        self._bridge: VTSBridge | None = None

        # =========================
        # BRAIN DISPLAY ~ Created once and inserted into the right panel of the splitter, above the rules editor area.
        # =========================
        # TODO: self._brain_display = BrainDisplay() !
        self._state_relay = _StateRelay()

        # =========================
        # RULES EDITOR ~ Passes the existing QListWidget from the .ui file so the list stays in its Designer-assigned position on the left panel.
        # The editor panel widget is inserted into editorOuterLayout below the brain display, replacing the static Designer placeholders.
        # =========================
        self._rules_editor = RulesEditorPanel(self.ui.rulesListWidget)

        # Remove the static Designer placeholders (rule name row, conditions group, outputs group) and replace with the live editor panel.
        while self.ui.editorOuterLayout.count() > 1:
            item = self.ui.editorOuterLayout.takeAt(1)
            if item.widget():
                item.widget().deleteLater()

        # Insert the live editor panel after the brain display
        self.ui.editorOuterLayout.addWidget(self._rules_editor.editor_widget)

        # Notify main window whenever rules change so it can mark unsaved state
        self._rules_editor.rules_changed.connect(self._on_rules_changed)

        # =========================
        # WIRE SIGNALS ~ Connect every button to its handler.
        # Doing this all in one place makes it easy to see the full input surface of the window at a glance.
        # =========================
        self._connect_signals()

        # =========================
        # INITIAL UI STATE ~ Disable Stop buttons until something is running.
        # CSV path fields start hidden—they appear when CSV radio is selected.
        # =========================
        self._set_pipeline_running(False)
        self._set_bridge_running(False)

    # =========================
    # CONNECT SIGNALS ~ One method, all signal→slot wiring.
    # Grouped by which part of the UI each block belongs to.
    # =========================
    def _connect_signals(self):

        # Top bar ~ rules file management
        self.ui.browseJsonButton.clicked.connect(self._browse_json)
        self.ui.loadButton.clicked.connect(self._load_rules)
        self.ui.saveButton.clicked.connect(self._save_rules)
        self.ui.fetchVTSButton.clicked.connect(self._fetch_from_vts)

        # Menu bar actions (mirror the top bar buttons)
        # ⚑ Likely redundant. May be removed soon.
        self.ui.actionLoad.triggered.connect(self._load_rules)
        self.ui.actionSave.triggered.connect(self._save_rules)
        self.ui.actionSaveAs.triggered.connect(self._save_rules_as)

        # Bridge launcher
        self.ui.bridgeStartButton.clicked.connect(self._start_bridge)
        self.ui.bridgeStopButton.clicked.connect(self._stop_bridge)

        # Pipeline launcher
        self.ui.simStartButton.clicked.connect(self._start_pipeline)
        self.ui.simStopButton.clicked.connect(self._stop_pipeline)

        # CSV radio toggle show/hide the path field and browse button
        self.ui.simCsvRadio.toggled.connect(self._on_csv_radio_toggled)

        # CSV browse button
        self.ui.simCsvBrowseButton.clicked.connect(self._browse_csv)

        # Rule list add / delete buttons (left panel)
        self.ui.addRuleButton.clicked.connect(self._rules_editor.add_rule)
        self.ui.deleteRuleButton.clicked.connect(self._rules_editor.delete_rule)

    # =========================
    # BRIDGE START ~ Reads the currently loaded rules, builds a VTSBridge,
    # and starts it. Updates the status label on success or failure.
    # =========================
    def _start_bridge(self):
        rules = self._get_current_rules()
        if not rules:
            self.ui.bridgeStatusLabel.setText("no rules loaded")
            return

        try:
            self._bridge = VTSBridge(rules)
            self._bridge.start()
            self._set_bridge_running(True)
            self.ui.bridgeStatusLabel.setText("running")
            self.ui.vtsStatusLabel.setText("VTS: connected")

        except RuntimeError as e:
            # Port already in use, or VTS not reachable
            self.ui.bridgeStatusLabel.setText(f"error: {e}")

    # =========================
    # BRIDGE STOP ~ Shuts down the bridge cleanly and resets status.
    # =========================
    def _stop_bridge(self):
        if self._bridge:
            self._bridge.stop()
            self._bridge = None

        self._set_bridge_running(False)
        self.ui.bridgeStatusLabel.setText("stopped")
        self.ui.vtsStatusLabel.setText("VTS: not connected")

    # =========================
    # PIPELINE START ~ Builds a config dict from the current UI state, creates an EEGPipeline, and starts it in the background.
    # The state relay is registered before start() so the very first tick is captured.
    # Registering after could miss early frames.
    # =========================
    def _start_pipeline(self):
        config = self._build_pipeline_config()

        try:
            self._pipeline = EEGPipeline(config)

            # Route pipeline state updates through the relay so update_state()
            # always runs on the main thread, never the pipeline thread
            self._pipeline.on_state(self._state_relay.state_received.emit)

            self._pipeline.start()
            self._set_pipeline_running(True)
            self.ui.simStatusLabel.setText("running")

        except Exception as e:
            self.ui.simStatusLabel.setText(f"error: {e}")

    # =========================
    # PIPELINE STOP ~ Shuts down the pipeline cleanly and resets status.
    # =========================
    def _stop_pipeline(self):
        if self._pipeline:
            self._pipeline.stop()
            self._pipeline = None

        self._set_pipeline_running(False)
        self.ui.simStatusLabel.setText("stopped")

    # =========================
    # BUILD PIPELINE CONFIG ~ Reads the launcher panel widgets and assembles
    # the config dict that EEGPipeline expects.
    # Adding a new setting to the UI only requires adding one line here.
    # =========================
    def _build_pipeline_config(self) -> dict:
        using_csv = self.ui.simCsvRadio.isChecked()
        using_synthetic = self.ui.SyntheticRadio.isChecked()

        return {
            # Source selection
            "csv": self.ui.simCsvEdit.text() if using_csv else None,
            "csv_format": "auto",
            "csv_rate": 10.0 if using_csv else None,
            "csv_loop": False, # We might just get rid of this feature at this point...

            # Use synthetic board if synthetic radio is checked
            "synthetic": using_synthetic,

            # Verbose mode off in GUI, we do not need a terminal output
            "verbose": False,
        }

    # =========================
    # JSON BROWSE ~ Opens a file dialog and puts the chosen path in the text field.
    # Does not load the file yet, the user still clicks Load, which calls _load_rules().
    # =========================
    def _browse_json(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Rules File", "", "JSON Files (*.json)"
        )
        if path:
            self.ui.jsonPathEdit.setText(path)

    # =========================
    # LOAD RULES ~ Reads the JSON file at the path shown in jsonPathEdit.
    # Feeds the parsed rules into the rules editor, which populates the list.
    # If the bridge is already running, hot-reloads the rules immediately.
    # =========================
    def _load_rules(self):
        path = self.ui.jsonPathEdit.text().strip() or "rules.json"

        # Read "rules" in JSON file—rules is saved as a list.
        try:
            with open(path) as f:
                data = json.load(f)
            rules = data.get("rules", [])

            # Feed into the visual editor which builds the rule list in a manner easily editable
            self._rules_editor.load_rules(rules)
            self.ui.statusbar.showMessage(f"Loaded {len(rules)} rules from {path}", 3000)

            # Hot-reload into a running bridge without restarting it
            if self._bridge:
                self._bridge.reload_rules(rules)

        except Exception as e:
            self.ui.statusbar.showMessage(f"Load failed: {e}", 5000)

    # =========================
    # SAVE RULES ~ Writes the current rules back to the same file.
    # Falls through to Save As if no path has been set yet.
    # =========================
    def _save_rules(self):
        path = self.ui.jsonPathEdit.text().strip()
        if not path:
            self._save_rules_as()
            return
        self._write_rules_to(path)

    # =========================
    # SAVE AS ~ Opens a file dialog to choose a new save location,
    # then updates the path field so subsequent saves go to the same file.
    # =========================
    def _save_rules_as(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Rules As", "", "JSON Files (*.json)"
        )

        if path:
            self.ui.jsonPathEdit.setText(path)
            self._write_rules_to(path)

    # =========================
    # WRITE RULES ~ Shared helper used by both save functions.
    # Wraps the rules list back into the {"rules": [...]} envelope the bridge expects.
    # =========================
    def _write_rules_to(self, path):
        rules = self._get_current_rules()

        if not rules:
            self.ui.statusbar.showMessage("Nothing to save, no rules loaded", 3000)
            return

        try:
            with open(path, "w") as f:
                json.dump({"rules": rules}, f, indent=2)
            self.ui.statusbar.showMessage(f"Saved to {path}", 3000)
            # Clear the unsaved-changes marker from the title
            self.setWindowTitle(self.windowTitle().rstrip(" *"))

        except Exception as e:
            self.ui.statusbar.showMessage(f"Save failed: {e}", 5000)

    # =========================
    # FETCH FROM VTS ~ Asks the bridge to query VTS for available expressions and hotkeys,
    # then passes them to the rules editor so output rows can populate their pickers.
    # =========================
    def _fetch_from_vts(self):
        if not self._bridge:
            self.ui.vtsStatusLabel.setText("VTS: bridge not running")
            return

        try:
            # Recall that these return lists of dictionaries. [{}, {}, ...]
            expressions = self._bridge.list_expressions()
            hotkeys = self._bridge.list_hotkeys()

            # Pass raw lists to the editor, where it is formatted for display
            expr_files = [e["file"] for e in expressions]
            self._rules_editor.set_vts_assets(expr_files, hotkeys)

            self.ui.statusbar.showMessage(
                f"VTS: {len(expressions)} expressions, {len(hotkeys)} hotkeys fetched", 5000
            )
        except Exception as e:
            self.ui.vtsStatusLabel.setText(f"VTS: fetch failed ({e})")

    # =========================
    # ON RULES CHANGED ~ Called by the rules editor whenever any field changes.
    # Marks the window title with an asterisk to indicate unsaved changes.
    # =========================
    def _on_rules_changed(self, rules: list):
        if self._bridge:
            self._bridge.reload_rules(rules)
        if not self.windowTitle().endswith("*"):
            self.setWindowTitle(self.windowTitle() + " *")

    # =========================
    # GET CURRENT RULES ~ Returns the full rules list from the editor,
    # flushing any unsaved edits from the current panel first.
    # =========================
    def _get_current_rules(self):
        rules = self._rules_editor.get_rules()
        return rules if rules else None

    # =========================
    # CSV RADIO TOGGLE ~ Shows or hides the CSV path field and browse button
    # depending on whether the CSV radio button is selected.
    # =========================
    def _on_csv_radio_toggled(self, checked: bool):
        self.ui.simCsvEdit.setVisible(checked)
        self.ui.simCsvBrowseButton.setVisible(checked)

    # =========================
    # CSV BROWSE ~ Opens a file dialog for selecting a CSV file for playback.
    # =========================
    def _browse_csv(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open CSV File", "", "CSV Files (*.csv)"
        )
        if path:
            self.ui.simCsvEdit.setText(path)

    # =========================
    # UI STATE HELPERS ~ Enable/disable buttons to match running state.
    # Prevents double-starting or stopping something that isn't running.
    # =========================
    def _set_bridge_running(self, running: bool):
        self.ui.bridgeStartButton.setEnabled(not running)
        self.ui.bridgeStopButton.setEnabled(running)

    def _set_pipeline_running(self, running: bool):
        self.ui.simStartButton.setEnabled(not running)
        self.ui.simStopButton.setEnabled(running)

    # =========================
    # CLOSE EVENT ~ Make sure both processes shut down cleanly when the
    # window is closed, rather than leaving orphaned threads running.
    # =========================
    def closeEvent(self, event):
        self._stop_pipeline()
        self._stop_bridge()
        event.accept()