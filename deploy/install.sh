#!/usr/bin/env bash
#
# ship-tracker host installer — idempotent, safe to re-run.
#
# Installs the udev rule, the four systemd units, the logrotate config, the
# USB reset helper, and the pipeline/API files, then enables and restarts the
# services. Run on the capture host (e.g. USB-John) as root.
#
#   sudo bash deploy/install.sh
#
# The target paths below match the unit files in deploy/systemd/. Adjust both
# together if you deploy to a different host.
set -euo pipefail

APP_USER="${APP_USER:-john}"
BASE_DIR="/home/${APP_USER}/rtl-ais"
SCRIPTS_DIR="${BASE_DIR}/scripts"
LOG_FILE="/home/${APP_USER}/ais_capture.log"

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "${HERE}/.." && pwd)"

echo "==> Target user: ${APP_USER}, base dir: ${BASE_DIR}"

# 1. Pipeline + API files
mkdir -p "${SCRIPTS_DIR}"
install -m 0644 "${REPO}/pipeline/ais_udp_listener.py"  "${SCRIPTS_DIR}/ais_udp_listener.py"
install -m 0644 "${REPO}/pipeline/ais_decoder.py"       "${BASE_DIR}/ais_decoder.py"
install -m 0644 "${REPO}/pipeline/ais_sqlite_ingest.py" "${BASE_DIR}/ais_sqlite_ingest.py"
install -m 0644 "${REPO}/api/ais_api.py"               "${BASE_DIR}/ais_api.py"
echo "    installed pipeline + API files"

# 2. udev rule (grant the dongle to the capture user)
install -m 0644 "${HERE}/99-ais.rules" "/etc/udev/rules.d/99-ais.rules"
udevadm control --reload-rules 2>/dev/null || true
echo "    installed udev rule"

# 3. logrotate (retention cap)
install -m 0644 "${HERE}/ais-capture.logrotate" "/etc/logrotate.d/ais-capture"
echo "    installed logrotate config"

# 4. USB reset helper
install -m 0755 "${HERE}/ais-usb-reset.sh" "/usr/local/sbin/ais-usb-reset.sh"
echo "    installed ais-usb-reset.sh"

# 5. systemd units
for u in rtl-ais ais-listener ais-api ais-sqlite-ingest; do
  install -m 0644 "${HERE}/systemd/${u}.service" "/etc/systemd/system/${u}.service"
done
echo "    installed 4 systemd units"

# 6. reload + enable + restart
systemctl daemon-reload
systemctl enable ais-listener.service ais-api.service ais-sqlite-ingest.service
# rtl-ais Requires ais-listener; start the listener first, then the decoder.
systemctl restart ais-listener.service
systemctl restart ais-sqlite-ingest.service
systemctl restart ais-api.service
if [ -x /usr/local/bin/rtl_ais ]; then
  systemctl restart rtl-ais.service
  echo "    restarted rtl-ais (decoder present)"
else
  echo "    NOTE: /usr/local/bin/rtl_ais not found — build it first (see rf/README.md)"
fi

echo "==> Done. Service status:"
systemctl --no-pager --lines=0 status ais-listener ais-sqlite-ingest ais-api rtl-ais 2>/dev/null | grep -E "●|Active" || true
