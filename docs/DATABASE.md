# Database Maintenance

LazyClipper stores metadata in PostgreSQL and media under `storage/projects/`. Back up and restore both together. Run the Compose commands below from the repository root. For schema and persistence design, see [Architecture](ARCHITECTURE.md#persistent-state-and-storage).

## Fresh installations and credentials

```bash
python3 scripts/setup_secrets.py
```

This creates installation-specific administrator and application credentials without displaying them or replacing existing values. `.secrets/` is private (0700), Git-ignored, and excluded from image builds. Files are read-only (0444), allowing the container's PostgreSQL UID to read mounted secrets while other host users cannot traverse the directory. Keep these files in protected deployment backups.

Fresh PostgreSQL volumes automatically run `scripts/init-database.sh` and `scripts/setup-database-role.sh`. The `lazyclipper` application role has no superuser, role-creation, database-creation, replication, or RLS-bypass privileges. It has runtime DML privileges and CREATE within the application database's public schema for fresh initialization. The API receives only the application password. Existing tables retain their ownership; migrations require the table owner's credentials.

For non-Compose development, provision a separately reachable PostgreSQL database/role and configure `DATABASE_URL`. `DATABASE_PASSWORD_FILE` can provide the password without embedding it in the URL. Compose does not publish a database host port.

## Coordinated backups and restore testing

Before role changes, migrations, or destructive resets:

1. Stop the API/frontend and keep PostgreSQL available:
   ```bash
   docker compose stop api frontend
   docker compose up -d --wait postgres
   ```
2. Create a protected custom-format database dump using `pg_dump -U postgres -d lazy_clipper -Fc` inside the PostgreSQL container.
3. Create a globals backup using `pg_dumpall -U postgres --globals-only` inside that container. Globals contain authentication metadata; do not print or share the backup.
4. Copy the matching `storage/` tree while processing is stopped. Preserve `.secrets/` and deployment configuration securely with the backup set.
5. Use a private backup directory and restrictive umask. Restore-test the database/globals and corresponding media in an isolated PostgreSQL/storage environment before changing the live installation.

A restore must use matching database metadata, roles, media, credentials, and application version. Keep the API/frontend stopped during restoration. Verify project/clip counts and media reads before reopening access. Do not test a restore by overwriting the live database.

## Existing-volume role/password changes

Changing secret files or Compose environment variables does **not** rotate an initialized PostgreSQL role's password.

1. Generate/preserve secret files, configure the private frontend binding, and keep the existing LLM/cookie settings.
2. Stop API/frontend, start only PostgreSQL, and take/restore-test the coordinated backups above.
3. Run the transactional, idempotent role/password setup:
   ```bash
   docker compose exec -T postgres sh /opt/lazyclipper/setup-database-role.sh
   ```
4. Complete any schema upgrade below, then rebuild/start the stack and verify project/clip counts and reads through the frontend.

The script creates/configures the application role, grants access to existing tables, and rotates the administrator password to its secret-file value. It requires local Unix-socket administrator access; it does not change authentication rules to bypass a custom policy. If access or table/role ownership differs from this dedicated-stack setup, resolve that before proceeding. Do not grant the runtime API superuser privileges.

## Schema upgrades

Startup initializes a genuinely fresh schema but refuses an unversioned, outdated, partial, or incompatible existing schema. `backend/app/migrations.py` is the revision authority; it never silently upgrades existing tables.

With the API/frontend stopped and coordinated backups restore-tested, use the table owner's connection. In the dedicated Compose setup, the administrator connection is:

```bash
docker compose stop api frontend
docker compose up -d --wait postgres
# Take and restore-test coordinated database/globals/media backups before proceeding.
docker compose build api
docker compose run --rm --no-deps -T --entrypoint python \
  -e DATABASE_URL=postgresql+psycopg://postgres@postgres:5432/lazy_clipper \
  -e DATABASE_PASSWORD_FILE=/run/secrets/migration_admin_password \
  -v "$(pwd)/.secrets/postgres_admin_password:/run/secrets/migration_admin_password:ro" \
  api -m backend.app.migrations upgrade
docker compose run --rm --no-deps -T --entrypoint python \
  api -m backend.app.migrations status
docker compose up -d --build --wait
```

The administrator secret is mounted only into the one-off maintenance process. Normal API containers retain the restricted application connection. If tables are owned by a different role, use that owner's credentials instead.

For non-Compose maintenance, stop the API and configure the table owner's `DATABASE_URL`/`DATABASE_PASSWORD_FILE` before running:

```bash
python -m backend.app.migrations upgrade
python -m backend.app.migrations status
```

Restore runtime credentials before restarting the API.

### Revision 1 and rollback

Revision 1 canonicalizes source URLs, adds a unique source-URL index and video-ID lookup index, and backfills `analysis_completed`. Legacy READY/RENDERING projects or projects with saved moments count as completed, including READY analyses with zero moments; other legacy failures remain incomplete. New analysis saves its completion marker and results atomically, including valid empty results. Source-only repair therefore reuses completed analysis.

Project IDs, child rows, media references, source artifacts, and activity timestamps are preserved. Invalid, duplicate, or conflicting legacy identities abort before schema changes; reconcile explicitly from backups rather than automatically deleting or merging projects.

PostgreSQL upgrades are transactional; SQLite upgrades explicitly begin a transaction before DDL. Repeating a completed upgrade is harmless. Downgrades are refused. Roll back by restoring the protected pre-upgrade metadata/globals/media set with its matching application version.

Continue running one API process per database/storage directory. See [Deployment](DEPLOYMENT.md#runtime-policies) and [Architecture](ARCHITECTURE.md#execution-and-startup) for execution ownership.
