#!/usr/bin/env sh
# Robust Alembic migrator for Docker
# - Only "upgrade head" by default (safe for CI & compose)
# - Optional autogenerate behind env flag
# - Optional DB wait via DATABASE_URL/+psql
set -eu

log() { printf "%s %s\n" "[migrator]" "$*"; }

# --- Config ---------------------------------------------------------------

APP_DIR="${APP_DIR:-/usr/src/fastapi}"

ALEMBIC_CFG_OPT=""
if [ -n "${ALEMBIC_CONFIG:-}" ]; then
  ALEMBIC_CFG_OPT="-c ${ALEMBIC_CONFIG}"
fi

AUTO_GENERATE_MIGRATIONS="${AUTO_GENERATE_MIGRATIONS:-0}"
MIGRATION_MSG="${MIGRATION_MSG:-autogen}"

WAIT_FOR_DB="${WAIT_FOR_DB:-0}"

PSQL_TIMEOUT_SEC="${PSQL_TIMEOUT_SEC:-60}"

# --- Helpers --------------------------------------------------------------

wait_for_db() {
  if [ "${WAIT_FOR_DB}" != "1" ]; then
    return 0
  fi

  if ! command -v psql >/dev/null 2>&1; then
    log "psql not found; skipping DB wait."
    return 0
  fi

  log "Waiting for DB up to ${PSQL_TIMEOUT_SEC}s..."
  end=$(( $(date +%s) + PSQL_TIMEOUT_SEC ))
  while true; do
    if [ -n "${DATABASE_URL:-}" ]; then
      if PGPASSWORD="${PGPASSWORD:-}" psql "${DATABASE_URL}" -c "select 1" >/dev/null 2>&1; then
        log "DB is ready (via DATABASE_URL)."
        break
      fi
    else
      if PGPASSWORD="${PGPASSWORD:-}" psql -h "${PGHOST:-localhost}" -p "${PGPORT:-5432}" -U "${PGUSER:-postgres}" -d "${PGDATABASE:-postgres}" -c "select 1" >/dev/null 2>&1; then
        log "DB is ready (via PG* env)."
        break
      fi
    fi

    if [ "$(date +%s)" -ge "${end}" ]; then
      log "DB did not become ready in time."
      return 1
    fi
    sleep 1
  done
}

upgrade_head() {
  log "Upgrading DB to head..."
  alembic ${ALEMBIC_CFG_OPT} upgrade head
  log "Upgrade completed."
}

maybe_autogenerate() {
  if [ "${AUTO_GENERATE_MIGRATIONS}" = "1" ]; then
    log "Autogenerate revision..."
    alembic ${ALEMBIC_CFG_OPT} revision --autogenerate -m "${MIGRATION_MSG}"
    log "Applying newly generated revision..."
    alembic ${ALEMBIC_CFG_OPT} upgrade head
    log "Autogenerate done."
  else
    log "AUTO_GENERATE_MIGRATIONS=0 — skipping autogenerate."
  fi
}

# --- Run ------------------------------------------------------------------

cd "${APP_DIR}"
log "Working dir: $(pwd)"
if [ -n "${ALEMBIC_CFG_OPT}" ]; then
  log "Using alembic config: ${ALEMBIC_CONFIG}"
fi

wait_for_db
upgrade_head
maybe_autogenerate

log "All done."