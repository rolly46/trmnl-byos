import os
import json
import uuid
import hashlib
import math
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import Flask, request, jsonify, send_from_directory, render_template_string
from PIL import Image, ImageDraw, ImageFont

app = Flask(__name__)
app.config['PREFERRED_URL_SCHEME'] = 'https'

DATA_DIR = os.environ.get("DATA_DIR", "/data")
DEVICES_FILE = os.path.join(DATA_DIR, "devices.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
TIMEZONE = ZoneInfo("Australia/Melbourne")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(os.path.join(DATA_DIR, "images"), exist_ok=True)

DEFAULT_SETTINGS = {"refresh_rate": 900}


def load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE) as f:
            return {**DEFAULT_SETTINGS, **json.load(f)}
    return dict(DEFAULT_SETTINGS)


def save_settings(settings):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=2)

# ── Every surfable beach on Phillip Island + nearby ──────────────────────
# Excluded: Summerland Bay (penguin restricted), Cowes (no surf)
BEACHES = [
    # South coast — main swell window
    # South coast — main swell window
    {"name": "Cape Woolamai",     "lat": -38.545, "lon": 145.357, "facing": 200, "offshore": 20,  "level": "Adv",     "type": "Beach/reef"},
    {"name": "Woolamai Beach",    "lat": -38.540, "lon": 145.345, "facing": 195, "offshore": 15,  "level": "Int-Adv", "type": "Beach"},
    {"name": "Sunderland Bay",    "lat": -38.530, "lon": 145.310, "facing": 190, "offshore": 10,  "level": "Int",     "type": "Beach"},
    {"name": "Smiths Beach",      "lat": -38.520, "lon": 145.275, "facing": 195, "offshore": 15,  "level": "Beg-Int", "type": "Beach"},
    {"name": "YCW",               "lat": -38.518, "lon": 145.265, "facing": 200, "offshore": 20,  "level": "Adv",     "type": "Reef"},
    {"name": "Punchbowl",         "lat": -38.515, "lon": 145.255, "facing": 210, "offshore": 30,  "level": "Int-Adv", "type": "Reef"},
    {"name": "Berry's Beach",     "lat": -38.515, "lon": 145.250, "facing": 210, "offshore": 30,  "level": "Int-Adv", "type": "Beach"},
    {"name": "Cat Bay",           "lat": -38.510, "lon": 145.230, "facing": 220, "offshore": 40,  "level": "Int",     "type": "Beach/reef"},
    {"name": "Forrest Caves",     "lat": -38.510, "lon": 145.215, "facing": 225, "offshore": 45,  "level": "Adv",     "type": "Reef"},
    {"name": "Flynn's Reef",      "lat": -38.508, "lon": 145.200, "facing": 225, "offshore": 45,  "level": "Adv",     "type": "Reef"},
    {"name": "The Colonnades",    "lat": -38.505, "lon": 145.185, "facing": 230, "offshore": 50,  "level": "Adv",     "type": "Reef"},
    {"name": "Pyramid Rock",      "lat": -38.510, "lon": 145.170, "facing": 225, "offshore": 45,  "level": "Expert",  "type": "Reef"},
    {"name": "Observation Pt",    "lat": -38.500, "lon": 145.140, "facing": 240, "offshore": 60,  "level": "Expert",  "type": "Reef"},
    # Bass Strait side
    {"name": "Surf Beach",        "lat": -38.510, "lon": 145.385, "facing": 135, "offshore": 315, "level": "Beg-Int", "type": "Beach"},
]

NUM_DISPLAY = 3  # Show top 3 on the e-ink


def get_base_url():
    host = request.headers.get('X-Forwarded-Host', request.host)
    return f"https://{host}"


def load_devices():
    if os.path.exists(DEVICES_FILE):
        with open(DEVICES_FILE) as f:
            return json.load(f)
    return {}


def save_devices(devices):
    with open(DEVICES_FILE, "w") as f:
        json.dump(devices, f, indent=2)


