# Phillip Island Surf Display — TRMNL BYOS Server

A self-hosted [TRMNL](https://usetrmnl.com) BYOS server that generates a live surf conditions display for Phillip Island, Australia. Designed for a 960x540 e-ink screen at a holiday house — shows guests and owners the best beaches to surf right now.

## What It Does

Every 15 minutes (configurable), the server:

1. Fetches live marine data (waves, swell, period) and wind data from [Open-Meteo](https://open-meteo.com) for **14 Phillip Island surf beaches**
2. Rates each beach based on wave height, swell period, and wind direction (offshore vs onshore)
3. Picks the **top 3** and generates a 960x540 grayscale BMP image
4. Serves it to the e-ink display via the TRMNL BYOS API

## Beaches Covered

Cape Woolamai, Woolamai Beach, Sunderland Bay, Smiths Beach, YCW, Punchbowl, Berry's Beach, Cat Bay, Forrest Caves, Flynn's Reef, The Colonnades, Pyramid Rock, Observation Point, Surf Beach

Each beach has a skill level tag (Beg-Int through Expert) and break type (Beach/Reef).

## Rating Algorithm

Beaches are scored 0–5 based on:
- **Wave height** — sweet spot 0.8–2.2m scores highest
- **Swell period** — longer period = cleaner waves (12s+ is excellent)
- **Wind** — offshore or glassy conditions score high, onshore penalised

The top 3 are shown ranked with a visual rating bar.

## Deploy to Fly.io

```bash
# Install flyctl
brew install flyctl

# Login
flyctl auth login

# Create app + volume
flyctl apps create trmnl-byos --machines
flyctl volumes create trmnl_data --region sjc --size 1 --yes

# Deploy
flyctl deploy
```

The server runs at `https://trmnl-byos.fly.dev`.

## Configuration

### Web Dashboard

Visit `https://trmnl-byos.fly.dev/` to:
- See registered devices and their last check-in
- Preview the current display image
- **Change the refresh interval** (5 min to 4 hours) via dropdown

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATA_DIR` | `/data` | Persistent storage directory |
| `REFRESH_RATE` | `900` | Initial refresh rate in seconds (overridden by web UI) |

## API Endpoints

Implements the TRMNL BYOS API contract:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/setup` | GET | Device registration — accepts MAC in `ID` header, returns API key |
| `GET /api/display` | GET | Returns image URL and refresh rate |
| `GET /images/<file>` | GET | Serves generated BMP images |
| `GET /` | GET | Web dashboard with settings |
| `POST /settings` | POST | Update refresh interval |

## Hardware

Designed for the [LilyGo T5 4.7" E-Paper](https://github.com/Xinyuan-LilyGO/LilyGo-EPD47) running the companion [trmnl-t5-47](https://github.com/rolly46/trmnl-t5-47) firmware. Any TRMNL-compatible device with a 960x540 display will work.

## Stack

- **Python/Flask** with Gunicorn
- **Pillow** for image generation (DejaVu Sans Bold throughout for e-ink readability)
- **Open-Meteo** Marine + Weather APIs (free, no key required)
- **Fly.io** with persistent volume for device data

## Project Structure

```
├── app.py              # Server — API, surf data fetching, image generation, dashboard
├── requirements.txt    # Python dependencies
├── Dockerfile          # Container build
└── fly.toml            # Fly.io deployment config
```

## Licence

MIT
