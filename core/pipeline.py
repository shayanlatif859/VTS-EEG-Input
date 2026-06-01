"""
EEG Streaming + Playback Pipeline

Features:
~ Live streaming via BrainFlow
~ CSV playback (Muse Monitor + Muse Direct formats)
~ Per-sensor band power extraction (delta → gamma) per channel
~ One shared normalizer per band across all sensors
~ ensures AF7 and AF8 are on the same scale, making FAA valid
~ OSC output: /brain/<band>/<sensor> per channel + /brain/<band> mean
~ Device-agnostic: channel names come from a board→label map with fallback
~ Optional terminal visualization
"""

import time
import numpy as np
import csv
import argparse
import threading

from pylsl import StreamInfo, StreamOutlet
from pythonosc.udp_client import SimpleUDPClient
from brainflow.board_shim import BoardShim, BrainFlowInputParams, BoardIds
from scipy.signal import butter, filtfilt


# =========================
# CHANNEL NAME MAP
#
# Maps BrainFlow board IDs to human-readable electrode labels in channel order.
# The order must match BrainFlow's EEG channel index list for that board.
#
# Muse 2 / Muse S EEG channels come out in this order:
#   index 0 → TP9  (left temporal-parietal)
#   index 1 → AF7  (left prefrontal)
#   index 2 → AF8  (right prefrontal)
#   index 3 → TP10 (right temporal-parietal)
#
# Synthetic testing board uses only 8 EEG channels, despite being a 13 channel output.
# To add a new device: look up its BoardIds value and add a list of labels
# matching the channel order returned by BoardShim.get_eeg_channels().
# If a board is not listed here, it falls back to generic "ch0", "ch1", ...
# labels, which still work correctly, they just won't have anatomical names.
# =========================
CHANNEL_NAME_MAP = {
    BoardIds.MUSE_2_BOARD.value: ["TP9", "AF7", "AF8", "TP10"],
    BoardIds.MUSE_S_BOARD.value: ["TP9", "AF7", "AF8", "TP10"],
    BoardIds.SYNTHETIC_BOARD.value: ["ch0", "ch1", "ch2", "ch3",
                                     "ch4", "ch5", "ch6", "ch7"],
    # Add other boards here, example:
    # BoardIds.CYTON_BOARD.value: ["Fp1","Fp2","C3","C4","P7","P8","O1","O2"],
    # Ensure that you know the right channel names when using a different board.
    # The Muse board uses 4 EEG channels, but has other channel streams for
}

# =========================
# CONFIG ~ Sets a few global variables
# List BANDS is also set up here as the five frequencies, with BAND_RANGES as a dictionary
# The target IP and port are used to set up the client with SimpleUDPClient
# =========================
OSC_IP = "127.0.0.1"
OSC_PORT = 9000

osc = SimpleUDPClient(OSC_IP, OSC_PORT)
BANDS = ["delta", "theta", "alpha", "beta", "gamma"]

# Sets the frequency ranges for each band. Discretize this properly.
BAND_RANGES = {
    "delta": (0.5,  4),
    "theta": (4,    8),
    "alpha": (8,   12),
    "beta":  (13,  30),
    "gamma": (30,  45),
}

# =========================
# VERBOSE DISPLAY ~ These are all to print what is happening in a clear manner
# It is supposed to show the logarithmic power of each brainwave using a bar
# =========================
BAR_WIDTH = 30

def bar(value, width=BAR_WIDTH):
    filled = int(value * width)
    return "[" + "█" * filled + "░" * (width - filled) + f"] {value:.2f}"

def print_state(state, row_label=""):
    print(f"\033[{len(state)}A", end="")
    for k, v in state.items():
        print(f"  {k:<6}{bar(v)}")

def _init_display(n_lines):
    print("\n" * (n_lines + 1))


# =========================
# NORMALIZER ~ Keeps a rolling 30s window per band and scales to 0-1 relative to the session's own range
# make_normalizer creates a history dictionary and a window length value, along with the normalize function using the two values
# Calling normalize stores a new value in history, removes the oldest value in history once it's too long
# It then finds the lowest and highest and normalizes to a range of 0-1
# =========================
def make_normalizer(rate):
    history = {band: [] for band in BANDS}
    window_len = int(rate * 30)

    def normalize(name, raw):
        history[name].append(raw)
        if len(history[name]) > window_len:
            history[name].pop(0)

        lo, hi = min(history[name]), max(history[name])
        if hi - lo < 1e-8:
            return 0.5

        # Min-Max normalization
        return max(0.0, min(1.0, (raw - lo) / (hi - lo)))

    return normalize

