"""
VTS Bridge
Features:
~ Check for port in use
~ Configuration of OSC server
~ Handling of various parameters/hotkeys/changes
"""

import json
import websocket
from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import BlockingOSCUDPServer
import threading
import time
import socket

# =========================
# CONFIG ~ Creates VTube Studio websocket, port, and loads rules from JSON
# =========================
VTS_URL = "ws://127.0.0.1:8001"
OSC_PORT = 9000

with open("rules.json") as f:
    RULES = json.load(f)["rules"]

# =========================
# CHECK PORT ~ Returns an error message if the port is already in use
# If you get this message, kill any process using the port
# =========================
def port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return False
        except OSError:
            return True

if port_in_use(OSC_PORT):
    print(f"Port {OSC_PORT} is already in use. Kill the existing process first:")
    print(f"  lsof -ti :{OSC_PORT} | xargs kill -9")
    exit(1)

# =========================
# CONNECT TO VTS ~ This part connects to the VTube Studio.
# Requests authentication token > sends confirmation using token > receives authorisation result
# =========================

ws = None
ws_lock = threading.Lock()

def connect_ws():
    global ws

    while True:
        try:
            print("Connecting to VTS...")
            ws = websocket.WebSocket()
            ws.connect(VTS_URL)

            # Auth request
            ws.send(json.dumps(auth_request))
            response = json.loads(ws.recv())
            token = response["data"]["authenticationToken"]

            # Auth confirm
            auth_confirm = {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "requestID": "auth_confirm",
                "messageType": "AuthenticationRequest",
                "data": {
                    "pluginName": "MuseBridge",
                    "pluginDeveloper": "You",
                    "authenticationToken": token
                }
            }

            auth_confirm["data"]["authenticationToken"] = token
            ws.send(json.dumps(auth_confirm))
            print("Auth:", ws.recv())

            print("Connected to VTS")
            return

        except Exception as e:
            print("Connect failed:", e)
            time.sleep(3)

# Request authentication token
auth_request = {
    "apiName": "VTubeStudioPublicAPI",
    "apiVersion": "1.0",
    "requestID": "auth_token",
    "messageType": "AuthenticationTokenRequest",
    "data": {
        "pluginName": "MuseBridge",
        "pluginDeveloper": "You"
    }
}

# =========================
# SEND FUNCTIONS ~ Here, there are functions for the actual movement of the modle.
# Valid inputs include parameters, expressions, and hotkeys (arguably the most useful one)
# _ws_send shares send wrapper with locking + reconnect handling
# Inspect the potential to add features such as color changes.
# =========================
def send_parameter(param_id, value):
    msg = {
        "apiName": "VTubeStudioPublicAPI",
        "apiVersion": "1.0",
        "requestID": "set_param",
        "messageType": "InjectParameterDataRequest",
        "data": {
            "parameterValues": [
                {
                    "id": param_id,
                    "value": float(value)
                }
            ]
        }
    }
    _ws_send(msg)

def send_expression(expression_file, active=True):
    msg = {
        "apiName": "VTubeStudioPublicAPI",
        "apiVersion": "1.0",
        "requestID": "set_expression",
        "messageType": "ExpressionActivationRequest",
        "data": {
            "expressionFile": expression_file,
            "active": active
        }
    }
    _ws_send(msg)

def send_hotkey(hotkey_id):
    msg = {
        "apiName": "VTubeStudioPublicAPI",
        "apiVersion": "1.0",
        "requestID": "trigger_hotkey",
        "messageType": "HotkeyTriggerRequest",
        "data": {
            "hotkeyID": hotkey_id
        }
    }
    _ws_send(msg)

# Shared send with threading lock
def _ws_send(msg):
    global ws

    # Drop message if not connected
    if ws is None:
        return
    try:
        # Prevent concurrent writes to WebSocket
        with ws_lock:
            ws.send(json.dumps(msg))
    except Exception as e:
        print(f"VTS error: {e}")

        # Force reconnect on failure
        try:
            ws.close()
        except:
            pass

# =========================
# OSC HANDLER ~ Creates dictionary for only valid bands, then appends to a dictionary called brain
# brain is going to contain values given in args for /brain/
# =========================
VALID_BANDS = {"delta", "theta", "alpha", "beta", "gamma"}
brain = {}
def handle_osc(address, *args):
    band = address.split("/")[-1]

    if band not in VALID_BANDS:
        return

    value = float(args[0])

    brain[band] = value

