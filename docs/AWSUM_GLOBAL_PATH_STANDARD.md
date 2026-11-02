# AWSUM PLATFORM / TUXEDO POS Global Path Standard

## Permanent business hierarchy

```text
AWSUM PLATFORM
  Platform Admin
  Agency
  Merchant
    Store
```

**A Store always belongs to a Merchant.** Agency is not a physical parent of
Merchant or Store. Agency associations (including different Agencies for different
Stores) remain database relationships, not directory nesting. This standard does
not change business-code prefixes, numeric IDs, foreign keys, or login availability.

## Owner-first logical namespaces

| Owner | Context root |
|---|---|
| PLATFORM | `/platform-admin` |
| AGENCY | `/agency/{agency_code}` |
| MERCHANT | `/merchant/{merchant_code}` |
| STORE | `/merchant/{merchant_code}/store/{store_code}` |

Every root supports `images/` and `data/`. The general pattern is:

```text
owner/context -> resource type -> category -> optional image subcategory -> file
```

Representative permanent paths:

```text
/platform-admin/images/logo/logo.webp
/platform-admin/data/security/
/platform-admin/data/audit/
/agency/AG000001/images/logo/logo.webp
/agency/AG000001/data/master/
/merchant/MC000001/images/logo/logo.webp
/merchant/MC000001/data/master/
/merchant/MC000001/store/ST000001/images/logo/logo.webp
/merchant/MC000001/store/ST000001/images/master/item/IT000025.webp
/merchant/MC000001/store/ST000001/images/employee/EMP000007.webp
/merchant/MC000001/store/ST000001/data/master/
/merchant/MC000001/store/ST000001/data/sales/
/merchant/MC000001/store/ST000001/data/transaction/
/merchant/MC000001/store/ST000002/data/inventory/
```

Production business codes currently use examples such as `AGT00001`, `CLT11001`,
and `CLT00001`. The illustrative AG/MC/ST prefixes above do not rename them.

Do **not** create new resources under `/store/{code}/`, `/data/store/{code}/`,
`/images/store/`, or `/agency/{code}/merchant/...`.

### Categories and naming

Images: `logo`, `master`, `users`, `employee`, `background`, `common`.
Optional `master` subcategories: `item`, `category`, `modifier`, `department`,
`group`, `other`. Do not force a subcategory when the module does not need it.

Data:

| Context | Supported categories |
|---|---|
| PLATFORM | `master`, `security`, `audit`, `common` |
| AGENCY / MERCHANT | `master`, `common` |
| STORE | `master`, `sales`, `transaction`, `employee`, `inventory`, `report`, `common` |

Directory names are lowercase and singular, except the reserved `users` category
for application-user profiles. Use `employee`, never `employees`,
`employee_photo`, `emp`, or `staff`. Codes keep their established case.
Logical/API/URL paths always use `/`, never Windows separators.
Registration/bootstrap initializes only the basic `images/` and `data/` roots.
Category directories are created lazily when writing/provisioning, never by
path resolution alone.
Add future categories centrally only when there is a real consuming module and
an explicit read/write security policy.

## Central resolver

Use [AwsumPathResolver](../backend/services/paths.py), not string concatenation
in routers, services, repositories, Svelte, or other frontend components:

```python
from backend.services.paths import AwsumPathResolver

owner = AwsumPathResolver(
    "STORE", merchant_code="MC000001", store_code="ST000001"
)
assert owner.get_context_root() == "/merchant/MC000001/store/ST000001"
assert owner.get_image_root() == "/merchant/MC000001/store/ST000001/images/"
assert owner.get_image_path("master", "IT000025.jpg", "item") == (
    "/merchant/MC000001/store/ST000001/images/master/item/IT000025.jpg"
)
assert owner.get_data_path("sales") == (
    "/merchant/MC000001/store/ST000001/data/sales/"
)
file_path = owner.get_data_path("sales", "SALE0001.json")
```

