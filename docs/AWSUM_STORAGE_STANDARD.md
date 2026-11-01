# AWSUM Storage Standard

**THIS DOCUMENT IS THE AUTHORITATIVE AWSUM STORAGE STANDARD.**

Before modifying AWSUM storage/path architecture, developers and coding agents
must read this document first.

## 1. Program and runtime separation

The permanent rule is:

```text
Awsum_Platform = PROGRAM / SOURCE / BUILT-IN PLATFORM ASSETS
Awsum_Data     = RUNTIME / BUSINESS DATA / UPLOADS
```

Recommended development layout:

```text
D:\01_Development\Awsum_Projects\
|-- Awsum_Platform\
`-- Awsum_Data\
```

The drive and parent directory are deployment choices, not business identifiers.
See the [installation guide](AWSUM_INSTALLATION_GUIDE.md) before relocating.
This standard takes precedence over conflicting historical storage descriptions.
The [global path implementation reference](AWSUM_GLOBAL_PATH_STANDARD.md) and
[completed migration report](FINAL_STORAGE_MIGRATION_REPORT.md) provide additional
implementation and historical evidence, not permission to reintroduce old layouts.

## 2. What belongs in each root

### Awsum_Platform: program

The top-level project structure is defined by the
[project architecture standard](AWSUM_PROJECT_ARCHITECTURE.md). Storage-relevant
roles:

* `backend/` ? **AWSUM Central Server / Platform Admin** logic: FastAPI API,
  services, models and storage resolvers. It contains no web asset folder.
* `templates/` ? server-rendered (Jinja) HTML, including the `/` and
  `/platform-admin` landing pages.
* `static/` ? Central Server web assets (CSS, JS, compiled components) and the
  built-in AWSUM Platform logo, background/main images, favicon, shared default
  placeholders and UI artwork under `static/images/platform/`.
* `shared/image-components/` ? source of the backend-owned common image
  components compiled into `static/components/image-uploader/`.
* `frontend/` ? **client applications only** (POS, KIOSK, Customer Ordering).
  It never owns Central Server pages or built-in Platform assets.
* `config/`, `tools/`, `docs/` ? configuration, tools/tests and documentation.

Local configuration may contain installation-specific absolute paths. Secrets
must stay outside version control, in protected local configuration or a secret
manager. Runtime uploads/business files do not belong in this tree.

### Central Server versus client applications

* **Central Server** = AWSUM Platform Admin / API / server-rendered pages
  (`backend/` + `templates/` + `static/`). It is never called a POS frontend.
* **Client applications** = programs that operate against the Central Server:
  TUXEDO POS, KIOSK and Customer Ordering, under `frontend/`.

Central Server built-in assets are owned by root `static/`, never by `frontend/`
or a `backend/static` folder. Client applications own only their own assets.

Current state (verified): no POS, KIOSK, Customer Ordering or Tauri application
exists yet; `frontend/` contains only a README. Target structure, created only
when a real client application exists:

```text
Awsum_Platform\
|-- backend\                 Central Server logic
|-- templates\               Central Server HTML
|-- static\images\platform\  built-in Platform assets
|-- shared\image-components\ common image component source
`-- frontend\                client applications (when present)
    |-- pos\
    |-- kiosk\
    `-- customer-ordering\
```

Do not create empty or placeholder client applications to satisfy this tree.

### Awsum_Data: runtime

* Platform runtime/custom images and private Platform data.
* Agency, Merchant and Store data/images.
* MD Master images, AWSUM User photos and POS/store Employee photos.
* Owner-scoped uploads and runtime business files.
* Private security metadata, audit records, backup/migration recovery records.

Only built-in shared AWSUM Platform imagery stays with the program. Uploaded
Agency/Merchant/Store images always belong to runtime storage, not frontend
public directories or program static directories.

## 3. Physical storage root

The single physical application-storage setting is:

```text
AWSUM_STORAGE_ROOT=D:\01_Development\Awsum_Projects\Awsum_Data
```

Current resolution order:

1. `AWSUM_STORAGE_ROOT` environment/local configuration.
2. Saved System Settings storage path, if no environment override exists.
3. Sibling `Awsum_Data` relative to the program root.

Process environment takes precedence over the local `.env` file. Existing
settings API/column names `data_root_path` / `c_data_root_path` remain compatible;
they are not an additional storage architecture.

The root must be absolute and outside the program tree. All runtime application
storage must remain beneath it. There is **no physical `media` container** between
the root and owner directories. `AWSUM_DATA_ROOT` is retired; a nonempty value
fails explicitly. Changing configuration does not move existing data.

Do not hard-code the development drive into application logic. Root relocation
requires coordinated backup, restore, reference validation and configuration
reconciliation described in the installation guide.

## 4. Final runtime hierarchy

```text
Awsum_Data/
|-- platform-admin/
|   |-- images/
|   |   |-- logo/
|   |   |-- master/
|   |   |-- users/
|   |   |-- employee/
|   |   |-- background/
|   |   `-- common/
|   `-- data/
|       |-- master/
|       |-- security/
|       |-- audit/
|       `-- common/
|-- agency/
|   `-- {AGENCY_CODE}/
|       |-- images/
|       `-- data/
`-- merchant/
    `-- {MERCHANT_CODE}/
        |-- images/
        |-- data/
        `-- store/
            `-- {STORE_CODE}/
                |-- images/
                `-- data/
