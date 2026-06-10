import dash
from dash import html, dcc
from dash.dependencies import Input, Output
import paho.mqtt.client as mqtt
import time
import numpy as np
from collections import deque, defaultdict
import io
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# PDF
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image
from reportlab.lib.styles import getSampleStyleSheet

# =========================
# GLOBAL STATE
# =========================
message_log = []

node_data = defaultdict(lambda: {
    'sensors': {},
    'message_history': deque(maxlen=100),
    'time_history': deque(maxlen=100),
    'bti_history': deque(maxlen=200),
    'time_axis': deque(maxlen=200),
    'f_hist': deque(maxlen=200),
    't_hist': deque(maxlen=200),
    'd_hist': deque(maxlen=200),
    'p_hist': deque(maxlen=200),
    'bti': 1.0,
    'last_seen': None,
    'status': 'online'
})

network_bti_history = deque(maxlen=200)
network_time_axis = deque(maxlen=200)
known_nodes = []

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
MQTT_PASS = "password"  # replace with password

def on_message(client, userdata, msg):
    topic = msg.topic
    payload = msg.payload.decode()

    message_log.append(f"[{topic}] {payload}")

    if topic == "iot/network/status":
        try:
            data = json.loads(payload)
            node_id = data.get('node')
            status = data.get('status', 'online')
            if node_id:
                node_data[node_id]['status'] = status
                node_data[node_id]['last_seen'] = time.time()
                if node_id not in known_nodes:
                    known_nodes.append(node_id)
        except:
            pass
        return

    if topic.startswith("iot/") and topic.endswith("/data"):
        try:
            data = json.loads(payload)
            node_id = data.get('node')
            if not node_id:
                parts = topic.split("/")
                node_id = parts[1] if len(parts) >= 2 else "unknown"

            if node_id not in known_nodes:
                known_nodes.append(node_id)

            node_data[node_id]['sensors'] = data
            node_data[node_id]['last_seen'] = time.time()
            node_data[node_id]['message_history'].append(payload)
            node_data[node_id]['time_history'].append(time.time())
        except:
            pass

client = mqtt.Client()
client.username_pw_set(MQTT_USER, MQTT_PASS)
client.on_message = on_message
client.connect("localhost", 1883, 60)
client.subscribe("iot/#")
client.subscribe("test/topic")
client.loop_start()

# =========================
# FEATURES (per node)
# =========================
def clamp(x):
    return max(0, min(1, x))

def F_metric(node_id):
    th = node_data[node_id]['time_history']
    if len(th) < 2:
        return 0.5
    duration = th[-1] - th[0]
    freq = len(th) / duration if duration != 0 else 0
    return clamp(freq / 2)

def T_metric(node_id):
    th = node_data[node_id]['time_history']
    if len(th) < 3:
        return 0.5
    intervals = np.diff(list(th))
    cv = np.std(intervals) / (np.mean(intervals) + 1e-6)
    return clamp(1 - cv)

def D_metric(node_id):
    mh = node_data[node_id]['message_history']
    vals = []
    for m in mh:
        try:
            d = json.loads(m)
            for v in d.values():
                if isinstance(v, (int, float)):
                    vals.append(float(v))
                    break
        except:
            try:
                vals.append(float(m))
            except:
                pass
    if len(vals) < 2:
        return 0.5
    var = np.var(vals)
    mean = np.mean(vals)
    return clamp(1 - var / (mean**2 + 1e-6))

def P_metric(node_id):
    mh = node_data[node_id]['message_history']
    if len(mh) < 2:
        return 0.5
    lengths = [len(m) for m in mh]
    return clamp(1 - np.std(lengths) / (np.mean(lengths) + 1e-6))

# =========================
# ATTACK EFFECT
# =========================
def apply_attack(F, T, D, P):
    if active_attack == "REPLAY":
        T *= 0.9
    elif active_attack == "TIMING":
        T *= 0.6
    elif active_attack == "FDO":
        D *= 0.5
    elif active_attack == "FLOOD":
        F = min(1.0, F * 1.8)
    elif active_attack == "MIMIC":
        F, T, D, P = 0.9, 0.9, 0.9, 0.9
    return clamp(F), clamp(T), clamp(D), clamp(P)

