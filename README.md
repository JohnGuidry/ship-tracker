# ship-tracker

A self-contained AIS (Automatic Identification System) ship tracker. An RTL-SDR
dongle captures VHF AIS radio signals, the signal is decoded into NMEA
sentences, stored in SQLite, served by a lightweight Python API, and rendered on
a Leaflet.js map + vessel-presence heatmap in the browser.

Live at: **https://john-guidry.com/ais/**

## Architecture

```
RTL-SDR dongle (161.975 / 162.025 MHz)
  → rtl_ais            (C binary, built from the rtl-ais fork — AIS → NMEA over UDP :10110)
    → ais_udp_listener.py   (UDP :10110 → timestamped ais_capture.log)
      ├── ais_api.py        (HTTP :8080 — /api/vessels live, /api/heatmap from SQLite)
      └── ais_sqlite_ingest.py  (decode NMEA → SQLite positions/vessels, WAL)
            → web/index.html + web/heatmap.html  (Leaflet map + vessel-presence heatmap)
              ← Nginx: /ais/ → static, /ais/api/ → :8080
```

Data flow is decoupled: the log is the lossless archive (retained up to 5 GB),
and SQLite is the fast query layer (indexed; full-history queries in ~20 ms).
The heatmap and any historical queries read from SQLite, so they scale to the
full log window without decompressing every rotated file.

## Repository layout

```
rf/                  RTL-SDR reception layer
  rtl-ais/           git submodule → JohnGuidry/rtl-ais (the decoder fork)
  README.md          how to build/install rtl_ais from the fork
pipeline/            data ingestion
  ais_udp_listener.py    UDP → timestamped log
  ais_decoder.py         standalone NMEA decoder (types 1/2/3/5/24)
  ais_sqlite_ingest.py   NMEA → SQLite (backfill + live-tail, verified vs pyais)
api/
  ais_api.py           HTTP API — live vessels + vessel-presence heatmap (SQLite-backed)
web/                 the dashboard (static HTML + Leaflet)
  index.html           live map
  heatmap.html         vessel-presence heatmap
deploy/              everything needed to deploy on the host
  install.sh           idempotent: udev + 4 systemd units + logrotate + pipeline files
  systemd/           rtl-ais, ais-listener, ais-api, ais-sqlite-ingest units
  99-ais.rules       udev rule (grant the dongle to the capture user)
  ais-capture.logrotate  log retention (size 10M, rotate 500 ≈ 5 GB cap)
  ais-usb-reset.sh   VBUS power-cycle for the dongle (clears PLL wedge on boot)
  deploy-dashboard.sh  inject CARTO key + rsync web/ → the site's /ais/ path
docs/
  ais-setup.md       detailed setup + tuning notes
```

## The rtl-ais fork

`rf/rtl-ais` is a **git submodule** pointing at [`JohnGuidry/rtl-ais`](https://github.com/JohnGuidry/rtl-ais)
(a fork of [dgiardini/rtl-ais](https://github.com/dgiardini/rtl-ais)). The fork is the
RF *reception layer* — one component of this project, not the project itself. See
[`rf/README.md`](rf/README.md) for the build/install steps.

## Install (on the capture host)

```bash
# 1. Build the decoder (one-time; persists across reboots) — see rf/README.md
# 2. Deploy the app (idempotent; safe to re-run)
sudo bash deploy/install.sh
```

`install.sh` installs the udev rule, the four systemd units, the logrotate config,
and the pipeline/API files, then enables and restarts the services.

## Deploy the dashboard

```bash
# Injects the CARTO basemaps key (from a gitignored .carto-key) into the
# __CARTO_KEY__ placeholder and rsyncs web/ to the site's /ais/ path.
bash deploy/deploy-dashboard.sh
```

## Security

- **No keys are committed.** The dashboard HTML uses a `__CARTO_KEY__` placeholder;
  `deploy-dashboard.sh` injects the real key at deploy time from a **gitignored**
  `.carto-key` file. The key never enters git history.
- `.gitignore` excludes `.carto-key`, `*.key`, `*.pem`, `.env*`, `*.db`, and the
  capture logs.

## Data retention

- **Log (lossless archive):** `deploy/ais-capture.logrotate` — `size 10M`,
  `rotate 500`, `compress`/`delaycompress` ≈ a **5 GB cap** before deletion begins
  (~70 years at the current ~184 KB/day rate).
- **SQLite (query layer):** `~ais_data/ais.db` (WAL). Indexed on `(mmsi, ts)` and
  `(ts)`. The ingest is idempotent (`INSERT OR IGNORE`) and survives logrotate
  `copytruncate`.

## Notes

- The decoder in `pipeline/` and `api/` is self-contained (stdlib only) and was
  verified line-for-line against `pyais` (35,705/35,705 on real captures) before
  deployment.
- Only **single-fragment** position reports (types 1/2/3 Class A, 18/19 Class B)
  are treated as positions — a hard guard that prevents the phantom-position
  artifacts from multi-fragment continuation payloads.
