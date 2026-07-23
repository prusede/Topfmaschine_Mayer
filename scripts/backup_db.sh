#!/usr/bin/env bash
# Topfmaschine Mayer – SQLite-Backup mit Integritätsprüfung
# Cron-Beispiel: 0 */2 * * * /opt/topfmaschine_mayer/scripts/backup_db.sh >> /var/log/topfmaschine_mayer_backup.log 2>&1

set -euo pipefail

DB_SRC="${TM_DB_PATH:-/opt/topfmaschine_mayer/data/topfmaschine_mayer.db}"
BACKUP_DIR="/opt/topfmaschine_mayer/backups"
KEEP_DAYS=30
TS=$(date +"%Y%m%d_%H%M%S")
DEST="${BACKUP_DIR}/topfmaschine_mayer_${TS}.db"

mkdir -p "${BACKUP_DIR}"

# SQLite Online-Backup (sicher während laufendem Betrieb)
if ! sqlite3 "${DB_SRC}" ".backup '${DEST}'"; then
  echo "[${TS}] FEHLER: Backup fehlgeschlagen" >&2
  exit 1
fi

# Integritätsprüfung
RESULT=$(sqlite3 "${DEST}" "PRAGMA integrity_check;" 2>&1)
if [ "${RESULT}" != "ok" ]; then
  echo "[${TS}] FEHLER: Integritätsprüfung fehlgeschlagen: ${RESULT}" >&2
  rm -f "${DEST}"
  exit 2
fi

# Alte Backups rotieren
find "${BACKUP_DIR}" -name "topfmaschine_mayer_*.db" -mtime "+${KEEP_DAYS}" -delete

SIZE=$(du -sh "${DEST}" | cut -f1)
echo "[${TS}] OK: ${DEST} (${SIZE})"
