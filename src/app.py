import io
import json
import os
import re
import tempfile
import time
from collections import defaultdict, deque
from urllib.parse import unquote

import dash
import matplotlib
import numpy as np
import paho.mqtt.client as mqtt
from dash import dcc, html
from dash.dependencies import Input, Output, State
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer

matplotlib.use("Agg")
import matplotlib.pyplot as plt

# =========================
# THEME (light + dark, presentation-friendly)
# =========================
LIGHT_THEME = {
    "bg": "#f4f7fb",
    "surface": "#ffffff",
    "surface_alt": "#f8fafc",
    "border": "#d9e2ec",
    "border_soft": "#e6edf5",
    "text": "#102a43",
    "muted": "#627d98",
    "accent": "#0b6efd",
    "accent_soft": "#e7f1ff",
    "success": "#2b8a3e",
    "success_soft": "#ebfbee",
    "warn": "#b7791f",
    "warn_soft": "#fff9db",
    "danger": "#c92a2a",
    "danger_soft": "#fff5f5",
    "purple": "#5f3dc4",
    "purple_soft": "#f3f0ff",
    "teal": "#0f766e",
    "teal_soft": "#e6fcf5",
}

DARK_THEME = {
    "bg": "#0b1220",
    "surface": "#111c2e",
    "surface_alt": "#0d1726",
    "border": "#233449",
    "border_soft": "#1a2a3d",
    "text": "#e6edf3",
    "muted": "#93a4b8",
    "accent": "#4c8dff",
    "accent_soft": "#16273f",
    "success": "#51cf66",
    "success_soft": "#12351f",
    "warn": "#f0b429",
    "warn_soft": "#33280f",
    "danger": "#ff6b6b",
    "danger_soft": "#3a1717",
    "purple": "#9775fa",
    "purple_soft": "#221a3a",
    "teal": "#38d9a9",
    "teal_soft": "#0d2f28",
}

THEMES = {"light": LIGHT_THEME, "dark": DARK_THEME}
THEME = dict(LIGHT_THEME)
ACTIVE_THEME_NAME = "light"


def set_theme(name):
    """Switch the active theme (builders read THEME at call time)."""
    global ACTIVE_THEME_NAME
    ACTIVE_THEME_NAME = "dark" if name == "dark" else "light"
    THEME.clear()
    THEME.update(THEMES[ACTIVE_THEME_NAME])

# =========================
# GLOBAL STATE
# =========================
message_log = []
network_messages = deque(maxlen=100)

node_data = defaultdict(lambda: {
    "sensors": {},
    "message_history": deque(maxlen=100),
    "time_history": deque(maxlen=100),
    "bti_history": deque(maxlen=200),
    "time_axis": deque(maxlen=200),
    "f_hist": deque(maxlen=200),
    "t_hist": deque(maxlen=200),
    "d_hist": deque(maxlen=200),
    "p_hist": deque(maxlen=200),
    "bti": 1.0,
    "last_seen": None,
    "status": "online",
})

network_bti_history = deque(maxlen=200)
network_time_axis = deque(maxlen=200)
known_nodes = []
network_alert_count = 0

BASE_SKIP_KEYS = {
    "node", "ip", "ip_address", "mac", "hostname", "rssi", "node1_temp", "node2_sound",
    "network_alerts", "source", "online", "data_age",
}

_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def scrub_ips(text):
    """Hide IPv4 addresses anywhere text is shown in the dashboard."""
    return _IP_RE.sub("[hidden]", str(text))

# =========================
# MODEL
# =========================
w1, w2, w3, w4 = 0.25, 0.30, 0.25, 0.20
alpha = 0.7
active_attack = "NONE"

# =========================
# MQTT
# =========================
MQTT_USER = "esp32user"
MQTT_PASS = os.environ.get("MQTT_PASS", "")


def on_message(client, userdata, msg):
    global network_alert_count
    topic = msg.topic
    payload = msg.payload.decode(errors="ignore")
    message_log.append(scrub_ips(f"[{topic}] {payload}"))

    if topic == "iot/network/status":
        try:
            data = json.loads(payload)
            node_id = data.get("node")
            status = data.get("status", "online")
            if node_id:
                node_data[node_id]["status"] = status
                node_data[node_id]["last_seen"] = time.time()
                if node_id not in known_nodes:
                    known_nodes.append(node_id)
        except Exception:
            pass
        return

    if topic == "iot/network/message":
        try:
            data = json.loads(payload)
            timestamp = time.strftime("%H:%M:%S")
            from_node = data.get("from", "unknown")
            event = data.get("event", "")
            alert = data.get("alert", "")
            value = data.get("value", "")
            if alert:
                network_alert_count += 1
                network_messages.appendleft({
                    "time": timestamp, "from": from_node, "type": "alert",
                    "text": f"{alert} (value: {value})",
                })
            elif event:
                network_messages.appendleft({
                    "time": timestamp, "from": from_node, "type": "event", "text": event,
                })
            else:
                network_messages.appendleft({
                    "time": timestamp, "from": from_node, "type": "info", "text": payload,
                })
        except Exception:
            network_messages.appendleft({
                "time": time.strftime("%H:%M:%S"), "from": "unknown", "type": "info", "text": payload,
            })
        return

    if topic.startswith("iot/") and topic.endswith("/data"):
        try:
            data = json.loads(payload)
            node_id = data.get("node")
            if not node_id:
                parts = topic.split("/")
                node_id = parts[1] if len(parts) >= 2 else "unknown"
            if node_id not in known_nodes:
                known_nodes.append(node_id)
            node_data[node_id]["sensors"] = data
            node_data[node_id]["last_seen"] = time.time()
            node_data[node_id]["message_history"].append(payload)
            node_data[node_id]["time_history"].append(time.time())
        except Exception:
            pass