# =========================
# BAND POWER ~ Creates a Butterworth bandpass filter, applies it, and returns mean power.
# Butterworth is designed to have a frequency response as flat as possible in the passband with no ripples.
# Filtfilt applies the filter forwards and backwards, cancelling phase delay.
# np.mean(filtered ** 2) calculates Power = amplitude ** 2.
# Averaging squared amplitude gives mean power.
# =========================
def band_power(signal, low, high, sample_rate):
    # Nyquist frequency is half the sample rate; frequencies must be normalized against it.
    nyq = sample_rate / 2.0
    b, a = butter(4, [low / nyq, high / nyq], btype="band")
    filtered = filtfilt(b, a, signal)
    return float(np.mean(filtered ** 2))


# =========================
# COMPUTE BANDS PER SENSOR ~ Takes raw EEG data and extracts how strong each frequency band is per electrode.
# Returns a flat dictionary with both per-sensor keys ("alpha/AF7") and mean keys ("alpha")
# The raw values returned here are not yet normalized, normalization happens in the caller using a make_normalizer instance.
# =========================
def compute_bands_per_sensor(data, eeg_channels, channel_names, band_ranges, sample_rate):
    result = {}

    # Iterate over each band definition
    for band, (low, high) in band_ranges.items():
        powers = {}

        # Compute power for each electrode and store by channel name
        for ch_idx, ch_name in zip(eeg_channels, channel_names):
            signal         = data[ch_idx, :].astype(np.float64)
            powers[ch_name] = band_power(signal, low, high, sample_rate)

        # Store mean across all sensors for this band
        result[band] = float(np.mean(list(powers.values())))

        # Store individual sensor values under "band/sensor" keys
        for ch_name, power in powers.items():
            result[f"{band}/{ch_name}"] = power

    return result


# =========================
# GET CHANNEL NAMES ~ Returns a list of label strings for the given board's EEG channels.
# Falls back to ["ch0", "ch1", ...] if the board isn't in the map,
# or if the map entry has fewer labels than actual channels.
# =========================
def get_channel_names(board_id, n_channels):
    labels = CHANNEL_NAME_MAP.get(board_id, [])

    if len(labels) >= n_channels:
        # Slice if there are more labels than channels on this board
        print(f"{len(labels)} labels found, slicing to {n_channels} channels...")
        return labels[:n_channels]

    # Fallback: generic names for any unlisted or partially-listed board
    return [f"ch{i}" for i in range(n_channels)]