def generate_api_key(mac):
    return hashlib.sha256(f"{mac}-{uuid.uuid4()}".encode()).hexdigest()[:32]


def generate_friendly_id(mac):
    return mac.replace(":", "")[-6:].upper()


# ── Surf data ────────────────────────────────────────────────────────────

def deg_to_compass(deg):
    dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return dirs[int((deg + 11.25) / 22.5) % 16]


def wave_label(h):
    """Human-readable wave height description."""
    if h < 0.3:  return "Flat"
    if h < 0.6:  return "Ankle"
    if h < 0.8:  return "Knee"
    if h < 1.0:  return "Waist"
    if h < 1.3:  return "Chest"
    if h < 1.6:  return "Shoulder"
    if h < 1.9:  return "Head high"
    if h < 2.3:  return "Overhead"
    if h < 3.0:  return "OH+"
    return "Double OH"


def _grid_key(lat, lon):
    """Round to 0.05 degree grid — Open-Meteo snaps to ~0.25 deg anyway."""
    return (round(lat, 1), round(lon, 1))


def _fetch_point(lat, lon):
    """Fetch marine + wind for a single grid point."""
    marine = requests.get(
        "https://marine-api.open-meteo.com/v1/marine",
        params={
            "latitude": lat, "longitude": lon,
            "current": "wave_height,wave_direction,wave_period,"
                       "swell_wave_height,swell_wave_direction,swell_wave_period",
            "timezone": "Australia/Melbourne",
        }, timeout=15
    ).json()

    wind = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat, "longitude": lon,
            "current": "wind_speed_10m,wind_direction_10m,wind_gusts_10m,"
                       "temperature_2m",
            "timezone": "Australia/Melbourne",
        }, timeout=15
    ).json()

    mc = marine.get("current", {})
    wc = wind.get("current", {})
    return {
        "wave_height": mc.get("wave_height", 0),
        "wave_dir": mc.get("wave_direction", 0),
        "wave_period": mc.get("wave_period", 0),
        "swell_height": mc.get("swell_wave_height", 0),
        "swell_dir": mc.get("swell_wave_direction", 0),
        "swell_period": mc.get("swell_wave_period", 0),
        "wind_speed": wc.get("wind_speed_10m", 0),
        "wind_dir": wc.get("wind_direction_10m", 0),
        "wind_gusts": wc.get("wind_gusts_10m", 0),
        "temp": wc.get("temperature_2m", 0),
        "ok": True,
    }


_EMPTY = {
    "wave_height": 0, "wave_dir": 0, "wave_period": 0,
    "swell_height": 0, "swell_dir": 0, "swell_period": 0,
    "wind_speed": 0, "wind_dir": 0, "wind_gusts": 0,
    "temp": 0, "ok": False,
}


def fetch_all_conditions():
    """Fetch unique grid points in parallel, then map to all beaches."""
    # Group beaches by grid point
    grid_points = {}
    for b in BEACHES:
        key = _grid_key(b["lat"], b["lon"])
        grid_points[key] = key

    # Fetch unique points in parallel
    grid_data = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(_fetch_point, k[0], k[1]): k for k in grid_points}
        for f in as_completed(futures):
            key = futures[f]
            try:
                grid_data[key] = f.result()
            except Exception:
                grid_data[key] = dict(_EMPTY)

    # Map grid data to each beach
    results = []
    for b in BEACHES:
        key = _grid_key(b["lat"], b["lon"])
        cond = grid_data.get(key, dict(_EMPTY))
        data = {
            "name": b["name"], "level": b["level"], "type": b["type"],
            "facing": b["facing"], "offshore": b["offshore"],
            **cond,
        }
        rating, desc = rate_beach(data)
        data["rating"] = rating
        data["desc"] = desc
        results.append(data)

    return results


