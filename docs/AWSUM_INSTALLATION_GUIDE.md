# AWSUM Installation and Migration Guide

**This is the authoritative AWSUM installation, relocation and restore guide.**

A complete AWSUM environment consists of **Awsum_Platform, Awsum_Data,
PostgreSQL, and environment/configuration**. Source code alone is not a complete
backup. Before installation or migration, read the
[authoritative storage standard](AWSUM_STORAGE_STANDARD.md).

## 1. Layout and portability

Recommended new-computer development layout:

```text
D:\01_Development\Awsum_Projects\
|-- Awsum_Platform\
`-- Awsum_Data\
```

Another location is valid:

```text
C:\Awsum_Projects\
|-- Awsum_Platform\
`-- Awsum_Data\
```

For application storage routing, only `AWSUM_STORAGE_ROOT` needs to point at the
chosen physical `Awsum_Data` location; do not rewrite logical business paths,
codes or owner relationships. Database connection settings, service credentials,
optional local dependencies and existing cutover metadata must also be reconciled
for the new environment as described below. An environment variable alone does
not copy data or update a strict old-root cutover marker.

Run commands from the indicated directory. Windows examples use PowerShell.
Replace example locations, hosts, database/role names and repository URL with
approved local values. No real passwords or secrets belong in this document.

## 2. Before leaving the old computer

1. **Source:** review changes, commit the intended source/docs/bundled assets and
   lockfiles, push to the authorized Git remote, and record branch/commit.
   Verify the push. If Git cannot be used, create a complete source backup,
   including intended untracked program files; exclude secrets and generated
   environments. Do not automatically commit confidential configuration.
2. **Inventory:** record actual owner codes/IDs, Store-to-Merchant relationships,
   DB names (including tenant DBs if used), service ports, PostgreSQL roles,
   grants/extensions, effective storage root and external dependency locations.
3. **Quiesce:** stop application/file writers or use a coordinated snapshot.
   Checkpoint/close SQLite databases before copying. A PostgreSQL dump is
   database-consistent but does not synchronize concurrent filesystem uploads;
   coordinate both for a recoverable environment snapshot.
4. **Storage:** back up all application content in `Awsum_Data`, including
   Platform/Agency/Merchant trees, uploaded/private files, security metadata,
   migration records and historical SQLite files. Preserve hierarchy.
5. **PostgreSQL:** create native logical backups of the Platform database and
   every required tenant database. Record roles/grants separately as needed.
   Do not copy a running `PostgreSQL/Data` directory as an application backup.
6. **Configuration:** securely preserve required environment values, `.env`,
   encryption keys, DB/SMTP credentials, proxy/network configuration and optional
   GeoIP database configuration. Keep secrets encrypted/access-controlled and
   separate from public source/artifacts.
7. **Dependencies:** record OS/toolchain versions used successfully, Python
   packages, Node/npm and PostgreSQL versions, service setup and optional desktop/
   Android toolchains. This is diagnostic information, not a permanent version pin.
8. **Verify before travel:** hash/count backed-up files, check SQLite integrity,
   list native dump contents and perform a restore test into an isolated database.
   Confirm decryption-dependent settings can be read with the preserved key.
   Maintain an independent backup copy; do not rely solely on the old computer.

## 3. New-computer prerequisites

Check the repository's **current dependency files at installation time**:

