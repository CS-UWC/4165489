"""
=========================================================
PAYLOAD BUILDER
=========================================================
Builds MQTT payloads structurally identical to the real
messages of the target node (same field order, same fields,
learned IP, compact JSON like ArduinoJson) so attack traffic
cannot be trivially flagged by payload schema, packet size or
IP differences.

Per-node schemas live in NODE_SCHEMAS. A node is resolved in
this order:

  1. an explicit node_id argument
  2. profile.node, set by adaptive_learner.build_profile()
  3. whichever schema's sensor fields the profile actually
     learned, so an unlabelled node4 profile still resolves
  4. node1 as the historical default

The module-level NODE1_FIELDS / BOOL_FIELDS / INT_FIELDS /
NON_NEGATIVE / DEFAULTS names are kept as node1 aliases
because the attack modules import them directly.
=========================================================
"""

import json
import random

# =========================
# PER-NODE SCHEMAS
# =========================
NODE_SCHEMAS = {
    "node1": {
        "fields": [
            "node", "temp", "humidity", "ozone", "ozone_v",
            "ip", "rssi",
            "node2_online", "node3_online", "node4_online", "node2_sound",
            "node3_distance", "node3_magnet", "network_alerts",
        ],
        # Real sensor readings, i.e. what False Data Injection perturbs.
        "sensor_fields": ("temp", "humidity", "ozone", "ozone_v"),
        "bool_fields": {"node2_online", "node3_online", "node4_online",
                        "node3_magnet"},
        "int_fields": {"ozone", "rssi", "node2_sound", "network_alerts"},
        "non_negative": {"ozone", "node2_sound", "node3_distance",
                         "network_alerts"},
        "defaults": {
            "node": "node1",
            "temp": 24.0,
            "humidity": 60.0,
            "ozone": 450,
            "ozone_v": 0.4,
            "ip": "0.0.0.0",
            "rssi": -55,
            "node2_online": False,
            "node3_online": True,
            "node4_online": True,
            "node2_sound": 1200,
            "node3_distance": 25.0,
            "node3_magnet": False,
            "network_alerts": 0,
        },
    },
    # Mirrors Node4\Node4.ino (AHT20 on I2C GPIO21/22 + LDR on GPIO34).
    # Field order and names must stay identical to the firmware's doc[]
    # build, because the payload is meant to be indistinguishable from a
    # genuine node4 message on the broker. temp and humidity are raw AHT20
    # floats (not rounded) while light_v is rounded to 2dp, exactly as the
    # firmware does.
    "node4": {
        "fields": [
            "node", "temp", "humidity", "light", "light_v",
            "ip", "rssi",
            "node1_online", "node2_online", "node3_online",
            "network_alerts",
        ],
        "sensor_fields": ("temp", "humidity", "light", "light_v"),
        "bool_fields": {"node1_online", "node2_online", "node3_online"},
        "int_fields": {"light", "rssi", "network_alerts"},
        "non_negative": {"light", "network_alerts"},
        "categories": {},
        "defaults": {
            "node": "node4",
            "temp": 24.0,
            "humidity": 60.0,
            "light": 1000,
            "light_v": 0.81,
            "ip": "0.0.0.0",
            "rssi": -58,
            "node1_online": True,
            "node2_online": True,
            "node3_online": True,
            "network_alerts": 0,
        },
    },
    # Mirrors Node2\Node2.ino lines 259-267. Field order is taken verbatim
    # from the firmware's doc[] build so a spoofed payload is byte-faithful to
    # a genuine node2 message.
    #   int   sound   = analogRead(MIC_PIN)          0..4095, ESP32 12-bit ADC
    #   float sound_v = sound * (3.3 / 4095.0)       0.00..3.30 V
    # sound/sound_v are node2's OWN sensors, as distinct from the node2_sound
    # field that node1 reports as a peer echo.
    "node2": {
        "fields": [
            "node", "sound", "sound_v",
            "ip", "rssi",
            "node1_online", "node3_online", "node4_online",
            "network_alerts",
        ],
        "sensor_fields": ("sound", "sound_v"),
        "bool_fields": {"node1_online", "node3_online", "node4_online"},
        "int_fields": {"sound", "rssi", "network_alerts"},
        "non_negative": {"sound", "network_alerts"},
        # String-valued fields the firmware emits verbatim. These cannot be
        # sampled from a Gaussian and must never be perturbed by FDI.
        "categories": {},
        "defaults": {
            "node": "node2",
            "sound": 1200,
            "sound_v": 0.97,
            "ip": "0.0.0.0",
            "rssi": -58,
            "node1_online": True,
            "node3_online": True,
            "node4_online": False,
            "network_alerts": 0,
        },
    },
    # node3 is an ARDUINO NANO (ATmega328P) running nano_node3.ino and talking
    # serial to node3_bridge.py, which republishes to iot/node3/data. It is NOT
    # the ESP32 Node3\Node3.ino that also sits in this repository - that file
    # describes a different, unbuilt node and was the source of two errors here
    # before this was corrected against live traffic via preflight.py:
    #
    #   reed_v   published by Node3.ino, absent from the deployed node. The
    #            Nano's sketch never emits it.
    #   0..4095  ESP32 12-bit ADC. An ATmega328P analogRead is 10-bit, so
    #            reed_ao spans 0..1023.
    #
    # The Nano emits a serial line of the form
    #   NODE=3;DIST=171.85;PROX=far;REED=clear;REEDAO=432;
    # and node3_bridge.py maps DIST->distance, PROX->proximity,
    # REED->reed_do, REEDAO->reed_ao, then adds ip/rssi/topology itself. The
    # field list below is taken verbatim from the observed MQTT payload, so a
    # spoofed node3 message carries exactly the keys the real node sends and
    # no others.
    #
    # proximity and reed_do are categorical strings and cannot enter a
    # Gaussian model, so neither is a sensor_field: perturbing them would be
    # label substitution rather than a false sensor reading.
    "node3": {
        "fields": [
            "node", "distance", "proximity", "reed_do", "reed_ao",
            "ip", "rssi",
            "node1_online", "node2_online", "node4_online",
            "node1_temp", "node2_sound",
            "network_alerts",
        ],
        "sensor_fields": ("distance", "reed_ao"),
        "bool_fields": {"node1_online", "node2_online", "node4_online"},
        "int_fields": {"reed_ao", "rssi", "node2_sound", "network_alerts"},
        "non_negative": {"distance", "reed_ao", "network_alerts"},
        "categories": {
            "proximity": ("unknown", "out_of_range", "very_close",
                          "near", "far", "very_far"),
            "reed_do": ("magnet", "clear"),
        },
        "defaults": {
            "node": "node3",
            "distance": 173.0,
            "proximity": "far",
            "reed_do": "clear",
            "reed_ao": 430,
            "ip": "0.0.0.0",
            "rssi": -61,
            "node1_online": True,
            "node2_online": True,
            "node4_online": False,
            "node1_temp": 24.0,
            "node2_sound": 1200,
            "network_alerts": 0,
        },
    },
}

