"""
gui.py ~ Temporary!
"""

import sys
import json

from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QFileDialog, QGroupBox
)
from PySide6.QtCore import Qt, QObject, Signal

from core.pipeline import EEGPipeline
from core.bridge import VTSBridge


# =========================
# STATE RELAY ~ Carries the pipeline state dict from the background thread to the main thread safely.
# =========================
class _StateRelay(QObject):
    state_received = Signal(dict)


# =========================
# MAIN WINDOW ~ Single QWidget, everything in one flat layout.
# =========================
class MuseBridgeGUI(QWidget):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("MuseBridge")
        self.setMinimumWidth(480)

        # Instance variables created with _start methods, deleted with _stop methods
        self._pipeline = None
        self._bridge = None
        self._rules = []

        # State relay for thread-safe pipeline callbacks
        self._relay = _StateRelay()

        # Build the UI
        self._build_ui()

    #     # Section for connecting signals
    #     self._connect_signals()
    #
    # def _connect_signals(self):
    #     self
    # =========================
    # BUILD UI ~ Creates all widgets and lays them out.
    # Everything is in one vertical stack.
    # =========================
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 16, 16, 16)

        # =========================
        # RULES FILE ROW ~ Path field + Browse + Load buttons
        # =========================
        rules_group = QGroupBox("Rules")
        rules_layout = QHBoxLayout(rules_group)

        self._rules_path = QLineEdit()
        self._rules_path.setPlaceholderText("rules.json")

        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self._browse_rules)

        load_btn = QPushButton("Load")
        load_btn.clicked.connect(self._load_rules)

        self._rules_status = QLabel("not loaded")
        self._rules_status.setStyleSheet("color: #888;")

        rules_layout.addWidget(self._rules_path)
        rules_layout.addWidget(browse_btn)
        rules_layout.addWidget(load_btn)
        rules_layout.addWidget(self._rules_status)
        layout.addWidget(rules_group)

        # =========================
        # BRIDGE ROW ~ Start + Stop + status
        # =========================
        bridge_group = QGroupBox("Bridge (VTube Studio)")
        bridge_layout = QHBoxLayout(bridge_group)

        self._bridge_start = QPushButton("Start")
        self._bridge_start.clicked.connect(self._start_bridge)

        self._bridge_stop = QPushButton("Stop")
        self._bridge_stop.clicked.connect(self._stop_bridge)
        self._bridge_stop.setEnabled(False)

        self._bridge_status = QLabel("stopped")
        self._bridge_status.setStyleSheet("color: #888;")

        bridge_layout.addWidget(self._bridge_start)
        bridge_layout.addWidget(self._bridge_stop)
        bridge_layout.addStretch()
        bridge_layout.addWidget(self._bridge_status)
        layout.addWidget(bridge_group)

        # =========================
        # PIPELINE ROW ~ Start + Stop + status
        # =========================
        pipeline_group = QGroupBox("Pipeline (EEG Source)")
        pipeline_layout = QHBoxLayout(pipeline_group)

        self._pipeline_start = QPushButton("Start")
        self._pipeline_start.clicked.connect(self._start_pipeline)

        self._pipeline_stop = QPushButton("Stop")
        self._pipeline_stop.clicked.connect(self._stop_pipeline)
        self._pipeline_stop.setEnabled(False)

        self._pipeline_status = QLabel("stopped")
        self._pipeline_status.setStyleSheet("color: #888;")

        pipeline_layout.addWidget(self._pipeline_start)
        pipeline_layout.addWidget(self._pipeline_stop)
        pipeline_layout.addStretch()
        pipeline_layout.addWidget(self._pipeline_status)
        layout.addWidget(pipeline_group)

        layout.addStretch()

    # =========================
    # BROWSE ~ Opens a file dialog for the rules JSON.
    # =========================
    def _browse_rules(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Rules File", "", "JSON Files (*.json)"
        )
        if path:
            self._rules_path.setText(path)

    # =========================
    # LOAD RULES ~ Reads the JSON and stores the rules list.
    # Hot-reloads into the bridge if it's already running.
    # =========================
    def _load_rules(self):
        path = self._rules_path.text().strip() or "rules.json"
        try:
            with open(path) as f:
                self._rules = json.load(f).get("rules", [])
            self._rules_status.setText(f"{len(self._rules)} rules loaded")
            self._rules_status.setStyleSheet("color: green;")

            if self._bridge:
                self._bridge.reload_rules(self._rules)

        except Exception as e:
            self._rules_status.setText(f"error: {e}")
            self._rules_status.setStyleSheet("color: red;")

    # =========================
    # START BRIDGE ~ Creates and starts a VTSBridge with the loaded rules.
    # =========================
    def _start_bridge(self):
        if not self._rules:
            self._bridge_status.setText("load rules first")
            return
        try:
            self._bridge = VTSBridge(self._rules)
            self._bridge.start()
            self._bridge_status.setText("running")
            self._bridge_status.setStyleSheet("color: green;")
            self._bridge_start.setEnabled(False)
            self._bridge_stop.setEnabled(True)
        except Exception as e:
            self._bridge_status.setText(f"error: {e}")
            self._bridge_status.setStyleSheet("color: red;")

    # =========================
    # STOP BRIDGE ~ Shuts down the bridge cleanly.
    # =========================
    def _stop_bridge(self):
        if self._bridge:
            self._bridge.stop()
            self._bridge = None
        self._bridge_status.setText("stopped")
        self._bridge_status.setStyleSheet("color: #888;")
        self._bridge_start.setEnabled(True)
        self._bridge_stop.setEnabled(False)

    # =========================
    # START PIPELINE ~ Creates and starts an EEGPipeline.
    # Uses synthetic board by default — safe to run without hardware.
    # =========================
    def _start_pipeline(self):
        config = {
            "csv":       None,
            "synthetic": True,    # change to False to try live Muse
            "verbose":   False,
        }
        try:
            self._pipeline = EEGPipeline(config)
            self._pipeline.start()
            self._pipeline_status.setText("running")
            self._pipeline_status.setStyleSheet("color: green;")
            self._pipeline_start.setEnabled(False)
            self._pipeline_stop.setEnabled(True)
        except Exception as e:
            self._pipeline_status.setText(f"error: {e}")
            self._pipeline_status.setStyleSheet("color: red;")

    # =========================
    # STOP PIPELINE ~ Shuts down the pipeline cleanly.
    # =========================
    def _stop_pipeline(self):
        if self._pipeline:
            self._pipeline.stop()
            self._pipeline = None
        self._pipeline_status.setText("stopped")
        self._pipeline_status.setStyleSheet("color: #888;")
        self._pipeline_start.setEnabled(True)
        self._pipeline_stop.setEnabled(False)

    # =========================
    # CLOSE EVENT ~ Clean shutdown when the window is closed.
    # =========================
    def closeEvent(self, event):
        self._stop_pipeline()
        self._stop_bridge()
        event.accept()


# =========================
# ENTRY POINT
# =========================
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MuseBridgeGUI()
    window.show()
    sys.exit(app.exec())