| Component | Purpose / source of requirements |
|---|---|
| Python | Backend runtime; inspect current backend dependencies and CI/runtime guidance |
| FastAPI, Uvicorn, SQLAlchemy, psycopg and related packages | Root [requirements.txt](../requirements.txt) |
| PostgreSQL and client tools | Database service, `psql`, `pg_dump`, `pg_restore`; check dump compatibility |
| Node.js/npm | Build toolchain for the common image components (and future client apps); satisfy current Vite/package engine requirements |
| Svelte, Vite, TypeScript, svelte-check | [shared/image-components/package.json](../shared/image-components/package.json) and lockfile; future client apps under `frontend\` have their own |
| Git | Source clone/updates |
| VS Code | Recommended development editor |
| Rust/Cargo, Tauri CLI, Windows native build tools, WebView2 | Only when a real Tauri desktop target is present |
| Java/JDK and Android SDK/build tools | When Android/mobile development is required |

The image components are **Svelte/Vite, not SvelteKit**. Active management
screens use FastAPI/Jinja with the compiled shared Svelte image components. Do
not assume a SvelteKit server or invent a frontend/backend proxy configuration.

Do not permanently depend on the exact versions used by one workstation.
Honor current manifests/lockfiles and the applicable PostgreSQL restore/version
compatibility rules. If no backend lockfile exists, preserve the old package
inventory and test the supported dependency combination before cutover.

## 4. Restore the source

Preferred:

```powershell
Set-Location 'D:\01_Development\Awsum_Projects'
git clone <AUTHORIZED_REPOSITORY_URL> Awsum_Platform
Set-Location '.\Awsum_Platform'
git status --short
git log -1 --oneline
```

Check out the recorded approved branch/commit using normal non-destructive Git
operations. Alternatively, restore the verified source backup. Ensure the
Central Server built-in assets under `static\images\platform` (logo,
background, favicon, defaults, ui) and required compiled assets under
`static\components` are present or rebuild them. Built-in Platform assets never
live in `frontend\` or a `backend\static` folder.

Repository roles (see [AWSUM_PROJECT_ARCHITECTURE.md](AWSUM_PROJECT_ARCHITECTURE.md)):

* `Awsum_Platform\backend` = AWSUM Central Server logic / Platform Admin / API.
* `Awsum_Platform\templates` = server-rendered HTML.
* `Awsum_Platform\static` = server web assets and built-in Platform images.
* `Awsum_Platform\shared\image-components` = common image component source
  (compiled into `static\components\image-uploader`).
* `Awsum_Platform\frontend` = client applications (POS, KIOSK, Customer
  Ordering) **when present**; currently only a README.
* `Awsum_Data` = runtime/business/uploaded data (separate restore, section 5).

**Do not rely on copying `.venv` or `node_modules`.** They may contain absolute
paths, native binaries and OS-specific state. Recreate them on the new machine.

## 5. Restore Awsum_Data

Restore application storage to the chosen external root and verify:

```text
Awsum_Data\
|-- platform-admin\
|-- agency\
`-- merchant\
    `-- {MERCHANT_CODE}\store\{STORE_CODE}\