# Physically realisable range per sensor field, (low, high), in the field's own
# published unit. Used to keep injected False Data Injection values INSIDE what
# the hardware could actually report.
#
# Why this exists: fabricate_value() in Attack_FalseData.py previously applied a
# single unit-blind sigma floor of 0.5 to every sensor field. On a field whose
# real baseline standard deviation is far below that (ozone_v, std 0.0065 V) the
# floor produced a +/-5.0 V excursion against a 0.23-0.26 V baseline, roughly
# twenty times the sensor's full output span. That value is not a plausible
# forgery, it is an impossible one, and a deterministic range check rejects it
# without any statistical model. Clamping to these ranges makes the attack the
# harder and more realistic case: every injected value is individually
# defensible, so only a model that understands the joint behaviour of the
# fields can detect it.
#
# node1 (DHT11 + MQ-131) and node4 (AHT20 + LDR) ranges come from the sensor
# datasheets and the ESP32 12-bit ADC span.
SENSOR_RANGES = {
    "node1": {
        "temp": (0.0, 50.0),        # DHT11 rated 0-50 C, 1 C quantisation
        "humidity": (20.0, 90.0),   # DHT11 rated 20-90 %RH, 2 %RH resolution
        "ozone": (0, 1023),         # MQ-131 12-bit ADC
        "ozone_v": (0.0, 3.3),      # ADC reference
    },
    "node2": {
        "sound": (0, 4095),         # ESP32 12-bit ADC on MIC_PIN
        "sound_v": (0.0, 3.3),
    },
    # node3 is the Arduino Nano (ATmega328P), not an ESP32. Two corrections:
    #   reed_ao   10-bit ADC, so 0..1023 and NOT 0..4095.
    #   distance  nano_node3.ino clamps to DIST_MIN_VALID 5.0 and
    #             DIST_MAX_VALID 400.0 and repeats lastValidDistance
    #             otherwise, so a genuine node3 can never emit <5 or >400.
    #             An injected 2.0 cm is not a plausible forgery at all; it is
    #             a value the hardware has already been told to reject.
    "node3": {
        "distance": (5.0, 400.0),    # firmware validity window, cm
        "reed_ao": (0, 1023),         # ATmega328P 10-bit ADC
    },
    "node4": {
        "temp": (-40.0, 85.0),      # AHT20 accuracy band, C
        "humidity": (0.0, 100.0),   # AHT20, %RH
        "light": (0, 4095),         # LDR via ADC
        "light_v": (0.0, 3.3),
    },
}