def rate_beach(d):
    """Rate 0-5 and generate concise description."""
    wh = d["wave_height"]
    sp = d["swell_period"]
    ws = d["wind_speed"]
    wd = d["wind_dir"]
    facing = d["facing"]
    offshore = d["offshore"]

    if wh < 0.3:
        return 0.0, "Flat"

    score = 0.0

    # Wave size — sweet spot 0.8-2.2m
    if 0.8 <= wh <= 2.2:
        score += 2.0
    elif wh > 2.2:
        score += 1.2
    elif wh >= 0.5:
        score += 0.8
    else:
        score += 0.3

    # Swell period
    if sp >= 12:
        score += 1.5
    elif sp >= 9:
        score += 1.0
    elif sp >= 6:
        score += 0.5

    # Wind — offshore is king
    wind_off = abs(((wd - offshore) + 180) % 360 - 180)
    wind_on = abs(((wd - facing) + 180) % 360 - 180)

    if ws < 6:
        score += 1.5
    elif wind_off < 45:
        score += 1.3
    elif wind_on > 120:
        score += 0.8
    elif wind_on < 45:
        score -= 0.5

    score = max(0.0, min(5.0, score))

    # Description
    parts = []
    parts.append(wave_label(wh))

    if sp >= 12:
        parts.append(f"clean {sp:.0f}s lines")
    elif sp >= 9:
        parts.append(f"{sp:.0f}s period")
    else:
        parts.append("short period")

    if ws < 6:
        parts.append("glassy")
    elif wind_off < 45:
        parts.append(f"offshore {deg_to_compass(wd)}")
    elif wind_on < 45:
        parts.append(f"onshore {ws:.0f}km/h")
    else:
        parts.append(f"{deg_to_compass(wd)} {ws:.0f}km/h")

    desc = " / ".join(parts)
    return round(score, 1), desc


def fetch_all_and_rank():
    """Fetch all beaches, rate, return top N."""
    results = fetch_all_conditions()
    results.sort(key=lambda x: (-x["rating"], -x["wave_height"]))
    temp = next((b["temp"] for b in results if b.get("ok")), 0)
    return results[:NUM_DISPLAY], len(results), temp


# ── Image generation ─────────────────────────────────────────────────────

def load_fonts():
    b = "/usr/share/fonts/truetype/dejavu"
    try:
        return {
            "header":     ImageFont.truetype(f"{b}/DejaVuSans-Bold.ttf", 24),
            "time":       ImageFont.truetype(f"{b}/DejaVuSans-Bold.ttf", 24),
            "beach":      ImageFont.truetype(f"{b}/DejaVuSans-Bold.ttf", 32),
            "wave_num":   ImageFont.truetype(f"{b}/DejaVuSans-Bold.ttf", 48),
            "wave_unit":  ImageFont.truetype(f"{b}/DejaVuSans-Bold.ttf", 24),
            "wave_lbl":   ImageFont.truetype(f"{b}/DejaVuSans-Bold.ttf", 22),
            "stat_val":   ImageFont.truetype(f"{b}/DejaVuSans-Bold.ttf", 24),
            "stat_lbl":   ImageFont.truetype(f"{b}/DejaVuSans-Bold.ttf", 18),
            "desc":       ImageFont.truetype(f"{b}/DejaVuSans-Bold.ttf", 20),
            "tag":        ImageFont.truetype(f"{b}/DejaVuSans-Bold.ttf", 15),
            "footer":     ImageFont.truetype(f"{b}/DejaVuSans-Bold.ttf", 14),
            "rank":       ImageFont.truetype(f"{b}/DejaVuSans-Bold.ttf", 28),
            "col_hdr":    ImageFont.truetype(f"{b}/DejaVuSans-Bold.ttf", 14),
        }
    except (OSError, IOError):
        f = ImageFont.load_default()
        return {k: f for k in ["header", "time", "beach", "wave_num",
                                "wave_unit", "wave_lbl", "stat_val", "stat_lbl",
                                "desc", "tag", "footer", "rank", "col_hdr"]}