```

Preserve owner codes, hierarchy, files and private audit/security records.
Stores must remain under their actual Merchant, not a standalone Store root.
Verify file counts/hashes against the backup manifest and SQLite integrity
against the recorded baseline. Set appropriate filesystem permissions for the
application service account. Never grant public access to the whole root.

If the old root also contained `PostgreSQL\Data`, treat it separately: install a
new PostgreSQL service/cluster and restore logical dumps. Do not merge a copied
cluster into a live new cluster or manipulate internal files.

### Existing layout-v2 marker on a different physical root

Current startup checks
`platform-admin\data\security\storage-layout.json` for version 2 and an exact
match between its `storage_root` and the resolved configured root. A file copied
from a different drive/OS will still contain its old physical path.

Before starting the restored application:

1. Keep writers stopped and retain the original marker/backup unchanged.
2. Verify the restored files, owners, logical DB references and new root.
3. Record an explicit old-root -> new-root relocation record in private audit
   storage, including original/new marker values and verification results.
4. In the **restored copy only**, reconcile the marker's `storage_root` with the
   new resolved root, preserve `version: 2`, and reconcile its `manifest`
   reference with the location of the preserved recovery manifest.
5. Do not rewrite the historical manifest's source/destination paths or hashes
   to pretend an old cutover occurred on the new machine. Keep historical paths
   as evidence and reference the separate relocation mapping.
6. Validate current persisted references and startup. Never delete the marker
   or disable validation to bypass missing data.

There is no automatic cross-machine marker-rebase command in the current
repository. This is a deliberate, reviewed metadata reconciliation step, not a
reason to move business files blindly. For an identical resolved root, preserve
the marker and verify it without unnecessary edits. Historical cutover
`--verify` checks are snapshot-specific and are not a replacement for new-machine
restore validation.

## 6. PostgreSQL backup and restore

Use installed PostgreSQL client tools compatible with the source server/dump.
Ensure commands are on PATH or invoke their full executable paths. Use password
prompts, an access-controlled password file or an approved secret mechanism;
do not embed real passwords in commands, source or documentation.

Example old-computer backup:

```powershell
pg_dump --host localhost --port 5432 --username <BACKUP_ROLE> --dbname awsum_platform --format custom --file '<BACKUP_DIR>\database\awsum_platform.dump'
pg_restore --list '<BACKUP_DIR>\database\awsum_platform.dump'
```

Check exit codes; catalogue listing alone is not a successful restore test.
Back up required tenant databases similarly. A database dump does not include
all cluster-global roles. Record/recreate required roles and grants, or use an
approved secure globals backup procedure; globals may contain credential hashes.

For an **empty, explicitly selected new target database**:

```powershell
createdb --host localhost --port 5432 --username <PROVISIONING_ROLE> --owner <APP_ROLE> awsum_platform
pg_restore --host localhost --port 5432 --username <RESTORE_ROLE> --dbname awsum_platform --exit-on-error '<BACKUP_DIR>\database\awsum_platform.dump'
psql --host localhost --port 5432 --username <APP_ROLE> --dbname awsum_platform --command 'SELECT current_database(), current_user;'
psql --host localhost --port 5432 --username <APP_ROLE> --dbname awsum_platform --command '\dt'
```

Create required roles first, satisfy restore privileges, and decide explicitly
whether to preserve ownership or use a reviewed ownership/grant remapping.
Do not blindly add `--clean`, drop a populated database, or restore over newer
business data. Stop on restore errors and inspect the target before retrying.

Verify schemas, tables, row counts, important owner/Account IDs, foreign keys,
sequences, extensions, privileges and required credentials. Verify the
application role can connect/read/write in an isolated test transaction or
fixture, and that role provisioning privileges meet actual deployment needs.
The startup path can initialize/migrate data: point it only at the intended
restored database, not an unrelated server.

## 7. Environment and secrets

Create/restore the protected, Git-ignored root `.env` or configure equivalent
service environment values. Example storage setting only:

```text
AWSUM_STORAGE_ROOT=C:\Awsum_Projects\Awsum_Data
```

Required/relevant configuration:

* `AWSUM_PLATFORM_DATABASE_URL`: explicit SQLAlchemy PostgreSQL connection URL
  for the intended host/port/database/role. `PLATFORM_DATABASE_URL` is the current
  fallback; prefer one authoritative value. Supply the real URL securely and
  URL-encode special credential characters. Do not rely on development defaults.
* `PLATFORM_PG_ADMIN_DB`: administrative database used where provisioning needs it.
* `AWSUM_SYSTEM_SETTINGS_ENCRYPTION_KEY`: preserve the existing valid encryption
  key when restoring encrypted System Settings such as SMTP credentials.
  Generating a different key does not decrypt old values.
* Required SMTP settings/secrets, or the restored encrypted DB settings and key.
* `AWSUM_TRUSTED_PROXY_CIDRS`: only real trusted proxy networks, when deployed
  behind a proxy; do not trust arbitrary forwarded headers.
* `AWSUM_GEOIP_DATABASE_PATH`: optional local GeoIP database location, if used.
* Any current session, application-mode, service and optional client settings:
  inspect current implementation/deployment configuration before setting them.
  Do not invent an `AWSUM_ENV` switch or assume `--reload` configures production.

Remove retired `AWSUM_DATA_ROOT`. Ensure stale process/service environment values
do not override the new `.env`. Align any saved System Settings path; the
environment override is authoritative when set.

Current application startup loads the root `.env`; not every standalone
database utility loads every local setting. Export required DB settings into the
process before standalone utilities, or use an approved configuration loader.

Protect configuration and backups, never commit them. Review current
authentication/signing configuration before a public deployment: this checkout
still has a development signing configuration in
[jwt_handler.py](../backend/utils/jwt_handler.py), not a demonstrated
environment-managed signing-secret interface. Do not assume adding an invented
secret variable fixes it. Production hardening requires separately approved
implementation and verification; this documentation does not change application
code or certify internet-facing readiness.

## 8. Recreate the backend and start FastAPI

From the **Awsum_Platform root**:

```powershell
Set-Location 'D:\01_Development\Awsum_Projects\Awsum_Platform'
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Use the intended supported Python interpreter. Activate the recreated environment
if desired; if PowerShell activation is restricted, use its executable directly.
After installing dependencies, the known project development command is:

```powershell
py -m uvicorn backend.main:app --reload
```

Run it from the root, not from `backend`. Ensure `py` resolves the recreated
environment; an unambiguous alternative is:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload --no-proxy-headers --host 127.0.0.1 --port 8000
```

`--no-proxy-headers` preserves transport peer information for AWSUM's own trusted
proxy handling. Match the repository's current proxy guidance for deployment.
Development reload mode is not a production service configuration.

Watch startup logs, confirm the intended root/database, then request
`http://127.0.0.1:8000/platform-admin`. Resolve missing-file, root-marker,
permission or migration errors explicitly; do not reset data to make startup pass.

