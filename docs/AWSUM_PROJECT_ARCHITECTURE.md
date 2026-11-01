# AWSUM Project Architecture

**THIS DOCUMENT IS THE AUTHORITATIVE AWSUM PROJECT (FOLDER) ARCHITECTURE STANDARD.**

It defines what each top-level folder of `Awsum_Platform` owns. Storage/runtime
rules are in [AWSUM_STORAGE_STANDARD.md](AWSUM_STORAGE_STANDARD.md); machine
setup and relocation are in [AWSUM_INSTALLATION_GUIDE.md](AWSUM_INSTALLATION_GUIDE.md).
If a historical document disagrees, this document wins for folder roles.

## 1. Core rule

```text
backend    = Central Server logic (FastAPI, services, models, resolvers)
templates  = Central Server HTML (Jinja)
static     = Central Server web assets + built-in Platform images
shared     = backend-owned shared component source (built into static)
frontend   = client applications ONLY (POS / KIOSK / Customer Ordering)
Awsum_Data = runtime / business data / uploads (outside Awsum_Platform)
```

## 2. Top-level layout

```text
Awsum_Projects\
|-- Awsum_Platform\                 PROGRAM (Git repository)
|   |-- backend\                    FastAPI app: main.py, routers, services, models,
|   |                               database, config, core, schemas, utils
|   |-- templates\                  Jinja HTML: platform\, client\, store\, shared\
|   |-- static\                     mounted at /static
|   |   |-- css\  js\  bootstrap\   server CSS/JS and offline Bootstrap
|   |   |-- components\             compiled bundles (image-uploader\)
|   |   `-- images\platform\        BUILT-IN Platform assets, served at /images/platform
|   |       |-- logo\               logo_full.png, icon.png, ...
|   |       |-- background\         hero_platform.jpg, tuxedo_banner.jpg
|   |       |-- favicon\            favicon.ico, favicon.png
|   |       |-- defaults\           default-user.svg, no-image.svg
|   |       `-- ui\                 landing/feature artwork
|   |-- shared\
|   |   `-- image-components\       Svelte/Vite source of the common image framework
|   |-- frontend\                   client apps only (currently README only)
|   |-- config\                     shared configuration (image.json)
|   |-- tools\                      tests (test_*.py), migration and admin scripts
|   |-- docs\                       standards, guides, reports
|   |-- AGENTS.md                   coding-agent rules
|   `-- requirements.txt, readme.md, PROJECT_STATUS.md, run/stop .bat scripts
`-- Awsum_Data\                     RUNTIME (AWSUM_STORAGE_ROOT)
    |-- platform-admin\
    |-- agency\{AGENCY_CODE}\
    `-- merchant\{MERCHANT_CODE}\store\{STORE_CODE}\
```

Local-only, never committed: `.env`, `.venv\`, `node_modules\`, `__pycache__\`,
logs, `db_backups\`.

## 3. Folder rules

### backend/
- Server logic only. **No web asset folder: `backend/static` must not exist.**
- Physical paths are produced only by `AwsumPathResolver` / `AwsumStorageService`
  (runtime) and `backend/services/builtin_assets.py` (built-in assets).

### templates/
- All server-rendered HTML, including `/` and `/platform-admin`
  (`templates/platform/platform_home.html`). Templates stay here.
- Built-in images are referenced only through `builtin_asset_url()` /
  `brand_*` context values, never hard-coded physical paths.

### static/
- Server CSS, JS, Bootstrap, compiled component bundles, and built-in images.
- `static/images/platform/` is the single built-in root (`BUILTIN_ROOT`).
  Canonical URLs are `/images/platform/<category>/<file>`; the incidental
  `/static/images/platform/...` alias must not be used or stored.
- Favicon: `/images/platform/favicon/favicon.png` (tab icon via
  `favicon_url()`), `/images/platform/favicon/favicon.ico`, and the public
  `/favicon.ico` route. A configured custom runtime Platform logo overrides the
  tab icon.
- Runtime/tenant uploads never go into `static/`.
- `static/components/image-uploader/` is **build output** of
  `shared/image-components`; do not edit it by hand.

### shared/
- `shared/image-components/`: ImageUploader, ImageCropper (Image Adjuster),
  Avatar, imageProcessor, public `AwsumImages` API. Reads `config/image.json`.
- Build: `npm.cmd ci`, `npm.cmd run check`, `npm.cmd run build` from that folder.
- Owned by the Central Server; every screen reuses it (no per-screen copies).

### frontend/
- Name stays `frontend`. Reserved for client applications only:
  `frontend/pos/`, `frontend/kiosk/`, `frontend/customer-ordering/`.
- Never contains Central Server pages, built-in Platform images, or the common
  image component source. Each client app owns its own package, build output and
  `node_modules`. Do not create empty placeholder apps.

### config/
- Shared, non-secret configuration read by backend and builds (`image.json`).
  Secrets stay in `.env` / a secret manager.

### tools/
- `test_*.py` unit/browser tests (`py -m unittest tools.<module>`), migration
  scripts (`migrate_*.py`), maintenance scripts, and `legacy_tools/`.
- Future option (not yet approved): split into `tests/` and `scripts/`.

### docs/
- Authoritative standards: this document, `AWSUM_STORAGE_STANDARD.md`,
  `AWSUM_INSTALLATION_GUIDE.md`, `AWSUM_MONEY_INPUT_STANDARD.md`,
  `AWSUM_GLOBAL_PATH_STANDARD.md`, `AWSUM_STORE_SERVER_ARCHITECTURE.md`; plus historical reports.

### Removed / not allowed
- `backend/static/` — removed; built-ins live in root `static/images/platform/`.
- `frontend/public/images`, `frontend/dist`, the Vite starter app — removed.
- `db_admin/`, `backend/db_backups/` — empty, unreferenced; removed.

### Store Runtime Architecture
- Each Store runs a **Primary Store Server** (Local Store DB + Sync Engine) that
  serves POS / KIOSK / SubStation devices over the Store LAN and keeps the Store
  operating while Internet or the Central Server is unavailable.
- Central Server = Platform-wide authority; Primary Store Server = Store-level
  local operational authority. Not yet implemented (design standard).
- Authoritative design: [AWSUM_STORE_SERVER_ARCHITECTURE.md](AWSUM_STORE_SERVER_ARCHITECTURE.md).

## 4. Mandatory developer and coding-agent rule

Before making changes related to paths, storage, filesystem, images, uploads,
Agency/Merchant/Store paths, project folders, `frontend`/`static`/`templates`/
`shared` placement, installation, migration, or backup/restore, read:

* [docs/AWSUM_PROJECT_ARCHITECTURE.md](AWSUM_PROJECT_ARCHITECTURE.md)
* [docs/AWSUM_STORAGE_STANDARD.md](AWSUM_STORAGE_STANDARD.md)
* [docs/AWSUM_INSTALLATION_GUIDE.md](AWSUM_INSTALLATION_GUIDE.md)

first. Do not introduce a conflicting architecture without explicit approval.
