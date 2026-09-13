#!/usr/bin/env bash
set -euo pipefail

backup_root="${PW155_BACKUP_ROOT:-/srv/pw155/backups/database}"
retention_days="${PW155_BACKUP_RETENTION_DAYS:-14}"

if [[ "$EUID" -ne 0 ]]; then
  echo "Backup database harus dijalankan sebagai root." >&2
  exit 1
fi
if [[ "$backup_root" != /srv/pw155/backups/database ]]; then
  echo "Lokasi backup di luar direktori proyek ditolak: $backup_root" >&2
  exit 1
fi
if [[ ! "$retention_days" =~ ^[0-9]+$ ]] || (( retention_days < 1 )); then
  echo "Retensi harus berupa jumlah hari positif." >&2
  exit 1
fi

dump_bin="$(command -v mariadb-dump || command -v mysqldump || true)"
if [[ -z "$dump_bin" ]]; then
  echo "mariadb-dump/mysqldump tidak ditemukan." >&2
  exit 1
fi

install -d -o root -g root -m 0700 "$backup_root"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
working="$backup_root/.incomplete-$stamp"
destination="$backup_root/$stamp"
mkdir -m 0700 "$working"
cleanup() { rm -rf -- "$working"; }
trap cleanup EXIT

for database in pw pw_portal; do
  "$dump_bin" --single-transaction --quick --skip-lock-tables --hex-blob \
    --routines --events --triggers "$database" | gzip -9 > "$working/$database.sql.gz"
  gzip -t "$working/$database.sql.gz"
done

mariadb --batch --skip-column-names <<'SQL' > "$working/metadata.txt"
SELECT CONCAT('pw.users=', COUNT(*)) FROM pw.users;
SELECT CONCAT('pw.roles=', COUNT(*)) FROM pw.roles;
SELECT CONCAT('pw_portal.accounts=', COUNT(*)) FROM pw_portal.accounts;
SELECT CONCAT('pw_portal.audit_log=', COUNT(*)) FROM pw_portal.audit_log;
SQL

(
  cd "$working"
  sha256sum pw.sql.gz pw_portal.sql.gz metadata.txt > SHA256SUMS
)
chmod 0600 "$working"/*
mv -- "$working" "$destination"
trap - EXIT

while IFS= read -r -d '' expired; do
  name="${expired##*/}"
  if [[ "$expired" == "$backup_root"/* && "$name" =~ ^[0-9]{8}T[0-9]{6}Z$ ]]; then
    rm -rf -- "$expired"
  fi
done < <(find "$backup_root" -mindepth 1 -maxdepth 1 -type d \
  -name '????????T??????Z' -mtime "+$retention_days" -print0)

echo "Backup selesai: $destination"
