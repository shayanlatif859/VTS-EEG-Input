"""
VTS Bridge
Called like this from the GUI:
    bridge = VTSBridge(rules, osc_port=9000, vts_url="ws://127.0.0.1:8001")

Differences from last bridge (see in Github history):
    bridge.on_brain_update(callback)   # optional ~ GUI calls this for live display
    bridge.start()                     # connect, bind port, launch threads
    bridge.stop()                      # clean shutdown
    bridge.reload_rules(new_rules)     # hot-swap rules without restarting
    bridge.get_brain_snapshot()        # returns a copy of current brain state

Headless/CLI entry point at the bottom: `python bridge.py`

~NOTE~ We should be adding type hints from now on for clarity of code.
Things like def function(int_value: int="int",string_value: string="str") -> float:
"""

import json
import threading
import time
import socket

import websocket
from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import BlockingOSCUDPServer


# =========================
# Module-level helper functions and variables
# =========================

VALID_BANDS = {"delta", "theta", "alpha", "beta", "gamma"}
BAR_WIDTH   = 30


def _port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return False
        except OSError:
            return True


def _bar(value, width = BAR_WIDTH):
    filled = int(value * width)
    return "█" * filled + "░" * (width - filled)


# =========================
# VTSBridge ~ Owns the full lifecycle of the VTube Studio connection and OSC server.
# A lot of the states here used to be global variables, they now are set with this class
# =========================
class VTSBridge:
    # Configure class variables
    DEFAULT_VTS_URL  = "ws://127.0.0.1:8001"
    DEFAULT_OSC_PORT = 9000
    HOTKEY_COOLDOWN  = 3.0

    def __init__(
        self, rules, osc_port = DEFAULT_OSC_PORT, vts_url = DEFAULT_VTS_URL):
        # Configure instance variable
        self.rules = rules
        self.osc_port = osc_port
        self.vts_url = vts_url

        # Runtime state, now set as instance variables
        # Notice the threading locks. This prevents multiple threads accessing the same resource at once.
        # This prevents a race condition where the data gets corrupted with multiple thread access.
        self._ws = None
        self._ws_lock = threading.Lock()
        self._brain = {}
        self._brain_lock: threading.Lock = threading.Lock()
        self._previous_active_rules = set()
        self._last_hotkey_time = {}

        # Threading
        self._running = False
        self._server = None
        self._threads = []

        # Optional GUI callback ~ called with a brain-state snapshot each tick
        self._brain_callback = None

        # PENDING QUERIES ~ Slots for one-shot VTS request/response pairs. Keyed by requestID and
        # The receiver thread checks incoming messages against this dict and deposits results so _query_vts() can return them.
        # =========================
        self._pending_queries = {}
        self._pending_lock = threading.Lock()

    # =========================
    # Register a callback invoked every interpreter tick (~10 Hz).
    # Signature: callback(brain: dict, active_rules: list, outputs: list)
    # The GUI uses this to update its live visualizer without polling.
    # =========================
    def on_brain_update(self, callback):
        self._brain_callback = callback

    # =========================
    # Connect to VTS, bind the OSC port, and launch background threads.
    # Raises RuntimeError if the OSC port is already occupied.
    # =========================
    def start(self):
        if _port_in_use(self.osc_port):
            raise RuntimeError(
                f"Port {self.osc_port} is already in use.\n"
                f"Kill the existing process first:\n"
                f"  lsof -ti :{self.osc_port} | xargs kill -9"
            )

        self._running = True
        self._connect_ws()

        # OSC server in its own thread so it doesn't block start()
        dispatcher = self._make_dispatcher()
        self._server = BlockingOSCUDPServer(("127.0.0.1", self.osc_port), dispatcher)

        self._launch(self._server.serve_forever, name="osc-server")
        self._launch(self._interpreter_loop, name="interpreter")
        self._launch(self._receiver, name="ws-receiver")

    # =========================
    # Cleanly shut down all threads, the OSC server, and the WebSocket.
    # Safe to call even if start() was never called.
    # =========================
    def stop(self):

        self._running = False

        if self._server:
            self._server.shutdown()
            self._server = None

        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None

        # Wait for threads to exit (they are daemons, so not strictly required,
        # but joining gives the GUI a clean signal that shutdown is complete)
        for t in self._threads:
            t.join(timeout=3)
        self._threads.clear()

    # =========================
    # Swap the rule set without restarting.
    # The GUI calls this immediately after the user saves edits.
    # Thread-safe: the interpreter loop reads self.rules each tick.
    # =========================
    def reload_rules(self, new_rules):
        self.rules = new_rules

    # =========================
    # Return a shallow copy of the current brain state with a dictionary.
    # =========================
    def get_brain_snapshot(self):
        with self._brain_lock:
            return dict(self._brain)

    # Send a one-shot query to VTS and return the response data dict. Because the receiver thread has the WS.read loop now,
    # threading.Event() must be registered so that matching responses can be deposited into the slot.
    # This was made primarily to address deadlocking when accessing rules from VTS (old locking rules).
    # Look at the type hints. Do this from now on for clarity.
    def _query_vts(self, request_id: str, message_type: str, data: dict,
                   timeout: float = 5.0) -> dict | None:

        # Register a pending slot the receiver thread will fill in
        event = threading.Event()
        result = [None]

        with self._pending_lock:
            self._pending_queries[request_id] = (event, result)

        msg = {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "requestID": request_id,
            "messageType": message_type,
            "data": data,
        }

        try:
            # Only lock for send, recv is handled by the receiver thread
            with self._ws_lock:
                self._ws.send(json.dumps(msg))
        except Exception as e:
            print(f"VTS query send error: {e}")
            with self._pending_lock:
                self._pending_queries.pop(request_id, None)
            return None

        # Wait for the receiver thread to deposit the response
        if not event.wait(timeout=timeout):
            print(f"VTS query timed out: {request_id}")
            with self._pending_lock:
                self._pending_queries.pop(request_id, None)
            return None

        return result[0]

    # =========================
    # Query VTS for available expressions. Returns list of dicts.
    # =========================
    def list_expressions(self):
        resp = self._query_vts(
            request_id="list_expr",
            message_type="ExpressionStateRequest",
            data={"details": True, "expressionFile": ""},
        )
        exp_response = resp.get("expressions", []) if resp else []
        return exp_response

    # =========================
    # Query VTS for available hotkeys. Returns list of dicts.
    # =========================
    def list_hotkeys(self):
        resp = self._query_vts(
            request_id="list_hotkeys",
            message_type="HotkeysInCurrentModelRequest",
            data={},
        )
        hot_response = resp.get("availableHotkeys", []) if resp else []
        return hot_response

    # =========================
    # CONNECT TO VTS ~ This part connects to the VTube Studio.
    # Requests authentication token → sends confirmation using token → receives authorisation result
    # Retries every 3 seconds on failure, using same behavior as before
    # =========================
    def _connect_ws(self):

        auth_request = {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "requestID": "auth_token",
            "messageType":"AuthenticationTokenRequest",
            "data": {
                "pluginName": "MuseBridge",
                "pluginDeveloper": "You",
            },
        }

        while True:
            try:
                print("Connecting to VTS...")
                ws = websocket.WebSocket()
                ws.connect(self.vts_url)

                # Request token
                ws.send(json.dumps(auth_request))
                token = json.loads(ws.recv())["data"]["authenticationToken"]

                # Confirm authentication
                auth_confirm = {
                    "apiName": "VTubeStudioPublicAPI",
                    "apiVersion": "1.0",
                    "requestID": "auth_confirm",
                    "messageType":"AuthenticationRequest",
                    "data": {
                        "pluginName": "MuseBridge",
                        "pluginDeveloper": "You",
                        "authenticationToken": token,
                    },
                }
                ws.send(json.dumps(auth_confirm))
                print("Auth:", ws.recv())

                self._ws = ws
                print("Connected to VTS.")
                return

            except Exception as e:
                print(f"VTS connection failed: {e}, retrying in 3s...")
                time.sleep(3)

    # =========================
    # START OSC SERVER ~ establish a websocket connection to VTS > run multithreading > receive and dispatch to OSC.
    # OSC is a communication protocol that allows for live data transfer between a client and a server (local server).
    # =========================

    # Dispatcher creator makes /brain/<band> messages in OSC format
    def _make_dispatcher(self):
        dispatcher = Dispatcher()
        dispatcher.map("/brain/*", self._handle_osc)
        return dispatcher


    # =========================
    # OSC HANDLER ~ Appends to brain dictionary, with updated handling for spacial formats
    # Receive /brain/<band> or /brain/<band>/<sensor> messages and updates self._brain with derived asymmetry indices.
    # Unchanged logic from the original handle_osc, now a method.
    # =========================
    def _handle_osc(self, address, *args):
        parts = [p for p in address.split("/") if p]     # e. g. : ["brain", "alpha"] or ["brain", "alpha", "AF7"]

        if len(parts) < 2 or parts[0] != "brain":
            return

        band = parts[1]
        # /brain
        if band not in VALID_BANDS:
            return

        value = float(args[0])

        with self._brain_lock:
            if len(parts) == 2:
                # /brain/band  →  mean key
                self._brain[band] = value

            elif len(parts) == 3:
                # /brain/band/sensor  →  per-sensor key + update mean
                sensor = parts[2]
                self._brain[f"{band}/{sensor}"] = value

                # Recompute mean from all sensors seen so far
                sensor_keys = [f"{band}/{s}" for s in ("AF7", "AF8", "TP9", "TP10")]
                present = [self._brain[k] for k in sensor_keys if k in self._brain]
                if present:
                    self._brain[band] = sum(present) / len(present)

                self._update_derived()

    # Recompute FAA and temporal alpha asymmetry. Must be called inside self._brain_lock.
    def _update_derived(self):
        af7  = self._brain.get("alpha/AF7")
        af8  = self._brain.get("alpha/AF8")
        tp9  = self._brain.get("alpha/TP9")
        tp10 = self._brain.get("alpha/TP10")

        if af7 is not None and af8 is not None:
            self._brain["faa"] = af7 - af8

        if tp9 is not None and tp10 is not None:
            self._brain["taa"] = tp9 - tp10

    # =========================
    # WEBSOCKET RECEIVER ~ Keeps websocket alive by reading from it, reconnecting on failure.
    # Runs as a daemon thread.
    # =========================

    def _receiver(self):
        while self._running:
            try:
                raw = self._ws.recv()  # blocking read
                msg = json.loads(raw)

                # =========================
                # PENDING QUERY CHECK ~ If this response matches a registered request ID, deposit it into the slot and signal the waiter.
                # This is how _query_vts() gets its response without holding the ws_lock across a blocking recv().
                # =========================
                req_id = msg.get("requestID")
                if req_id:
                    with self._pending_lock:
                        slot = self._pending_queries.pop(req_id, None)
                    if slot:
                        event, result = slot
                        result[0] = msg.get("data", {})
                        event.set()
                        # Doesn't process further if it was a query response
                        continue

            except Exception as e:
                if not self._running:
                    break  # clean shutdown, not an error
                print(f"WebSocket lost: {e} — reconnecting")
                try:
                    self._ws.close()
                except Exception:
                    pass
                self._connect_ws()
    # =========================
    # DISCRETE INTERPRETER ~ This detects transitions in between settings/states.
    # Works at ~10 Hz, detecting with just_entered and just_exited and dispatches to VTS.
    # Runs as a Daemon thread.
    # =========================

    def _interpreter_loop(self):

        while self._running:
            brain = self.get_brain_snapshot()

            # Wait until there is more brain data
            if not brain:
                time.sleep(0.05)
                continue

            try:
                # Evaluate rules > outputs + active rule list
                outputs, active = self._evaluate_rules(brain)
                active_set      = set(active)

                # Find rules that just turned on or just turned off
                just_entered = active_set - self._previous_active_rules
                just_exited  = self._previous_active_rules - active_set

                # Apply outputs (parameters, expressions, hotkeys)
                self._apply_outputs(outputs, brain, just_entered, just_exited)

                # Notify GUI (or any subscriber) with current state
                if self._brain_callback:
                    self._brain_callback(brain, active, outputs)
                else:
                    # Headless: print dashboard to terminal
                    lines = self._render_dashboard(brain, active, outputs)
                    self._print_dashboard(lines)

                # Update state for next frame
                self._previous_active_rules = active_set

            except Exception as e:
                print(f"Interpreter error: {e}")

            time.sleep(0.1)

    # =========================
    # CHECK CONDITION ~ This will return a boolean based on the brain library and conditions in JSON
    # NOTE: check_condition, evaluate_rules, and apply_outputs are all related to the JSON file
    # It now reads an optional "sensor" field from the condition.
    # If absent, it defaults to the mean key (fully backward-compatible).
    #
    # Example condition objects:
    #   {"band": "alpha", "op": ">", "value": 0.3}            → reads brain["alpha"]
    #   {"band": "alpha", "sensor": "AF7", "op": ">", "value": 0.3} → reads brain["alpha/AF7"]
    #   {"band": "faa",   "op": "<", "value": -0.1}            → reads brain["faa"]
    # =========================

    def _check_condition(self, cond, brain):
        band = cond["band"]
        op = cond["op"]
        value = cond["value"]
        sensor = cond.get("sensor")

        key = f"{band}/{sensor}" if sensor and sensor != "mean" else band
        x = brain.get(key, 0.5)

        # Build the lookup key
        if op == "<":
            return x < value
        if op == ">":
            return x > value
        if op == "<=":
            return x <= value
        if op == ">=":
            return x >= value
        if op == "==":
            return x == value
        return False

    # =========================
    # EVALUATE RULES ~ Return a tuple of triggered outputs ([list, list]) based on if all the booleans return true
    # For each condition given the brain library (for condition in conditions)
    # Also gives us a list of active rules/names of condition
    # =========================
    def _evaluate_rules(self, brain):
        """Return (triggered_outputs, active_rule_names)."""
        triggered_outputs = []
        active_rules      = []

        for rule in self.rules:
            if all(self._check_condition(c, brain) for c in rule["conditions"]):
                active_rules.append(rule["name"])
                triggered_outputs.extend(rule["outputs"])

        return triggered_outputs, active_rules

    # =========================
    # OUTPUT DISPATCH ~ Determines the right output from the JSON file, any value+source+scale, and sends to VTS
    # Also acts as a dispatcher for parameter, expression, item
    # Now reads an optional "sensor" field on parameter outputs. If present, sources the value from brain["beta/AF8"] etc.
    # If absent, falls back to brain["beta"] (the mean).
    # =========================

    def _apply_outputs(self, outputs, brain, just_entered, just_exited):
        # Build a map from output object id → rule name for hotkey/expression gating
        rule_output_map = {
            id(out): rule["name"]
            for rule in self.rules
            for out in rule["outputs"]
        }

        for out in outputs:
            out_type = out.get("type", "parameter")
            rule_name = rule_output_map.get(id(out), "")

            if out_type == "parameter":
                if "value" in out:
                    value = float(out["value"])
                elif "source" in out:
                    sensor = out.get("sensor")
                    key = f"{out['source']}/{sensor}" if sensor and sensor != "mean" else out["source"]
                    raw = brain.get(key, 0.5)
                    value = (raw - 0.5) * out.get("scale", 1.0) + out.get("offset", 0.0)
                else:
                    continue
                self._send_parameter(out["param"], value)

            elif out_type == "expression":
                if rule_name in just_entered:
                    self._send_expression(out["expression"], active=True)

            elif out_type == "hotkey":
                if rule_name in just_entered:
                    hid = out["hotkey_id"]
                    now = time.time()
                    cooldown = out.get("cooldown", self.HOTKEY_COOLDOWN)
                    if now - self._last_hotkey_time.get(hid, 0) >= cooldown:
                        self._send_hotkey(hid)
                        self._last_hotkey_time[hid] = now

            else:
                print(f"Unknown output type: {out_type} — skipping")

        # Deactivate expressions for rules that just exited
        for rule in self.rules:
            if rule["name"] in just_exited:
                for out in rule["outputs"]:
                    if out.get("type") == "expression":
                        self._send_expression(out["expression"], active=False)

    # =========================
    # SEND FUNCTIONS ~ Here, there are functions for the actual movement of the model.
    # Valid inputs include parameters, expressions, and hotkeys (arguably the most useful one)
    # _ws_send shares send wrapper with locking + reconnect handling
    # Inspect the potential to add features such as color changes.
    # =========================

    def _send_parameter(self, param_id, value):
        self._ws_send({
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "requestID": "set_param",
            "messageType":"InjectParameterDataRequest",
            "data": {
                "parameterValues": [{"id": param_id, "value": float(value)}]
            },
        })

    def _send_expression(self, expression_file, active = True):
        self._ws_send({
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "requestID": "set_expression",
            "messageType":"ExpressionActivationRequest",
            "data": {"expressionFile": expression_file, "active": active},
        })

    def _send_hotkey(self, hotkey_id):
        self._ws_send({
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "requestID": "trigger_hotkey",
            "messageType":"HotkeyTriggerRequest",
            "data": {"hotkeyID": hotkey_id},
        })

    # Thread-safe WebSocket send with reconnect on failure
    def _ws_send(self, msg):
        if self._ws is None:
            return
        try:
            with self._ws_lock:
                self._ws.send(json.dumps(msg))
        except Exception as e:
            print(f"VTS send error: {e}")
            try:
                self._ws.close()
            except Exception:
                pass
            # Reconnect is handled by _receiver; don't block here

    # =========================
    # Terminal dashboard print (headless mode only)
    # =========================

    def _render_dashboard(self, brain, active_rules, outputs):
        lines = ["Brain Activity", "──────────────"]

        for band in ["delta", "theta", "alpha", "beta", "gamma"]:
            v = brain.get(band, 0.0)
            lines.append(f"{band:<6} [{_bar(v)}] {v:.2f}")
            for sensor in ["AF7", "AF8", "TP9", "TP10"]:
                key = f"{band}/{sensor}"
                if key in brain:
                    sv = brain[key]
                    lines.append(f"       [{sensor}] [{_bar(sv)}] {sv:.2f}")

        lines += ["", "Active States:"]
        lines += [f"  • {r}" for r in active_rules] or ["  • neutral"]

        lines.append("Outputs:")
        if outputs:
            for o in outputs:
                out_type = o.get("type", "parameter")
                if out_type == "parameter":
                    if "value" in o:
                        dv = float(o["value"])
                    elif "source" in o:
                        raw = brain.get(o["source"], 0.5)
                        dv = (raw - 0.5) * o.get("scale", 1.0) + o.get("offset", 0.0)
                    else:
                        dv = 0.0
                    lines.append(f"  [param]   {o.get('param', '?'):<20} = {dv:.2f}")
                elif out_type == "expression":
                    state = "ON" if o.get("active", True) else "OFF"
                    lines.append(f"  [expr]    {o.get('expression', '?')} → {state}")
                elif out_type == "hotkey":
                    lines.append(f"  [hotkey]  {o.get('hotkey_id', '?')}")
        else:
            lines.append("  (none)")

        return lines

    def _print_dashboard(self, lines: list[str]):
        # move to top, clear screen
        print("\033[H\033[J", end="")
        print("\n".join(lines))

    # =========================
    # Threading helper ~ Starts a daemon thread and tracks it for clean shutdown.
    # =========================

    def _launch(self, target, name: str):
        t = threading.Thread(target=target, name=name, daemon=True)
        t.start()
        self._threads.append(t)


# =========================
# Headless CLI entry point
# =========================

def main():
    import argparse

    parser = argparse.ArgumentParser(description="VTS Bridge (headless)")
    parser.add_argument("--rules",    default="rules.json", help="Path to rules JSON file")
    parser.add_argument("--osc-port", type=int, default=9000)
    parser.add_argument("--vts-url",  default="ws://127.0.0.1:8001")
    args = parser.parse_args()

    with open(args.rules) as f:
        rules = json.load(f)["rules"]

    bridge = VTSBridge(rules, osc_port=args.osc_port, vts_url=args.vts_url)

    # Print available VTS assets before starting the loop
    bridge._connect_ws()
    for e in bridge.list_expressions():
        print(f"  Expression: {e['file']}  active={e['active']}")
    for h in bridge.list_hotkeys():
        print(f"  Hotkey: {h['hotkeyID']}  name={h['name']}  type={h['type']}")

    print("\033[2J")    # clear terminal before dashboard

    try:
        bridge.start()
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        bridge.stop()


if __name__ == "__main__":
    main()