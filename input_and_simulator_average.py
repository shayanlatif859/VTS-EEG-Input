"""
EEG Streaming + Playback Pipeline

Features:
~ Live Muse (BrainFlow) streaming with synthetic fallback
~ CSV playback (Muse Monitor + Muse Direct formats)
~ Band power extraction (delta → gamma)
~ Rolling normalization (30s window)
~ OSC output for real-time applications
~ Optional terminal visualization
"""

import time
import numpy as np
from pylsl import StreamInfo, StreamOutlet
from pythonosc.udp_client import SimpleUDPClient
from brainflow.board_shim import BoardShim, BrainFlowInputParams, BoardIds
import csv
import argparse
from scipy.signal import butter, filtfilt

# =========================
# ARGPARSE ~ Adds arguments to be run with the script.
# =========================
parser = argparse.ArgumentParser()
parser.add_argument("--csv", type=str, default=None,
                    help="Path to a Muse Monitor or Muse Direct CSV for replay")
parser.add_argument("--csv-format", choices=["auto", "muse-monitor", "muse-direct"], default="auto",
                    help="Choose format for CSV file (based on what app made the CSV)")
parser.add_argument("--csv-rate", type=float, default=10.0,
                    help="Playback rate in Hz for CSV replay (default 10Hz for easy observation)")
parser.add_argument("--csv-loop", action="store_true",
                    help="Loop CSV playback indefinitely")
parser.add_argument("--synthetic",     action="store_true",
                    help="Force synthetic board even if Muse is reachable (dev mode)")
parser.add_argument("--verbose", action="store_true",
                    help="Prints useful information in a nice format")
args = parser.parse_args()

# =========================
# CONFIG ~ Sets a few global variables
# List BANDS is also set up here as the five frequencies
# The target IP and port are used to set up the client with SimpleUDPClient
# LSL (Lab Streaming Layer) stream used to be set here. Check just above the "band_power" function to see them now.
# =========================
OSC_IP = "127.0.0.1"
OSC_PORT = 9000

osc = SimpleUDPClient(OSC_IP, OSC_PORT)
BANDS = ["delta", "theta", "alpha", "beta", "gamma"]


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

def init_display(n_lines):
    print("\n" * (n_lines + 1))

# =========================
# NORMALIZER ~ Keeps a rolling 30s window per band and scales to 0-1 relative to the session's own range
# make_normalizer creates a history dictionary and a window length value, along with the normalize function using the two values
# Calling normalize stores a new value in history, removes the oldest value in history (if it's too long)
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
# OSC SEND ~ sends normalized bands over OSC as:
# /brain/<band> → value (0.0–1.0)
# =========================
def send_state(state, row_label=""):
    for key, value in state.items():
        osc.send_message(f"/brain/{key}", value)

    # Optional debug print of current state + playback position
    if args.verbose:
        print_state(state, row_label)

# =========================
# CSV: MUSE MONITOR ~ Loads a Muse Monitor CSV and replays it at a fixed rate,
# emitting normalized band values over OSC in real time. Comments for what it does are placed through the function.
# =========================
def run_csv_muse_monitor(path, rate=10.0, loop=False):
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

    # Call sliding-window normalizer (per band, ~30s window)
    normalize = make_normalizer(rate)

    # Carry-forward for any sparse rows. If a band is missing in a row, reuse the last valid value
    last = {b: 0.5 for b in BANDS}
    skipped = 0

    if args.verbose:
        init_display(len(BANDS))

    # Playback loop (optionally repeat forever)
    while True:
        for i, row in enumerate(rows):

            # Skip rows with poor signal quality using good_fit
            if not good_fit(row):
                skipped += 1
                continue

            # Extract raw band values and store as dictionary with avg_band processing each signal
            raw = {
                "delta": avg_band(row, "Delta"),
                "theta": avg_band(row, "Theta"),
                "alpha": avg_band(row, "Alpha"),
                "beta":  avg_band(row, "Beta"),
                "gamma": avg_band(row, "Gamma"),
            }

            # Update carry-forward only where there was a real value
            for k, v in raw.items():
                if v is not None:
                    last[k] = v

            # Normalize each band to 0–1 based on recent history
            bands = {k: normalize(k, last[k]) for k in BANDS}

            # Send to OSC + optional debug output
            send_state(bands, row_label=f"{i+1}/{len(rows)} skip={skipped}")

            # Maintain playback timing
            time.sleep(1.0 / rate)

        if not loop:
            break
        print("\n  Looping...")
    print(f"\nDone (skipped {skipped} bad-fit rows)")

