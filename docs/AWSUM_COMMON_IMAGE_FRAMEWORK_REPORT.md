# AWSUM Common Image Framework - Implementation Report

Date: 2026-10-29

The [storage standard](AWSUM_STORAGE_STANDARD.md#awsum-common-image-standard)
is authoritative. This report describes the implementation and verification,
not a separate architecture.

## 1. Existing implementation and refactoring

The application already shared `ImageUploader`, image validation/path helpers
and compensating image transactions. However:

- User selection enabled a profile-only cropper and User-only WEBP processor.
- MD used the same uploader without adjustment and persisted PNG.
- Company and organization logo controls bypassed the adjuster.
- Logo writes used separate persistence/cleanup code and generated unique names.
- Output dimensions and encoding settings were independently defined in browser
  and backend code.

No separate User/Master/Employee uploader was added. The profile-mode branch and
User-only processor were removed. The existing cropper now always opens as
**Adjust Image**. The former `backend/services/master_images.py` implementation
was moved/generalized to [image_service.py](../backend/services/image_service.py).
Independent logo-save/rollback helpers were removed in favor of that service.
Private `.master-image.lock` / `.master-image-*.tmp` names remain compatible;
they do not imply a separate MD implementation.

## 2. One common processing path

```text
Select/Replace
  -> ImageUploader / existing input adapter
  -> ImageCropper: Adjust Image
  -> move / zoom / reset / square crop
  -> imageProcessor.processImage
  -> temporary File / local preview
  -> parent Save
  -> images.read_image_upload
  -> image_service.commit_entity_image
  -> images.process_image
  -> AwsumPathResolver
  -> AwsumStorageService / validated local storage
  -> Awsum_Data
```

**USER and MD MASTER now pass through the SAME common image framework.**
The automated integration test records two calls to the same backend processor
with identical source bytes and verifies identical persisted WEBP bytes, with
only the canonical owner/category/entity path differing.

### Frontend

- [ImageUploader](../shared/image-components/src/lib/ImageUploader.svelte): shared selection,
  replacement, local preview, removal and parent-form multipart state.
- [ImageCropper](../shared/image-components/src/lib/ImageCropper.svelte): the common native
  dialog/canvas adjuster, centered-contain initial/Reset scale, bounded Pointer Events dragging,
  keyboard movement, zoom, Reset, Cancel/Escape and Apply.
- [imageProcessor](../shared/image-components/src/lib/imageProcessor.ts): shared validation and
  canvas encoding, with no owner/path construction.
- [images.ts](../shared/image-components/src/images.ts): public uploader API and
  `adjustImage` / `bindImageInput` adapter for existing logo controls.

### Configuration and backend

[config/image.json](../config/image.json) is read by both the browser build and
[backend configuration loader](../backend/core/image_config.py):

| Setting | Current value |
|---|---|
| Output | 512 x 512, square 1:1 |
| Encoding | WEBP, quality 0.88 |
| Input | JPG/JPEG, PNG, WEBP |
| Maximum input | 5 MiB, 20 megapixels |
| Background | Configurable, currently opaque white (`#ffffff`) |
| Zoom | Minimum 0.25 x contain; maximum 4 x cover scale |

Zoom behavior was subsequently updated in the same common component: complete
portrait/landscape products fit at Reset without stretching, users can zoom out
with white margins or zoom in to cover/crop, and movement no longer enforces
cover. The shared backend uses contain with the same background for direct
uploads and preserves browser-adjusted square layouts. Existing saved images,
Save/Cancel semantics and canonical paths remain unchanged.

Zoom-update verification: 191 integrated tests passed (including isolated
PostgreSQL and Chromium), shared component check reported 0 errors/0 warnings,
and both builds passed. Added checks cover portrait/landscape/square/User-like
photos, extreme aspect ratio, JPG/PNG/WEBP, transparent PNG, zoom-out padding,
repositioning, off-canvas bounds, centered Reset, 512-square WEBP, configured
backend background and aspect preservation. The original implementation
verification below is retained as historical evidence.

[images.py](../backend/services/images.py) validates original filenames,
raw multipart filename headers, extension/MIME/content, input limits and EXIF
orientation, then uses one square WEBP processor for all categories.
The [image service](../backend/services/image_service.py) handles User, MD and
logo transactions. The same service accepts an authorized future Employee or
other category context; no Employee-specific processor was introduced.

## 3. Paths and database behavior

| Entity | Logical DB reference |
|---|---|
| User 5 | `/platform-admin/images/users/5.webp` |
| MD00001 | `/platform-admin/images/master/MD00001.webp` |
| Store Employee | `/merchant/CLT11001/store/CLT00001/images/employee/EMP00001.webp` |
| Owner singleton logo | `{OWNER_ROOT}/images/logo/logo.webp` |

Development physical examples:

```text
D:\01_Development\Awsum_Projects\Awsum_Data\platform-admin\images\users\5.webp
D:\01_Development\Awsum_Projects\Awsum_Data\platform-admin\images\master\MD00001.webp
```

[AwsumPathResolver](../backend/services/paths.py) generates canonical logical
references from stable keys. [AwsumStorageService](../backend/services/resource_storage.py)
resolves actual owner relationships and physical image files under
`AWSUM_STORAGE_ROOT`. The browser never supplies a physical destination.

Existing nullable `c_image_path` columns remain unchanged. New entity Save
obtains its numeric ID/issued code before assigning the image reference.
Account/item-only edits preserve the existing reference and bytes, including
legacy JPG/PNG. No schema change or bulk image conversion was performed.

The generic owner-scoped create-only upload API now requires a separate stable
`entity_key` query parameter; the original upload name is not a storage key.
Current User and Platform MD writes remain reserved to their authenticated
parent Save APIs, where the backend derives the entity key.
Future Employee CRUD must still validate its actual saved entity and membership;
this work does not introduce an Employee screen or grant new access.

## 4. Apply, Cancel, Remove and transactional safety

- Apply returns a temporary WEBP File to the form; it performs no network write.
- Cancel/Escape in the adjuster preserves the previously saved/pending image.
- Canceling User/MD Edit discards pending replacement or removal.
- Remove clears the reference and removes the runtime custom image only after
  successful parent Save. The shared built-in placeholder is not copied.
- Existing organization logo Upload/Reset buttons remain explicit logo-save
  actions. Selection/Apply alone does not save. Company logo persists on Company
  Save. Existing logo inputs use the common adjuster and local preview.
- Writes use an owner/category operation lock, staged/fsynced/verified bytes,
  atomic publication and compensating filesystem rollback around SQL commit.
- The previous verified referenced file is removed only after commit succeeds.
  Unknown variants are not glob-deleted. Occupied unreferenced canonical targets
  return 409 rather than being overwritten.
- Legacy shared program logos are preserved during owner-specific replacement.
- Cleanup failures are logged and surfaced as `image_warning`. Forced process
  termination may still require administrator reconciliation of private recovery
  files; this is not a distributed filesystem/database transaction.

## 5. Original simple-filename error

The earlier reproduced failure applied the canonical-storage filename regex to
original upload names, rejecting names with uppercase extensions/spaces.
Actual account-only requests did not submit the displayed image URL.

Original upload names and existing logical image references remain separate.
Strict leaf-name/raw-header validation still rejects directories, traversal,
drive/URL syntax and unsafe characters. Valid original names may contain
spaces/non-ASCII text and case-insensitive supported extensions.
Unchanged edits submit no image file. New adjusted uploads submit `image.webp`;
only the backend determines the permanent entity filename and directory.

## 6. Cache and defaults

The existing common `image_url` function appends file `st_mtime_ns` as `?v=...`.
Canonical names stay stable. User/MD list refresh and current-user avatar update
use returned versioned URLs. Private image responses remain `private, no-store`.

Built-in defaults stay under `static/images/platform/defaults`.
No runtime default copies were created. Existing built-in assets were not
re-encoded or moved.

## 7. Verification

Final regression command from the program root:

```powershell
$env:AWSUM_TEST_POSTGRES_SESSIONS = "1"
py -W ignore -m unittest tools.test_user_images tools.test_master_images tools.test_images tools.test_image_components tools.test_system_settings tools.test_branding tools.test_final_storage tools.test_license_email tools.test_global_paths tools.test_storage_initialization tools.test_master_group
```

Result: **188 tests passed**, including isolated PostgreSQL concurrency,
transaction recovery and real Chromium component tests.

Frontend (now run from `shared/image-components`):

```powershell
npm.cmd run check
npm.cmd run build
```

Result: **0 errors / 0 warnings**, both builds succeeded.

Coverage includes:

- Same User/MD processor calls and identical normalized bytes.
- Every image category using the common transaction service/processor and
  centralized dimensions; owner hierarchy and containment.
- JPG/JPEG/PNG/WEBP, EXIF orientation, oversized inputs, malicious filenames,
  raw Windows multipart paths, invalid decoded content and canonical collisions.
- Portrait/landscape/high-resolution crop, zoom, mouse/touch-pointer algorithms,
  Reset, output pixels matching the adjusted frame, 512 x 512 WEBP.
- Apply without upload, canceling a second crop, parent-form Cancel, deferred
  Remove, default fallback, blob URL cleanup and logo-input adapter behavior.
- Legacy MD/User JPG/PNG preservation on unchanged edits and removal only after
  successful replacement commit; rollback and cleanup-warning behavior.
- Existing roles, authorization, origin/session checks, branding, paths, storage,
  exports and Master Group regression tests.

### Real application browser checks

An isolated in-memory database and temporary storage root were used, with legacy
PNG fixtures:

| Check | Observed result |
|---|---|
| MD name-only Save | HTTP 200, no image file, existing `.png` reference retained |
| MD crop Apply then Cancel Edit | Local 512 x 512 preview; no image write; `.png` retained |
| MD replacement Save | HTTP 200; `image.webp` submitted; canonical `MD00001.webp` returned |
| Add MD with adjusted image | HTTP 201; issued code `MD00002`; canonical WEBP saved |
| MD refresh / Remove Cancel / Remove Save | WEBP persisted after refresh/Cancel, then reference cleared on Save |
| User name-only Save | Succeeded with legacy PNG |
| User adjustment/replacement | Same Adjust Image; zero writes at Apply; Save HTTP 200 and canonical `4.webp` |
| User role/status-only Save | HTTP 200, no image file, existing WEBP retained |
| Existing Merchant logo control | Same Adjust Image, pending `image.webp`, local blob preview |

Temporary physical files were opened with Pillow and verified as WEBP,
512 x 512; obsolete referenced fixture PNGs were absent after successful Save.
Real PostgreSQL business image references were inspected read-only: the observed
User 4/5 references were WEBP and MD 1 remained PNG with a valid file. No actual
business images were bulk-converted/deleted by this task.

The fixture server and exact temporary storage were cleaned up. The shared
development server was left running and checked separately.
Physical mobile-device touch testing remains an additional acceptance check;
simulated Pointer Events are not a claim of testing a physical device.

## 8. Changed files

Configuration and common implementation:

- [config/image.json](../config/image.json)
- [backend/core/image_config.py](../backend/core/image_config.py)
- [shared/image-components/src/lib/imageProcessor.ts](../shared/image-components/src/lib/imageProcessor.ts)
- [shared/image-components/src/lib/ImageCropper.svelte](../shared/image-components/src/lib/ImageCropper.svelte)
- [shared/image-components/src/lib/ImageUploader.svelte](../shared/image-components/src/lib/ImageUploader.svelte)
- [shared/image-components/src/images.ts](../shared/image-components/src/images.ts)
- [backend/services/image_service.py](../backend/services/image_service.py)
  (replaces the former `master_images.py` implementation)
- [backend/services/images.py](../backend/services/images.py)
- [backend/services/paths.py](../backend/services/paths.py)
- [backend/services/resource_storage.py](../backend/services/resource_storage.py)
- [backend/services/master_group.py](../backend/services/master_group.py)

API/screen integration and served bundle:

- [backend/routers/platform_store.py](../backend/routers/platform_store.py)
- [backend/routers/system_settings.py](../backend/routers/system_settings.py)
- [backend/routers/images.py](../backend/routers/images.py)
- [static/js/system_settings.js](../static/js/system_settings.js)
- [templates/platform/master_management.html](../templates/platform/master_management.html)
- [templates/platform/users.html](../templates/platform/users.html)
- [templates/platform/platform_base.html](../templates/platform/platform_base.html)
- [templates/platform/system_settings.html](../templates/platform/system_settings.html)
- [static/components/image-uploader/image-uploader.js](../static/components/image-uploader/image-uploader.js)
- [static/components/image-uploader/image-uploader.css](../static/components/image-uploader/image-uploader.css)

Tests and documentation:

- [tools/test_user_images.py](../tools/test_user_images.py)
- [tools/test_master_images.py](../tools/test_master_images.py)
- [tools/test_images.py](../tools/test_images.py)
- [tools/test_image_components.py](../tools/test_image_components.py)
- [tools/test_system_settings.py](../tools/test_system_settings.py)
- [tools/test_global_paths.py](../tools/test_global_paths.py)
- [docs/AWSUM_STORAGE_STANDARD.md](AWSUM_STORAGE_STANDARD.md)
- [docs/AWSUM_INSTALLATION_GUIDE.md](AWSUM_INSTALLATION_GUIDE.md)
- [docs/AWSUM_GLOBAL_PATH_STANDARD.md](AWSUM_GLOBAL_PATH_STANDARD.md)
- [readme.md](../readme.md)
- This report.