```

This is the permitted hierarchy, not a requirement to eagerly create every
category. Categories/subcategories are lazy-created when first needed.

**Store MUST always physically exist under its actual Merchant:**

```text
/merchant/{MERCHANT_CODE}/store/{STORE_CODE}/
```

Never create standalone `/store/{STORE_CODE}/` runtime storage. A legacy read
redirect is compatibility behavior, not a valid new storage namespace.

Agency is a database relationship, **not the physical parent of Merchant/Store**.
Do not introduce `/agency/{code}/merchant/...` storage. Determine Store ownership
from the actual database relation, not from a submitted path or display name.

An existing installation may also have `Awsum_Data/PostgreSQL/Data` for its
database service. That cluster is infrastructure-managed, not an owner resource;
never traverse, rename, copy live, edit or delete its internal files as part of
application storage operations. PostgreSQL may instead live elsewhere.

## 5. Logical paths and database references

| Context | Logical root |
|---|---|
| Platform | `/platform-admin/` |
| Agency | `/agency/{AGENCY_CODE}/` |
| Merchant | `/merchant/{MERCHANT_CODE}/` |
| Store | `/merchant/{MERCHANT_CODE}/store/{STORE_CODE}/` |

Database business records store logical file references, **not Windows absolute
paths**. For example:

```text
DB reference:
/merchant/CLT11001/store/CLT00001/images/logo/logo.png

Development physical file:
D:\01_Development\Awsum_Projects\Awsum_Data\merchant\CLT11001\store\CLT00001\images\logo\logo.png
```

Only the storage implementation joins the configured root and validated logical
path. Logical paths use `/` independent of host OS. Business codes, numeric IDs
and foreign keys must survive relocation unchanged. PostgreSQL database names
are identifiers, not filesystem paths.

Physical paths must never be exposed to frontend components, API business
responses or browser upload forms. Use backend-resolved logical/versioned URLs.
Administrative offline recovery manifests may record physical paths; keep these
private and do not confuse them with portable business references.

## 6. Ownership of path and storage behavior

* [AwsumPathResolver](../backend/services/paths.py) owns logical path generation,
  parsing, categories and owner namespace rules.
* [AwsumStorageService](../backend/services/resource_storage.py) owns owner storage
  lifecycle/physical preparation and coordinates common storage operations.
* The shared local adapter, resource/image helpers and transactional image
  service perform validated physical mapping and writes. Reuse these layers;
  do not implement ad hoc directory concatenation in routers/components.
* Resolve actual owners using database IDs and verified relationships. A path,
  code, logo or branding context is not authorization.
* Filesystem errors must be surfaced/logged. Do not silently claim storage
  initialization or image saves succeeded.

Future local/object/cloud adapters must preserve logical references and owner
authorization. Object storage/CDN support is a portability goal, not a claim that
the current filesystem implementation already provides those adapters.

## 7. Image categories, naming and defaults

Runtime image categories:

```text
logo
master
users
employee
background
common
```

`users` means **AWSUM login account photos**.
`employee` means **POS/store employee photos**.
They are distinct domains and must not be aliases.

Master image shape:

```text
images/master/{CODE}.{ext}
```

Never introduce `images/master/md-master/`. New/replaced images use the common
standard below. Existing JPG/PNG images remain valid and are not bulk-converted.
Display-name/email edits do not change image identities or stored bytes.

### AWSUM COMMON IMAGE STANDARD

**All AWSUM images use the same common image processing framework.
Image types do not have separate upload/processing implementations.
The main difference is the owner/category/entity context that determines the
storage path.**

```text
Select/Replace -> ImageUploader -> ImageCropper ("Adjust Image")
-> Crop/Move/Zoom/Reset -> imageProcessor -> local Apply/Preview
-> Save parent form -> common Image Service -> AwsumPathResolver
-> AwsumStorageService -> Awsum_Data
```

- [config/image.json](../config/image.json) is the single source read by both
  browser and backend: current aspect ratio **1:1**, **512 x 512**, **WEBP**,
  quality **0.88**, JPG/JPEG/PNG/WEBP input up to **5 MiB** and 20 megapixels.
  The shared opaque output `background` is currently **`#ffffff` (white)**.
  Transparent pixels and surrounding canvas space use the same configured color.
  User, MD Master and future Employee images do not define independent sizes.
  A future non-square logo/background policy must extend this same configuration
  and processor, not fork the implementation.
