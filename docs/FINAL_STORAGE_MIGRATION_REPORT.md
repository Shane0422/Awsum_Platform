# Final program/data storage cutover

Date: 2026-10-29. Result: **COMPLETED**, including verified legacy-source cleanup.

## Effective configuration

```text
Program: D:\01_Development\Awsum_Projects\Awsum_Platform
Storage: D:\01_Development\Awsum_Projects\Awsum_Data
AWSUM_STORAGE_ROOT=D:\01_Development\Awsum_Projects\Awsum_Data
```

The Git-ignored server configuration has been updated. Application and standalone
commands read the same storage setting; process environment wins over local
configuration. The precedence is environment, saved System Settings, then the
sibling `Awsum_Data` default. Paths must be absolute and outside the program tree.
`AWSUM_DATA_ROOT` is retired and produces an explicit error if still set.
Existing System Settings API/database names remain compatible.

Logical resource references have not changed. Physical image mapping no longer
inserts `media`. Examples:

| Resource | Previous physical path, relative to program | New physical path, relative to storage |
|---|---|---|
| Merchant logo | `data/media/merchant/CLT11001/images/logo/logo.png` | `merchant/CLT11001/images/logo/logo.png` |
| Store logo | `data/media/merchant/CLT11001/store/CLT00001/images/logo/logo.png` | `merchant/CLT11001/store/CLT00001/images/logo/logo.png` |
| MD image | `data/media/platform-admin/images/master/MD00001.png` | `platform-admin/images/master/MD00001.png` |
| Historical SQLite | `data/platform-admin/data/master/admin.db` | `platform-admin/data/master/admin.db` |

No schema, business-code, Account ID, password, permission, internal-ID or
foreign-key changes were needed for this cutover. PostgreSQL business/security
table fingerprints matched before and after application startup and cleanup.
Session audit rows alone are intentionally excluded from those fingerprints.

## Inventory, backup and migration

Manifest ID: `20261029T120115Z-c8df0f5b`.

The private recovery directory is:

```text
Awsum_Data\platform-admin\data\audit\storage-migrations\20261029T120115Z-c8df0f5b\
  manifest.json
  platform.dump
  backup\{original-project-relative-path}
```

| Item | Actual result |
|---|---:|
| Inventoried legacy files | 124 |
| Source copies backed up and SHA-256 verified | 124 |
| Bundled program assets moved to Vite public assets | 15 |
| Owner-scoped runtime/historical SQLite files copied | 9 |
| Historical, unknown-owner or redundant files retained in recovery archive | 100 |
| Destination collisions | 0 |
| Verified actual owner roots | 7 |
| Verified persisted nonempty resource references | 4 |
| Legacy source files remaining after cleanup | 0 |

The native PostgreSQL custom-format dump is 162,736 bytes and was validated with
PostgreSQL 16 `pg_restore --list` (401 TOC entries). Every original source also
remains in the recovery backup, including files that became bundled assets.
The archive can contain confidential account data; it is not publicly mounted.

`admin.db` is 102,400 bytes, retains its original SHA-256
`6c6d9d60156a56ff09bad0ecb6def1ef92d1f676343e6080242f450b98950dfb`,
and passed read-only SQLite `PRAGMA integrity_check` before legacy removal and
again after cutover. The active Platform backend is PostgreSQL; preserving this
historical SQLite file does not switch the backend to SQLite.

Existing `Awsum_Data/PostgreSQL/Data` is the active cluster and was excluded from
the file inventory, copy and cleanup. No PostgreSQL internal file was edited.
Database backup used `pg_dump`, not a live cluster-directory copy.

Unknown historical Merchant `CLT11003` and standalone Store assets such as
`STR20007`, `STR20009`, and `STR20010` were not assigned invented owners. They
remain recoverable in the archive. Prior migration maps and schema snapshots
are archived there too.

## Actual owner preservation

| Owner | DB ID | Code | Logical root |
|---|---:|---|---|
| Platform | - | - | `/platform-admin` |
| Agency | 1 | AGT00001 | `/agency/AGT00001` |
| Agency | 2 | AGT00002 | `/agency/AGT00002` |
| Agency | 3 | AGT00003 | `/agency/AGT00003` |
| Merchant | 1 | CLT11001 | `/merchant/CLT11001` |
| Merchant | 12 | CLT11002 | `/merchant/CLT11002` |
| Store | 1 | CLT00001 | `/merchant/CLT11001/store/CLT00001` |

Store 1 still belongs to Merchant 1. Technical HQ Store 1000001 (`Admin`) was not
treated as a business Store and has no invented tenant directory.

All five existing User photo references remain NULL. No test User, MD record or
photo was added to the actual database. Existing MD 1 and its image were preserved.
The four actual persisted file references are Merchant 1 logo, Merchant 12 logo,
Store 1 logo, and MD 1 photo; their logical DB values remained unchanged.

## Resulting trees

Program assets (the frontend is Svelte/Vite, not SvelteKit):

```text
Awsum_Platform\
  backend\
  templates\
  tools\
  docs\
  static\
    css\
    js\
    components\image-uploader\
  frontend\
    src\
    public\images\platform\
      logo\             logo_full.png, icon.png, icon_01.png..icon_03.png
      background\       hero_platform.jpg, tuxedo_banner.jpg
      ui\               dashboard/feature/collection images (8)
      defaults\         default-user.svg, no-image.svg
    dist\               generated Vite build
```

There are 15 preserved bundled raster assets plus two new shared SVG defaults.
No separate white-logo asset was found or invented. Custom Platform/organization
logos remain runtime references, with custom-logo priority and existing
Store -> Merchant -> Agency -> Platform inheritance. Unconfigured runtime files
are not silently selected as the Platform default.