# Replays a Muse Direct CSV. Different apps = different csv.
# Uses *_relative_* columns (more consistently populated than absolute values).
def run_csv_muse_direct(path, rate=10.0, loop=False):
    print(f"\nMuse Direct playback: {path}")

    # Map each band to its 4 electrode columns
    # alpha_relative_1..4 = cols 50-53, beta = 54-57, gamma = 58-61
    COL_NAMES = {b: [f"{b}_relative_{i}" for i in range(1, 5)] for b in BANDS}

    # Create list of rows found in the header of the csv file
    rows = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            rows.append(row)
    print(f"    Loaded {len(rows)} rows")

    # Average a set of columns, ignoring invalid/missing values
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

    # Same idea as monitor version, but using precision columns
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

    # This should hold the last known good values if the row is not filled
    # Common with direct CSV. Direct files are sparsely populated. Investigate why.
    last = {b: 0.5 for b in BANDS}

    if args.verbose:
        init_display(len(BANDS))

    # While True is generally not great programming practice. Find an alternative.
    while True:
        for i, row in enumerate(rows):
            if not good_fit(row):
                skipped += 1
                continue

            # Update each band if new data exists, otherwise reuse last
            for band in BANDS:
                v = avg_cols(row, COL_NAMES[band])
                if v is not None:
                    last[band] = v

            # Normalize and send to OSC with a debug output.
            bands = {k: normalize(k, last[k]) for k in BANDS}
            send_state(bands, row_label=f"{i+1}/{len(rows)} skip={skipped}")

            # Maintain playback timing
            time.sleep(1.0 / rate)

        if not loop:
            break
        print("\n  Looping...")
    print(f"\nDone")


# =========================
# CSV DISPATCHER ~ This simply determines if the muse-monitor or muse-direct CSV file is being used.
# =========================
if args.csv:
    fmt = args.csv_format
    if fmt == "auto":
        with open(args.csv) as f:
            header = f.readline()
        if "Alpha_TP9" in header:
            fmt = "muse-monitor"
        elif "alpha_absolute_1" in header:
            fmt = "muse-direct"
        else:
            print("Could not auto-detect CSV format. Use --csv-format muse-monitor or muse-direct")
            exit(1)
        print(f"  Detected format: {fmt}")

    if fmt == "muse-monitor":
        run_csv_muse_monitor(args.csv, rate=args.csv_rate, loop=args.csv_loop)
    else:
        run_csv_muse_direct(args.csv, rate=args.csv_rate, loop=args.csv_loop)
    exit(0)

# =========================
# MAIN LOOP ~ not done if provided with CSV
# =========================

# =========================
# DETECT DEVICE ~ This just connects the MUSE device, and uses a synthetic board as a fallback.
# The synthetic device is for development purposes without a MUSE headband. It simulates an EEG input.
# =========================
params = BrainFlowInputParams()
if args.synthetic:
    # Forced synthetic (no scan)
    print("Synthetic board (forced via --synthetic)")
    board    = BoardShim(BoardIds.SYNTHETIC_BOARD, params)
    board.prepare_session()
    board.start_stream()
    board_id = BoardIds.SYNTHETIC_BOARD

else:
    # Try live Muse, fall back to synthetic
    try:
        print("Connecting to Muse device...")
        board    = BoardShim(BoardIds.MUSE_2_BOARD, params)
        board.prepare_session()
        board.start_stream()
        board_id = BoardIds.MUSE_2_BOARD
        print("Connected.")
    except Exception as e:
        print(f"Muse not found ({e})\nFalling back to synthetic board (use --synthetic to skip BLE scan)")
        params   = BrainFlowInputParams()
        board    = BoardShim(BoardIds.SYNTHETIC_BOARD, params)
        board.prepare_session()
        board.start_stream()
        board_id = BoardIds.SYNTHETIC_BOARD