def draw_rating_bar(draw, x, y, rating, w=80, h=8):
    draw.rounded_rectangle([x, y, x + w, y + h], radius=4, fill=220)
    fill_w = max(0, int((rating / 5.0) * w))
    if fill_w > 2:
        draw.rounded_rectangle([x, y, x + fill_w, y + h], radius=4, fill=0)


def draw_wind_arrow(draw, cx, cy, direction, size=10):
    rad = math.radians(direction)
    dx, dy = math.sin(rad), -math.cos(rad)
    x1, y1 = cx + dx * size, cy + dy * size
    x2, y2 = cx - dx * size, cy - dy * size
    draw.line([(x2, y2), (x1, y1)], fill=0, width=2)
    ax = x1 - dx * 4 - dy * 3
    ay = y1 - dy * 4 + dx * 3
    bx = x1 - dx * 4 + dy * 3
    by = y1 - dy * 4 - dx * 3
    draw.polygon([(x1, y1), (ax, ay), (bx, by)], fill=0)


def generate_surf_image(device):
    W, H = 960, 540
    img = Image.new("L", (W, H), 255)
    draw = ImageDraw.Draw(img)
    fonts = load_fonts()

    top4, total, temp = fetch_all_and_rank()
    now = datetime.now(TIMEZONE)

    # ── Thin header ────────────────────────────────────────────────
    draw.rectangle([0, 0, W, 40], fill=0)
    draw.text((20, 8), "SURF REPORT", font=fonts["header"], fill=255)
    draw.text((W - 20, 8), f"Updated {now.strftime('%H:%M %a %d %b')}   {temp:.0f}\u00b0C",
              font=fonts["header"], fill=255, anchor="rt")

    # ── Column headers ───────────────────────────────────────────────
    hy = 46
    draw.text((70, hy), "BEST SPOTS NOW", font=fonts["col_hdr"], fill=100)
    draw.text((490, hy), "WAVES", font=fonts["col_hdr"], fill=100)
    draw.text((640, hy), "SWELL", font=fonts["col_hdr"], fill=100)
    draw.text((800, hy), "WIND", font=fonts["col_hdr"], fill=100)
    draw.line([(20, hy + 20), (W - 20, hy + 20)], fill=140, width=2)

    # ── Beach rows — 3 rows, ~150px each ─────────────────────────
    row_y = 72
    row_h = 150

    for i, b in enumerate(top4):
        y = row_y + i * row_h

        # Alternating background
        if i % 2 == 0:
            draw.rounded_rectangle([8, y, W - 8, y + row_h - 8], radius=10, fill=238)

        # Rank circle
        rx, ry = 38, y + 38
        if i == 0:
            draw.ellipse([rx - 22, ry - 22, rx + 22, ry + 22], fill=0)
            draw.text((rx, ry), "1", font=fonts["rank"], fill=255, anchor="mm")
        else:
            draw.ellipse([rx - 22, ry - 22, rx + 22, ry + 22], outline=0, width=3)
            draw.text((rx, ry), str(i + 1), font=fonts["rank"], fill=0, anchor="mm")

        # Beach name — big and bold
        draw.text((74, y + 8), b["name"], font=fonts["beach"], fill=0)

        # Tags: level + type
        tag_x = 74
        tag_y = y + 48
        for tag_text in [b["level"], b["type"]]:
            tw = draw.textlength(tag_text, font=fonts["tag"])
            draw.rounded_rectangle([tag_x, tag_y, tag_x + tw + 16, tag_y + 24],
                                   radius=5, outline=0, width=2)
            draw.text((tag_x + 8, tag_y + 3), tag_text, font=fonts["tag"], fill=0)
            tag_x += tw + 22

        # Rating bar
        draw_rating_bar(draw, 74, y + 84, b["rating"], w=130, h=14)
        draw.text((212, y + 80), f"{b['rating']}/5", font=fonts["stat_lbl"], fill=40)

        # Description
        draw.text((74, y + 108), b["desc"], font=fonts["desc"], fill=20)

        # ── Stats — 3 columns ────────────────────────────────────

        # WAVES — big number
        draw.text((480, y + 6), f"{b['wave_height']:.1f}", font=fonts["wave_num"], fill=0)
        draw.text((564, y + 18), "m", font=fonts["wave_unit"], fill=40)
        draw.text((480, y + 62), wave_label(b["wave_height"]), font=fonts["wave_lbl"], fill=20)
        draw.text((480, y + 92), f"{b['wave_period']:.0f}s  {deg_to_compass(b['wave_dir'])}",
                  font=fonts["stat_lbl"], fill=70)

        # SWELL
        draw.text((640, y + 10), f"{b['swell_height']:.1f}m", font=fonts["stat_val"], fill=0)
        draw.text((640, y + 42), f"{b['swell_period']:.0f}s period", font=fonts["stat_val"], fill=0)
        draw.text((640, y + 74), deg_to_compass(b["swell_dir"]), font=fonts["stat_val"], fill=40)

        # WIND
        draw.text((800, y + 10), f"{b['wind_speed']:.0f}", font=fonts["stat_val"], fill=0)
        draw.text((846, y + 12), "km/h", font=fonts["stat_lbl"], fill=40)
        draw.text((800, y + 42), deg_to_compass(b["wind_dir"]), font=fonts["stat_val"], fill=40)
        draw.text((800, y + 74), f"gust {b['wind_gusts']:.0f}", font=fonts["stat_lbl"], fill=60)
        draw_wind_arrow(draw, W - 26, y + 30, b["wind_dir"], size=18)

    # ── Footer ───────────────────────────────────────────────────────
    fy = H - 22
    draw.rectangle([0, fy - 8, W, fy - 6], fill=0)
    draw.text((24, fy), f"Best {NUM_DISPLAY} beaches right now   |   92 Ocean Reach Dr",
              font=fonts["footer"], fill=60)
    draw.text((W - 24, fy), "Refreshes every 15 min", font=fonts["footer"], fill=100, anchor="rt")

    path = os.path.join(DATA_DIR, "images", f"{device.get('friendly_id', 'surf')}_display.bmp")
    img.save(path, "BMP")
    return path