`PLATFORM` accepts no owner codes. `AGENCY` requires `agency_code`; `MERCHANT`
requires `merchant_code`; `STORE` requires **both** `merchant_code` and `store_code`.
Passing an Agency to a Merchant/Store context is rejected.
`parse_resource()` validates incoming logical paths and returns the owner and
resource tail. A standalone Store path is not a valid new resource.

[ImagePath](../backend/services/images.py) is a typed image adapter to the same
resolver, not an independent hierarchy:

```python
from backend.services.images import ImagePath, image_url

image = ImagePath("STORE", "ST000001", "master", "IT000025.jpg",
                  "item", merchant_code="MC000001")
url = image_url(image.logical_path)
```

The backend returns the final usable/versioned image URL. Templates use the
registered `image_url` helper. Frontend components must use returned URLs, not
reconstruct paths. Current Svelte screens have no image/data path builders.

### MD Master images

Platform Admin MD Master uses the direct `master` category, without `item/` or
`md-master/`. Use `owner.get_master_image_path(master_code)` for any owner context:

```python
assert AwsumPathResolver("PLATFORM").get_master_image_path("MD00001") == (
    "/platform-admin/images/master/MD00001.webp"
)
assert owner.get_master_image_path("MD00001") == (
    "/merchant/MC000001/store/ST000001/images/master/MD00001.webp"
)
```

The established `MD` + five-digit issuance rule is unchanged. Uploads are decoded
and normalized to 512 x 512 WEBP by the common processor, regardless of original
JPG/JPEG/PNG/WebP format. The final filename is the generated code, never the
uploaded name or a UUID. `tb_md_master.c_image_path` is nullable and stores only
the logical reference; `image_url` includes a filesystem-version cache query.
The physical Platform example is
`effective-root\platform-admin\images\master\MD00001.webp` (no `media` container).

The existing modal previews locally and defers replacement/removal until Save.
Cancel discards only local changes. Create/Edit use authenticated multipart
endpoints:

```text
POST /platform/api/master-group/md/with-image
PUT  /platform/api/master-group/md/{numeric_id}/with-image
fields: payload (JSON object), remove_image (boolean), image (optional file)
```

Authorization and origin checks are identical to the existing MD CRUD API.
Clients cannot select the owner, code or destination via the image API.
Existing JSON CRUD clients remain supported and preserve image references.
The additive `migrate_md_images()` runs at normal application startup and is
idempotent; it does not rewrite existing codes, IDs, prices or relationships.

MD writes take a PostgreSQL transaction advisory lock, including canonical
filename checks. An OS byte/file lock (`.master-image.lock`, not a served asset)
serializes workers through filesystem recovery even after a failed SQL commit
has released its transaction lock. New complete bytes are staged in the target directory; existing
bytes are privately backed up before replacement/removal. A DB commit failure
restores the original bytes and reference and removes staged files. Successful
saves delete the prior extension and temporary backup. MD record deletion uses
the same mechanism and retains the existing in-use deletion guard. Generic image
upload/delete reserves MD-code filenames and cannot bypass MD Save/Delete.
Unexpected occupied destinations return 409 rather than overwrite unowned files.
Cleanup errors are logged and surfaced as `image_warning`, not hidden success.
If rollback restoration fails, the backup is retained and a server recovery error
is returned. Recovery paths are logged; do not delete those backups blindly.
This is compensating filesystem rollback around SQL commit, not a distributed
filesystem/DB transaction; forced process termination/power loss during a save
requires administrator reconciliation of the private `.master-image-*.tmp`
files against the MD reference before removing them.

Master image reads remain authenticated and `private, no-store`. Approximate
40px list thumbnails and modal preview use returned URLs and neutral fallbacks.
Images are not added to CSV/Excel/PDF/report exports; price/Cost rules are unchanged.

### User photos and shared form components

Application **Users** are distinct from POS **employees**. Their photos use
`users/`, never `employee/`. Platform photos use the immutable numeric
`tb_platform_user.i_platform_user_id`, not names, email addresses, or a new code:

```text
/platform-admin/images/users/100001.webp
```

