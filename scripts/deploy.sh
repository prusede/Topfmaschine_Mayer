#!/usr/bin/env bash
set -Eeuo pipefail
trap 'echo "Deployment abgebrochen (Zeile ${LINENO}); keine weiteren Schritte ausgefuehrt." >&2' ERR
# Funktionskoerper vor git pull einlesen, auch wenn das Skript selbst aktualisiert wird.
main() {
    APP_DIR="${TM_APP_DIR:-/opt/topfmaschine_mayer}"
    cd "${APP_DIR}"
    bash scripts/backup_db.sh
    git pull --ff-only
    venv/bin/python -m pip install -r requirements.txt
    venv/bin/python -m pip check
    venv/bin/python -m compileall -q app
    sudo systemctl restart topfmaschine-mayer
    sudo systemctl is-active --quiet topfmaschine-mayer
    echo "Deployment abgeschlossen. Browserseiten neu laden."
}
main "$@"