def sensor_range(node_id, field):
    """Physically realisable (low, high) for a sensor field, or None."""
    return SENSOR_RANGES.get(node_id, {}).get(field)


# Derived fields and the raw reading each is computed from. Every node in this
# testbed derives its voltage field with the same identity in firmware:
#   node1  float volt     = ozone  * (3.3 / 4095.0)   Node1.ino:238
#   node2  float sound_v  = sound  * (3.3 / 4095.0)   Node2.ino
#   node3  float reed_v   = reed_ao * (3.3 / 4095.0)  Node3.ino
#   node4  float lightV   = light  * (3.3 / 4095.0)   Node4.ino:206
ADC_SPAN = 4095.0
ADC_VREF = 3.3

DERIVED_FIELDS = {
    "node1": {"ozone_v": "ozone"},
    "node2": {"sound_v": "sound"},
    "node3": {"reed_v": "reed_ao"},
    "node4": {"light_v": "light"},
}


def derived_value(node_id, derived, parent_value):
    """Recompute a derived field from its raw parent, as the firmware does.

    Without this, FDI forges each sensor field independently and the forged
    payload contradicts itself: ozone_v must equal ozone * 3.3/4095, so a pair
    that violates that identity is detectable by a single cross-field
    consistency check, with no statistical model and no learning required.
    Keeping the identity intact removes that free signal and leaves only the
    genuinely hard case, where every field is individually plausible and
    mutually consistent yet jointly improbable.
    """
    if derived not in DERIVED_FIELDS.get(node_id, {}):
        return None
    return round(parent_value * (ADC_VREF / ADC_SPAN), 2)

DEFAULT_NODE = "node1"

# Fields that only one node publishes. node1 and node4 overlap on temp and
# humidity, so detect_node() needs a tie-break signal that is unique to a
# node: ozone/node3_* for node1, light for node4. A profile carrying one of
# these is attributed to that node before the shared-field fallback runs.
DISTINCTIVE_FIELDS = {
    "node1": ("ozone", "ozone_v", "node3_distance", "node3_magnet", "node2_sound"),
    "node2": ("sound", "sound_v"),
    "node3": ("distance", "reed_ao", "reed_v", "reed_do", "proximity"),
    "node4": ("light", "light_v"),
}

# Back-compat aliases for node1, used directly by the attack modules.
NODE1_FIELDS = NODE_SCHEMAS["node1"]["fields"]
BOOL_FIELDS = NODE_SCHEMAS["node1"]["bool_fields"]
INT_FIELDS = NODE_SCHEMAS["node1"]["int_fields"]
NON_NEGATIVE = NODE_SCHEMAS["node1"]["non_negative"]
DEFAULTS = NODE_SCHEMAS["node1"]["defaults"]