# =========================
# BAND POWER ~ This creates a butterworth bandpass filter.
# Global variables are obtained for sample rate, window size, EEG channels along with a new function to normalize
# Using the sample rate.
# =========================
SAMPLE_RATE = BoardShim.get_sampling_rate(board.get_board_id())
WINDOW_SIZE = SAMPLE_RATE * 4  # Provides 2-second window
EEG_CHANNELS = BoardShim.get_eeg_channels(board.get_board_id())
normalize_live = make_normalizer(SAMPLE_RATE)

# The stream and outlet are set up using LSL to be pushed
info = StreamInfo("EEG", "EEG", len(EEG_CHANNELS), SAMPLE_RATE, "float32", "musebridge")
outlet = StreamOutlet(info)

# Print what device is actually getting used. Poorly placed code, but I could not find other solutions.
board_id = board.get_board_id()
board_name = "Muse 2" if board_id == BoardIds.MUSE_2_BOARD.value else "Synthetic"
print(f"Board: {board_name}  |  Sample rate: {SAMPLE_RATE}hz  |  Channels: {EEG_CHANNELS}")

# Creates the nyquist frequency, applies the butterworth, and filters and returns a float
# Butterworth is designed to have a frequency response as flat as possible in the passband with no ripples
# Filtfilt applies the filter forwards and backwards, cancelling phase delay
# np.mean(filtered ** 2) calculates Power = amplitude ** 2. Averaging squared amplitude = mean power
def band_power(signal, low, high, sample_rate):  # [FIX 1]
    nyq = sample_rate / 2.0
    b, a = butter(4, [low / nyq, high / nyq], btype="band")
    filtered = filtfilt(b, a, signal)
    return float(np.mean(filtered ** 2))

# =========================
# COMPUTE BANDS ~ Takes raw EEG data and extracts how much of each frequency is
# present in the signal. It returns a dictionary of raw data for each band.
# The raw data is not normalized once it is given from this function.
# =========================
def compute_bands(data, channels, band_ranges, sample_rate):
    raw = {}
    #  Iterate over each band definition
    for band, (low, high) in band_ranges.items():
        powers = []
        #  Iterate over each electrode channel
        for ch in channels:
            ch_signal = data[ch, :].astype(np.float64)
            powers.append(band_power(ch_signal, low, high, sample_rate))
        raw[band] = float(np.mean(powers))
    return raw

# Sets the frequency ranges for each band. Discretize this properly.
BAND_RANGES = {
    "delta": (0.5,  4),
    "theta": (4,    8),
    "alpha": (8,   12),
    "beta":  (13,  30),
    "gamma": (30,  45),
}

if args.verbose:
    init_display(len(BANDS))

print("Starting, waiting for data window to load up...")

try:
    while True:
        # Pull the most recent N samples from the board buffer
        data = board.get_current_board_data(WINDOW_SIZE)
        n_samples = data.shape[1]

        # Wait until we have enough data to fill the processing window
        if n_samples < WINDOW_SIZE:
            time.sleep(0.1)
            continue

        # Compute raw band power for each frequency range
        raw = compute_bands(data, EEG_CHANNELS, BAND_RANGES, SAMPLE_RATE)

        # Feed raw values to the normalizer for each band in BANDS and creates a dictionary called bands
        bands = {b: normalize_live(b, raw[b]) for b in BANDS}

        # Collapse all EEG channels into a single averaged signal for LSL
        # This method was used. It used to look like lsl_sample = [float(np.mean(data[ch, :])) for ch in EEG_CHANNELS[:4]]
        # Also note that it was sliced to four channels. Now, EEG channels is set with the board. This may be inaccurate
        # With the synthetic board, which indexes channels 1-8 at actual EEG sensors.
        lsl_sample = [float(np.mean(data[ch, :])) for ch in EEG_CHANNELS]
        outlet.push_sample(lsl_sample)

        # Send normalized band values over OSC
        send_state(bands, row_label="live")

        # Run loop at ~10 Hz
        time.sleep(1.0 / 10)

except KeyboardInterrupt:
    print("\nStopping...")
finally:
    # Shutdown BrainFlow session
    board.stop_stream()
    board.release_session()