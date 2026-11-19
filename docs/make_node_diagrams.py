"""Generate clean wiring diagrams for node3 (Nano) and node4 (ESP32)."""
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Circle

# Write to the images/ folder next to docs/ regardless of the working directory.
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "images")

WIRE = {
    "red": "#c92a2a", "black": "#212529", "orange": "#e8590c",
    "blue": "#1c7ed6", "green": "#2b8a3e", "purple": "#6741d9",
    "yellow": "#f08c00",
}


def board(ax, x, y, w, h, title, subtitle, left, right):
    ax.add_patch(FancyBboxPatch((x, y), w, h,
                                boxstyle="round,pad=0.01,rounding_size=1.5",
                                facecolor="#e9ecef", edgecolor="#343a40", lw=1.8))
    ax.text(x + w / 2, y + h - 4, title, ha="center", va="center",
            fontsize=12, fontweight="bold", color="#102a43")
    ax.text(x + w / 2, y + h - 9, subtitle, ha="center", va="center",
            fontsize=8, color="#495057")
    pts = {}
    for label, py, side in left:
        ax.add_patch(Circle((x, py), 0.9, facecolor="white",
                            edgecolor="#343a40", lw=1.2, zorder=3))
        ax.text(x + 2.0, py, label, ha="left", va="center", fontsize=8.5,
                color="#102a43")
        pts[label] = (x, py)
    for label, py, side in right:
        ax.add_patch(Circle((x + w, py), 0.9, facecolor="white",
                            edgecolor="#343a40", lw=1.2, zorder=3))
        ax.text(x + w - 2.0, py, label, ha="right", va="center", fontsize=8.5,
                color="#102a43")
        pts[label] = (x + w, py)
    return pts


def sensor(ax, x, y, w, h, title, pins, pin_side="left"):
    ax.add_patch(FancyBboxPatch((x, y), w, h,
                                boxstyle="round,pad=0.01,rounding_size=1.2",
                                facecolor="#e7f5ff", edgecolor="#1971c2", lw=1.6))
    ax.text(x + w / 2, y + h - 3.5, title, ha="center", va="center",
            fontsize=10.5, fontweight="bold", color="#0b6efd")
    pts = {}
    span = h - 10
    for i, label in enumerate(pins):
        py = y + h - 7 - i * (span / max(1, len(pins) - 1))
        px = x if pin_side == "left" else x + w
        ax.add_patch(Circle((px, py), 0.9, facecolor="white",
                            edgecolor="#1971c2", lw=1.2, zorder=3))
        if pin_side == "left":
            ax.text(x + 2.0, py, label, ha="left", va="center", fontsize=8.5,
                    color="#102a43")
        else:
            ax.text(x + w - 2.0, py, label, ha="right", va="center", fontsize=8.5,
                    color="#102a43")
        pts[label] = (px, py)
    return pts


def wire(ax, a, b, color, label, label_xy):
    ax.plot([a[0], b[0]], [a[1], b[1]], color=WIRE[color], lw=2.0, zorder=1)
    ax.text(*label_xy, label, color=WIRE[color], fontsize=7.5,
            ha="center", va="center",
            bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.85),
            zorder=4)


def setup(title, subtitle):
    fig, ax = plt.subplots(figsize=(11, 7.5), dpi=140)
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 72)
    ax.set_aspect("equal")
    ax.axis("off")
    fig.patch.set_facecolor("white")
    ax.text(50, 69, title, ha="center", va="center", fontsize=15,
            fontweight="bold", color="#102a43")
    ax.text(50, 65, subtitle, ha="center", va="center", fontsize=9, color="#495057")
    return fig, ax


# ---------------- Node 3 ----------------
fig, ax = setup("Node 3 - Arduino Nano wiring",
                "HC-SR04 ultrasonic + KY-025 reed switch | USB serial 115200 baud -> node3_bridge.py -> iot/node3/data")
nano = board(ax, 8, 14, 34, 46, "ARDUINO NANO", "ATmega328P",
             [("D5", 50, "left"), ("D6", 44, "left"),
              ("D7", 38, "left"), ("A0", 32, "left")],
             [("5V", 50, "right"), ("GND", 44, "right")])
sr = sensor(ax, 70, 38, 24, 22, "HC-SR04",
            ["TRIG", "ECHO", "VCC", "GND"])
reed = sensor(ax, 70, 10, 24, 22, "KY-025 REED",
              ["DO", "AO", "VCC", "GND"])
wire(ax, nano["D5"], sr["TRIG"], "orange", "TRIG", (56, 54))
wire(ax, nano["D6"], sr["ECHO"], "blue", "ECHO", (56, 47))
wire(ax, nano["D7"], reed["DO"], "green", "DO", (56, 33))
wire(ax, nano["A0"], reed["AO"], "purple", "AO", (56, 26))
wire(ax, nano["5V"], (70, sr["VCC"][1]), "red", "5V", (56, 44))
wire(ax, (43, 50), (70, reed["VCC"][1]), "red", "5V", (56, 20))
wire(ax, nano["GND"], (70, sr["GND"][1]), "black", "GND", (56, 40))
wire(ax, (43, 44), (70, reed["GND"][1]), "black", "GND", (56, 14))
ax.text(50, 8, "Reed DO: HIGH = magnet   |   Reed AO: 10-bit (0-1023)   |   DIST < 5 cm discarded",
        ha="center", fontsize=8, color="#495057")
fig.savefig(os.path.join(OUT, "node3.png"), dpi=140, bbox_inches="tight", facecolor="white")
plt.close(fig)

# ---------------- Node 4 ----------------
fig, ax = setup("Node 4 - ESP32-WROOM-32 wiring",
                "AHT20 (I2C) + LDR ambient light | 12-bit ADC | publishes iot/node4/data")
esp = board(ax, 8, 14, 34, 46, "ESP32-WROOM-32", "DevKit V1 (38-pin)",
            [("GPIO21", 50, "left"), ("GPIO22", 44, "left"),
             ("GPIO34", 38, "left")],
            [("3V3", 50, "right"), ("GND", 44, "right")])
aht = sensor(ax, 70, 38, 24, 22, "AHT20",
             ["SDA", "SCL", "VCC", "GND"])
ldr = sensor(ax, 70, 10, 24, 22, "LDR / LIGHT",
             ["AOUT", "VCC", "GND"])
wire(ax, esp["GPIO21"], aht["SDA"], "blue", "SDA (I2C)", (56, 54))
wire(ax, esp["GPIO22"], aht["SCL"], "green", "SCL (I2C)", (56, 47))
wire(ax, esp["GPIO34"], ldr["AOUT"], "purple", "AOUT (ADC1)", (56, 32))
wire(ax, esp["3V3"], (70, aht["VCC"][1]), "red", "3.3V", (56, 44))
wire(ax, (43, 50), (70, ldr["VCC"][1]), "red", "3.3V", (56, 20))
wire(ax, esp["GND"], (70, aht["GND"][1]), "black", "GND", (56, 40))
wire(ax, (43, 44), (70, ldr["GND"][1]), "black", "GND", (56, 14))
ax.text(50, 8, "light_v = raw x 3.3 / 4095   |   AHT20 uses Wire defaults (SDA 21, SCL 22)   |   alerts: temp > 35 C, light > 3000",
        ha="center", fontsize=8, color="#495057")
fig.savefig(os.path.join(OUT, "node4.png"), dpi=140, bbox_inches="tight", facecolor="white")
plt.close(fig)

print("generated node3.png and node4.png")