- [ImageUploader](../shared/image-components/src/lib/ImageUploader.svelte) and
  [ImageCropper](../shared/image-components/src/lib/ImageCropper.svelte) are shared by Users and
  MD Master. The adjuster uses native canvas/Image/Blob and Pointer Events,
  **Zoom In, Zoom Out, Move, Contain, Cover, Reset**, Cancel/Escape and Apply.
  Initial fit and Reset use **centered contain**: the complete image fits the
  square, without cropping its top/bottom. Reset restores zoom 1 and both X/Y
  offsets to zero for every category, not a separate Master implementation.
  Zoom is relative to contain scale. The configured minimum is **0.25x**;
  maximum is `maxCoverZoom * coverScale / containScale` (currently **4 times
  cover scale**). Thus even extreme aspect ratios can zoom from a small complete
  image to cover and beyond. The image is never required to cover the frame.
  Scaling always preserves the original aspect ratio; never stretch to 512x512.
  Mouse/touch/keyboard positioning permits background space on either axis.
  Bounds retain at least one quarter of the smaller image/frame dimension
  visible on each axis, preventing the image being moved entirely off-canvas.
  The preview and final **512x512 WEBP** both encode the configured background;
  zoomed-out padding is part of the saved image, not just CSS.
  Fit/placeholder styling is presentation,
  not a switch to another processor. No User-only crop mode exists.
- [imageProcessor.ts](../shared/image-components/src/lib/imageProcessor.ts) validates inputs and
  exports adjusted WEBP. `AwsumImages.adjustImage` / `bindImageInput` let existing
  logo controls use the exact same adjuster without a duplicate editor.
- **Apply does not save.** It returns a temporary File to the parent form;
  no upload/delete occurs. Cancel/Escape preserves the previous selection.
  Canceling User/MD Edit discards pending Apply or Remove. Remove becomes
  persistent only on successful parent Save; then clear the DB reference and
  safely remove the custom file. Existing logo Upload/Reset buttons are explicit
  logo-save actions, separate from organization field editing; selection/Apply
  alone never writes. Company logo persists only on Company Save.
- [images.py](../backend/services/images.py) provides one upload validator and
  one `process_image` implementation for **all** categories. It checks original
  names including raw multipart headers, matching extension/MIME/decoded content,
  size/pixels, EXIF orientation, aspect-preserving contain normalization and
  opaque background composition before WEBP output. Browser-adjusted square
  images retain their layout; direct/internal non-square uploads use centered
  contain rather than a second center-crop. Legacy saved images are unchanged
  until replacement. Changes to shared background/zoom configuration require a
  shared bundle rebuild and backend reload.
- [image_service.py](../backend/services/image_service.py) coordinates canonical
  writes and database commits for User, MD and logos, using one operation lock,
  staged/fsynced/verified bytes, atomic publication, rollback backups and explicit
  cleanup warnings. Business services validate authorized saved entities and
  derive ownership/key; browser paths never choose destinations.
- [AwsumPathResolver](../backend/services/paths.py) generates canonical paths
  from owner/category/stable entity key. [AwsumStorageService](../backend/services/resource_storage.py)
  resolves actual DB ownership and physical destinations under the configured
  root. Store paths always retain the Merchant parent.