client = mqtt.Client()
client.username_pw_set(MQTT_USER, MQTT_PASS)
client.on_message = on_message
client.connect("localhost", 1883, 60)
client.subscribe("iot/#")
client.subscribe("test/topic")
client.loop_start()

# =========================
# METRICS
# =========================

def clamp(x):
    return max(0, min(1, x))


def F_metric(node_id):
    th = node_data[node_id]["time_history"]
    if len(th) < 2:
        return 0.5
    duration = th[-1] - th[0]
    freq = len(th) / duration if duration != 0 else 0
    return clamp(freq / 0.5 * 0.7)


def T_metric(node_id):
    th = node_data[node_id]["time_history"]
    if len(th) < 3:
        return 0.5
    intervals = np.sort(np.diff(list(th)))
    trim = max(1, len(intervals) // 10)
    core = intervals[trim:-trim] if len(intervals) > 2 * trim else intervals
    if core.size == 0:
        core = intervals
    mean = core.mean()
    if mean <= 0:
        return 0.5
    cv = core.std() / mean
    return clamp(1.0 - cv)


def D_metric(node_id):
    mh = node_data[node_id]["message_history"]
    if len(mh) < 2:
        return 0.5
    field_vals = defaultdict(list)
    for m in mh:
        try:
            d = json.loads(m)
            for k, v in d.items():
                if k in BASE_SKIP_KEYS or k.endswith("_online"):
                    continue
                if isinstance(v, (int, float)):
                    field_vals[k].append(float(v))
        except Exception:
            pass
    if not field_vals:
        return 0.5
    stabilities = []
    for vals in field_vals.values():
        if len(vals) < 2:
            continue
        var = np.var(vals)
        mean = np.mean(vals)
        if mean != 0:
            stabilities.append(1 - var / (mean**2 + 1e-6))
    return clamp(np.mean(stabilities)) if stabilities else 0.5


def P_metric(node_id):
    mh = node_data[node_id]["message_history"]
    if len(mh) < 2:
        return 0.5
    lengths = [len(m) for m in mh]
    return clamp(1 - np.std(lengths) / (np.mean(lengths) + 1e-6))


def apply_attack(F, T, D, P):
    return clamp(F), clamp(T), clamp(D), clamp(P)


def detect_attack(F, T, D, P):
    if F > 0.9:
        return "Flood Attack Detected", THEME["danger"]
    if T < 0.4:
        return "Timing Attack Detected", THEME["danger"]
    if D < 0.5:
        return "False Data Injection", THEME["danger"]
    if F > 0.8 and T > 0.8 and D > 0.8:
        return "Mimic Attack (Stealthy)", THEME["warn"]
    return "", THEME["surface"]


def compute_bti(node_id, F, T, D, P):
    current = w1 * F + w2 * T + w3 * D + w4 * P
    old_bti = node_data[node_id]["bti"]
    new_bti = alpha * old_bti + (1 - alpha) * current
    node_data[node_id]["bti"] = new_bti
    return new_bti


def compute_network_bti():
    if not known_nodes:
        return 1.0
    return float(np.mean([node_data[n]["bti"] for n in known_nodes]))


def bti_color(val):
    if val < 0.4:
        return THEME["danger"]
    if val < 0.7:
        return THEME["warn"]
    return THEME["success"]


def is_online(node_id):
    last = node_data[node_id]["last_seen"]
    if last is None:
        return False
    return (time.time() - last) < 10


def messages_for_node(node_id, limit=25):
    nid = (node_id or "").lower()
    out = []
    for m in reversed(message_log):
        ml = m.lower()
        if f"/{nid}/" in ml or f"node={nid}" in ml or f"node\": \"{nid}\"" in ml or f"from': '{nid}" in ml or f'"from": "{nid}"' in ml:
            out.append(m)
        if len(out) >= limit:
            break
    return out


# =========================
# PDF
# =========================

def generate_pdf():
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer)
    styles = getSampleStyleSheet()
    content = []
    content.append(Paragraph("IoT Trust Monitoring System Report", styles["Title"]))
    content.append(Spacer(1, 12))
    content.append(Paragraph("Behaviour-Based Trust Index (BTI) evaluation under normal and adversarial conditions.", styles["Normal"]))
    content.append(Paragraph(f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}", styles["Normal"]))
    content.append(Spacer(1, 16))
    net_bti = compute_network_bti()
    content.append(Paragraph(f"Network BTI: {net_bti:.3f} | Active Nodes: {len(known_nodes)} | Attack label: {active_attack}", styles["Normal"]))
    content.append(Spacer(1, 10))
    for node_id in known_nodes:
        nd = node_data[node_id]
        content.append(Paragraph(f"{node_id.upper()} — BTI {nd['bti']:.3f} — {'Online' if is_online(node_id) else 'Offline'}", styles["Heading3"]))
        if nd["f_hist"]:
            content.append(Paragraph(f"F/T/D/P avg: {np.mean(nd['f_hist']):.2f}/{np.mean(nd['t_hist']):.2f}/{np.mean(nd['d_hist']):.2f}/{np.mean(nd['p_hist']):.2f}", styles["Normal"]))
    tmpdir = tempfile.gettempdir()
    if len(network_bti_history) > 5:
        plt.figure(figsize=(8, 3))
        plt.plot(list(network_bti_history), color="#0b6efd")
        plt.ylim(0, 1)
        plt.title("Network BTI")
        plt.tight_layout()
        p = os.path.join(tmpdir, "network_bti.png")
        plt.savefig(p, dpi=120)
        plt.close()
        content.append(Spacer(1, 8))
        content.append(Image(p, width=420, height=150))
    doc.build(content)
    buffer.seek(0)
    return buffer


# =========================
# UI HELPERS
# =========================

def nav_bar(active="overview"):
    def link(label, href, key):
        on = key == active
        return dcc.Link(label, href=href, style={
            "textDecoration": "none", "padding": "8px 12px", "borderRadius": "10px",
            "fontWeight": "700", "fontSize": "13px",
            "color": THEME["accent"] if on else THEME["muted"],
            "backgroundColor": THEME["accent_soft"] if on else "transparent",
            "border": f"1px solid {'#cfe2ff' if on else 'transparent'}",
        })

    node_links = [link(n.upper(), f"/node/{n}", f"node:{n}") for n in known_nodes]
    return html.Div(style={
        "display": "flex", "alignItems": "center", "justifyContent": "space-between",
        "gap": "12px", "flexWrap": "wrap", "marginBottom": "18px",
        "backgroundColor": THEME["surface"], "border": f"1px solid {THEME['border']}",
        "borderRadius": "14px", "padding": "12px 14px", "boxShadow": "0 2px 8px rgba(16,42,67,0.04)",
    }, children=[
        html.Div(style={"display": "flex", "gap": "8px", "flexWrap": "wrap", "alignItems": "center"}, children=[
            link("Overview", "/", "overview"),
            html.Span("Nodes:", style={"color": THEME["muted"], "fontSize": "12px", "fontWeight": "700", "marginLeft": "6px"}) if node_links else html.Span(),
            *node_links,
        ]),
        html.Div(style={"display": "flex", "alignItems": "center", "gap": "10px", "flexWrap": "wrap"}, children=[
            html.Div(f"Attack label: {active_attack}", style={"color": THEME["muted"], "fontSize": "12px", "fontWeight": "700"}),
            html.Button(
                "Light mode" if ACTIVE_THEME_NAME == "dark" else "Dark mode",
                id="theme-toggle",
                n_clicks=0,
                style={
                    "backgroundColor": THEME["surface_alt"],
                    "color": THEME["text"],
                    "border": f"1px solid {THEME['border']}",
                    "borderRadius": "999px",
                    "padding": "6px 12px",
                    "fontSize": "12px",
                    "fontWeight": "700",
                    "cursor": "pointer",
                },
            ),
        ]),
    ])


def page_shell(children, active="overview"):
    return html.Div(style={
        "backgroundColor": THEME["bg"], "minHeight": "100vh", "padding": "24px 28px",
        "color": THEME["text"], "fontFamily": 'Inter, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
    }, children=[
        html.Div(style={"display": "flex", "justifyContent": "space-between", "alignItems": "flex-start", "gap": "16px", "flexWrap": "wrap", "marginBottom": "14px"}, children=[
            html.Div([
                html.Div("IoT Trust-Based Monitoring Dashboard", style={"fontSize": "28px", "fontWeight": "800", "letterSpacing": "-0.02em"}),
                html.Div("Behaviour-Based Trust Index (BTI) · Multi-Node MQTT Network", style={"color": THEME["muted"], "fontSize": "13px", "marginTop": "4px"}),
            ]),
            html.Div("BTI = 0.25F + 0.30T + 0.25D + 0.20P", style={"backgroundColor": THEME["surface"], "border": f"1px solid {THEME['border']}", "borderRadius": "10px", "padding": "8px 12px", "color": THEME["accent"], "fontWeight": "800", "fontSize": "13px"}),
        ]),
        nav_bar(active),
        *(children if isinstance(children, (list, tuple)) else [children]),
    ])


def sensor_badge(label, value, color=None):
    color = color or THEME["accent"]
    return html.Div(style={
        "backgroundColor": THEME["surface"], "border": f"1px solid {THEME['border']}", "borderTop": f"3px solid {color}",
        "borderRadius": "10px", "padding": "10px 14px", "textAlign": "center", "minWidth": "92px",
    }, children=[
        html.Div(label, style={"color": THEME["muted"], "fontSize": "10px", "letterSpacing": "0.08em", "fontWeight": "700", "marginBottom": "4px"}),
        html.Div(scrub_ips(value), style={"color": THEME["text"], "fontSize": "17px", "fontWeight": "700"}),
    ])


def metric_badge(label, value, color):
    return html.Div(style={"backgroundColor": THEME["surface"], "border": f"1px solid {THEME['border']}", "borderRadius": "10px", "padding": "10px 12px", "textAlign": "center", "minWidth": "72px", "flex": "1"}, children=[
        html.Div(label, style={"color": color, "fontSize": "11px", "letterSpacing": "0.12em", "fontWeight": "800"}),
        html.Div(f"{value:.2f}", style={"color": THEME["text"], "fontSize": "22px", "fontWeight": "700", "marginTop": "2px"}),
    ])


def status_chip(online):
    if online:
        return html.Span("ONLINE", style={"backgroundColor": THEME["success_soft"], "color": THEME["success"], "border": "1px solid #b7e4c7", "padding": "4px 10px", "borderRadius": "999px", "fontSize": "11px", "fontWeight": "700"})
    return html.Span("OFFLINE", style={"backgroundColor": THEME["danger_soft"], "color": THEME["danger"], "border": "1px solid #ffc9c9", "padding": "4px 10px", "borderRadius": "999px", "fontSize": "11px", "fontWeight": "700"})


def overview_card(title, value_id, color, subtitle=""):
    return html.Div(style={"backgroundColor": THEME["surface"], "border": f"1px solid {THEME['border']}", "borderTop": f"4px solid {color}", "borderRadius": "14px", "padding": "16px 18px", "flex": "1", "minWidth": "160px", "boxShadow": "0 4px 12px rgba(16,42,67,0.04)"}, children=[
        html.Div(title, style={"color": THEME["muted"], "fontSize": "11px", "fontWeight": "800", "letterSpacing": "0.1em"}),
        html.Div(id=value_id, style={"color": THEME["text"], "fontSize": "32px", "fontWeight": "800", "marginTop": "6px"}),
        html.Div(subtitle, style={"color": THEME["muted"], "fontSize": "12px", "marginTop": "4px"}) if subtitle else html.Div(),
    ])


def action_button(label, btn_id, accent, filled=False):
    return html.Button(label, id=btn_id, n_clicks=0, style={
        "backgroundColor": accent if filled else THEME["surface"], "color": "#fff" if filled else THEME["text"],
        "border": f"1px solid {accent}", "padding": "9px 13px", "borderRadius": "10px",
        "fontSize": "14px", "fontWeight": "700", "cursor": "pointer", "minWidth": "104px",
    })


def action_card(kind, title, btn_id, accent, filled=False):
    return html.Div(style={"backgroundColor": THEME["surface"], "border": f"1px solid {THEME['border']}", "borderRadius": "14px", "padding": "12px 14px", "flex": "1", "minWidth": "135px"}, children=[
        html.Div(kind, style={"color": accent, "marginBottom": "8px", "fontSize": "10px", "letterSpacing": "0.12em", "fontWeight": "800"}),
        action_button(title, btn_id, accent, filled=filled),
    ])


def node_card(node_id, bti_val, sensors, online, alert_text, F, T, D, P):
    color = bti_color(bti_val)
    badges = []
    skip_keys = BASE_SKIP_KEYS | {f"{n}_online" for n in known_nodes}
    for k, v in sensors.items():
        if k not in skip_keys and v is not None and not (isinstance(v, (int, float)) and v < 0) and not (isinstance(v, str) and v.strip() == ""):
            badges.append(sensor_badge(k.upper(), round(v, 2) if isinstance(v, float) else v))
    if "rssi" in sensors and isinstance(sensors["rssi"], (int, float)) and sensors["rssi"] < 0:
        badges.append(sensor_badge("RSSI", sensors["rssi"], THEME["purple"]))
    return html.Div(style={"backgroundColor": THEME["surface"], "border": f"1px solid {THEME['border']}", "borderLeft": f"6px solid {color}", "borderRadius": "14px", "padding": "18px", "marginBottom": "16px", "boxShadow": "0 4px 12px rgba(16,42,67,0.04)"}, children=[
        html.Div(style={"display": "flex", "justifyContent": "space-between", "alignItems": "center", "marginBottom": "8px", "gap": "10px", "flexWrap": "wrap"}, children=[
            html.Div([dcc.Link(node_id.upper(), href=f"/node/{node_id}", style={"color": THEME["text"], "fontSize": "20px", "fontWeight": "800", "textDecoration": "none"}), html.Div(f"iot/{node_id}/data", style={"color": THEME["muted"], "fontSize": "12px"})]),
            status_chip(online),
        ]),
        html.Div(style={"display": "flex", "alignItems": "baseline", "gap": "10px", "marginBottom": "12px", "flexWrap": "wrap"}, children=[
            html.Span("BTI", style={"color": THEME["muted"], "fontSize": "12px", "fontWeight": "700"}),
            html.Span(f"{bti_val:.3f}", style={"color": color, "fontSize": "32px", "fontWeight": "800"}),
            html.Span(alert_text, style={"color": THEME["danger"], "fontSize": "13px", "fontWeight": "700"}) if alert_text else html.Span(),
            dcc.Link("Open node page →", href=f"/node/{node_id}", style={"marginLeft": "auto", "color": THEME["accent"], "fontSize": "13px", "fontWeight": "700", "textDecoration": "none"}),
        ]),
        html.Div(style={"display": "flex", "gap": "10px", "marginBottom": "14px", "flexWrap": "wrap"}, children=[metric_badge("F", F, THEME["accent"]), metric_badge("T", T, THEME["purple"]), metric_badge("D", D, THEME["success"]), metric_badge("P", P, THEME["warn"])]),
        html.Div(style={"display": "flex", "gap": "10px", "flexWrap": "wrap"}, children=badges or [html.Div("No sensor readings yet.", style={"color": THEME["muted"], "fontSize": "13px"})]),
    ])


def network_message_row(nm):
    if nm["type"] == "alert":
        fg, bg, bd, label = THEME["danger"], THEME["danger_soft"], "#ffc9c9", "ALERT"
    elif nm["type"] == "event":
        fg, bg, bd, label = THEME["accent"], THEME["accent_soft"], "#cfe2ff", "EVENT"
    else:
        fg, bg, bd, label = THEME["muted"], THEME["surface_alt"], THEME["border"], "INFO"
    return html.Div(style={"display": "flex", "gap": "10px", "alignItems": "flex-start", "padding": "8px 0", "borderBottom": f"1px solid {THEME['border_soft']}", "flexWrap": "wrap"}, children=[
        html.Span(nm["time"], style={"color": THEME["muted"], "fontSize": "12px", "minWidth": "62px"}),
        html.Span(label, style={"color": fg, "backgroundColor": bg, "fontSize": "10px", "fontWeight": "800", "border": f"1px solid {bd}", "padding": "2px 8px", "borderRadius": "999px"}),
        html.Span(scrub_ips(nm["from"]).upper(), style={"color": THEME["teal"], "fontSize": "12px", "fontWeight": "700"}),
        html.Span(scrub_ips(nm["text"]), style={"color": THEME["text"], "fontSize": "13px", "flex": "1", "minWidth": "180px"}),
    ])


def plotly_light(title, xs, ys, color, ytitle="BTI", yrange=(0, 1)):
    return {
        "data": [{"x": list(xs), "y": list(ys), "mode": "lines", "line": {"color": color, "width": 3}, "fill": "tozeroy", "fillcolor": color + "18"}],
        "layout": {
            "paper_bgcolor": THEME["surface"], "plot_bgcolor": THEME["surface"],
            "font": {"color": THEME["text"], "family": "Inter, Segoe UI, Arial, sans-serif"},
            "xaxis": {"showgrid": False, "tickfont": {"color": THEME["muted"]}},
            "yaxis": {"range": list(yrange), "gridcolor": THEME["border_soft"], "tickfont": {"color": THEME["muted"]}, "title": ytitle},
            "margin": {"t": 34, "r": 14, "b": 30, "l": 44}, "title": {"text": title, "font": {"size": 15}},
            "showlegend": False,
        },
    }


# =========================
# PAGES
# =========================

def overview_layout():
    return page_shell(active="overview", children=[
        html.Div(id="alert-banner", style={"display": "none"}),
        html.Div(style={"display": "flex", "gap": "10px", "flexWrap": "wrap", "marginBottom": "18px"}, children=[
            action_card("MODE", "Normal", "btn-none", THEME["muted"]),
            action_card("SIMULATE", "Replay", "btn-replay", THEME["purple"]),
            action_card("SIMULATE", "Timing", "btn-timing", THEME["purple"]),
            action_card("SIMULATE", "False Data", "btn-fdo", THEME["danger"]),
            action_card("SIMULATE", "Flood", "btn-flood", THEME["danger"]),
            action_card("SIMULATE", "Mimic", "btn-mimic", THEME["warn"]),
            action_card("EXPORT", "Report (PDF)", "btn-download", THEME["accent"], filled=True),
        ]),
        dcc.Download(id="download"),
        html.Div(style={"display": "flex", "gap": "12px", "marginBottom": "16px", "flexWrap": "wrap"}, children=[
            overview_card("NETWORK BTI", "network-bti", THEME["accent"], "Average across nodes"),
            overview_card("ACTIVE NODES", "active-nodes", THEME["success"], "Known nodes"),
            overview_card("BTI ALERTS", "network-alerts", THEME["danger"], "Heuristic alerts now"),
            overview_card("NODE ALERTS", "node-alerts", THEME["warn"], "Inter-node alerts"),
        ]),
        html.Div(style={"backgroundColor": THEME["surface"], "border": f"1px solid {THEME['border']}", "borderRadius": "14px", "padding": "10px", "marginBottom": "18px"}, children=[
            dcc.Graph(id="network-bti-graph", config={"displayModeBar": False}, style={"height": "300px"}),
        ]),
        html.Div(style={"display": "grid", "gridTemplateColumns": "repeat(auto-fit, minmax(320px, 1fr))", "gap": "16px"}, children=[
            html.Div([
                html.Div("INTER-NODE COMMUNICATION", style={"color": THEME["muted"], "fontSize": "11px", "fontWeight": "800", "letterSpacing": "0.1em", "marginBottom": "8px"}),
                html.Div(id="network-message-log", style={"backgroundColor": THEME["surface"], "border": f"1px solid {THEME['border']}", "borderRadius": "14px", "padding": "12px 14px", "maxHeight": "320px", "overflowY": "auto"}),
                html.Div("RECENT MQTT LOG", style={"color": THEME["muted"], "fontSize": "11px", "fontWeight": "800", "letterSpacing": "0.1em", "margin": "14px 0 8px"}),
                html.Div(id="message-log", style={"backgroundColor": THEME["surface"], "border": f"1px solid {THEME['border']}", "borderRadius": "14px", "padding": "12px 14px", "fontSize": "12px", "color": THEME["muted"], "maxHeight": "220px", "overflowY": "auto", "fontFamily": "Consolas, monospace"}),
            ]),
            html.Div([
                html.Div("NODE STATUS (click a node for details)", style={"color": THEME["muted"], "fontSize": "11px", "fontWeight": "800", "letterSpacing": "0.1em", "marginBottom": "8px"}),
                html.Div(id="node-cards"),
            ]),
        ]),
        dcc.Interval(id="interval-overview", interval=1000),
    ])


def node_layout(node_id):
    nid = node_id
    return page_shell(active=f"node:{nid}", children=[
        html.Div(style={"display": "flex", "justifyContent": "space-between", "alignItems": "center", "gap": "10px", "flexWrap": "wrap", "marginBottom": "14px"}, children=[
            html.Div([
                html.Div(f"Node: {nid.upper()}", style={"fontSize": "24px", "fontWeight": "800"}),
                html.Div(f"Topic iot/{nid}/data · individual BTI, metrics, sensors, and messages", style={"color": THEME["muted"], "fontSize": "13px", "marginTop": "2px"}),
            ]),
            dcc.Link("← Back to overview", href="/", style={"color": THEME["accent"], "fontWeight": "700", "textDecoration": "none"}),
        ]),
        html.Div(id="node-detail"),
        dcc.Interval(id="interval-node", interval=1000),
        html.Div(id="node-id-store", children=nid, style={"display": "none"}),
    ])


# =========================
# APP
# =========================
app = dash.Dash(__name__, title="IoT Trust Dashboard", suppress_callback_exceptions=True)
server = app.server

app.layout = html.Div([
    dcc.Location(id="url", refresh=False),
    dcc.Store(id="theme-store", data="light", storage_type="session"),
    html.Div(id="theme-sync", style={"display": "none"}),
    html.Div(id="page-content"),
])


@app.callback(
    Output("page-content", "children"),
    Input("url", "pathname"),
    Input("theme-store", "data"),
)
def route(pathname, theme_name):
    set_theme(theme_name)
    path = (pathname or "/").strip("/")
    if path.startswith("node/"):
        node_id = unquote(path.split("/", 1)[1])
        if node_id:
            return node_layout(node_id)
    return overview_layout()


@app.callback(
    Output("theme-store", "data"),
    Input("theme-toggle", "n_clicks"),
    State("theme-store", "data"),
    prevent_initial_call=True,
)
def toggle_theme(n_clicks, current):
    if not n_clicks:
        raise dash.exceptions.PreventUpdate
    return "light" if (current or "light") == "dark" else "dark"


app.clientside_callback(
    "function(theme){"
    "var dark = (theme || 'light') === 'dark';"
    "document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');"
    "document.documentElement.style.background = dark ? '#0b1220' : '#f4f7fb';"
    "document.body.style.setProperty('background-color', dark ? '#0b1220' : '#f4f7fb', 'important');"
    "document.documentElement.style.colorScheme = dark ? 'dark' : 'light';"
    "return '';"
    "}",
    Output("theme-sync", "children"),
    Input("theme-store", "data"),
)


# ---- Overview callbacks ----
@app.callback(
    Output("alert-banner", "children"), Output("alert-banner", "style"),
    Input("btn-none", "n_clicks"), Input("btn-replay", "n_clicks"), Input("btn-timing", "n_clicks"),
    Input("btn-fdo", "n_clicks"), Input("btn-flood", "n_clicks"), Input("btn-mimic", "n_clicks"),
    prevent_initial_call=True,
)
def set_attack(*args):
    global active_attack
    ctx = dash.callback_context
    if not ctx.triggered:
        raise dash.exceptions.PreventUpdate
    btn = ctx.triggered[0]["prop_id"].split(".")[0]
    mapping = {"btn-none": "NONE", "btn-replay": "REPLAY", "btn-timing": "TIMING", "btn-fdo": "FDO", "btn-flood": "FLOOD", "btn-mimic": "MIMIC"}
    active_attack = mapping.get(btn, "NONE")
    accent, bg = THEME["muted"], THEME["surface"]
    if active_attack in ("REPLAY", "TIMING"):
        accent, bg = THEME["purple"], THEME["purple_soft"]
    elif active_attack in ("FDO", "FLOOD"):
        accent, bg = THEME["danger"], THEME["danger_soft"]
    elif active_attack == "MIMIC":
        accent, bg = THEME["warn"], THEME["warn_soft"]
    return html.Div([html.Span("Attack label: ", style={"color": THEME["muted"], "fontWeight": "700"}), html.Span(active_attack, style={"color": accent, "fontWeight": "800"})]), {
        "display": "block", "backgroundColor": bg, "padding": "10px 14px", "borderRadius": "12px", "marginBottom": "14px", "border": f"1px solid {accent}",
    }


@app.callback(
    Output("node-cards", "children"), Output("network-bti", "children"), Output("active-nodes", "children"),
    Output("network-alerts", "children"), Output("node-alerts", "children"), Output("network-bti-graph", "figure"),
    Output("message-log", "children"), Output("network-message-log", "children"),
    Input("interval-overview", "n_intervals"),
)
def update_overview(n):
    alert_count = 0
    cards = []
    for node_id in known_nodes:
        F, T, D, P = apply_attack(F_metric(node_id), T_metric(node_id), D_metric(node_id), P_metric(node_id))
        bti_val = compute_bti(node_id, F, T, D, P)
        nd = node_data[node_id]
        nd["f_hist"].append(F); nd["t_hist"].append(T); nd["d_hist"].append(D); nd["p_hist"].append(P)
        nd["bti_history"].append(bti_val); nd["time_axis"].append(time.strftime("%H:%M:%S"))
        alert_text, _ = detect_attack(F, T, D, P)
        if alert_text:
            alert_count += 1
        cards.append(node_card(node_id, bti_val, nd["sensors"], is_online(node_id), alert_text, F, T, D, P))
    if not cards:
        cards = [html.Div("Waiting for MQTT node data… (messages on iot/<node>/data)", style={"backgroundColor": THEME["surface"], "border": f"1px dashed {THEME['border']}", "borderRadius": "14px", "padding": "22px", "color": THEME["muted"], "textAlign": "center"})]
    net_bti = compute_network_bti()
    network_bti_history.append(net_bti)
    network_time_axis.append(time.strftime("%H:%M:%S"))
    fig = plotly_light("Network BTI Over Time", network_time_axis, network_bti_history, THEME["accent"])
    log = [html.Div(f"→ {m}", style={"padding": "2px 0", "borderBottom": f"1px solid {THEME['border_soft']}"}) for m in message_log[-15:]] or [html.Div("No messages yet.", style={"color": THEME["muted"]})]
    net_rows = [network_message_row(nm) for nm in list(network_messages)[:20]] or [html.Div("No inter-node messages yet.", style={"color": THEME["muted"], "fontSize": "13px"})]
    return cards, f"{net_bti:.3f}", str(len(known_nodes)), str(alert_count), str(network_alert_count), fig, log, net_rows


@app.callback(Output("download", "data"), Input("btn-download", "n_clicks"), prevent_initial_call=True)
def download(n):
    return dcc.send_bytes(generate_pdf().read(), "report.pdf")


# ---- Node detail callback ----
@app.callback(Output("node-detail", "children"), Input("interval-node", "n_intervals"), Input("node-id-store", "children"))
def update_node(n, node_id):
    if not node_id:
        return html.Div("No node selected.")
    nid = str(node_id)
    nd = node_data[nid]
    online = is_online(nid)
    # Compute/update if we have history (or use last values)
    if nd["time_history"]:
        F, T, D, P = apply_attack(F_metric(nid), T_metric(nid), D_metric(nid), P_metric(nid))
        bti_val = compute_bti(nid, F, T, D, P)
        nd["f_hist"].append(F); nd["t_hist"].append(T); nd["d_hist"].append(D); nd["p_hist"].append(P)
        nd["bti_history"].append(bti_val); nd["time_axis"].append(time.strftime("%H:%M:%S"))
        alert_text, _ = detect_attack(F, T, D, P)
    else:
        F = T = D = P = 0.5
        bti_val = nd.get("bti", 1.0)
        alert_text = ""
    color = bti_color(bti_val)

    sensors = nd["sensors"] or {}
    badges = []
    skip_keys = BASE_SKIP_KEYS | {f"{n}_online" for n in known_nodes}
    for k, v in sensors.items():
        if k not in skip_keys and v is not None and not (isinstance(v, (int, float)) and v < 0) and not (isinstance(v, str) and v.strip() == ""):
            badges.append(sensor_badge(k.upper(), round(v, 2) if isinstance(v, float) else v))

    node_msgs = messages_for_node(nid, limit=30)
    node_msgs_ui = [html.Div(m, style={"padding": "6px 0", "borderBottom": f"1px solid {THEME['border_soft']}", "fontSize": "12px", "fontFamily": "Consolas, monospace", "color": THEME["text"], "wordBreak": "break-all"}) for m in node_msgs] or [html.Div("No messages received for this node yet.", style={"color": THEME["muted"]})]

    involved = [network_message_row(nm) for nm in list(network_messages) if (nm.get("from", "").lower() == nid.lower() or nid.lower() in (nm.get("text", "").lower()))][:15]
    involved_ui = involved or [html.Div("No inter-node messages involving this node.", style={"color": THEME["muted"], "fontSize": "13px"})]

    metrics_fig = {
        "data": [
            {"x": list(nd["time_axis"]), "y": list(nd["f_hist"]), "name": "F", "line": {"color": THEME["accent"]}},
            {"x": list(nd["time_axis"]), "y": list(nd["t_hist"]), "name": "T", "line": {"color": THEME["purple"]}},
            {"x": list(nd["time_axis"]), "y": list(nd["d_hist"]), "name": "D", "line": {"color": THEME["success"]}},
            {"x": list(nd["time_axis"]), "y": list(nd["p_hist"]), "name": "P", "line": {"color": THEME["warn"]}},
        ],
        "layout": {
            "paper_bgcolor": THEME["surface"], "plot_bgcolor": THEME["surface"],
            "font": {"color": THEME["text"]}, "yaxis": {"range": [0, 1], "gridcolor": THEME["border_soft"]},
            "xaxis": {"showgrid": False}, "margin": {"t": 34, "r": 14, "b": 30, "l": 44},
            "title": {"text": "F, T, D, P Over Time", "font": {"size": 15}}, "legend": {"orientation": "h", "y": 1.12},
        },
    }

    return html.Div([
        html.Div(style={"display": "flex", "gap": "12px", "flexWrap": "wrap", "marginBottom": "16px"}, children=[
            html.Div(style={"backgroundColor": THEME["surface"], "border": f"1px solid {THEME['border']}", "borderLeft": f"6px solid {color}", "borderRadius": "14px", "padding": "16px 18px", "flex": "1", "minWidth": "220px"}, children=[
                html.Div(style={"display": "flex", "justifyContent": "space-between", "alignItems": "center"}, children=[html.Div("CURRENT BTI", style={"color": THEME["muted"], "fontSize": "11px", "fontWeight": "800", "letterSpacing": "0.1em"}), status_chip(online)]),
                html.Div(f"{bti_val:.3f}", style={"color": color, "fontSize": "42px", "fontWeight": "800", "marginTop": "6px"}),
                html.Div(alert_text or "No heuristic alert", style={"color": THEME["danger"] if alert_text else THEME["muted"], "fontSize": "13px", "fontWeight": "700", "marginTop": "4px"}),
            ]),
            html.Div(style={"backgroundColor": THEME["surface"], "border": f"1px solid {THEME['border']}", "borderRadius": "14px", "padding": "16px 18px", "flex": "2", "minWidth": "280px"}, children=[
                html.Div("CURRENT METRICS", style={"color": THEME["muted"], "fontSize": "11px", "fontWeight": "800", "letterSpacing": "0.1em", "marginBottom": "10px"}),
                html.Div(style={"display": "flex", "gap": "10px", "flexWrap": "wrap"}, children=[metric_badge("F", F, THEME["accent"]), metric_badge("T", T, THEME["purple"]), metric_badge("D", D, THEME["success"]), metric_badge("P", P, THEME["warn"])]),
            ]),
        ]),
        html.Div(style={"display": "grid", "gridTemplateColumns": "repeat(auto-fit, minmax(320px, 1fr))", "gap": "16px", "marginBottom": "16px"}, children=[
            html.Div(style={"backgroundColor": THEME["surface"], "border": f"1px solid {THEME['border']}", "borderRadius": "14px", "padding": "10px"}, children=[dcc.Graph(figure=plotly_light(f"{nid.upper()} BTI Over Time", nd["time_axis"], nd["bti_history"], color), config={"displayModeBar": False}, style={"height": "280px"})]),
            html.Div(style={"backgroundColor": THEME["surface"], "border": f"1px solid {THEME['border']}", "borderRadius": "14px", "padding": "10px"}, children=[dcc.Graph(figure=metrics_fig, config={"displayModeBar": False}, style={"height": "280px"})]),
        ]),
        html.Div(style={"display": "grid", "gridTemplateColumns": "repeat(auto-fit, minmax(320px, 1fr))", "gap": "16px"}, children=[
            html.Div([
                html.Div("LATEST SENSOR READINGS", style={"color": THEME["muted"], "fontSize": "11px", "fontWeight": "800", "letterSpacing": "0.1em", "marginBottom": "8px"}),
                html.Div(style={"backgroundColor": THEME["surface"], "border": f"1px solid {THEME['border']}", "borderRadius": "14px", "padding": "14px"}, children=[
                    html.Div(style={"display": "flex", "gap": "10px", "flexWrap": "wrap"}, children=badges or [html.Div("No sensor readings yet.", style={"color": THEME["muted"]})]),
                ]),
                html.Div("INTER-NODE MESSAGES (this node)", style={"color": THEME["muted"], "fontSize": "11px", "fontWeight": "800", "letterSpacing": "0.1em", "margin": "14px 0 8px"}),
                html.Div(style={"backgroundColor": THEME["surface"], "border": f"1px solid {THEME['border']}", "borderRadius": "14px", "padding": "12px 14px", "maxHeight": "240px", "overflowY": "auto"}, children=involved_ui),
            ]),
            html.Div([
                html.Div(f"MESSAGES RECEIVED FOR {nid.upper()} (MQTT log)", style={"color": THEME["muted"], "fontSize": "11px", "fontWeight": "800", "letterSpacing": "0.1em", "marginBottom": "8px"}),
                html.Div(style={"backgroundColor": THEME["surface"], "border": f"1px solid {THEME['border']}", "borderRadius": "14px", "padding": "12px 14px", "maxHeight": "420px", "overflowY": "auto"}, children=node_msgs_ui),
            ]),
        ]),
    ])


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