# =========================
# DETECTION
# =========================
def detect_attack(F, T, D, P):
    if F > 0.9:
        return "Flood Attack Detected", "#ff3b3b"
    if T < 0.4:
        return "Timing Attack Detected", "#ff3b3b"
    if D < 0.5:
        return "False Data Injection", "#ff3b3b"
    if F > 0.8 and T > 0.8 and D > 0.8:
        return "Mimic Attack (Stealthy)", "#ffaa00"
    return "", "#1a1a2e"

# =========================
# BTI PER NODE
# =========================
def compute_bti(node_id, F, T, D, P):
    current = w1*F + w2*T + w3*D + w4*P
    old_bti = node_data[node_id]['bti']
    new_bti = alpha * old_bti + (1 - alpha) * current
    node_data[node_id]['bti'] = new_bti
    return new_bti

# =========================
# NETWORK BTI
# =========================
def compute_network_bti():
    if not known_nodes:
        return 1.0
    return np.mean([node_data[n]['bti'] for n in known_nodes])

# =========================
# BTI COLOR
# =========================
def bti_color(val):
    if val < 0.4:
        return "#ff3b3b"
    elif val < 0.7:
        return "#ffaa00"
    return "#00ff88"

# =========================
# NODE ONLINE CHECK
# =========================
def is_online(node_id):
    last = node_data[node_id]['last_seen']
    if last is None:
        return False
    return (time.time() - last) < 10