| Context | Canonical logical reference |
|---|---|
| Platform User | `/platform-admin/images/users/5.webp` |
| Platform MD Master | `/platform-admin/images/master/MD00001.webp` |
| Merchant Master | `/merchant/CLT11001/images/master/MD00001.webp` |
| Store Employee (framework-ready; screen not yet implemented) | `/merchant/CLT11001/store/CLT00001/images/employee/EMP00001.webp` |
| Owner logo | `{OWNER_ROOT}/images/logo/logo.webp` |

Physical examples:

```text
{AWSUM_STORAGE_ROOT}\platform-admin\images\users\5.webp
{AWSUM_STORAGE_ROOT}\platform-admin\images\master\MD00001.webp
```

DB columns `tb_platform_user.c_image_path` and `tb_md_master.c_image_path`
remain nullable logical references; no schema change is required. New record
Save obtains the numeric ID/issued code before assigning the image filename.
Never use an original upload filename or random permanent filename instead of
the entity identity. Logo singleton identity is `logo` within its owner root.

Account/item-only edits preserve legacy JPG/PNG reference and bytes. Replacement
removes only the previous verified owner/entity reference **after DB commit**.
Never glob-delete unknown variants. Unreferenced occupied canonical destinations
return 409. Shared legacy program logos are preserved, not deleted as owner data.
Failed saves restore prior reference/bytes; cleanup failures are logged and
returned as `image_warning`, without exposing physical paths. Forced process
termination may require administrator recovery of private staging/backup files.

The generic owner-scoped upload API requires a separate stable `entity_key`
query parameter (and optional master subcategory); the original filename only
undergoes security/content validation. It is create-only. User and Platform MD
images remain reserved to their authenticated parent Save APIs, which derive
keys server-side. Future Employee/tenant screens must validate real entities
and membership before using the common service; this framework is not a new
Employee CRUD/authorization implementation.

Use the common returned `image_url` with file `st_mtime_ns` cache version.
Canonical filenames stay stable; list/header refresh uses the new URL.
`master/users/employee` stay private/no-store. Defaults are shared built-in
`default-user.svg` / `no-image.svg`, never copied into runtime directories.
Reject path traversal, separators, drive/URL/absolute names, malformed images
and containment escapes. Backend validation is mandatory even after adjustment.

**BEFORE IMPLEMENTING ANY NEW IMAGE FEATURE: DO NOT create a new image
upload/storage implementation. Use the existing AWSUM Common Image Framework.
Only provide Owner, Category, Entity Key and, only when truly necessary,
image configuration.**

An existing image reference, a displayed image URL and a new upload's original
filename are different values. Account-only edits submit no image file and keep
the saved reference; never validate a saved URL as a new upload filename.
New upload names may contain spaces/non-ASCII text and case-insensitive supported
extensions, but must be leaf names: reject directories, drive/URL syntax and
traversal. Validate MIME/content/size independently. The backend generates the
canonical destination from owner/category/User ID; the original upload name
never determines its directory or permanent filename.

The single authoritative built-in Platform asset root (Central Server owned) is:

```text
Awsum_Platform\static\images\platform\
|-- logo\         logo_full.png, icon.png, ...
|-- background\   hero_platform.jpg, ...
|-- favicon\      favicon.ico, favicon.png (from logo\icon.png)
|-- defaults\     default-user.svg, no-image.svg
`-- ui\           landing/feature artwork
```

`BUILTIN_ROOT` in the
[built-in asset resolver](../backend/services/builtin_assets.py) is the only
definition of this physical root; the FastAPI `/images/platform` mount and
migration tools import it. Browser URLs stay clean (`/images/platform/...`) and
the filesystem location. Because root `static/` is also mounted at `/static`,
the same files are technically reachable at `/static/images/platform/...`; that
URL is not canonical and must not be used in templates or stored in the DB.
Templates use `builtin_asset_url()`, which appends an mtime `?v=` cache-busting
value; consumers must not guess machine paths. Built-in assets must not live in
`backend/static`, `frontend/` or any client application directory.

Favicon: `favicon_url()` in the same resolver returns the built-in
`/images/platform/favicon/favicon.png` unless a custom runtime Platform logo is
configured. `/favicon.ico` is a public route serving
`/images/platform/favicon/favicon.ico`.
Add new built-in categories only by extending the resolver's category list.

**Do NOT copy default images into every Agency/Merchant/Store.** If no custom
image exists, reuse the shared built-in Platform default or placeholder.
Current shared placeholders are `default-user.svg` and `no-image.svg`.
Logo inheritance remains Store -> Merchant -> applicable Agency -> Platform,
using the first available custom logo and ultimately the built-in Platform logo.
Platform login entry always retains Platform context, irrespective of prior
sessions. A missing required built-in asset is a deployment error, not a reason
to silently choose an arbitrary runtime file.

## 8. Registration and storage lifecycle

Initialize storage after successful DB registration/commit and final code
issuance, using saved owner relationships:

```text
Agency:
Awsum_Data/agency/{AGENCY_CODE}/images/
Awsum_Data/agency/{AGENCY_CODE}/data/

