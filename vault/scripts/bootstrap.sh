#!/bin/sh
# Bootstraps Vault for the NullOps stack. Runs once per "docker compose up" and is
# idempotent: anything that already exists is left untouched.
#
#   1. initialise + unseal Vault (lab setup: 1 key share, kept in a Docker volume)
#   2. enable a KV v2 mount at "nullops/"
#   3. generate the MariaDB passwords *inside* this script and store them in Vault,
#      so no credential is ever written to .env or docker-compose.yml
#   4. create an AppRole + least-privilege policy and drop role_id / secret_id in a
#      volume the app container reads (the "trusted orchestrator" pattern)
#   5. render the credential files MariaDB reads through its MARIADB_*_FILE support
set -eu

export VAULT_ADDR="${VAULT_ADDR:-http://vault:8200}"

INIT_FILE="${INIT_FILE:-/vault/init/init.txt}"   # unseal key + root token
APPROLE_DIR="${APPROLE_DIR:-/vault/approle}"     # read-only mount in the app container
DB_SECRET_DIR="${DB_SECRET_DIR:-/vault/db}"      # read-only mount in the db container

DB_USER="${DB_USER:-nullops}"
DB_NAME="${DB_NAME:-nullops_db}"

KV_MOUNT=nullops
APP_SECRET="db/app"
ROOT_SECRET="db/root"
POLICY_FILE=/vault/policies/nullops-app.hcl
APPROLE_NAME=nullops-app

log() { echo "[vault-bootstrap] $*"; }

# "vault status" exits 0 when unsealed, 2 when sealed, 1 when the API is unreachable
vault_status_code() {
  rc=0
  vault status >/dev/null 2>&1 || rc=$?
  echo "$rc"
}

status_field() {
  vault status 2>/dev/null | awk -v key="$1" '$1 == key { print $2 }'
}

random_password() {
  tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 32
  echo
}

# Writes a secret to a file atomically, world-readable so the db / app containers
# (which run as their own unprivileged users) can read it.
write_secret_file() {
  path="$1"
  value="$2"
  printf '%s' "$value" > "$path.tmp"
  chmod 0444 "$path.tmp"
  mv "$path.tmp" "$path"
}

mkdir -p "$(dirname "$INIT_FILE")" "$APPROLE_DIR" "$DB_SECRET_DIR"

# ---------------------------------------------------------------- 1. init + unseal
log "waiting for the Vault API on $VAULT_ADDR"
attempt=0
# An uninitialised or sealed Vault still answers and exits 2; only 1 means "not listening".
while [ "$(vault_status_code)" -eq 1 ]; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 60 ]; then
    log "ERROR: Vault did not become reachable in time"
    exit 1
  fi
  sleep 1
done

if [ "$(status_field Initialized)" = "true" ]; then
  log "Vault is already initialised"
else
  log "initialising Vault (1 key share, lab setup)"
  umask 077
  # The output holds the unseal key and the root token; it is the only copy.
  vault operator init -key-shares=1 -key-threshold=1 > "$INIT_FILE.tmp"
  chmod 0400 "$INIT_FILE.tmp"
  mv "$INIT_FILE.tmp" "$INIT_FILE"
  umask 022
fi

if [ ! -s "$INIT_FILE" ]; then
  log "ERROR: $INIT_FILE is missing, but Vault is already initialised."
  log "       The unseal key was lost. Recreate the stack with 'docker compose down -v'."
  exit 1
fi

UNSEAL_KEY=$(awk '/^Unseal Key 1:/ { print $4 }' "$INIT_FILE")
ROOT_TOKEN=$(awk '/^Initial Root Token:/ { print $4 }' "$INIT_FILE")

if [ "$(status_field Sealed)" = "true" ]; then
  log "unsealing Vault"
  vault operator unseal "$UNSEAL_KEY" >/dev/null
else
  log "Vault is already unsealed"
fi

# Only this bootstrap step uses the root token; the app never sees it.
export VAULT_TOKEN="$ROOT_TOKEN"

# ------------------------------------------------------------------- 2. KV v2 mount
if vault secrets list | grep -q "^${KV_MOUNT}/"; then
  log "KV mount ${KV_MOUNT}/ already enabled"
else
  log "enabling KV v2 at ${KV_MOUNT}/"
  vault secrets enable -path="$KV_MOUNT" kv-v2 >/dev/null
fi

# --------------------------------------------------------------- 3. seed credentials
if vault kv get "$KV_MOUNT/$APP_SECRET" >/dev/null 2>&1; then
  log "$KV_MOUNT/$APP_SECRET already exists, keeping the stored password"
else
  log "generating the application database password"
  vault kv put "$KV_MOUNT/$APP_SECRET" \
    username="$DB_USER" \
    password="$(random_password)" \
    database="$DB_NAME" >/dev/null
fi

if vault kv get "$KV_MOUNT/$ROOT_SECRET" >/dev/null 2>&1; then
  log "$KV_MOUNT/$ROOT_SECRET already exists, keeping the stored password"
else
  log "generating the database root password"
  vault kv put "$KV_MOUNT/$ROOT_SECRET" password="$(random_password)" >/dev/null
fi

# ------------------------------------------------------------- 4. AppRole for the app
if vault auth list | grep -q '^approle/'; then
  log "approle auth method already enabled"
else
  log "enabling the approle auth method"
  vault auth enable approle >/dev/null
fi

log "writing policy and role $APPROLE_NAME"
vault policy write "$APPROLE_NAME" "$POLICY_FILE" >/dev/null
vault write "auth/approle/role/$APPROLE_NAME" \
  token_policies="$APPROLE_NAME" \
  token_ttl=1h \
  token_max_ttl=4h \
  secret_id_ttl=0 \
  secret_id_num_uses=0 >/dev/null

ROLE_ID=$(vault read -field=role_id "auth/approle/role/$APPROLE_NAME/role-id")
# A fresh secret_id every run, so the app can always log in even if the Vault volume
# was recreated and the old secret_id on disk no longer exists.
SECRET_ID=$(vault write -f -field=secret_id "auth/approle/role/$APPROLE_NAME/secret-id")

write_secret_file "$APPROLE_DIR/role_id" "$ROLE_ID"
write_secret_file "$APPROLE_DIR/secret_id" "$SECRET_ID"
log "AppRole credentials written to $APPROLE_DIR"

# --------------------------------------------- 5. credential files for the db container
# MariaDB needs its user and password at startup and cannot talk to Vault itself, so the
# values are rendered to files that only the db container mounts (MARIADB_*_FILE).
write_secret_file "$DB_SECRET_DIR/db_user" \
  "$(vault kv get -field=username "$KV_MOUNT/$APP_SECRET")"
write_secret_file "$DB_SECRET_DIR/db_password" \
  "$(vault kv get -field=password "$KV_MOUNT/$APP_SECRET")"
write_secret_file "$DB_SECRET_DIR/db_name" \
  "$(vault kv get -field=database "$KV_MOUNT/$APP_SECRET")"
write_secret_file "$DB_SECRET_DIR/db_root_password" \
  "$(vault kv get -field=password "$KV_MOUNT/$ROOT_SECRET")"
log "database credential files written to $DB_SECRET_DIR"

log "bootstrap complete"
