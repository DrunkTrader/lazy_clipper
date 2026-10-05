#!/bin/sh
# Run explicitly for existing volumes, after a verified backup and with API stopped.
# Passwords are read from mounted files, never command-line arguments or output.
set -eu
APP_DATABASE_PASSWORD=$(cat "${APP_DATABASE_PASSWORD_FILE:?Missing application password file}")
if [ -n "${POSTGRES_PASSWORD:-}" ]; then
    # During fresh-volume initialization the official entrypoint has already
    # read the admin secret and unset POSTGRES_PASSWORD_FILE.
    ADMIN_PASSWORD=$POSTGRES_PASSWORD
else
    ADMIN_PASSWORD=$(cat "${POSTGRES_PASSWORD_FILE:?Missing administrator password file}")
fi
test -n "$APP_DATABASE_PASSWORD" && test -n "$ADMIN_PASSWORD"
export APP_DATABASE_PASSWORD ADMIN_PASSWORD

# Avoid logging password-bearing SQL, even on an error. Local Unix-socket admin
# access is required; this script never edits pg_hba.conf or bypasses auth.
export PGOPTIONS='-c log_statement=none -c log_min_error_statement=panic -c log_error_verbosity=terse'
psql -X -q -U postgres -d lazy_clipper -v ON_ERROR_STOP=1 -v VERBOSITY=terse -v SHOW_CONTEXT=never <<'SQL'
\getenv app_password APP_DATABASE_PASSWORD
\getenv admin_password ADMIN_PASSWORD
BEGIN;
SELECT 'CREATE ROLE lazyclipper' WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lazyclipper') \gexec
ALTER ROLE lazyclipper WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD :'app_password';
ALTER ROLE postgres PASSWORD :'admin_password';
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT CONNECT ON DATABASE lazy_clipper TO lazyclipper;
-- CREATE is restricted to this database's schema for fresh-schema initialization.
-- Existing postgres-owned tables remain owned by postgres; grant only runtime DML.
GRANT USAGE, CREATE ON SCHEMA public TO lazyclipper;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO lazyclipper;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO lazyclipper;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO lazyclipper;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO lazyclipper;
COMMIT;
SQL
unset APP_DATABASE_PASSWORD ADMIN_PASSWORD
printf '%s\n' 'Database application role and credentials configured.'
