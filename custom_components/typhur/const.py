"""Constants for the Typhur integration."""

DOMAIN = "typhur"

CONF_REGION = "region"
DEFAULT_REGION = "us"

TYPHUR_API_BY_REGION: dict[str, str] = {
    "us": "https://api.iot.typhur.com",
    "eu": "https://api.iot.typhur.de",
}

# x-region header value expected by each API endpoint
X_REGION_BY_REGION: dict[str, str] = {
    "us": "US",
    "eu": "NO",
}

# Extracted from Typhur APK
SIGN_CONSTANT = "7d02d81bd7f4483a9a0ac580f2b6ad44"
APP_ID = "ap206cba3069ed4a11"
APP_VERSION = "4200"

# All temperature values in MQTT payloads are integers; divide by 10 to get °F
TEMPERATURE_DIVISOR = 10

# ── globalStatus values ────────────────────────────────────────────────────
STATUS_OFFLINE = "offline"
STATUS_ONLINE = "online"
STATUS_COOKING = "cooking"
STATUS_PAUSED = "paused"
STATUS_DONE = "done"

# ── cookingState integer → friendly name ──────────────────────────────────
# All values confirmed from live capture of a Typhur Dome 2 (model AF04):
#   0 = paused  (both button-pause and basket-removal; basket state distinguishes them)
#   3 = cooking (actively running)
#   4 = done    (timer reached 0; globalStatus stays "cooking")
# Values 1, 2, 5, 6 are inferred and have not been observed yet.
COOKING_STATE_MAP: dict[int, str] = {
    0: "paused",
    1: "preheating",
    2: "preheat_ready",
    3: "cooking",
    4: "done",
    5: "cooling",
    6: "error",
}

# ── cookingMode integer → preset name ─────────────────────────────────────
# Mode 1 = "Air Fry" confirmed from live capture (setTemperature 450°F, standard
# air-fry session).  Remaining presets ordered to match the Dome 2 UI sequence;
# will be validated against live captures as more modes are observed.
COOKING_MODE_MAP: dict[int, str] = {
    0: "Custom",
    1: "Air Fry",
    2: "Roast",
    3: "Bake",
    4: "Broil",
    5: "Reheat",
    6: "Dehydrate",
    7: "Frozen Food",
    8: "Pizza",
    9: "Wings",
    10: "Fries",
    11: "Steak",
    12: "Vegetables",
    13: "Seafood",
    14: "Pork",
}

# curBasketState values
BASKET_IN = 0
BASKET_OUT = 1

# ── Timing / retry config ─────────────────────────────────────────────────
MQTT_INITIAL_RECONNECT_DELAY = 5       # seconds before first reconnect attempt
MQTT_MAX_RECONNECT_DELAY = 300         # cap for exponential back-off (5 minutes)
TOKEN_REFRESH_INTERVAL = 82800         # refresh token every 23 hours
CERT_REFRESH_INTERVAL = 82800          # refresh MQTT certs every 23 hours
FALLBACK_POLL_INTERVAL = 30            # REST poll interval while MQTT is down

# Error / response codes from the Typhur cloud API
API_CODE_OK = "0"
API_CODE_TOKEN_EXPIRED = "52"