# =========================
# EEG PIPELINE CLASS ~ Owns the BrainFlow board connection and OSC output.
# =========================
class EEGPipeline:

    # =========================
    # CONFIG ~ Sets a few class variables.
    # OSC target IP and port must match what bridge is listening on.
    # =========================
    DEFAULT_OSC_IP = "127.0.0.1"
    DEFAULT_OSC_PORT = 9000

    def __init__(self, config):
        # Config used to resemble argparse. View it in the older files.
        # The GUI builds this dict and passes it in.
        self.config = config

        # Runtime state, these object variables are created when start() is called.
        self._board = None
        self._outlet = None
        self._osc = None
        self._normalize_live = None
        self._running = False
        self._thread = None

        # Optional GUI callback ~ called each tick with the normalized state dict
        self._state_callback = None

    # =========================
    # ON STATE ~ Lets the GUI subscribe to live updates instead of polling.
    # Signature: callback(state: dict) where state contains all normalized
    # band/sensor keys (e.g. {"alpha": 0.6, "alpha/AF7": 0.7, ...})
    # =========================
    def on_state(self, callback):
        self._state_callback = callback

    # =========================
    # START ~ Public entry point.
    # Decides whether to run CSV playback or live board streaming,
    # then launches the appropriate path in a background thread.
    # =========================
    def start(self):
        self._running = True

        csv_path = self.config.get("csv")

        if csv_path:
            # CSV playback ~ detects format and launch the right reader
            fmt = self._detect_csv_format(csv_path, self.config.get("csv_format", "auto"))

            if fmt == "muse-monitor":
                target = lambda: self._run_csv_muse_monitor(
                    csv_path,
                    rate=self.config.get("csv_rate", 10.0),
                    loop=self.config.get("csv_loop", False),
                )
            elif fmt == "muse-direct":
                target = lambda: self._run_csv_muse_direct(
                    csv_path,
                    rate=self.config.get("csv_rate", 10.0),
                    loop=self.config.get("csv_loop", False),
                )
            else:
                raise ValueError(f"Unknown CSV format: {fmt}")

        else:
            # If no csv provided, set up board for input
            self._setup_board()
            target = self._live_loop

        # Set up the OSC client now to get port and IP
        self._osc = SimpleUDPClient(
            self.config.get("osc_ip",   self.DEFAULT_OSC_IP),
            self.config.get("osc_port", self.DEFAULT_OSC_PORT),
        )

        # Launch in a daemon thread so the GUI stays responsive
        self._thread = threading.Thread(target=target, daemon=True, name="eeg-pipeline")
        self._thread.start()

    # =========================
    # STOP ~ Clean shutdown.
    # Sets the flag so loops exit, then waits for the thread and releases the board.
    # =========================
    def stop(self):
        self._running = False

        if self._thread:
            # Give the thread up to 3 seconds to exit cleanly before giving up
            self._thread.join(timeout=3)
            self._thread = None

        # Release BrainFlow resources
        if self._board:
            try:
                self._board.stop_stream()
                self._board.release_session()
            except Exception as e:
                print(f"Board release error: {e}")
            self._board = None

    # =========================
    # SETUP BOARD ~ Connects to hardware or synthetic board.
    # Sets instance variables for board ID, sample rate, channels, etc.
    # so the live loop can use them without looking them up each tick.
    # =========================
    def _setup_board(self):
        params = BrainFlowInputParams()

        if self.config.get("synthetic"):
            # Forcing synthetic board skip BLE scan entirely. Useful for development
            # Synthetic boards use oscillating patterns and do not reliably model brainwaves.
            print("Synthetic board (forced via config)")
            self._board = BoardShim(BoardIds.SYNTHETIC_BOARD, params)
            self._board.prepare_session()
            self._board.start_stream()

        else:
            # Try live Muse first, ⚑ (fall back code removed for clarity)
            try:
                print("Connecting to Muse device...")
                self._board = BoardShim(BoardIds.MUSE_2_BOARD, params)
                self._board.prepare_session()
                self._board.start_stream()
                print("Connected.")

            except Exception as e:
                print(f"Warning: Muse device not found ({e}).")
                raise(ConnectionError("Muse device not found. Check drivers and connection."))

        # Cache board properties so we don't call BoardShim getters on every tick
        board_id = self._board.get_board_id()
        self._sample_rate = BoardShim.get_sampling_rate(board_id)
        self._window_size = self._sample_rate * 4   # 4-second analysis window
        self._eeg_channels= BoardShim.get_eeg_channels(board_id)
        self._channel_names = get_channel_names(board_id, len(self._eeg_channels))

        # One normalizer per pipeline instance, seeded with the live sample rate
        self._normalize_live = make_normalizer(self._sample_rate)

        # Set up LSL stream and for EEG inputs and outlet to be pushed or broadcasted for other tools... not utilized.
        info = StreamInfo("EEG", "EEG", len(self._eeg_channels),
                                   self._sample_rate, "float32", "musebridge")
        self._outlet  = StreamOutlet(info)

        # Print-friendly board name for logging
        board_name = {
            BoardIds.MUSE_2_BOARD.value:    "Muse 2",
            BoardIds.MUSE_S_BOARD.value:    "Muse S",
            BoardIds.SYNTHETIC_BOARD.value: "Synthetic",
        }.get(board_id, f"Board {board_id}")

        print(f"Board: {board_name}  |  Sample rate: {self._sample_rate}hz  |  Channels: {self._eeg_channels}")

    # =========================
    # LIVE LOOP ~ Main processing loop for hardware streaming.
    # Pulls data from the board buffer, computes band power, normalizes,
    # and sends over OSC. Runs at ~10 Hz in a background thread.
    # =========================
    def _live_loop(self):
        print("Starting, waiting for data window to load up...")

        if self.config.get("verbose"):
            _init_display(len(BANDS))

        while self._running:
            # Pull the most recent N samples from the board buffer
            data = self._board.get_current_board_data(self._window_size)
            n_samples = data.shape[1]

            # Wait until we have enough data to fill the full processing window
            if n_samples < self._window_size:
                time.sleep(0.1)
                continue

            # Compute raw band power for each frequency range and electrode
            raw = compute_bands_per_sensor(
                data, self._eeg_channels, self._channel_names,
                BAND_RANGES, self._sample_rate,
            )

            # Normalize every value using the shared per-band normalizer. Makes a dictionary to store each band, along with
            # average, with the normalized value.
            # Both mean keys ("alpha") and per-sensor keys ("alpha/AF7") are normalized against the same 30-second history
            # window, so they remain on a common scale, so FAA and temporal asymmetry are valid.
            # FAA and temporal asymmetry are only valid on MUSE device or any similarly configured EEG setup.
            state = {}
            for key, raw_val in raw.items():
                # Extract the band name from keys ("alpha/AF7" → "alpha", "alpha" → "alpha")
                band = key.split("/")[0]
                state[key]  = self._normalize_live(band, raw_val)

            # Send normalized band values over OSC and notify the GUI if registered
            self._send_state(state)

            # Run loop at ~10 Hz
            time.sleep(1.0 / 10)

    # =========================
    # OSC SEND ~ Sends normalized bands over OSC as /brain/<key> → value (0.0–1.0)
    # Also fires the GUI callback if one was registered via on_state(),
    # otherwise prints the terminal bar display in verbose/headless mode.
    # =========================
    def _send_state(self, state, row_label=""):
        for key, value in state.items():
            self._osc.send_message(f"/brain/{key}", float(value))

        # GUI mode hand off the state dict and let the widget render it
        if self._state_callback:
            self._state_callback(state)
        elif self.config.get("verbose"):
            print_state(state, row_label)

    # =========================
    # CSV FORMAT DETECTION ~ Reads just the header line to determine which app produced the CSV.
    # Falls back to the user-specified format if auto-detection fails.
    # Exits with an error if the format cannot be determined.
    # =========================
    def _detect_csv_format(self, path: str, fmt: str) -> str:
        if fmt != "auto":
            # User told us explicitly — trust them
            return fmt

        with open(path) as f:
            header = f.readline()

        if "Alpha_TP9" in header:
            print("  Detected format: muse-monitor")
            return "muse-monitor"
        elif "alpha_absolute_1" in header:
            print("  Detected format: muse-direct")
            return "muse-direct"
        else:
            raise ValueError(
                "Could not auto-detect CSV format. "
                "Set csv_format to 'muse-monitor' or 'muse-direct' in config."
            )

    # =========================
    # CSV: MUSE MONITOR ~ Loads a Muse Monitor CSV and replays it at a fixed rate,
    # emitting normalized band values over OSC in real time.
    # =========================
    def _run_csv_muse_monitor(self, path: str, rate: float = 10.0, loop: bool = False):
        print(f"\nMuse Monitor playback: {path}")
        rows = []

        # Read entire CSV into memory (row = dict of column → string)
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                rows.append(row)
        print(f"    Loaded {len(rows)} rows (~{len(rows)/rate:.0f}s at {rate}hz)")

        # Average a frequency band across electrodes (TP9, AF7, AF8, TP10).
        # Ignores missing/invalid values. Returns None if nothing usable.
        # NOTE: This functionality may be subject to change later
        def avg_band(row, band):
            vals = []
            for electrode in ["TP9", "AF7", "AF8", "TP10"]:
                try:
                    v = float(row[f"{band}_{electrode}"])
                    if not np.isnan(v):
                        vals.append(v)
                except (ValueError, KeyError):
                    pass
            return np.mean(vals) if vals else None

        # Determine if the headset signal quality is acceptable.
        # Lower HSI = better contact. Skip rows with poor average fit.
        def good_fit(row):
            scores = []
            for electrode in ["TP9", "AF7", "AF8", "TP10"]:
                try:
                    scores.append(float(row[f"HSI_{electrode}"]))
                except (ValueError, KeyError):
                    pass
            return np.mean(scores) <= 3.5 if scores else True

        # Build a normalizer seeded with the playback rate (not live sample rate)
        normalize = make_normalizer(rate)

        # Carry-forward for sparse rows. If a band is missing in a row, reuse the last valid value
        last = {b: 0.5 for b in BANDS}
        skipped = 0

        if self.config.get("verbose"):
            _init_display(len(BANDS))

        # Playback loop (optionally repeat forever)
        while self._running:
            for i, row in enumerate(rows):
                if not self._running:
                    break

                # Skip rows with poor signal quality
                if not good_fit(row):
                    skipped += 1
                    continue

                # Extract raw band values and average across sensors
                raw = {
                    "delta": avg_band(row, "Delta"),
                    "theta": avg_band(row, "Theta"),
                    "alpha": avg_band(row, "Alpha"),
                    "beta":  avg_band(row, "Beta"),
                    "gamma": avg_band(row, "Gamma"),
                }

                # Update carry-forward only where there was a real value this row
                for k, v in raw.items():
                    if v is not None:
                        last[k] = v

                # Normalize each band to 0-1 based on recent history
                bands = {k: normalize(k, last[k]) for k in BANDS}

                # Send to OSC and notify GUI / terminal
                self._send_state(bands, row_label=f"{i+1}/{len(rows)} skip={skipped}")

                # Maintain playback timing
                time.sleep(1.0 / rate)

            if not loop:
                break
            print("\n  Looping...")

        print(f"\nDone (skipped {skipped} bad-fit rows)")

    # =========================
    # CSV: MUSE DIRECT ~ Replays a Muse Direct CSV.
    # Different apps produce different CSV formats — this one uses *_relative_*
    # columns which are more consistently populated than absolute values.
    # Direct files are sparsely populated — carry-forward handles missing rows.
    # NOTE: Investigate why Muse Direct files are so sparse compared to Monitor.
    # =========================
    def _run_csv_muse_direct(self, path: str, rate: float = 10.0, loop: bool = False):
        print(f"\nMuse Direct playback: {path}")

        # Map each band to its 4 electrode columns.
        # alpha_relative_1..4 correspond to the 4 electrodes in order.
        COL_NAMES = {b: [f"{b}_relative_{i}" for i in range(1, 5)] for b in BANDS}

        # Read entire CSV into memory
        rows = []
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                rows.append(row)
        print(f"    Loaded {len(rows)} rows")

        # Average a set of columns, ignoring invalid or missing values
        def avg_cols(row, col_names):
            vals = []
            for c in col_names:
                try:
                    v = float(row[c])
                    if not np.isnan(v):
                        vals.append(v)
                except (ValueError, KeyError):
                    pass
            return np.mean(vals) if vals else None

        # Same signal quality check as monitor version, using precision columns
        def good_fit(row):
            scores = []
            for i in range(1, 5):
                try:
                    scores.append(float(row[f"hsi_precision_{i}"]))
                except (ValueError, KeyError):
                    pass
            return np.mean(scores) <= 3.5 if scores else True

        normalize = make_normalizer(rate)
        skipped = 0

        # Carry-forward holds the last known good values for sparse rows
        last = {b: 0.5 for b in BANDS}

        if self.config.get("verbose"):
            _init_display(len(BANDS))

        # Playback loop (optionally repeat forever)
        # NOTE: Replace while True pattern with a cleaner loop-control mechanism
        while self._running:
            for i, row in enumerate(rows):
                if not self._running:
                    break

                if not good_fit(row):
                    skipped += 1
                    continue

                # Update each band if new data exists, otherwise reuse last known value
                for band in BANDS:
                    v = avg_cols(row, COL_NAMES[band])
                    if v is not None:
                        last[band] = v

                # Normalize and send to OSC
                bands = {k: normalize(k, last[k]) for k in BANDS}
                self._send_state(bands, row_label=f"{i+1}/{len(rows)} skip={skipped}")

                # Maintain playback timing
                time.sleep(1.0 / rate)

            if not loop:
                break
            print("\n  Looping...")

        print(f"\nDone (skipped {skipped} bad-fit rows)")


