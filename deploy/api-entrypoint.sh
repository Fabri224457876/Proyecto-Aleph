#!/bin/sh
# Antes de levantar la API: crea las tablas, el administrador (si la base no tiene ninguno)
# y, con ALEPH_SEED_DEMO=1, el caso de demostración ficticio. Las claves vienen del entorno.
set -e
python - <<'PY'
from aleph.core.db import init_db
init_db()
PY
admins=$(python -m aleph.demo.estado | tail -n 1)
if [ "$admins" = "0" ]; then
  if [ -z "$ALEPH_ADMIN_PASSWORD" ]; then
    echo "Falta ALEPH_ADMIN_PASSWORD para crear el primer administrador." >&2
    exit 1
  fi
  python -m aleph.api.bootstrap --username "${ALEPH_ADMIN_USERNAME:-admin}"
fi
if [ "${ALEPH_SEED_DEMO:-0}" = "1" ]; then
  python -m aleph.demo.seed
fi
exec "$@"
