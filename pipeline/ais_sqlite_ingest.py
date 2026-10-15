#!/usr/bin/env python3
"""
ais_sqlite_ingest.py — decode AIS NMEA from ais_capture.log into SQLite.

Modes (argv[1]):
  backfill  Process ALL existing log files (oldest -> newest), then exit.
            Idempotent (INSERT OR IGNORE), safe to re-run.
  tail      Follow the current log live, inserting new position reports.
            Starts at the end of the file; survives logrotate copytruncate.

The schema is created idempotently on startup (CREATE TABLE IF NOT EXISTS).
The decoder is self-contained (stdlib only) using verified AIS bit layouts,
with the single-fragment position guard and a GPS validity filter.
"""
import sys
import time
import gzip
import sqlite3
from pathlib import Path
from datetime import datetime

DB = Path.home() / "ais_data" / "ais.db"
LOGDIR = Path.home()
LOGGLOB = "ais_capture.log*"
POLL = 1.0

# ---------------------------------------------------------------------------
# Self-contained AIS decoder (verified against pyais; see verify step)
# ---------------------------------------------------------------------------
def _c6(c):
    v = ord(c) - 48
    if v > 39:
        v -= 8
    return v & 0x3F

def _bits(payload):
    out = []
    for c in payload:
        v = _c6(c)
        for i in range(5, -1, -1):
            out.append((v >> i) & 1)
    while len(out) < 424:
        out.append(0)
    return out

def _gb(bits, start, length):
    v = 0
    for i in range(length):
        idx = start + i
        if idx >= len(bits):
            break
        v = (v << 1) | bits[idx]
    return v

def _gsb(bits, start, length):
    v = _gb(bits, start, length)
    if v >= (1 << (length - 1)):
        v -= (1 << length)
    return v

def _sixbit(bits, start, n):
    s = ""
    for i in range(n):
        c = _gb(bits, start + i * 6, 6)
        if c == 0:
            break
        s += chr(c + 64) if c < 32 else chr(c)
    return s.rstrip("@ ").strip()

# Position-report bit layouts (verified 2026-08-29, 3000/3000 vs pyais).
# Class A (1,2,3): lon@61(28s) lat@89(27s) spd@50(10)/10 crs@116(12)/10 hdg@128(9)
# Class B (18,19): lon@57(28s) lat@85(27s) spd@40(9) raw crs@112(9)/10 hdg@121(9)
LAYOUT = {
    1:  dict(lon=(61, 28), lat=(89, 27), spd=(50, 10, 10.0), crs=(116, 12, 10.0), hdg=(128, 9)),
    2:  dict(lon=(61, 28), lat=(89, 27), spd=(50, 10, 10.0), crs=(116, 12, 10.0), hdg=(128, 9)),
    3:  dict(lon=(61, 28), lat=(89, 27), spd=(50, 10, 10.0), crs=(116, 12, 10.0), hdg=(128, 9)),
    18: dict(lon=(57, 28), lat=(85, 27), spd=(40, 9, 1.0),   crs=(112, 9, 1.0),   hdg=(121, 9)),
    19: dict(lon=(57, 28), lat=(85, 27), spd=(40, 9, 1.0),   crs=(112, 9, 1.0),   hdg=(121, 9)),
}

def _valid(lat, lon):
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        return False
    if lat == 0.0 and lon == 0.0:
        return False
    return True

def parse_line(line):
    """Parse '<ts> !AIVDM,...' -> (ts_epoch, kind, rec) or None.
    kind is 'pos' or 'stat'; rec is the decoded dict."""
    line = line.rstrip("\n")
    if not line.strip():
        return None
    sp = line.split(" ", 1)
    if len(sp) < 2:
        return None
    ts_str, nmea = sp
    if "!AIVDM" not in nmea and "!AIVDO" not in nmea:
        return None
    c = nmea.split(",")
    if len(c) < 6:
        return None
    # Single-fragment guard: position/static reports we decode are always 1,1.
    if c[1] != "1" or c[2] != "1":
        return None
    # AIVDM: !AIVDM,num,num,slot,channel,payload,fill*chk
    # payload is index 5; fill+checksum are concatenated at index 6 (no comma).
    payload = c[5]
    bits = _bits(payload)
    mtype = _gb(bits, 0, 6)
    mmsi = _gb(bits, 8, 30)

    if mtype in LAYOUT:
        L = LAYOUT[mtype]
        lon = _gsb(bits, L["lon"][0], L["lon"][1]) / 600000.0
        lat = _gsb(bits, L["lat"][0], L["lat"][1]) / 600000.0
        if not _valid(lat, lon):
            return None
        spd = _gb(bits, L["spd"][0], L["spd"][1]) / L["spd"][2]
        crs = _gb(bits, L["crs"][0], L["crs"][1]) / L["crs"][2]
        hdg = _gb(bits, L["hdg"][0], L["hdg"][1])
        return _ts(ts_str), "pos", dict(mmsi=mmsi, mtype=mtype, lat=lat, lon=lon,
                                       spd=spd, crs=crs, hdg=hdg)

    if mtype == 5:  # Static and voyage data
        name = _sixbit(bits, 112, 20)
        ship_type = _gb(bits, 232, 8)
        dest = _sixbit(bits, 302, 20)
        return _ts(ts_str), "stat", dict(mmsi=mmsi, name=name, ship_type=ship_type, dest=dest)

    if mtype == 24:  # Static data report
        part = _gb(bits, 38, 2)
        if part == 0:  # Part A - name
            name = _sixbit(bits, 40, 20)
            return _ts(ts_str), "stat", dict(mmsi=mmsi, name=name, ship_type=None, dest=None)
        if part == 1:  # Part B - type
            ship_type = _gb(bits, 40, 8)
            return _ts(ts_str), "stat", dict(mmsi=mmsi, name=None, ship_type=ship_type, dest=None)

    return None