# =========================
# CLI ENTRY POINT ~ Runs the pipeline directly from the terminal without the GUI.
# The GUI imports EEGPipeline and builds the config dict itself
# argparse only lives here so the script stays usable standalone for now.
# =========================
def main():

    parser = argparse.ArgumentParser(description="EEG Pipeline (headless)")
    parser.add_argument("--csv", type=str, default=None,
                        help="Path to a Muse Monitor or Muse Direct CSV for replay")
    parser.add_argument("--csv-format", choices=["auto", "muse-monitor", "muse-direct"], default="auto",
                        help="Choose format for CSV file (based on what app made the CSV)")
    parser.add_argument("--csv-rate", type=float, default=10.0,
                        help="Playback rate in Hz for CSV replay (default 10Hz for easy observation)")
    parser.add_argument("--csv-loop", action="store_true",
                        help="Loop CSV playback indefinitely")
    parser.add_argument("--synthetic", action="store_true",
                        help="Force synthetic board even if Muse is reachable (dev mode)")
    parser.add_argument("--verbose", action="store_true",
                        help="Prints useful information in a nice format")
    args = parser.parse_args()

    # Build the same config dict the GUI will build, just from argparse instead
    config = {
        "csv":        args.csv,
        "csv_format": args.csv_format.replace("-", "_"),
        "csv_rate":   args.csv_rate,
        "csv_loop":   args.csv_loop,
        "synthetic":  args.synthetic,
        "verbose":    args.verbose,
    }

    # Create pipeline object and run in daemon thread with .start()
    pipeline = EEGPipeline(config)

    try:
        pipeline.start()
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        pipeline.stop()


if __name__ == "__main__":
    main()