def node_from_topic(topic):
    """'iot/node4/data' -> 'node4'. Falls back to node1."""
    if not topic:
        return DEFAULT_NODE
    for part in str(topic).split("/"):
        if part.startswith("node"):
            return part
    return DEFAULT_NODE


def schema_for(node_id):
    return NODE_SCHEMAS.get(node_id, NODE_SCHEMAS[DEFAULT_NODE])


def detect_node(profile, node_id=None):
    """Resolve which node schema applies to this profile.

    Matching is done on DISTINCTIVE_FIELDS first, then on the full
    sensor_fields. The two-pass order matters: node1 and node4 both
    publish temp and humidity, so a single sensor_fields pass would
    send every unlabelled node4 profile to node1 just because node1 is
    listed first in NODE_SCHEMAS.
    """
    if node_id and node_id in NODE_SCHEMAS:
        return node_id

    labelled = getattr(profile, "node", None)
    if labelled in NODE_SCHEMAS:
        return labelled

    stats = getattr(profile, "field_stats", None) or {}
    for candidate, distinctive in DISTINCTIVE_FIELDS.items():
        if any(f in stats for f in distinctive):
            return candidate

    for candidate, schema in NODE_SCHEMAS.items():
        if any(f in stats for f in schema["sensor_fields"]):
            return candidate

    return DEFAULT_NODE


def sensor_fields_for(node_id=None):
    """The real sensor fields for a node, used by False Data Injection."""
    return schema_for(node_id)["sensor_fields"]


def defaults_for(node_id=None):
    return schema_for(node_id)["defaults"]


def _clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


def sample_field(profile, field, default, bool_fields=None,
                 int_fields=None, non_negative=None, categories=None):
    """Sample a field value from the learned distribution of that field."""
    bool_fields = BOOL_FIELDS if bool_fields is None else bool_fields
    int_fields = INT_FIELDS if int_fields is None else int_fields
    non_negative = NON_NEGATIVE if non_negative is None else non_negative
    categories = categories or {}

    stats = profile.field_stats.get(field)
    if not stats:
        return default

    # Categorical string fields (node3 proximity / reed_do) are emitted by the
    # firmware as labels, not numbers. Reproduce whichever label the node was
    # observed publishing most often, falling back to the schema default.
    if field in categories:
        mean = stats.get('mean')
        best = default
        if isinstance(mean, dict):
            best = max(mean, key=mean.get)
        elif isinstance(mean, str):
            best = mean
        return best if best in categories[field] else default

    mean = stats['mean']
    std = max(stats['std'], 1e-6)
    if field in bool_fields:
        return random.random() < _clamp(mean)
    if field in int_fields:
        value = int(round(random.gauss(mean, std)))
        if field in non_negative:
            value = max(0, value)
        return value
    value = round(random.gauss(mean, std), 2)
    if field in non_negative:
        value = max(0.0, value)
    return value


def build_payload(profile, overrides=None, node_id=None):
    """Build a JSON payload string matching the target node's real schema.

    overrides: {field: value} to replace (used by attacks to
    perturb specific fields while everything else stays normal).
    """
    overrides = overrides or {}
    node = detect_node(profile, node_id)
    schema = NODE_SCHEMAS[node]
    fields = schema["fields"]
    defaults = schema["defaults"]
    bool_fields = schema["bool_fields"]
    int_fields = schema["int_fields"]
    non_negative = schema["non_negative"]
    categories = schema.get("categories", {})

    doc = {}
    for field in fields:
        if field in overrides:
            doc[field] = overrides[field]
        elif field == "node":
            doc[field] = node
        elif field == "ip":
            doc[field] = profile.string_stats.get("ip", defaults["ip"])
        else:
            doc[field] = sample_field(
                profile, field, defaults[field],
                bool_fields=bool_fields,
                int_fields=int_fields,
                non_negative=non_negative,
                categories=categories,
            )
    return json.dumps(doc, separators=(",", ":"), ensure_ascii=False)