# =========================
# PDF GENERATOR
# =========================
def generate_pdf():
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer)
    styles = getSampleStyleSheet()
    content = []

    content.append(Paragraph("IoT Trust Monitoring System Report", styles['Title']))
    content.append(Spacer(1, 12))
    content.append(Paragraph(
        "This report presents the Behaviour-Based Trust Index (BTI) evaluation "
        "of IoT node communication under normal and adversarial conditions.",
        styles['Normal']
    ))
    content.append(Paragraph(f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}", styles['Normal']))
    content.append(Spacer(1, 20))

    net_bti = compute_network_bti()
    content.append(Paragraph("Network Overview", styles['Heading2']))
    content.append(Paragraph(f"Active Nodes: {len(known_nodes)}", styles['Normal']))
    content.append(Paragraph(f"Network BTI: {net_bti:.3f}", styles['Normal']))
    content.append(Paragraph(f"Simulated Attack: {active_attack}", styles['Normal']))
    content.append(Spacer(1, 12))

    for node_id in known_nodes:
        nd = node_data[node_id]
        content.append(Paragraph(f"Node: {node_id}", styles['Heading2']))
        content.append(Paragraph(f"Status: {'Online' if is_online(node_id) else 'Offline'}", styles['Normal']))
        content.append(Paragraph(f"BTI Score: {nd['bti']:.3f}", styles['Normal']))

        if nd['f_hist']:
            content.append(Paragraph(f"F avg: {np.mean(nd['f_hist']):.3f}", styles['Normal']))
            content.append(Paragraph(f"T avg: {np.mean(nd['t_hist']):.3f}", styles['Normal']))
            content.append(Paragraph(f"D avg: {np.mean(nd['d_hist']):.3f}", styles['Normal']))
            content.append(Paragraph(f"P avg: {np.mean(nd['p_hist']):.3f}", styles['Normal']))

        sensors = nd['sensors']
        if sensors:
            content.append(Paragraph("Sensor Readings:", styles['Normal']))
            for k, v in sensors.items():
                if k != 'node':
                    content.append(Paragraph(f"  {k}: {v}", styles['Normal']))

        content.append(Spacer(1, 12))

    if len(network_bti_history) > 5:
        plt.figure(figsize=(8, 3))
        plt.plot(list(network_bti_history), color='#00d4ff')
        plt.title("Network BTI Over Time")
        plt.xlabel("Time Steps")
        plt.ylabel("BTI")
        plt.tight_layout()
        graph_path = "/tmp/network_bti.png"
        plt.savefig(graph_path)
        plt.close()
        content.append(Paragraph("Network BTI Trend", styles['Heading2']))
        content.append(Image(graph_path, width=400, height=150))
        content.append(Spacer(1, 12))

    for node_id in known_nodes:
        bh = node_data[node_id]['bti_history']
        if len(bh) > 5:
            plt.figure(figsize=(8, 3))
            plt.plot(list(bh), color='#00ff88')
            plt.title(f"BTI Over Time - {node_id}")
            plt.xlabel("Time Steps")
            plt.ylabel("BTI")
            plt.tight_layout()
            path = f"/tmp/bti_{node_id}.png"
            plt.savefig(path)
            plt.close()
            content.append(Paragraph(f"BTI Trend - {node_id}", styles['Heading2']))
            content.append(Image(path, width=400, height=150))
            content.append(Spacer(1, 12))

    content.append(Paragraph("Conclusion", styles['Heading2']))
    if net_bti < 0.4:
        conclusion = "The network exhibits critical trust degradation, indicating a severe anomaly or attack."
    elif net_bti < 0.7:
        conclusion = "The network shows moderate instability, suggesting potential anomalous behaviour."
    else:
        conclusion = "The network remains stable with high trust and no significant anomalies."
    content.append(Paragraph(conclusion, styles['Normal']))
    content.append(Spacer(1, 12))

    content.append(Paragraph("Recent Messages", styles['Heading2']))
    for msg in message_log[-15:]:
        content.append(Paragraph(msg, styles['Normal']))

    doc.build(content)
    buffer.seek(0)
    return buffer

# =========================
# UI HELPERS
# =========================
def sensor_badge(label, value, color="#00d4ff"):
    return html.Div(style={
        'backgroundColor': '#0f0f1a',
        'border': f'1px solid {color}',
        'borderRadius': '8px',
        'padding': '8px 14px',
        'textAlign': 'center',
        'minWidth': '80px'
    }, children=[
        html.P(label, style={'color': color, 'fontSize': '10px', 'letterSpacing': '1px', 'margin': '0'}),
        html.P(str(value), style={'color': 'white', 'fontSize': '16px', 'margin': '0', 'fontWeight': 'bold'})
    ])

def metric_badge(label, value, color):
    return html.Div(style={
        'backgroundColor': '#0f0f1a',
        'border': f'1px solid {color}',
        'borderRadius': '8px',
        'padding': '8px 14px',
        'textAlign': 'center',
        'minWidth': '70px',
        'flex': '1'
    }, children=[
        html.P(label, style={'color': color, 'fontSize': '10px', 'letterSpacing': '2px', 'margin': '0', 'fontWeight': 'bold'}),
        html.P(f"{value:.2f}", style={'color': 'white', 'fontSize': '20px', 'margin': '0', 'fontWeight': 'bold'})
    ])

def node_card(node_id, bti_val, sensors, online, alert_text, F, T, D, P):
    color = bti_color(bti_val)
    status_color = "#00ff88" if online else "#ff3b3b"
    status_text = "ONLINE" if online else "OFFLINE"

    # Sensor badges
    badges = []
    for k, v in sensors.items():
        if k not in ('node', 'ip', 'rssi'):
            badges.append(sensor_badge(k.upper(), round(v, 2) if isinstance(v, float) else v))
    if 'rssi' in sensors:
        badges.append(sensor_badge("RSSI", sensors['rssi'], "#7c3aed"))

    return html.Div(style={
        'backgroundColor': '#1a1a2e',
        'border': f'2px solid {color}',
        'borderRadius': '12px',
        'padding': '20px',
        'marginBottom': '20px'
    }, children=[

        # Header row — node name + online status
        html.Div(style={'display': 'flex', 'justifyContent': 'space-between', 'alignItems': 'center', 'marginBottom': '12px'}, children=[
            html.H3(node_id.upper(), style={'color': color, 'margin': '0'}),
            html.Span(status_text, style={
                'backgroundColor': status_color,
                'color': 'black',
                'padding': '4px 10px',
                'borderRadius': '20px',
                'fontSize': '11px',
                'fontWeight': 'bold'
            }),
        ]),

        # BTI score + alert
        html.Div(style={'display': 'flex', 'alignItems': 'center', 'gap': '10px', 'marginBottom': '16px'}, children=[
            html.Span("BTI:", style={'color': '#aaa', 'fontSize': '13px'}),
            html.Span(f"{bti_val:.3f}", style={'color': color, 'fontSize': '28px', 'fontWeight': 'bold'}),
            html.Span(f"| {alert_text}" if alert_text else "", style={'color': '#ff3b3b', 'fontSize': '13px'})
        ]),

        # F T D P metric badges
        html.P("BEHAVIOURAL METRICS", style={'color': '#555', 'fontSize': '10px', 'letterSpacing': '2px', 'margin': '0 0 8px 0'}),
        html.Div(style={'display': 'flex', 'gap': '10px', 'marginBottom': '16px'}, children=[
            metric_badge("F", F, "#00d4ff"),
            metric_badge("T", T, "#7c3aed"),
            metric_badge("D", D, "#00ff88"),
            metric_badge("P", P, "#ffaa00"),
        ]),

        # Divider
        html.Hr(style={'borderColor': '#2a2a3e', 'margin': '0 0 12px 0'}),

        # Sensor readings label
        html.P("SENSOR READINGS", style={'color': '#555', 'fontSize': '10px', 'letterSpacing': '2px', 'margin': '0 0 8px 0'}),

        # Sensor badges
        html.Div(style={'display': 'flex', 'gap': '10px', 'flexWrap': 'wrap'}, children=badges),

        # IP address
        html.P(f"IP: {sensors.get('ip', 'N/A')}", style={'color': '#555', 'fontSize': '11px', 'marginTop': '10px', 'marginBottom': '0'})
    ])

def card(title, id, color):
    return html.Div(style={
        'backgroundColor': '#1a1a2e',
        'border': f'2px solid {color}',
        'borderRadius': '12px',
        'padding': '20px',
        'flex': '1',
        'textAlign': 'center'
    }, children=[
        html.P(title, style={'color': color}),
        html.H2(id=id)
    ])

# =========================
# APP
# =========================
app = dash.Dash(__name__)

app.layout = html.Div(style={
    'backgroundColor': '#0f0f1a',
    'padding': '30px',
    'color': 'white',
    'fontFamily': 'sans-serif'
}, children=[

    html.H1("IoT Trust-Based Monitoring Dashboard", style={'color': '#00d4ff'}),
    html.P("Behaviour-Based Trust Index (BTI) — Multi-Node Network"),
    html.Hr(style={'borderColor': '#00d4ff'}),

    html.Div(id='alert-banner', style={'display': 'none', 'padding': '10px', 'borderRadius': '8px', 'marginBottom': '20px'}),

    # Attack buttons
    html.Div(style={'display': 'flex', 'gap': '20px', 'flexWrap': 'wrap', 'marginBottom': '30px'}, children=[
        html.Div(style={'backgroundColor': '#1a1a2e', 'border': '2px solid #555', 'borderRadius': '12px', 'padding': '20px', 'flex': '1', 'textAlign': 'center', 'cursor': 'pointer', 'minWidth': '120px'}, children=[
            html.P("NONE", style={'color': '#555', 'margin': '0 0 8px 0', 'fontSize': '11px', 'letterSpacing': '2px'}),
            html.Button("Normal", id='btn-none', style={'background': 'transparent', 'border': 'none', 'color': '#aaa', 'fontSize': '18px', 'cursor': 'pointer'})
        ]),
        html.Div(style={'backgroundColor': '#1a1a2e', 'border': '2px solid #7c3aed', 'borderRadius': '12px', 'padding': '20px', 'flex': '1', 'textAlign': 'center', 'cursor': 'pointer', 'minWidth': '120px'}, children=[
            html.P("SIMULATE", style={'color': '#7c3aed', 'margin': '0 0 8px 0', 'fontSize': '11px', 'letterSpacing': '2px'}),
            html.Button("Replay", id='btn-replay', style={'background': 'transparent', 'border': 'none', 'color': 'white', 'fontSize': '18px', 'cursor': 'pointer'})
        ]),
        html.Div(style={'backgroundColor': '#1a1a2e', 'border': '2px solid #7c3aed', 'borderRadius': '12px', 'padding': '20px', 'flex': '1', 'textAlign': 'center', 'cursor': 'pointer', 'minWidth': '120px'}, children=[
            html.P("SIMULATE", style={'color': '#7c3aed', 'margin': '0 0 8px 0', 'fontSize': '11px', 'letterSpacing': '2px'}),
            html.Button("Timing", id='btn-timing', style={'background': 'transparent', 'border': 'none', 'color': 'white', 'fontSize': '18px', 'cursor': 'pointer'})
        ]),
        html.Div(style={'backgroundColor': '#1a1a2e', 'border': '2px solid #ff3b3b', 'borderRadius': '12px', 'padding': '20px', 'flex': '1', 'textAlign': 'center', 'cursor': 'pointer', 'minWidth': '120px'}, children=[
            html.P("SIMULATE", style={'color': '#ff3b3b', 'margin': '0 0 8px 0', 'fontSize': '11px', 'letterSpacing': '2px'}),
            html.Button("False Data", id='btn-fdo', style={'background': 'transparent', 'border': 'none', 'color': 'white', 'fontSize': '18px', 'cursor': 'pointer'})
        ]),
        html.Div(style={'backgroundColor': '#1a1a2e', 'border': '2px solid #ff3b3b', 'borderRadius': '12px', 'padding': '20px', 'flex': '1', 'textAlign': 'center', 'cursor': 'pointer', 'minWidth': '120px'}, children=[
            html.P("SIMULATE", style={'color': '#ff3b3b', 'margin': '0 0 8px 0', 'fontSize': '11px', 'letterSpacing': '2px'}),
            html.Button("Flood", id='btn-flood', style={'background': 'transparent', 'border': 'none', 'color': 'white', 'fontSize': '18px', 'cursor': 'pointer'})
        ]),
        html.Div(style={'backgroundColor': '#1a1a2e', 'border': '2px solid #ffaa00', 'borderRadius': '12px', 'padding': '20px', 'flex': '1', 'textAlign': 'center', 'cursor': 'pointer', 'minWidth': '120px'}, children=[
            html.P("SIMULATE", style={'color': '#ffaa00', 'margin': '0 0 8px 0', 'fontSize': '11px', 'letterSpacing': '2px'}),
            html.Button("Mimic", id='btn-mimic', style={'background': 'transparent', 'border': 'none', 'color': 'white', 'fontSize': '18px', 'cursor': 'pointer'})
        ]),
        html.Div(style={'backgroundColor': '#1a1a2e', 'border': '2px solid #00d4ff', 'borderRadius': '12px', 'padding': '20px', 'flex': '1', 'textAlign': 'center', 'cursor': 'pointer', 'minWidth': '120px'}, children=[
            html.P("EXPORT", style={'color': '#00d4ff', 'margin': '0 0 8px 0', 'fontSize': '11px', 'letterSpacing': '2px'}),
            html.Button("Report", id='btn-download', style={'background': 'transparent', 'border': 'none', 'color': 'white', 'fontSize': '18px', 'cursor': 'pointer'})
        ]),
    ]),

    dcc.Download(id="download"),

    html.H3("Network Overview", style={'color': '#00d4ff'}),
    html.Div(style={'display': 'flex', 'gap': '20px', 'marginBottom': '20px'}, children=[
        card("NETWORK BTI", "network-bti", "#00d4ff"),
        card("ACTIVE NODES", "active-nodes", "#00ff88"),
        card("ALERTS", "network-alerts", "#ff3b3b"),
    ]),

    dcc.Graph(id='network-bti-graph'),

    html.Hr(style={'borderColor': '#1a1a2e', 'marginTop': '30px'}),

    html.H3("Node Status", style={'color': '#00d4ff'}),
    html.Div(id='node-cards'),

    html.H3("Message Log"),
    html.Div(id='message-log', style={'fontSize': '12px', 'color': '#aaa'}),

    dcc.Interval(id='interval', interval=1000)
])

# =========================
# ATTACK BUTTONS
# =========================
@app.callback(
    Output('alert-banner', 'children'),
    Output('alert-banner', 'style'),
    Input('btn-none', 'n_clicks'),
    Input('btn-replay', 'n_clicks'),
    Input('btn-timing', 'n_clicks'),
    Input('btn-fdo', 'n_clicks'),
    Input('btn-flood', 'n_clicks'),
    Input('btn-mimic', 'n_clicks')
)
def set_attack(*args):
    global active_attack
    ctx = dash.callback_context
    if not ctx.triggered:
        return "", {'display': 'none'}

    btn = ctx.triggered[0]['prop_id'].split('.')[0]
    mapping = {
        'btn-none': "NONE",
        'btn-replay': "REPLAY",
        'btn-timing': "TIMING",
        'btn-fdo': "FDO",
        'btn-flood': "FLOOD",
        'btn-mimic': "MIMIC"
    }
    active_attack = mapping[btn]
    return f"Active Attack: {active_attack}", {
        'display': 'block',
        'backgroundColor': '#ffaa00',
        'color': 'black',
        'padding': '10px',
        'borderRadius': '8px',
        'marginBottom': '20px'
    }

# =========================
# MAIN UPDATE
# =========================
@app.callback(
    Output('node-cards', 'children'),
    Output('network-bti', 'children'),
    Output('active-nodes', 'children'),
    Output('network-alerts', 'children'),
    Output('network-bti-graph', 'figure'),
    Output('message-log', 'children'),
    Input('interval', 'n_intervals'),
    prevent_initial_call=True
)
def update(n):
    alert_count = 0
    cards = []

    for node_id in known_nodes:
        F = F_metric(node_id)
        T = T_metric(node_id)
        D = D_metric(node_id)
        P = P_metric(node_id)
        F, T, D, P = apply_attack(F, T, D, P)

        bti_val = compute_bti(node_id, F, T, D, P)

        node_data[node_id]['f_hist'].append(F)
        node_data[node_id]['t_hist'].append(T)
        node_data[node_id]['d_hist'].append(D)
        node_data[node_id]['p_hist'].append(P)
        node_data[node_id]['bti_history'].append(bti_val)
        node_data[node_id]['time_axis'].append(time.strftime("%H:%M:%S"))

        alert_text, _ = detect_attack(F, T, D, P)
        if alert_text:
            alert_count += 1

        online = is_online(node_id)
        sensors = node_data[node_id]['sensors']

        # Pass F T D P into node_card
        cards.append(node_card(node_id, bti_val, sensors, online, alert_text, F, T, D, P))

    net_bti = compute_network_bti()
    network_bti_history.append(net_bti)
    network_time_axis.append(time.strftime("%H:%M:%S"))

    fig = {
        'data': [{'x': list(network_time_axis), 'y': list(network_bti_history), 'line': {'color': '#00d4ff'}, 'name': 'Network BTI'}],
        'layout': {
            'paper_bgcolor': '#0f0f1a',
            'plot_bgcolor': '#1a1a2e',
            'font': {'color': 'white'},
            'yaxis': {'range': [0, 1]},
            'margin': {'t': 20},
            'title': {'text': 'Network BTI Over Time', 'font': {'color': '#00d4ff'}}
        }
    }

    log = [html.Div(f"-> {m}") for m in message_log[-15:]]

    return cards, f"{net_bti:.3f}", str(len(known_nodes)), str(alert_count), fig, log

# =========================
# DOWNLOAD
# =========================
@app.callback(
    Output("download", "data"),
    Input("btn-download", "n_clicks"),
    prevent_initial_call=True
)
def download(n):
    return dcc.send_bytes(generate_pdf().read(), "report.pdf")

# =========================
# RUN
# =========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