On Linux, use the corresponding `python`/`python3`, virtualenv `bin/python` and
POSIX physical paths. Logical resource paths remain unchanged.

## 9. Frontend setup and scripts

### Common image components (required)

The backend-owned common image components are built from
`shared\image-components`. Use its package configuration and committed lockfile:

```powershell
Set-Location 'D:\01_Development\Awsum_Projects\Awsum_Platform\shared\image-components'
npm.cmd ci
npm.cmd run check
npm.cmd run build
```

Use `npm` on platforms without the Windows execution-policy issue. If there is
no committed lockfile in a future checkout, follow that checkout's documented
package-manager workflow instead of pretending `npm ci` can run.

Current scripts are `check` (svelte-check) and `build`. `build` emits the shared
components served by FastAPI under `static/components/image-uploader/`
(`image-uploader.js`, `image-uploader.css`). Restore `config/image.json` with the
source: both backend and the browser build read this one common image
configuration. After changing it, rebuild the shared bundle and restart/reload
the backend; do not edit generated JavaScript settings.

### Client applications (when present)

`frontend\` is reserved for client applications (`pos\`, `kiosk\`,
`customer-ordering\`). None exists yet. When one is added, install and run it
from its own folder using that app's package configuration and scripts.
`/` and `/platform-admin` always remain on FastAPI.

## 10. Restore acceptance checklist

Use restored real data read-only first. Run registration, upload and mutation
checks in an isolated fixture/database or with explicitly approved disposable
records; do not silently add test accounts to the business database.

- [ ] Platform Admin login and direct `/platform-admin` entry work.
- [ ] Expired-session entry shows Platform branding and the login modal.
- [ ] Logout returns to the correct login context.
- [ ] Dashboard and Master Group screens work.
- [ ] MD Master list/details and existing MD images load.
- [ ] MD image upload/replace/remove/cancel behave correctly.
- [ ] Users and MD both open the same Adjust Image window and save configured
      square WEBP through the common image framework; legacy JPG/PNG stays intact
      until a replacement is saved.
- [ ] Users and saved User images load; new photos use numeric User IDs.
- [ ] Agency, Merchant and Store lists/edit flows preserve IDs and relationships.
- [ ] Store files remain beneath the correct Merchant.
- [ ] Device/License screens and existing management functions work.
- [ ] Sessions and one-active-web-session behavior work.
- [ ] Session IP/location describe the client, not the server.
- [ ] Reports/CSV/Excel/PDF exports render with required fonts and images.
- [ ] PostgreSQL read/write, credentials and grants are verified.
- [ ] Runtime writes remain under the configured external root.
- [ ] Built-in Platform logo/background/default placeholders load.
- [ ] Uploads validate content and private images require authorization.
- [ ] No whole-root static mount exposes data, audit files or backups.
- [ ] Restart succeeds without recreating old program-data directories.

Optional non-mutating owner preview from the program root:

```powershell
.\.venv\Scripts\python.exe -m tools.initialize_storage --dry-run
```

Use current repository tests for the affected functionality. Some tests need
extra test-only tools such as headless Chrome or isolated PostgreSQL schemas;
inspect their prerequisites rather than assuming runtime dependencies include
every test dependency. Never aim an ad hoc migration/reset test at actual data.

## 11. Windows/Tauri verification

First verify the backend and the common image component build on Windows. No
client application (and no Tauri script or Tauri/Rust project manifest) exists
under `frontend\` yet; there is no Tauri build command to run here yet. Mark desktop packaging **not applicable**
until the actual desktop target is present.

When developing a real Tauri target, install its supported Rust toolchain,
Windows native/C++ build tools, WebView2 and matching Tauri CLI. Use the actual
target manifest/scripts, verify development launch and Windows release build,
and launch the produced application. Check API connectivity, authentication,
branding, permissions, uploads and client metadata on the target Windows PC.
Do not add scaffolding or invent commands as part of installation.

## 12. Android verification when required

Android is optional unless a mobile target is being developed. Install the
target's supported Java/JDK and Android SDK/platform/build tools; add NDK/CMake
only when its actual build requires them. Configure `JAVA_HOME` and the Android
SDK location according to that toolchain. Verify Java and `adb` discovery.

For an actual mobile/Tauri target, follow its checked-in build scripts, verify
Gradle/toolchain compatibility, build the debug target, and install/run it on an
emulator or authorized device. Verify network access to the backend:
`127.0.0.1` on a phone is the phone, not the development PC.
Test login, images, uploads and application lifecycle. Keep signing keys private.
No Android project/build script is currently established under `frontend\`;
record **not applicable**, not a fabricated successful Android build.

## 13. Old absolute-path check

Search source/configuration templates for old-machine paths using VS Code search
or an available search tool. For example, from the program root with ripgrep:

```powershell
rg --hidden --fixed-strings 'D:\01_Development\' backend templates static shared frontend tools config
```

Exclude `.git`, `.venv`, `node_modules`, generated build output and historical
backups when assessing live dependencies. Review matches; do not bulk-replace
them. Documentation examples and test fixtures are not necessarily live paths.

Inspect persisted logo/photo/document/file-DB references using read-only queries
and the current schema. They must use logical owner paths, not old drive paths.
Review saved storage settings separately as installation configuration.
Check optional font/GeoIP/tool locations, service working directories and the
layout marker. Offline recovery manifests intentionally retain historical paths;
preserve their evidence and record relocation mappings separately.

Application logic and business DB records must not depend on
`D:\01_Development\...`. Only local configuration/operational metadata and clearly
labelled historical/example evidence may describe that physical workstation.
Do not rename codes, IDs, PostgreSQL DB names or cluster files while fixing paths.

## 14. Future backup standard

Recommended backup package concept:

```text
AWSUM_BACKUP_YYYYMMDD/
|-- storage/
|   `-- Awsum_Data/          application storage snapshot
|-- database/
|   `-- PostgreSQL backups  Platform and required tenant logical dumps
`-- manifest/
    `-- backup information
