#!/usr/bin/env bash
# Mint the private Langfuse's secrets, once (deploy/langfuse/README.md):
#
#   deploy/langfuse/init.sh
#
# Writes two files in secrets/ (mode 600), and never overwrites either:
#
#   secrets/langfuse.env       every Langfuse service's settings: the database,
#                              ClickHouse, Redis and MinIO passwords, the
#                              encryption keys, and the first org, project,
#                              project keys and sign-in user (Langfuse creates
#                              them on its first boot, headless).
#   secrets/langfuse-otlp.env  LANGFUSE_OTLP_AUTH alone, for the otel-collector,
#                              the one container that sends to Langfuse.
#
# Prints the sign-in email and where the password is; never the password.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
secrets="${TEENY_SECRETS:-$root/secrets}"
env_file="$secrets/langfuse.env"
otlp_file="$secrets/langfuse-otlp.env"

[ -d "$secrets" ] || { echo "no $secrets directory" >&2; exit 1; }
if [ -e "$env_file" ] || [ -e "$otlp_file" ]; then
  echo "$env_file or $otlp_file already exists; leaving both alone" >&2
  exit 1
fi

hex() { python3 -c 'import secrets, sys; print(secrets.token_hex(int(sys.argv[1])))' "$1"; }
pg_password="$(hex 24)"
public_key="pk-lf-$(hex 16)"
secret_key="sk-lf-$(hex 24)"
minio_password="$(hex 24)"
user_email="teeny@langfuse.local"

umask 077
cat >"$env_file" <<EOF
# The private Langfuse (deploy/langfuse/README.md). Minted by deploy/langfuse/init.sh.
POSTGRES_USER=langfuse
POSTGRES_PASSWORD=$pg_password
POSTGRES_DB=langfuse
DATABASE_URL=postgresql://langfuse:$pg_password@langfuse-postgres:5432/langfuse
CLICKHOUSE_USER=langfuse
CLICKHOUSE_PASSWORD=$(hex 24)
REDIS_AUTH=$(hex 24)
MINIO_ROOT_USER=langfuse
MINIO_ROOT_PASSWORD=$minio_password
LANGFUSE_S3_EVENT_UPLOAD_ACCESS_KEY_ID=langfuse
LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY=$minio_password
LANGFUSE_S3_MEDIA_UPLOAD_ACCESS_KEY_ID=langfuse
LANGFUSE_S3_MEDIA_UPLOAD_SECRET_ACCESS_KEY=$minio_password
SALT=$(hex 32)
ENCRYPTION_KEY=$(hex 32)
NEXTAUTH_SECRET=$(hex 32)
LANGFUSE_INIT_ORG_ID=teeny
LANGFUSE_INIT_ORG_NAME=Teeny
LANGFUSE_INIT_PROJECT_ID=teeny-turns
LANGFUSE_INIT_PROJECT_NAME=turns
LANGFUSE_INIT_PROJECT_PUBLIC_KEY=$public_key
LANGFUSE_INIT_PROJECT_SECRET_KEY=$secret_key
LANGFUSE_INIT_USER_EMAIL=$user_email
LANGFUSE_INIT_USER_NAME=Teeny
LANGFUSE_INIT_USER_PASSWORD=$(hex 16)
EOF
printf 'LANGFUSE_OTLP_AUTH=%s\n' "$(printf '%s:%s' "$public_key" "$secret_key" | base64 -w0)" >"$otlp_file"
chmod 600 "$env_file" "$otlp_file"

echo "wrote $env_file and $otlp_file"
echo "sign in as $user_email; the password is LANGFUSE_INIT_USER_PASSWORD in $env_file"