`owner.get_user_image_path(user_id)` supports all owner contexts, including
`/merchant/{merchant_code}/store/{store_code}/images/users/{user_id}.webp`.
Future organization-user screens/persistence/authorization are not implemented;
those paths cannot be served or mutated through the generic image API today.
No independent Store root or Agency physical parent is introduced.

The additive, idempotent `migrate_user_images()` startup migration adds nullable
`tb_platform_user.c_image_path`. Passwords, roles, IDs, sessions, and existing
relationships are untouched. Existing accounts without photos need no backfill.
JPG/JPEG/PNG/WebP inputs retain the shared filename/content/5 MiB validation.
All new business images use square 512 x 512 WEBP at quality 88.
The reusable native-canvas `ImageCropper` opens for both User and MD selection and provides
aspect-preserving Zoom In/Out, contain/cover positioning, centered-contain Reset,
Cancel/Escape and local Apply before Save. The shared minimum zoom is 0.25 of
contain scale; the maximum reaches four times cover scale. The configured
opaque background (currently white) is encoded into both preview and output,
including transparent PNG pixels and zoomed-out margins.
Existing JPG/PNG references are preserved on account-only edits and the old
referenced extension is removed only after successful replacement commit.
See the authoritative [common image standard](AWSUM_STORAGE_STANDARD.md#awsum-common-image-standard).
The backend returns `image_path` and versioned `image_url` on user list/save:

```text
POST /platform/user/with-image
PUT  /platform/user/{numeric_id}/with-image
fields: payload (JSON object), remove_image (boolean), image (optional file)
```

Existing JSON user endpoints remain supported and retain their image reference.
Only the existing Platform User administrators can save photos; normal
authentication, origin checks, HQ scope, self-deactivation and last-SuperAdmin
guards remain in force. Photo reads are authenticated and private; generic image
upload/delete cannot bypass the User Save workflow.

MD and Users delegate staged writes, collision protection, compensation, and
cleanup to `CanonicalImageChange` / `commit_entity_image()` in
[image_service.py](../backend/services/image_service.py). User saves hold the
same owner/category OS operation lock through commit and recovery, and lock
existing user rows. The private `.master-image.lock` / `.master-image-*.tmp`
filenames are retained for compatibility in both categories. Forced termination
has the same administrator-reconciliation limitations described above.

Startup image migration recognizes completed source/destination pairs in its
`COMMITTED` recovery maps. A migrated legacy archive is no longer authoritative:
later canonical replacements are preserved, and deleted unreferenced images are
not resurrected on restart. Live legacy DB references still undergo full
preflight/collision checks; missing referenced canonical files remain explicit
errors. No conflict is resolved by overwriting or deleting either file.

The active administration screens remain Jinja forms. They both mount the **one**
[Svelte/TypeScript ImageUploader](../shared/image-components/src/lib/ImageUploader.svelte) through
[the shared bridge](../shared/image-components/src/images.ts), rather than duplicating upload
state/validation in each screen or migrating unrelated pages. Local preview and
Remove are deferred until Save; Cancel/reset/unmount release blob URLs without
calling an API. Saving locks image controls and modal dismissal.
Both forms place a compact 120px preview at the upper right on desktop; narrower
screens stack responsively and retain a fixed footer with scrollable content.
[Avatar](../shared/image-components/src/lib/Avatar.svelte) uses the same server image URL, cover
fitting, and an icon fallback: 36px in Users and 26px in the common header.
Saving one's own photo updates the header without copying the file.

Build and validate after changing the shared components:

```powershell
cd shared\image-components
npm.cmd run check
npm.cmd run build
```

The dedicated build emits
`static/components/image-uploader/image-uploader.{js,css}` and does not replace
the separate Svelte application build. Keep these served bundles in deployment.
The frontend never knows physical storage paths or generates canonical filenames.

## Logical paths versus physical storage

Existing `c_logo_url`, `c_db_path` (when a file reference), and
`c_contract_pdf_path` hold logical paths, not Windows absolute paths. Existing
relational keys remain authoritative; no duplicate owner/path columns are needed.

The local adapter currently maps:

```text
effective-root\merchant\CLT11001\store\CLT00001\images\logo\logo.webp
effective-root\merchant\CLT11001\store\CLT00001\data\master\main.db
effective-root\merchant\CLT11001\store\CLT00001\data\common\contract-{id}-{version}.pdf
effective-root\platform-admin\data\audit\...
```

`effective-root` is `AWSUM_STORAGE_ROOT`, then saved System Settings, then
`PROJECT_ROOT.parent / "Awsum_Data"`. It must be absolute and outside the program
tree. `AWSUM_DATA_ROOT` is retired: a nonempty value fails explicitly instead of
silently selecting old storage. The existing settings API/column names
`data_root_path` / `c_data_root_path` remain for compatibility.
Images and data both map directly beneath this root; there is no physical
`media` container. Changing configuration does not move files.
The [local storage adapter](../backend/services/resource_storage.py) and
`resource_storage_path()` are the replacement points for local/object/CDN storage;
business records and components must not know the physical implementation.
S3/CDN support itself is not implemented.

## Automatic initialization and repair

[AwsumStorageService](../backend/services/resource_storage.py) is the central
storage lifecycle service. It reuses `AwsumPathResolver` for every logical path
and the existing local adapter for physical containment. Call it with **database
IDs**, not browser-provided owner codes or destination directories:

```python
storage = AwsumStorageService(db)
storage.initialize_platform_storage()
storage.initialize_agency_storage(agency_id)
storage.initialize_merchant_storage(merchant_id)
storage.initialize_store_storage(store_id)
```

Store initialization queries `tb_store.i_merchant_id` and the referenced
`tb_merchant.c_merchant_code`. Missing/invalid relationships are reported as failures;
the service never guesses a Merchant or creates a standalone Store root.
The technical HQ Store (`ADMIN_STORE_ID`) is not a business Store and uses
Platform storage; bulk repair deliberately excludes it.

All **mounted registration entry points** initialize after the successful DB
commit and final generated code: Agency, Merchant, Store, and
Merchant-linked Store. The following basic roots are ensured, without creating
future category folders:

```text
/platform-admin/{images,data}/
/agency/{agency_code}/{images,data}/
/merchant/{merchant_code}/{images,data}/
/merchant/{merchant_code}/store/{store_code}/{images,data}/
```

An empty Merchant `store/` directory is not required: the nested namespace is
available and its physical parents are created with the first Store.
Registration has no editable storage-path fields. Display-name edits do not move
directories; permanent codes and relationships remain authoritative. Deleting or
deactivating an organization never deletes its storage. Physical cleanup/retention
is a separate, not-yet-implemented administrative operation.

Initialization is idempotent and uses `mkdir(..., exist_ok=True)`. It never
replaces files, renames directories or removes old paths. File-vs-directory
collisions, traversal, invalid codes and escaping symlinks are rejected rather
than repaired destructively. A partially created root is retained for safe retry.
Business records remain committed on storage errors. The registration response
includes `storage_initialized: false` plus an explicit `storage_warning`, which
both current registration UI flows display. Do not retry the business registration
to fix storage: rerun repair or use the resource after correcting storage access.

Normal startup calls `initialize_missing_storage()` after schema/bootstrap
transactions, for Platform and every saved Agency/Merchant/business Store,
including inactive records whose files may need retention. Per-owner failures
are logged and do not prevent initialization of other owners. Missing directories
are retried on the next startup or before the relevant resource write.
Authorized image/data writes, MD canonical-image staging, and Store SQLite/backup
provisioning all use the shared lazy directory helper. Permission and owner checks
are still required by their existing APIs; initialization itself grants no access.
Path resolution and `--dry-run` do not create directories.

Administrator CLI, from the project directory:

```powershell
py -m tools.initialize_storage --dry-run
py -m tools.initialize_storage
py -m tools.initialize_storage  # safe to repeat
py -W ignore -m unittest tools.test_storage_initialization tools.test_global_paths tools.test_images tools.test_master_images
```

The CLI returns nonzero if any owner could not be resolved/initialized. Its JSON
report contains only logical paths and failed entity IDs; preflight resolves
owner relationships without writing. Check server logs for underlying errors.
Events include `PLATFORM_STORAGE_INITIALIZED`, `AGENCY_STORAGE_INITIALIZED`,
`MERCHANT_STORAGE_INITIALIZED`, `STORE_STORAGE_INITIALIZED`,
`*_STORAGE_INITIALIZATION_FAILED` and `STORAGE_INITIALIZATION_SUMMARY`, with
entity ID/code, logical root, UTC timestamp and success/failure. No file contents
are logged.

**Configuration:** use `AWSUM_STORAGE_ROOT`, then saved System Settings, then
the sibling `Awsum_Data` directory. Do not retain `AWSUM_DATA_ROOT`.
For example:

```text
logical:  /merchant/CLT11001/store/CLT00001/images/
physical: effective-root\merchant\CLT11001\store\CLT00001\images\
logical:  /merchant/CLT11001/store/CLT00001/data/
physical: effective-root\merchant\CLT11001\store\CLT00001\data\
```

This preserves existing logical image URLs while removing the `media` container. Physical
paths remain inside storage implementation/logs, never in business references or
registration API responses. Future object storage can replace initialization and
directory preparation in this service without changing logical owner rules;
no cloud adapter or bucket/prefix provisioning is introduced by this feature.

Merchant initialization also creates its `store/` namespace; individual category
folders remain lazy. Use the reviewed final-storage migration below to separate
bundled assets, map old `media` contents and preserve historical recovery maps.
Do not blindly copy the old root into the program or final root.

Per-Store SQLite helpers now require `(merchant_code, store_code, db=...)`.
They verify the relation before opening/provisioning and refuse unmigrated legacy
DBs rather than create an empty replacement. Schema backup CSVs use that Store's
`data/common/`; Platform schema backups use `/platform-admin/data/audit/`.
PostgreSQL database names are DB identifiers, **not paths**: do not rename them or
edit PostgreSQL internal data files for this standard. The unused historical
`backend/database/admin_db.py` SQLite bootstrap is not the active Platform DB;
its optional SQLite provider now resolves `/platform-admin/data/master/admin.db`
and refuses an unmigrated legacy archive. The final migration copies and integrity-checks that archive if present. Its
original and historical backups remain in the private recovery archive even
after verified legacy-source cleanup.

## Authorization and storage safety

* `validate_owner(db, owner)` checks the actual organization and Merchant/Store
  relation for every canonical image read, upload, delete and data operation,
  even for authorized Platform operators and public logos.
* `authorize_resource(request, db, owner)` checks the active server-side session
  and role. Matching organization-admin sessions and authorized Platform
  administration may manage resources. Branding results and supplied paths never
  grant authorization. Store sessions cannot manage siblings or their Merchant.
* `logo`, `background`, `common` **images** are explicitly public for pre-login use;
  never upload sensitive content there. `master` and `employee` images require
  authorization and use `private, no-store`.
* **All data remains private.** There is no public data-root mount. Existing
  authenticated contract downloads remain authenticated; future data endpoints
  must call `authorized_data_path()` / `write_data()` before touching storage.
* Filenames, context, resource type, categories, extensions, traversal, encoded
  separators, Windows reserved names and storage-root containment are validated.
* Image upload validates matching filename extension, MIME and decoded
  PNG/JPEG/WebP content, 5 MiB input, and 20 megapixels. Normalized output
  is configured square WEBP through one processor. Generic uploads require
  a separate stable `entity_key`; original filenames are not storage keys.
  Generic data writes have a 100 MiB limit and an explicit extension
  allowlist; consuming modules must add content validation appropriate to their
  format. No generic arbitrary-data upload endpoint is exposed.
* Generic writes publish complete files create-only and reject collisions (409),
  never overwrite another asset. Generated PDFs are immutable versioned
  files. User, MD and owner-logo canonical replacement is the transaction-protected
  exception described above. Active logos and MD-managed images cannot be
  deleted through the generic image API.
* A Store with owned files/references cannot be reassigned to another Merchant
  through the ordinary edit form (409). A reviewed ownership transfer must first
  handle resources and references; changing the FK alone is not a file move.

Canonical Store upload API:

```text
POST /api/images/merchant/{merchant_code}/store/{store_code}/{category}
    ?sub_category=item
multipart: file

DELETE /api/images?image_path={canonical_logical_path}
```

Platform/Agency/Merchant retain
`POST /api/images/{context}/{owner_code}/{category}` (Platform owner placeholder:
`platform-admin`). Old generic Store upload API requires explicit `merchant_code`;
missing parent is rejected. Existing numeric-ID logo endpoints derive the parent
from the DB. Cross-origin writes are rejected.

## Branding

Platform entry resolves its configured custom logo, or the bundled Platform
default, independent of previous sessions. Agency and Merchant use their own namespaces. Store logos use
the nested Merchant/Store namespace. Inheritance remains Store -> Merchant ->
applicable Agency relationship -> Platform; no Agency is added to a physical path.
Login paths (for example `/login/store/{code}`) are authentication entries, not
resource roots, and are unchanged.

## Bundled program assets versus runtime files

The common image components (`shared/image-components/`) are Svelte with Vite,
not SvelteKit; `frontend/` is reserved for client applications. Built-in
Platform assets are owned by the Central Server under root
`static/images/platform/{logo,background,favicon,defaults,ui}/` and are served
at `/images/platform/...`. `builtin_assets.py` (`BUILTIN_ROOT`) is the single
authoritative root; it resolves and validates these assets. Templates use
`builtin_asset_url()` rather than runtime storage paths.

Platform's configured custom logo remains an uploaded runtime asset and has
priority over the bundled default. Organization inheritance is unchanged.
Shared placeholders are `defaults/default-user.svg` and `defaults/no-image.svg`;
do not copy them into every tenant. Merchant/Store logos, MD photos and User
photos remain runtime files. There is no public mount of the storage root,
private data, migration archives, SQLite files or PostgreSQL files.

## Final storage migration and recovery

The physical layout is:

```text
Awsum_Projects\
  Awsum_Platform\                 source, templates, compiled/program assets
    static\images\platform\
  Awsum_Data\                     AWSUM_STORAGE_ROOT
    platform-admin\images\...
    platform-admin\data\{master,audit,security}\...
    agency\AGT00001\{images,data}\
    merchant\CLT11001\{images,data,store}\
    merchant\CLT11001\store\CLT00001\{images,data}\
    PostgreSQL\Data\...            existing cluster; excluded from migration
```

The final CLI inventories the old project `data`, `static/images`, and
`db_backups` directories, resolves owners from the DB, and separates built-ins
from current runtime files. Unknown owners and historical/redundant files go to
the private recovery backup, never to a guessed organization. Stop writers and
checkpoint/close SQLite before applying:

```powershell
py -m tools.migrate_final_storage --destination "D:\01_Development\Awsum_Projects\Awsum_Data"
py -m tools.migrate_final_storage --destination "D:\01_Development\Awsum_Projects\Awsum_Data" --apply --pg-dump "C:\Program Files\PostgreSQL\16\bin\pg_dump.exe"
# Set AWSUM_STORAGE_ROOT, remove deprecated AWSUM_DATA_ROOT, then start the app.
py -m tools.migrate_final_storage --manifest "<recorded manifest.json>" --verify
# Only after UI/API/upload checks; optional, exact inventoried legacy files only:
py -m tools.migrate_final_storage --manifest "<recorded manifest.json>" --cleanup --app-verified
```

Private records live under
`platform-admin/data/audit/storage-migrations/{timestamp-id}/`: `manifest.json`,
native `platform.dump`, and `backup/{original-project-relative-path}`.
The manifest records source/destination paths, hashes, sizes, owner mappings and
database fingerprints. PostgreSQL is backed up using `pg_dump`; its cluster
files are never traversed, renamed, edited or removed. Backup contents can
contain confidential data and must not be published.

Differing destinations, invalid ownership, symlinks, active SQLite journals,
changed business/security rows, changed source bytes or newly appearing source
files block cleanup. Verification checks every backup/target hash and SQLite
integrity. Internal IDs, FKs, codes, passwords and permissions do not change.
Session audit rows are excluded from fingerprints so application verification
can create legitimate session activity.

After copying, `platform-admin/data/security/storage-layout.json` identifies
layout version 2 and its recovery manifest. Normal startup validates persisted
runtime references and initializes owners; it no longer replays old archives or
copies built-in defaults into runtime storage. Missing referenced files stop
startup explicitly. Do not remove the marker to bypass a missing file.

Cleanup deletes only verified inventoried source files and then empty legacy
directories; it never recursively deletes an application/data root. Repeating
cleanup against the same manifest is safe while its verification still matches.
Reuse that manifest for verification/cleanup; do not generate a fresh cutover
inventory after deployment.

Recovery is deliberate, not an automatic overwrite: stop all writers, inspect
the manifest/status and reconcile any writes after cutover, restore individual
files from `backup/` into their recorded original paths only if absent or
identical, and restore the prior root configuration. Restore the matching native
database dump with `pg_restore` only if database recovery is actually necessary;
do not restore over newer business transactions blindly. Keep the final root and
archive intact until recovery is validated. The migration itself changes no DB
business values. See the [actual cutover report](FINAL_STORAGE_MIGRATION_REPORT.md).

## Earlier logical-path migration (legacy installations only)

Back up the Platform DB and application data root. Quiesce file writers before
data cutover, especially SQLite. Preview before deploying:

```powershell
py -m tools.migrate_global_paths --dry-run
py -m tools.migrate_global_paths
```

The command preflights image and data domains before applying either. Before final-layout cutover, startup also runs their idempotent migrations. Each domain commits its reference transaction
separately; the process is resumable, not one distributed filesystem/DB transaction.
No existing files or unrelated records are deleted.

* Legacy static images and former `media\store\{code}` images are copied into
  canonical owner paths. Stored Store logos are updated using current DB parent
  relationships. Unknown historical standalone Stores are retained and reported,
  **not** assigned to an arbitrary Merchant.
* Old `/store/{code}/images/...` GETs are read-only compatibility redirects:
  backend resolves the current Merchant from DB, verifies authorization for
  private categories, and redirects to the canonical resource. Old paths cannot
  create/update/delete resources. Old static files are not retained as served
  assets after final-storage cleanup.
* `company\{store_code}\main.db`, recognized Store data files/backups and stored
  legacy `data/contracts/...` references are copied and mapped. Unknown files or
  missing referenced files require review. Unreferenced contract archives remain
  intact; only records prove their ownership. PostgreSQL names/URLs are preserved.
* Source/destination hashes, conflicts, references and ownership are preflighted.
  Different destination contents, invalid owner relations, active SQLite journals,
  or changing source/reference values stop migration instead of overwriting.
* `platform-admin/data/audit/image-migrations/*.json` and
  `platform-admin/data/audit/path-migrations/*.json` are private recovery maps
  with source/destination hashes, before/after references, row IDs and status.
  They are written before copying. Sources remain available for recovery.
* Reruns do not resurrect reset logos or overwrite a canonical SQLite DB that
  now has newer transactions. Do not send legacy writers back to old DB copies
  after cutover.
* `COPIED_PENDING_DB_COMMIT` requires checking recorded references after a crash.
  DB failures roll back reference changes and retain recoverable copies.
  Restore the DB backup or carefully restore recorded `before` values only when
  the matching `after` values still apply. Never delete the whole media/data root.

Tests:

```powershell
$env:AWSUM_TEST_POSTGRES_SESSIONS = "1"
py -W ignore -m unittest tools.test_global_paths tools.test_images tools.test_branding tools.test_web_sessions tools.test_system_settings tools.test_license_email
```

Future POS data repositories, uploads and object-store adapters must reuse this
resolver and authorization policy. No POS data synchronization protocol, new
employee/master image UI, general move endpoint, or new tenant login is introduced
by the path standard.
