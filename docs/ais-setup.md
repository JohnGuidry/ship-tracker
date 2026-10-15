# /ais/ — Live AIS Ship Tracker

The `/ais/` path on john-guidry.com serves a real-time vessel tracker. An
RTL-SDR dongle captures AIS radio signals, the signal is decoded into NMEA
sentences, stored in SQLite, served by a lightweight Python API, and rendered on
a Leaflet.js map + vessel-presence heatmap in the browser.

## Architecture

```
RTL-SDR dongle (161.975 / 162.025 MHz)
  → rtl_ais (C binary, built from the rtl-ais fork — AIS → NMEA over UDP :10110)
    → ais_udp_listener.py (UDP :10110 → timestamped ais_capture.log)
      ├── ais_api.py (HTTP :8080 — /api/vessels live, /api/heatmap from SQLite)
      └── ais_sqlite_ingest.py (decode NMEA → SQLite positions/vessels, WAL)
            → web/index.html + web/heatmap.html (Leaflet map + vessel-presence heatmap)
              ← Nginx: /ais/ → static, /ais/api/ → :8080
```

The log is the **lossless archive** (retained up to 5 GB); SQLite is the **fast
query layer** (indexed; full-history queries in ~20 ms). The heatmap and any
historical queries read from SQLite, so they scale to the full log window without
decompressing every rotated file.

## Components

### 1. rtl_ais (SDR → NMEA decoder)

Built from the [`JohnGuidry/rtl-ais`](https://github.com/JohnGuidry/rtl-ais) fork
(dgiardini lineage). Tunes the AIS frequencies and outputs NMEA `!AIVDM`
sentences over UDP to port 10110. See [`../rf/README.md`](../rf/README.md).

```bash
rtl_ais -n -p <ppm>        # NMEA to stderr (-n) AND UDP 127.0.0.1:10110
```

PPM calibration is per-dongle — measure with `rtl_test -p` (not `kal -s GSM`,
since US 2G is decommissioned).

### 2. ais_udp_listener.py (UDP → log)

Listens on UDP port 10110, writes each NMEA sentence with a timestamp to
`~/ais_capture.log`:

```
2026-08-09T00:15:42 !AIVDM,1,1,,A,15N4cJ@P00JrSJHDrJP1rLJ004S3,0*3F
```

### 3. ais_sqlite_ingest.py (log → SQLite)

Self-contained (stdlib only) decoder that writes decoded position reports to
`~/ais_data/ais.db` (WAL). Two modes:

- `backfill` — one-time, ingests all rotated logs (idempotent via `INSERT OR
  IGNORE`).
- `tail` — the running service; follows the live log, survives logrotate
  `copytruncate`, starts at end-of-file so it doesn't re-ingest the backfill.

Captures types 1/2/3 (Class A) + 18/19 (Class B) + static 5/24, with a
single-fragment guard and a GPS validity filter. The decoder was verified
line-for-line against `pyais` (35,705/35,705 on real captures) before deployment.

Schema:

```sql
positions (ts, mmsi, msg_type, lat, lon, speed, course, heading,
           UNIQUE(mmsi, ts, msg_type, lat, lon, speed, course, heading))
vessels   (mmsi, name, ship_type, dest, updated)
-- indexes on (mmsi, ts) and (ts)
```

### 4. ais_api.py (JSON API)

Lightweight HTTP server on port 8080. `/api/vessels` tails the live log;
`/api/heatmap` reads the SQLite `positions` table (falling back to the raw logs
if the DB is absent).

**Heatmap method (vessel-presence):** weight per grid cell = number of distinct
`(vessel, 0.005° cell, 10-minute bucket)` presences. A docked ferry beaconing
every 2 s adds 6 units/hour (not 1800); a yacht that transits a cell in 10
minutes adds 1. Heat accumulates where vessels actually spend time. A
median-centered 100 km geo-fence (`AIS_FENCE_KM`) drops broken-GPS transmitters.

### 5. web/ (frontend)

Standalone vanilla HTML/JS using [Leaflet.js](https://leafletjs.com/) with a dark
CARTO basemap:

- Live vessel markers with heading arrows, colored by MMSI
- Position trail lines that fade over 5 minutes
- Vessel sidebar panel with click-to-pan; mobile-responsive collapse
- Stale vessel filtering (30 min timeout); polls `/ais/api/vessels` every 5 s
- `heatmap.html` renders the vessel-presence density grid

`web/*.html` use a `__CARTO_KEY__` placeholder; `deploy/deploy-dashboard.sh`
injects the real key at deploy time (the key is never committed).

## Nginx

See [`../deploy/nginx-ais.conf`](../deploy/nginx-ais.conf). The API backend runs
on the capture host (192.168.1.49:8080); the static dashboard is served from the
site's web root. The site's main deploy must use `--exclude=ais/` so it does not
remove the ship-tracker-deployed dashboard.

## Deployment

- **Capture host:** `sudo bash deploy/install.sh` — idempotent; installs the udev
  rule, the four systemd units, the logrotate config, the USB reset helper, and
  the pipeline/API files, then enables and restarts the services.
- **Dashboard:** `bash deploy/deploy-dashboard.sh` — injects the CARTO key and
  rsyncs `web/` to the site's `/ais/` path.
- **Decoder build:** one-time, see [`../rf/README.md`](../rf/README.md).

## Data retention

- **Log (lossless archive):** `deploy/ais-capture.logrotate` — `size 10M`,
  `rotate 500`, `compress`/`delaycompress` ≈ a **5 GB cap** before deletion begins.
- **SQLite (query layer):** `~/ais_data/ais.db` (WAL), indexed.

## CORS

`ais_api.py` sets `Access-Control-Allow-Origin: *` on responses, so the map page
can be served from any origin.