# =========================
# DISCRETE INTERPRETER ~ This just detects transitions in between settings/states.
# =========================
previous_active_rules = set()
def interpreter_loop():
    global previous_active_rules

    while True:
        # Wait until there is more brain data
        if not brain:
            time.sleep(0.05)
            continue

        try:
            # Evaluate rules > outputs + active rule list
            outputs, active = evaluate_rules(brain)
            active_set = set(active)

            # Find rules that just turned on or just turned off
            just_entered = active_set - previous_active_rules
            just_exited  = previous_active_rules - active_set

            # Apply outputs (parameters, expressions, hotkeys)
            apply_outputs(outputs, brain, just_entered, just_exited)

            # Render + print debug dashboard
            lines = render_dashboard(brain, active, outputs)
            print_dashboard(lines)

            # Update state for next frame
            previous_active_rules = active_set

        except Exception as e:
            print(f"Interpreter error: {e}")

        # ~10 Hz update loop
        time.sleep(0.1)

# =========================
# RECEIVE WS ~ This one is used in threading, useful for Daemon threading.
# =========================
def receiver():
    global ws
    while True:
        try:
            # Blocking read from VTS (keeps connection alive)
            msg = ws.recv()

        except Exception as e:
            print("Socket lost:", e)

            # Clean up broken connection
            try:
                ws.close()
            except:
                pass

            # Reconnect automatically
            connect_ws()

# =========================
# CHECK CONDITION ~ This will return a boolean based on the brain library and conditions in JSON
# NOTE: check_condition, evaluate_rules, and apply_outputs are all related to the JSON file
# This JSON will soon be easily editable by the user, once I learn PyQt.
# =========================
def check_condition(cond, brain):
    band = cond["band"]
    op = cond["op"]
    value = cond["value"]

    x = brain.get(band, 0.5)

    if op == "<":
        return x < value
    elif op == ">":
        return x > value
    elif op == "<=":
        return x <= value
    elif op == ">=":
        return x >= value
    elif op == "==":
        return x == value

    return False

# =========================
# EVALUATE RULES ~ Return a list of triggered outputs based on if all the booleans return true
# For each condition given the brain library (for condition in conditions)
# Also gives us a list of active rules/names of condition
# =========================
def evaluate_rules(brain):
    triggered_outputs = []
    active_rules = []
    for rule in RULES:
        if all(check_condition(condition, brain) for condition in rule["conditions"]):
            active_rules.append(rule["name"])
            for output in rule["outputs"]:
                triggered_outputs.append(output)

    return triggered_outputs, active_rules

# =========================
# APPLY OUTPUTS ~ Determines the right output from the JSON file, any value+source+scale, and sends to VTS
# Also acts as a dispatcher for parameter, expression, item
# =========================
last_hotkey_time = {}  # hotkey_id -> timestamp
HOTKEY_COOLDOWN = 3.0


def apply_outputs(outputs, brain, just_entered=None, just_exited=None):
    if just_entered is None: just_entered = set()
    if just_exited  is None: just_exited  = set()

    # Build a map of which rule each output belongs to
    # so we can know if it just entered/exited
    rule_output_map = {}
    for rule in RULES:
        for out in rule["outputs"]:
            rule_output_map[id(out)] = rule["name"]

    for out in outputs:
        out_type = out.get("type", "parameter")
        rule_name = rule_output_map.get(id(out), "")

        # Parameters are continuous and must be handled differently
        if out_type == "parameter":

            # Parameters update every frame continuously
            if "value" in out:
                # Static value
                value = float(out["value"])
            elif "source" in out:
                # Derived from brain signal... still experimental. Inspect for brain signal accuracy.
                raw = brain.get(out["source"], 0.5)
                scale = out.get("scale", 1.0)
                offset = out.get("offset", 0.0)

                # Centered around 0.5 > remap
                value = (raw - 0.5) * scale + offset
            else:
                continue
            send_parameter(out["param"], value)

        # Expression fired only when the rule transitions, not every frame
        elif out_type == "expression":
            if rule_name in just_entered:
                send_expression(out["expression"], active=True)
            # Deactivation is handled below via just_exited

        # Hotkey fired only once on entry
        elif out_type == "hotkey":

            if rule_name in just_entered:
                hid = out["hotkey_id"]
                now = time.time()
                cooldown = out.get("cooldown", HOTKEY_COOLDOWN)
                if now - last_hotkey_time.get(hid, 0) >= cooldown:
                    send_hotkey(hid)
                    last_hotkey_time[hid] = now

        else:
            print(f"Unknown output type: {out_type} ~ skipping")

    # Deactivate expressions belonging to rules that just exited
    for rule in RULES:
        if rule["name"] in just_exited:
            for out in rule["outputs"]:
                if out.get("type") == "expression":
                    send_expression(out["expression"], active=False)