# ── API routes ───────────────────────────────────────────────────────────

@app.route("/api/setup", methods=["GET"])
def api_setup():
    mac = request.headers.get("ID", "")
    if not mac:
        return jsonify({"status": 400, "message": "Missing ID header"}), 400

    devices = load_devices()
    if mac in devices:
        device = devices[mac]
    else:
        friendly_id = generate_friendly_id(mac)
        api_key = generate_api_key(mac)
        device = {
            "mac": mac, "api_key": api_key, "friendly_id": friendly_id,
            "registered_at": datetime.now().isoformat(),
            "fw_version": request.headers.get("FW-Version", ""),
            "model": request.headers.get("Model", ""),
        }
        devices[mac] = device
        save_devices(devices)

    generate_surf_image(device)
    image_url = f"{get_base_url()}/images/{device['friendly_id']}_display.bmp"

    return jsonify({
        "status": 200, "api_key": device["api_key"],
        "friendly_id": device["friendly_id"],
        "image_url": image_url, "message": "Device registered",
    })


@app.route("/api/display", methods=["GET"])
def api_display():
    mac = request.headers.get("ID", "")
    token = request.headers.get("Access-Token", "")
    if not mac or not token:
        return jsonify({"status": 401, "message": "Missing credentials"}), 401

    devices = load_devices()
    device = devices.get(mac)
    if not device or device.get("api_key") != token:
        return jsonify({"status": 403, "message": "Invalid credentials"}), 403

    device["last_seen"] = datetime.now().isoformat()
    device["battery_voltage"] = request.headers.get("Battery-Voltage", "?")
    device["rssi"] = request.headers.get("RSSI", "?")
    save_devices(devices)

    generate_surf_image(device)
    filename = f"{device['friendly_id']}_display.bmp"

    return jsonify({
        "status": 0, "image_url": f"{get_base_url()}/images/{filename}",
        "filename": filename, "refresh_rate": load_settings()["refresh_rate"],
        "reset_firmware": False, "update_firmware": False,
    })