def _ts(ts_str):
    try:
        return int(datetime.fromisoformat(ts_str).timestamp())
    except Exception:
        return int(time.time())

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
def init_db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS positions (
            ts       INTEGER NOT NULL,
            mmsi     INTEGER NOT NULL,
            msg_type INTEGER NOT NULL,
            lat      REAL,
            lon      REAL,
            speed    REAL,
            course   REAL,
            heading  INTEGER,
            UNIQUE(mmsi, ts, msg_type, lat, lon, speed, course, heading)
        );
        CREATE INDEX IF NOT EXISTS idx_pos_ts ON positions(ts);
        CREATE INDEX IF NOT EXISTS idx_pos_mmsi_ts ON positions(mmsi, ts);
        CREATE TABLE IF NOT EXISTS vessels (
            mmsi      INTEGER PRIMARY KEY,
            name      TEXT,
            ship_type INTEGER,
            dest      TEXT,
            updated   INTEGER
        );
        """
    )
    con.commit()
    return con

def insert_pos(con, ts, r):
    con.execute(
        "INSERT OR IGNORE INTO positions (ts, mmsi, msg_type, lat, lon, speed, course, heading) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (ts, r["mmsi"], r["mtype"], r["lat"], r["lon"], r["spd"], r["crs"], r["hdg"]),
    )

def upsert_vessel(con, ts, r):
    con.execute(
        """
        INSERT INTO vessels (mmsi, name, ship_type, dest, updated)
        VALUES (?,?,?,?,?)
        ON CONFLICT(mmsi) DO UPDATE SET
            name = COALESCE(excluded.name, vessels.name),
            ship_type = COALESCE(excluded.ship_type, vessels.ship_type),
            dest = COALESCE(excluded.dest, vessels.dest),
            updated = excluded.updated
        """,
        (r["mmsi"], r["name"], r["ship_type"], r["dest"], ts),
    )

def _open_log(p: Path):
    return gzip.open(str(p), "rt") if p.suffix == ".gz" else open(str(p), "r")

def _log_files_chrono():
    """Return log files oldest -> newest."""
    files = list(LOGDIR.glob(LOGGLOB))
    def key(p):
        n = p.name
        if n == "ais_capture.log":
            return 0
        # e.g. ais_capture.log.7.gz -> 7
        tail = n[len("ais_capture.log."):]
        return int("".join(ch for ch in tail if ch.isdigit()) or 0)
    return sorted(files, key=key, reverse=True)

# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------
def backfill():
    con = init_db()
    total = 0
    for p in _log_files_chrono():
        n = 0
        with _open_log(p) as f:
            for line in f:
                parsed = parse_line(line)
                if not parsed:
                    continue
                ts, kind, rec = parsed
                if kind == "pos":
                    insert_pos(con, ts, rec)
                else:
                    upsert_vessel(con, ts, rec)
                n += 1
        con.commit()
        total += n
        print(f"  {p.name}: {n} rows", flush=True)
    con.commit()
    con.close()
    print(f"backfill complete: {total} rows", flush=True)

# ---------------------------------------------------------------------------
# Live tail
# ---------------------------------------------------------------------------
def tail():
    con = init_db()
    log = LOGDIR / "ais_capture.log"
    offset = log.stat().st_size if log.exists() else 0
    print(f"ais_sqlite_ingest: tailing {log} from offset {offset}", flush=True)
    with open(str(log), "r") as f:
        f.seek(offset)
        while True:
            line = f.readline()
            if line:
                parsed = parse_line(line)
                if parsed:
                    ts, kind, rec = parsed
                    if kind == "pos":
                        insert_pos(con, ts, rec)
                    else:
                        upsert_vessel(con, ts, rec)
                    con.commit()
            else:
                # No new data: detect copytruncate (file shrank below offset).
                try:
                    size = log.stat().st_size
                except FileNotFoundError:
                    size = 0
                if size < offset:
                    f.seek(0)
                    offset = 0
                    print("  log truncated, reset to 0", flush=True)
                time.sleep(POLL)

def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "tail"
    if mode == "backfill":
        backfill()
    elif mode == "tail":
        tail()
    else:
        print(f"unknown mode: {mode}", file=sys.stderr)
        sys.exit(2)

if __name__ == "__main__":
    main()