# =========================
# PRINT ~ Better way to print everything rather than spamming terminal.
# =========================
def render_dashboard(brain, active_rules, outputs):
    lines = []
    lines.append("Brain Activity")
    lines.append("──────────────")

    for band in ["delta", "theta", "alpha", "beta", "gamma"]:
        v = brain.get(band, 0.0)
        lines.append(f"{band:<6} [{bar(v)}] {v:.2f}")

    lines.append("")
    lines.append("Active States:")
    if active_rules:
        for r in active_rules:
            lines.append(f"  • {r}")
    else:
        lines.append("  • neutral")

    lines.append("Outputs:")
    if outputs:
        for o in outputs:
            out_type = o.get("type", "parameter")
            if out_type == "parameter":
                if "value" in o:
                    display_val = float(o["value"])
                elif "source" in o:
                    raw = brain.get(o["source"], 0.5)
                    display_val = (raw - 0.5) * o.get("scale", 1.0) + o.get("offset", 0.0)
                else:
                    display_val = 0.0
                lines.append(f"  [param]   {o.get('param', '?'):<20} = {display_val:.2f}")
            elif out_type == "expression":
                state = "ON" if o.get("active", True) else "OFF"
                lines.append(f"  [expr]    {o.get('expression','?')} → {state}")
            elif out_type == "hotkey":
                lines.append(f"  [hotkey]  {o.get('hotkey_id','?')}")
    else:
        lines.append("  (none)")

    return lines

# =========================
# BAR ~ Bar function found in the simulator script + more printing functions
# =========================
BAR_WIDTH = 30
def bar(value, width=BAR_WIDTH):
    filled = int(value * width)
    return "█" * filled + "░" * (width - filled)

# Moves cursor up to prevent spam
def print_dashboard(lines):
    print("\033[H", end="")
    # Clears screen
    print("\033[J", end="")
    for line in lines:
        print(line)

# This is supposed to clear the screen
def init_dashboard():
    print("\033[2J")


# =========================
# START OSC SERVER ~ establish a websocket connection to VTS > run multithreading > receive and dispatch to OSC
# =========================

# Create multiple threads
connect_ws()

# Print lists of available VTS assets
def list_expressions():
    global ws
    msg = {
        "apiName": "VTubeStudioPublicAPI",
        "apiVersion": "1.0",
        "requestID": "list_expr",
        "messageType": "ExpressionStateRequest",
        "data": {"details": True, "expressionFile": ""}
    }
    with ws_lock:
        ws.send(json.dumps(msg))
        resp = json.loads(ws.recv())
    for e in resp["data"].get("expressions", []):
        print(f"  Expression: {e['file']}  active={e['active']}")

def list_hotkeys():
    global ws
    msg = {
        "apiName": "VTubeStudioPublicAPI",
        "apiVersion": "1.0",
        "requestID": "list_hotkeys",
        "messageType": "HotkeysInCurrentModelRequest",
        "data": {}
    }
    with ws_lock:
        ws.send(json.dumps(msg))
        resp = json.loads(ws.recv())
    for h in resp["data"].get("availableHotkeys", []):
        print(f"  Hotkey: {h['hotkeyID']}  name={h['name']}  type={h['type']}")

# Inspect available VTS controls
list_expressions()
list_hotkeys()

# Initialize dashboard UI
init_dashboard()

# Start background threads
threading.Thread(target=interpreter_loop, daemon=True).start()
threading.Thread(target=receiver, daemon=True).start()

# OSC server: receives /brain/<band> messages
dispatcher = Dispatcher()
dispatcher.map("/brain/*", handle_osc)

server = BlockingOSCUDPServer(("127.0.0.1", OSC_PORT), dispatcher)

print("Listening for OSC...")
server.serve_forever()