@app.route("/images/<path:filename>")
def serve_image(filename):
    return send_from_directory(os.path.join(DATA_DIR, "images"), filename)


@app.route("/")
def dashboard():
    devices = load_devices()
    settings = load_settings()
    return render_template_string(DASHBOARD_HTML, devices=devices, settings=settings)


@app.route("/settings", methods=["POST"])
def update_settings():
    settings = load_settings()
    rate = request.form.get("refresh_rate")
    if rate and rate.isdigit() and int(rate) >= 60:
        settings["refresh_rate"] = int(rate)
        save_settings(settings)
    return render_template_string(
        '<script>window.location="/"</script>', devices={}, settings=settings)


DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
<title>Phillip Island Surf</title>
<style>
body{font-family:system-ui,sans-serif;max-width:960px;margin:40px auto;padding:0 20px;color:#1a1a1a}
h1{font-size:28px}
h2{font-size:20px;margin-top:32px}
p{margin:8px 0}
code{background:#f0f0f0;padding:2px 6px;border-radius:4px}
img{max-width:100%;border:1px solid #ddd;border-radius:8px;margin:20px 0}
.settings{background:#f8f8f8;border:1px solid #ddd;border-radius:12px;padding:24px;margin-top:24px}
.settings label{font-weight:600;font-size:15px;display:block;margin-bottom:6px}
.settings select{padding:10px 16px;font-size:16px;border:2px solid #ddd;border-radius:8px;min-width:200px}
.settings button{padding:10px 24px;font-size:16px;font-weight:600;background:#1a1a1a;color:#fff;border:none;border-radius:8px;cursor:pointer;margin-left:12px}
.settings button:hover{background:#333}
.settings .current{color:#666;font-size:14px;margin-top:8px}
</style>
</head>
<body>
<h1>Phillip Island Surf Display</h1>

<div class="settings">
<h2>Settings</h2>
<form action="/settings" method="POST" style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
<div>
<label for="refresh_rate">Refresh Interval</label>
<select name="refresh_rate" id="refresh_rate">
<option value="300" {{ 'selected' if settings.refresh_rate == 300 }}>5 minutes</option>
<option value="600" {{ 'selected' if settings.refresh_rate == 600 }}>10 minutes</option>
<option value="900" {{ 'selected' if settings.refresh_rate == 900 }}>15 minutes</option>
<option value="1800" {{ 'selected' if settings.refresh_rate == 1800 }}>30 minutes</option>
<option value="3600" {{ 'selected' if settings.refresh_rate == 3600 }}>1 hour</option>
<option value="7200" {{ 'selected' if settings.refresh_rate == 7200 }}>2 hours</option>
<option value="14400" {{ 'selected' if settings.refresh_rate == 14400 }}>4 hours</option>
</select>
</div>
<button type="submit">Save</button>
</form>
<p class="current">Currently set to {{ settings.refresh_rate // 60 }} minutes. Takes effect on next device check-in.</p>
</div>

{% for mac, d in devices.items() %}
<h2>Device {{ d.friendly_id }}</h2>
<p>MAC: {{ d.mac }} &mdash; Last check-in: {{ d.get('last_seen', 'never')[:19] }}
&mdash; Battery: {{ d.get('battery_voltage', '?') }}V
&mdash; RSSI: {{ d.get('rssi', '?') }} dBm</p>
<img src="/images/{{ d.friendly_id }}_display.bmp" alt="Surf display">
{% endfor %}
</body>
</html>
"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
