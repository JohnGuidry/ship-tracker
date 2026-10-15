# rf/ — RTL-SDR reception layer

`rtl-ais/` is a git submodule → [`JohnGuidry/rtl-ais`](https://github.com/JohnGuidry/rtl-ais)
(fork of [dgiardini/rtl-ais](https://github.com/dgiardini/rtl-ais)). It is the
RF reception layer — the C binary that tunes the AIS frequencies and decodes them
into NMEA `!AIVDM` sentences over UDP.

> **Use the `dgiardini/rtl-ais` lineage, not `nicetester/rtl-ais`.** The fork
> tracks the dgiardini decoder.

## Build & install (one-time; persists across reboots)

```bash
cd rf/rtl-ais
make
sudo make install          # → /usr/local/bin/rtl_ais
```

`/usr/local` is outside the OS-managed layer, so the binary persists across
reboots/OS updates.

## Run

```bash
rtl_ais -n -p <ppm>        # NMEA to stderr (-n) AND UDP 127.0.0.1:10110
```

## PPM calibration (per-dongle)

PPM is per-crystal — never reuse a value across dongles. Measure with
`rtl_test -p` (crystal PPM via sample counting). **Do not use `kal -s GSM`** —
US 2G is decommissioned (AT&T 2017, T-Mobile 2022), so it finds zero towers.

## Hardware / USB pitfalls

- **Passive USB extenders break the tuner's I2C control bus.** A long passive
  extender drops the 5V rail / corrupts the control bus (`rtlsdr_demod_write_reg
  failed with -9`). Plug the dongle directly, or use an *active* (powered)
  extender.
- **"R82XX PLL not locked" from `rtl_test` is a known false positive** in
  librtlsdr 2.0.1. The tuner still tunes/streams. Verify the real data path
  before assuming a hardware fault.

## udev

`deploy/99-ais.rules` grants the dongle (`0bda:2838`) to the capture user's
group so `rtl_ais` runs without sudo. Installed by `deploy/install.sh`.