```

This is an operational convention, not a claim that an automatic packager exists.
Exclude live PostgreSQL cluster internals from the application storage snapshot.
Use native PostgreSQL backups or a separately designed supported physical-backup
procedure. Protect SQLite consistency.

Record UTC backup time, source commit, resolved roots, DB/server versions,
database list, roles/ownership strategy, owner counts, checksums/file sizes,
backup status and tested restore results. Preserve original migration evidence.
Reference separately secured environment/encryption/signing secrets without
embedding their values in a shared manifest. Keep independent encrypted copies,
document retention/access controls and periodically rehearse a restore.

Do not delete the old computer's only working environment until the new
installation passes acceptance and independent backups are verified.

## 15. U.S. reinstallation checklist

Moving to the United States does not require code/owner-path changes. Review
network/firewall settings, client-IP forwarding, system timezone and report
fonts/settings explicitly; never hard-code a U.S. location into session metadata.

- [ ] Install Git
- [ ] Install VS Code
- [ ] Install Python
- [ ] Install Node.js/npm
- [ ] Install PostgreSQL
- [ ] Clone/restore Awsum_Platform
- [ ] Restore Awsum_Data
- [ ] Restore PostgreSQL
- [ ] Recreate Python environment
- [ ] Install backend dependencies
- [ ] Install frontend dependencies (`shared\image-components`; client apps when present)
- [ ] Create/restore .env
- [ ] Set AWSUM_STORAGE_ROOT
- [ ] Reconcile relocated layout marker and preserve recovery mapping
- [ ] Verify database connection
- [ ] Start FastAPI
- [ ] Start frontend (build image components; start client apps when present)
- [ ] Test /platform-admin
- [ ] Test images/uploads
- [ ] Test Agency/Merchant/Store
- [ ] Test MD Master
- [ ] Test Users
- [ ] Test Sessions
- [ ] Test Reports
- [ ] Install Tauri/Java/Android tools when needed
- [ ] Test Windows build
- [ ] Test Android build

For optional desktop/mobile items, record the target and result, or explicitly
record not applicable with the reason. Do not claim a build was tested if its
project/toolchain is absent.

## Mandatory developer and coding-agent rule

Before making changes related to storage, filesystem, images, uploads, Agency
paths, Merchant paths, Store paths, installation, migration, or backup/restore,
read these documents first:

* [docs/AWSUM_PROJECT_ARCHITECTURE.md](AWSUM_PROJECT_ARCHITECTURE.md)
* [docs/AWSUM_STORAGE_STANDARD.md](AWSUM_STORAGE_STANDARD.md)
* [docs/AWSUM_INSTALLATION_GUIDE.md](AWSUM_INSTALLATION_GUIDE.md)

Do not introduce a conflicting architecture without explicit approval.