Actual owner/data layout (archive and PostgreSQL internals intentionally omitted):

```text
Awsum_Data\
  platform-admin\
    images\
      logo\icon_03.png
      master\MD00001.png
    data\
      master\admin.db
      audit\storage-migrations\20261029T120115Z-c8df0f5b\...
      security\storage-layout.json
  agency\
    AGT00001\{images,data}\
    AGT00002\{images,data}\
    AGT00003\{images,data}\
  merchant\
    CLT11001\
      images\logo\{logo.png,icon.png}
      data\
      store\CLT00001\
        images\logo\{logo.png,icon.png}
        data\
    CLT11002\
      images\logo\{logo.png,icon.png}
      data\
      store\
  PostgreSQL\Data\...    existing cluster; untouched
```

The retained Platform runtime `icon_03.png` differs from the bundled asset; it was
preserved rather than discarded or selected implicitly. Category directories are
created on demand. Newly saved User photos use `platform-admin/images/users/`,
not `employee/`. Merchant `store/` namespaces initialize automatically.

After verification, only inventoried legacy files and empty descendants were
removed. These program directories no longer exist:

* `data/`
* `static/images/`
* `db_backups/`

The program `static/` directory, source, templates, compiled components, new
bundled assets, external runtime root, recovery archive and PostgreSQL cluster
remain. No broad application/data-root recursive deletion was used.

## Implementation surfaces

* [Storage configuration](../backend/services/system_settings.py) and
  [path resolution](../backend/services/paths.py): one external storage root.
* [Image resolver](../backend/services/images.py),
  [bundled asset resolver](../backend/services/builtin_assets.py),
  [application mounts](../backend/main.py) and templates: program/runtime split.
* [Owner storage lifecycle](../backend/services/resource_storage.py):
  consistent registration, startup and repair.
* [Cutover validation](../backend/services/storage_layout.py) and
  [startup](../backend/database/db_init_platform.py): layout-v2 marker and
  persisted-reference checks instead of replaying obsolete source archives.
* [Final migration CLI](../tools/migrate_final_storage.py): inventory,
  collision refusal, native backup, hash verification, SQLite checks, recorded
  mapping and explicit verified cleanup.
* [Database snapshots](../tools/backup_platform_db_snapshot.py),
  [schema backups](../backend/utils/safe_schema_migrate.py), and
  [business-code recovery maps](../tools/migrate_business_codes.py):
  new output defaults remain in private Platform audit storage.
* Shared [Avatar](../shared/image-components/src/lib/Avatar.svelte) and
  [ImageUploader](../shared/image-components/src/lib/ImageUploader.svelte): reusable bundled
  placeholders, without per-tenant default copies.
* [Final-storage tests](../tools/test_final_storage.py), related existing test
  updates, [README](../readme.md), and
  [permanent standard](AWSUM_GLOBAL_PATH_STANDARD.md).

## Verification

* Full focused regression: **233 tests passed**, with isolated PostgreSQL
  session/image concurrency and migration tests enabled, plus headless Chromium
  component/report tests.
* `npm.cmd run check:images`: zero errors and zero warnings.
* `npm.cmd run build:images` and `npm.cmd run build`: succeeded.
* Actual server: Platform login/expired-session entry, bundled logo, and migrated
  Store logo returned HTTP 200. The expired-session modal showed the bundled logo.
  Stale/invalid session cookies did not block public bundled assets.
* Without a valid session, private MD image and direct SQLite/audit backup URL
  probes returned HTTP 401. With an authorized isolated fixture, nonexistent
  direct audit-file routing returned HTTP 404. There is no public data-root mount.
* Isolated authenticated browser checks: dashboard, Agency, Merchant, Store,
  Users and Sessions screens plus their list APIs returned HTTP 200.
* Browser-created User photo saved at
  `/platform-admin/images/users/4.png`, returned HTTP 200 with
  `private, no-store`, and displayed in the Users list.
* Browser-created MD photo saved at
  `/platform-admin/images/master/MD00001.png` in the same isolated root.
  Both files physically existed directly under that root; no `media/` appeared.
  These records/files were test fixtures, not actual business records.
* Existing automated coverage verified registration/edit initialization,
  owner authorization, save/remove/cancel/error recovery, single-active-session
  rules, CSV/Excel/PDF/report behavior, and original code issuance.
* Actual manifest verification passed after cleanup. All 124 backups remained;
  DB fingerprints matched and SQLite integrity remained OK.
* A fresh full-lifespan application instance started after the old source
  directories were gone; login, bundled logo and migrated Store logo returned
  HTTP 200. Its temporary verification process was stopped afterward.

## Reverification and recovery

From the program root:

```powershell
py -m tools.migrate_final_storage --manifest "D:\01_Development\Awsum_Projects\Awsum_Data\platform-admin\data\audit\storage-migrations\20261029T120115Z-c8df0f5b\manifest.json" --verify
py -m tools.initialize_storage --dry-run
```

Manifest verification is a cutover snapshot check, not an ongoing health check:
new legitimate business writes or image replacements will change its recorded
hashes. Reconcile such changes rather than overwriting them to make a snapshot
pass. Reuse this manifest; do not generate a fresh cutover inventory after cleanup.

Recovery is manual and reviewable. Stop writers, inspect the manifest and any
post-cutover changes, restore only the necessary original files from `backup/`
to their recorded source paths if absent/identical, and restore the old root
configuration only for a coordinated application rollback. Do not send legacy
writers to obsolete SQLite copies. A PostgreSQL restore must use the matching
native dump and must not discard newer business transactions blindly.
Keep the final root and recovery archive until recovery is validated.

No required cutover work remains. Recovery archives are intentionally retained;
future retention/deletion needs a separate reviewed decision. Employee/POS data
sync and cloud/object storage adapters remain outside this task.