Merchant:
Awsum_Data/merchant/{MERCHANT_CODE}/images/
Awsum_Data/merchant/{MERCHANT_CODE}/data/
Awsum_Data/merchant/{MERCHANT_CODE}/store/

Store:
Awsum_Data/merchant/{MERCHANT_CODE}/store/{STORE_CODE}/images/
Awsum_Data/merchant/{MERCHANT_CODE}/store/{STORE_CODE}/data/
```

Startup and the repeatable storage repair command use the same service.
Subcategories are lazy-created. The technical HQ Store is not a business Store;
do not invent tenant storage for it.

* Failed registration must not create an owner tree for an uncommitted record.
* If post-commit storage initialization fails, retain the business record,
  surface the storage warning and repair; do not register a duplicate owner.
* Do not automatically delete owner storage on deactivation/deletion.
* Entity display-name changes must not rename storage paths.
* Store reassignment is not an ordinary directory rename. Owned files/references
  require a separately reviewed transfer that preserves ownership and access.
* Retention/physical deletion requires a separate explicit administrative decision.

## 9. Containment and access safety

Validate owner, category, filename, extension and resolved containment beneath
`AWSUM_STORAGE_ROOT`. Reject traversal, encoded separators, invalid/reserved
names, arbitrary absolute destinations and symlink escapes. Never accept a
frontend-provided physical path as a destination.

The entire runtime root must never be publicly mounted. Private `data/`,
security/audit records, SQLite and backups are not static files. Current
`master`, `users` and `employee` images require authorized active sessions and
private cache behavior. `logo`, `background` and `common` images are public
presentation categories; do not store confidential content in them.
Image content/size validation and existing role/membership/session guards remain
mandatory. Approximate location/branding must not grant access.

Use the existing staged/transactional image workflow for managed images.
Reject collisions rather than overwrite another owner's file. Keep recoverable
mapping/backup information for filesystem migrations.

## 10. Legacy migration, portability and recovery

Never blindly rename legacy folders/codes. Inventory actual files and DB records,
resolve Merchant/Store relationships, preflight collisions and missing references,
back up the database/files, then apply an explicit reviewed mapping.
Preserve unknown-owner files for review rather than inventing ownership.
Do not reset databases or change numeric IDs/FKs to make paths match.

Verify file sizes/hashes, SQLite integrity where applicable, PostgreSQL logical
restore, business references and UI/API behavior before deleting any old copy.
Never edit PostgreSQL cluster files. Keep recovery archives private.

Storage must remain portable to another Windows computer, drive, Linux, server
deployment and future object/cloud storage without rewriting business references.
Use platform-aware path handling in the adapter and `/` logical references.
Local environment, the layout-v2 cutover marker and offline migration records
may contain machine-specific physical paths; relocation must reconcile these
deliberately, not rewrite historical manifests or bypass startup validation.
See the [installation guide](AWSUM_INSTALLATION_GUIDE.md) for this procedure.

## Mandatory developer and coding-agent rule

Before making changes related to storage, filesystem, images, uploads, Agency
paths, Merchant paths, Store paths, installation, migration, or backup/restore,
read these documents first:

* [docs/AWSUM_PROJECT_ARCHITECTURE.md](AWSUM_PROJECT_ARCHITECTURE.md)
* [docs/AWSUM_STORAGE_STANDARD.md](AWSUM_STORAGE_STANDARD.md)
* [docs/AWSUM_INSTALLATION_GUIDE.md](AWSUM_INSTALLATION_GUIDE.md)

Do not introduce a conflicting architecture without explicit approval.
