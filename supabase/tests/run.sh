#!/usr/bin/env bash
# Перевірка прав доступу на локальному Postgres (імітація Supabase).
# Потрібен запущений Postgres; параметри підключення — через PGHOST/PGPORT/PGUSER.
set -euo pipefail
cd "$(dirname "$0")"
psql -q -c "drop database if exists inventar_test" -c "create database inventar_test"
psql -q -d inventar_test -v ON_ERROR_STOP=1 -f supabase_stub.sql
psql -q -d inventar_test -v ON_ERROR_STOP=1 -f ../schema.sql 2>&1 | grep -v NOTICE || true
psql -q -d inventar_test -v ON_ERROR_STOP=1 -f test.sql 2>&1 | grep -E "^(OK|ALL|psql|ERROR)"
