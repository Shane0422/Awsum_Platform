# Awsum Platform

Multi-store SaaS platform

Backend
- FastAPI
- SQLAlchemy

Frontend
- Svelte
- Active Platform administration uses Jinja forms with a shared embedded
  Svelte/TypeScript image uploader and avatar component. After component changes,
  run `npm.cmd run check` and `npm.cmd run build` in `shared/image-components`.
  `frontend/` is reserved for POS/KIOSK/Customer Ordering client apps.
  Project folder roles: [AWSUM_PROJECT_ARCHITECTURE.md](docs/AWSUM_PROJECT_ARCHITECTURE.md).
  Deploy the generated `static/components/image-uploader` assets with the backend.
  MD photos keep their MD-code filenames; User photos use immutable numeric IDs.
  See [the image/storage standard](docs/AWSUM_GLOBAL_PATH_STANDARD.md#user-photos-and-shared-form-components).

Platform navigation
- The shared Platform shell uses horizontal Dashboard, Master Group, Business,
  Device & License, and Reports navigation instead of a left Sidebar.
- Master Group also contains the existing Billing, Security, and permission-gated
  System sections. Existing menu routes and disabled Coming soon items are retained.
- Mega Menus highlight the current page and fit the viewport; Escape or clicking
  outside closes them. Reports has no new route and remains Coming soon.
- Focused checks: `.venv\Scripts\python.exe -m unittest tools.test_platform_navigation`.
- Master Group lists share `static/css/awsum-list.css` through the existing
  `master_group.html` template. Scope new list styles under `.awsum-list` rather
  than duplicating them per entity; list behavior and edit-modal styles are unchanged.
- Only `.mg-page > .awsum-page-header` is sticky beneath the Common Navigation,
  using its observed height (`--platform-header-height`) at every viewport size.
  Icon, title, description and actions stick together; messages, filters and tables
  scroll normally. Other management page headers are unchanged.
- Common organization selectors use Agency / Merchant / Store order without adding
  fields absent from a screen. Store List's Agency remains the installation Agency:
  its Merchant filter includes Merchants with Stores installed by that Agency.
  Common License/Subscription Store options follow the selected Merchant; License
  Merchant options follow its Agency. Installation Agency is not Merchant ownership.
- List UI checks: `.venv\Scripts\python.exe -m unittest tools.test_master_group_ui`.
- The same stylesheet is attached by `platform_base.html` for existing management,
  Users, Sessions, Store Management, Merchants Tree, and System Settings pages.
  `.awsum-admin` maps legacy cards, tables, controls, and tabs to the common palette;
  page-specific layout, visibility, responsive scrolling, and validation remain intact.
  Dashboard and authentication pages do not opt in.
- Admin UI checks: `.venv\Scripts\python.exe -m unittest tools.test_platform_admin_ui`.
- Management lists use shared `page_header` and `surface` macros from
  `templates/platform/components/management_layout.html`: page-level actions,
  a separate filter card, then the table card. Master Group uses the same components.
  Store keeps its device panel and splitter below its filters. System Settings uses
  the shared header and tab card followed by its existing settings forms.
- Platform, Agency, Merchant and Store use `platform_base.html` and
  `awsum_header.html` for the same App shell and Top Navigation. Merchant
  dashboards retain their existing content but no longer own a Sidebar/Topbar.
  `app_navigation.py` resolves the authenticated session scope and the shared
  menu/API permission policy. Existing Platform/Agency navigation is unchanged.
- Merchant Admin and Store Admin (`Admin`, `CustomerAdmin`, `StoreAdmin`)
  may CRUD/export Tax and Category I/II/III via the existing Common Master Group.
  Other Store roles have no Master menu or API access. Merchant and Store routes
  render the same template and use the same JavaScript and service.
  Store uses its saved Merchant relationship, never a requested Merchant ID.
  Business/Reports dropdowns remain Coming soon; no new business/report features
  are introduced.
- `migrate_master_scopes` adds nullable Merchant FKs to the four existing master
  tables. NULL preserves existing Platform data; new tenant rows are isolated
  per Merchant, including parent selection, lookup options and exports.
  Store shares its Merchant's records. Existing codes/rows are not rewritten,
  and existing global code uniqueness/code generation remains unchanged.
  The migration runs during normal schema initialization.
- Focused checks: `.venv\Scripts\python.exe -m unittest
  tools.test_common_app_navigation tools.test_platform_navigation
  tools.test_master_group_ui.MasterGroupListTemplateTests`.

Database
- AdminDB
- StoreDB

## Permanent monetary input standard

All current and future monetary fields must use `AwsumMoneyInput` and the common
money formatter, with `#,##0.00` presentation and comma-free numeric/decimal
DB/API values. Percentages, quantities, IDs, codes and display order are not
money. Read [AWSUM MONEY INPUT STANDARD](docs/AWSUM_MONEY_INPUT_STANDARD.md)
before adding or changing monetary inputs or presentation.

Platform Admin Policy (fixed)
- Store ID: Admin (fixed, do not change)
- Email: is2ceo@gmail.com (can be changed internally)
- Password: Awsum123! (can be changed internally)
- Login/auth flows must prioritize Store ID=Admin for Platform Admin routing

- PS D:\01_Development\Awsum_Projects> cd Awsum_Platform\backend
- PS D:\01_Development\Awsum_Projects\Awsum_Platform\backend> py -m uvicorn main:app --reload --no-proxy-headers

## New registration business codes

Agency, Merchant, and Store creation use
`backend/services/business_code.py::generate_business_code`:
`AGT` / `MER` / `STR` + registration year `YY` + four random characters.
The alphabet is `ABCDEFGHJKLMNPQRSTUVWXYZ23456789` (no `0`, `O`, `1`, `I`).
Existing codes in the corresponding entity table are checked and collisions
regenerated. PostgreSQL transaction advisory locks serialize same-entity
generation; DB unique constraints remain.
Store sequence allocation and all numeric PK/FK rules are unchanged.
Existing codes are not rewritten; storage, images, auth and licenses still use
the saved codes and relationships. No migration is required for this generator.
Focused checks: `py -m unittest tools.test_business_codes.BusinessCodeTests`.

## Common organization registration email

Agency, Merchant and Store reuse `build_registration_email`, `AwsumEmailLayout`,
`AwsumEmailPreview.open` and the configured SMTP sender. Merchant/Store expose
Send Registration Email in Contacts; the existing Agency action stays in place.
Common endpoints are `/platform/registration-email/{owner_type}/{owner_id}`
and its `/preview` suffix; the existing Agency endpoints delegate to them.
Preview and resend never create users or reset passwords. To Email overrides
apply only to the email, not to the Primary Contact or login identity.

Active saves create the first scoped Admin from the saved Primary Contact.
For new owners, save Inactive, add a Primary Contact, then activate. Initial
passwords use phone digits (`Awsum@` plus the last four) or a random fallback;
only hashes are stored and password change remains optional.
Existing users and passwords are never regenerated.

`migrate_registration_users` extends `tb_platform_user` with nullable
`i_merchant_id`, scoped email uniqueness and Merchant/Store Admin roles.
It runs during normal schema initialization without altering existing
credentials. All three owners use the existing `/login/{type}/{code}` page,
`/auth/login`, server sessions and Change Password flow. Platform administration
remains unavailable to organization users. Store operational DB authentication
is not implemented by this registration feature.

Focused checks: `.venv\Scripts\python.exe -m unittest tools.test_registration_email
tools.test_agency_activation tools.test_email_layout`.

## Merchant model and API naming

Merchant business entities use `backend/models_admin/merchant.py::Merchant`,
`tb_merchant`, `i_merchant_id`, `c_merchant_code`, and `c_merchant_name`.
The Merchant FK column is `i_merchant_id` in Store, Billing, Contract, Invoice,
License, Payment Method, Session, and Subscription. Existing numeric IDs,
business codes, row values, FK relationships, and image/data paths are preserved.

Use `GET /platform/merchants`, `POST /platform/merchant`, and
`PUT/DELETE /platform/merchant/{merchant_id}` with `merchant_name`,
`merchant_code`, and `merchant_id` JSON fields. Image Add/Edit uses the same
Merchant save implementation via `/with-image` and the existing Common image
service. Linked Stores use `/platform/merchant/{merchant_id}/stores`;
the tree is `/platform/merchants-tree`. There are no former business-model or
Merchant route compatibility aliases. The System Merchant is seeded only into
an empty Merchant table, so an existing system record is never duplicated.

Existing installations must complete the reviewed PostgreSQL table/column and
dependent identifier renames before restarting this code; startup does not
silently fall back to the old schema. Do not rewrite existing names/codes or
move uploaded files as part of this naming change. Historical migration input
paths remain historical recovery formats, not current Merchant API contracts.
HTTP/browser/network client metadata, Customer pages, banking/accounting and
User authentication accounts are separate concepts and retain their names.

Focused checks: `py -m unittest tools.test_merchant tools.test_business_codes
tools.test_store_logo tools.test_branding tools.test_agency_portal`.

## Historical business code separator migration

Legacy Merchant, Store, and Agency codes include `CLT11001`,
`CLT00001`/`STR00001`, and `AGT00001`. The historical separator conversion
retains their prefixes and digit widths; it is separate from new registration.
Store
database files use `AWSUM_STORAGE_ROOT`, then saved System Settings, then
the sibling `Awsum_Data` directory. Tenant database names generated from
numeric Store IDs also omit the separator (for example, `CUST00001`).

Only when explicitly performing the historical separator migration, preview
the conversion and check for collisions:

```powershell
py tools\migrate_business_codes.py
```

After reviewing the preview and backing up the platform database and data
directories, apply the conversion:

```powershell
py tools\migrate_business_codes.py --apply
```

The command writes a JSON code/path mapping under
the effective storage root's private
`platform-admin/data/audit/code-migrations` directory.
Keep that file to restore the prior codes and paths if needed:

```powershell
py tools\migrate_business_codes.py --rollback "C:\path\to\business-code-underscores-YYYYMMDDTHHMMSSZ.json"
```

The migration preserves numeric database IDs, foreign keys, passwords, and
permissions. It checks code and path collisions before changing anything and
does not initialize or reset the database.

## Sending license information by email

The Store page's Devices & Licenses panel can email selected licenses to an
Agency contact or another address entered for that send. Configure SMTP on the
server process before using Send:

- `AWSUM_SMTP_HOST` (defaults to `smtp.gmail.com`)
- `AWSUM_SMTP_PORT` (defaults to `465`)
- `AWSUM_SMTP_FROM_EMAIL` (defaults to `info@awsumsolution.com`)
- `AWSUM_SMTP_USERNAME` (defaults to `info@awsumsolution.com`) and
  `AWSUM_SMTP_PASSWORD` (required when authentication is enabled)
- `AWSUM_SMTP_SENDER_NAME`, `AWSUM_SMTP_REPLY_TO_EMAIL`, and
  `AWSUM_SMTP_AUTHENTICATION` (optional environment-managed email fields)
- `AWSUM_SMTP_USE_SSL` (defaults to `true`)
- `AWSUM_SMTP_STARTTLS` (defaults to `false` with SSL)
- `AWSUM_SMTP_TIMEOUT` (seconds, defaults to `20`)

Keep SMTP credentials in the server environment or secret manager, never in
source code or browser configuration. The server validates each selected
license against the chosen Store and records the sender, recipient, Store,
license IDs, outcome, and SMTP Message-ID in `tb_license_email_log`. A
successful result means the SMTP server accepted the message; it does not
confirm delivery to the recipient. License keys are not written to the audit
log.

## Agency activation and registration

Register a new Agency as **Inactive**, add its active **Primary Contact** in the
existing Contacts component, then save it as **Active**. Activation requires a
Primary Contact with a name and valid email. It creates the first Agency Admin
in `tb_platform_user`, with the contact's name/email/phone, `i_agency_id`, Active
status and optional password change. Saving again never duplicates
the user, changes its login email, or resets its password. Agency login identity
is Agency scope + Agency ID + normalized Login Email; the same email may belong
to another Agency or a Platform administrator. Scope-specific UNIQUE indexes
prevent duplicates within one Agency and retain Store/email uniqueness for
non-Agency users. Migration creates those indexes before replacing legacy
cross-scope email constraints; it never changes or deletes existing users.
Startup adds the nullable Agency FK/index and seeds the **Agency Admin** Role;
existing Platform accounts remain unchanged.

The initial password is `Awsum@` plus the last four digits extracted from the
Primary Contact Phone (for example, `703-855-5858` becomes `Awsum@5858`).
Only fewer than four digits triggers the existing random-password fallback.
Passwords are hashed, never stored in plaintext or logged. Later Contact Phone
changes do not update the User phone snapshot or password.

Agency Save never sends email or opens Preview. When a saved Agency has an
Agency Admin, its Edit footer **Registration Email** button opens the existing License
Email Preview/Send component independently; only **Send Email** sends it. Its
editable **To Email** defaults to Primary Contact Email and affects only that
send, never the contact or login email. Preview and Send use the same branded
email builder and configured SMTP service. Failures do not undo activation.
The same button can resend later. A phone-derived initial password is included
only while it still matches the stored hash and has not been changed. The
random fallback is held only in this page's memory after creation for the first
email; refreshing or successfully sending clears it. When it is unavailable,
email includes existing-password/login instructions instead; no password is reset.
Save alerts only show `Agency Admin User created.`, never the password.

### Common email layout

`backend/services/email_layout.py` provides `AwsumEmailLayout`, extracted from
the existing License email design: Platform branding/logo, 600px content card,
background, typography, sections, information rows/boxes, key cards, spacing,
Sign In button and footer. `license_email.py` supplies only License content;
`agency_email.py` supplies Agency/Account information and optional initial
password, using the same class. No License layout is copied into Agency email.
Agency's title is **Welcome to AWSUM Platform**, context **Agency Registration**;
resends without an initial password omit the password row.

Preview and MIME send share `AwsumEmailLayout.render`. Browser previews replace
embedded CID logo references with data URLs from the same MIME image bytes;
no separate preview layout exists. Future registration/notification emails can
pass their own content to this small class. SMTP settings and the existing
System Settings test-email compatibility helper remain unchanged.
Rendering-only validation: `py -m unittest tools.test_email_layout`.

Agency users sign in from `/` with Agency Code, Login Email and password, or
from `/login/agency/{agency_code}`, through the existing `/auth/login` and web
session service. New Agency users are not forced to change their password and
may continue using it. The existing Change Password page is available from the
Agency Portal Settings menu for voluntary changes. Previously existing
users, passwords and Must Change Password flags are left unchanged by Save.
Agency sign-in opens `/agency/dashboard`; the old registration-completed URL
redirects there. Agency accounts cannot sign in as
Platform administrators. Inactive Agencies cannot sign in or keep using a session.

### Agency Portal

Platform Admin dashboard authorization reuses `get_platform_admin_session`, the
same check as the existing Platform middleware/login, rather than requiring a
fixed Role ID. `/platform/admin` is a protected compatibility redirect to
`/platform/dashboard`; Agency sessions are forbidden on both Platform URLs.

Menus: Dashboard, Merchants, Stores, Users, Reports, Settings.
Agency UI reuses the Platform shell, common management layout/forms and list CSS.
`awsum_header.html` renders the same logo (scale 1.5), header, navigation items,
active state and profile area for Platform Admin and Agency. Only menu data and
dashboard/settings targets differ; existing Platform permission-gated Mega Menus
remain unchanged. Both use the same responsive CSS/JS without Agency overrides.
Desktop header height is unchanged; narrow-screen navigation uses a single
scrollable row so differing menu counts do not change header height.
Image logos are width-limited on narrow screens.
All requests use existing User/Auth/web sessions and server-enforced Agency scope.
Merchants are restricted by `Merchant.i_agency_id`; Stores and device counts/details
are restricted by **Store.i_installed_by_agency_id**, not the Merchant assignment.
The technical Admin Store is always excluded.

- `AgencyPortalPermissions` is the shared UI/API capability and record-scope service.
  Merchant/Store/Device are read-only; Users have CRUD within their own Agency.
  Every Agency route uses the common permission dependency; templates use the same policy.
- Merchant/Store: read-only existing assigned records. No create/update/delete,
  Agency reassignment, license, or entitlement changes.
- Users: only this Agency's Agency Admin accounts; create with an entered password,
  edit Name/Login Email/Phone and status. Reuses scoped email checks and bcrypt;
  no plaintext password storage, role selection, password reset, or forced password change.
  Self-deactivation is blocked. Deactivation terminates active sessions through the
  existing common session helper, so reactivation does not revive an old token.
  Delete follows the existing Platform User policy: deactivate without deleting
  identity/password/history, and terminate active sessions. Self-delete is blocked.
- Devices: read-only per assigned Store; Name/Type/OS/Last Seen/Status only.
  Activation codes, hardware IDs, keys and License mutation APIs are not exposed.
- Reports: assigned Merchant/Store/Device counts and CSV download, no sales reports.
- Settings: own display Name/Phone; Login Email remains unchanged here;
  voluntary password changes use the existing Change Password page.

The Portal extends `platform_base.html` with Agency navigation, reuses
`management_layout.html`, `awsum-list.css`, Bootstrap modals, and shared
`management_form.html`/`management-form.js` for Settings. No separate UI theme or Auth/Session
system and no DB migration are introduced.
Focused tests: `.venv\Scripts\python.exe -m unittest tools.test_agency_portal tools.test_agency_portal_ui`.

Platform and Agency Users both include `platform/components/user_management.html`,
extracted from the existing Platform Users screen (styles, list, modal and JS).
`platform/users.html` is a wrapper, not a second implementation. The component's
scope/transport/capability configuration supports later Merchant integration;
Store Employee management is not part of this component.
Platform photo, Role selection, Reset Password, Must Change Password and existing
APIs are retained. Agency uses its existing scoped create/update/status/soft-delete
APIs, fixed Agency Admin role, Phone and central `AgencyPortalPermissions`.
Both scopes use the same Profile Photo uploader, Must Change Password control and
Reset Password modal/JS. Agency keeps Phone and its optional-password-change
creation default; the administrator can explicitly set Must Change Password.
Agency image adapters reuse `image_mutation_form`, `commit_entity_image` and
`AwsumStorageService` with the login-derived Agency owner. Agency photos remain
private and are served only after common membership/read checks (or Platform
Admin authorization). Reset Password reuses the extracted existing Platform
password validation/hash/update function; Agency scope is resolved before calling
it, and central Users **update** permission gates Reset Password, including POST.
Platform endpoints and behavior remain unchanged; Activation/Registration defaults
are not affected.
`GET /agency/users` still renders HTML; `Accept: application/json` returns paginated
safe User display data from the same server-enforced scope for the common list.
Agency IDs are derived from login, never trusted from browser query parameters.
Focused common User UI checks: `py -m unittest tools.test_common_user_management`.

Platform authorization denials emit `PLATFORM_AUTH_FORBIDDEN` warnings from the
existing Auth helper and Master role gate. JSON fields identify path, location,
reason, session existence, login context, principal/scope, token/current Role IDs,
User/Role status and Access Code match (boolean only). JWTs, cookies, passwords,
session keys and Access Code values are never logged. This is diagnostic only;
401/403 classification, redirects, permissions and timeouts are unchanged.

Store Master and Merchant > Store editors use the same `AwsumImages.createUploader`
as User Profile Photo, configured with label `Store Logo` and contain preview.
Replace/Remove/file validation/crop/Cancel are local component state; only Store
Save submits its `formData` to `/platform/store[/<id>]/with-image`.
The multipart adapter reuses existing Store field handling plus
`image_mutation_form`, `image_operation_lock` and `commit_entity_image`
(`STORE` owner, `logo` category, `c_logo_url` reference). Store fields and logo
commit together, with filesystem compensation on failure. Existing Merchant/Store
path rules and JSON/legacy logo APIs are retained; there is no immediate Store
upload/reset button. Existing legacy files are not rewritten during preview/Cancel.
Agency and Merchant Add/Edit use this same uploader,
with `AGENCY`/`MERCHANT` owner and `logo` category options. Their multipart Save
adapters reuse the existing create/update/activation handlers and the same atomic
image service. Default paths remain `/agency/<code>/images/logo/logo.webp` and
`/merchant/<code>/images/logo/logo.webp`; JSON and legacy logo APIs are retained.
There are no separate Choose File/Upload/Reset controls. Lists provide the saved
image reference and common versioned preview URL. User Profile Photo and existing
Store uploader behavior are unchanged.
Organization image forms share the responsive `awsum-image-form` layout in
`platform-modal.css`: existing input rows on the left and a compact 154px image
column at the top-right. Below 768px the image and fields stack vertically.
Only the first Information section shares the image column; subsequent sections
(starting with Primary Contact) span the full form width.
This also applies to the linked Store editor; uploader/save APIs are unchanged.
Focused logo/API/browser checks: `py -m unittest tools.test_store_logo`.

Focused validation: `py -m unittest tools.test_agency_activation tools.test_agency_activation_browser tools.test_license_email`.

### Common Master form modal

`platform/components/form_modal.html::form_modal` owns the shared Master
Header/Body/Footer shell. Agency, Merchant, Store and other Master modules retain
`masterEditModal`, their dynamic fields, validation, button IDs and event handlers.
The component accepts header/body slots, optional left-action data and an optional
Delete ID; it does not replace the Master save API with the simpler Portal editor.
`platform-modal.css` supplies the common `.pm-modal`/`.awsum-form-modal` layout:
fixed header/footer, body-only scrolling, stable Cancel / Delete / Save order.
On narrow screens optional left actions occupy a separate row above primary actions.

Agency supplies `btnAgencyRegistrationEmail` as a left action. It is hidden before
the Agency is saved; Edit always shows it enabled, independent of `has_agency_admin`.
The existing Preview and Send endpoints check the current account on every request.
Registration uses the original active-Agency/active-Admin flow. Saving an Agency
as Active creates its first Admin through the existing activation helper.
Preview/Send never fabricates login details or creates/resets accounts.
Registration Email defaults to the current active Primary Contact Email through
`BusinessContactService.get_primary_contact`; other contacts and Login Email are
never fallback recipients. Missing/invalid Primary Contact Email blocks Preview
and Send even when a recipient override is supplied. Preview To edits remain
send-only, and changing the Primary Contact never updates the Admin login.
Resend never resets a password or reads a plaintext password from storage.
The existing saved-Agency requirement, Preview/Send, recipient validation and API remain.
Field/tab-specific styling stays in the module; modal chrome uses the common CSS.
Focused UI regressions:
`py -m unittest tools.test_master_form_modal tools.test_agency_activation_browser`.

## System Settings

Super Admins can open **Platform → SYSTEM → System Settings**. Settings live in
the central `awsum_platform` database in `tb_system_settings`; change metadata
is recorded in `tb_system_settings_audit`. On application startup, the existing
non-destructive schema migration creates these tables if missing.

`AWSUM_STORAGE_ROOT`, when set, is the effective data path and is read-only
in the UI. Otherwise the saved System Settings path is used; if none is saved,
the sibling `Awsum_Data` directory is the default. The path must be absolute
and outside the program tree. Saving a new path does not move existing data.
The deprecated `AWSUM_DATA_ROOT` must be removed; a nonempty value fails
explicitly rather than silently selecting legacy storage.

In the absence of SMTP environment settings, SMTP configuration can be saved
in System Settings. Any supported `AWSUM_SMTP_*` variable selects the
environment mode as a whole; incomplete environment configuration is reported
instead of being mixed with database values. Existing license emails use this
same effective SMTP configuration. The default SMTP profile is Username and
From Email `info@awsumsolution.com`, Host `smtp.gmail.com`, port `465`,
SSL-TLS, and SMTP authentication enabled. Environment variables and explicitly
saved valid System Settings values take precedence over these defaults.
Incomplete drafts can be saved but cannot send email. For Gmail, an
administrator must supply the account's app password in System Settings or the
server secret manager; no password is bundled with the application.

Company logo uploads accept actual PNG, JPEG, and WebP image files up to 5 MB.
They use the common Adjust Image/processor and canonical WEBP runtime location
`AWSUM_STORAGE_ROOT\platform-admin\images\logo\logo.webp`. The common platform header uses the
saved image and cache-busts it after changes. Select a logo and save Company
settings to upload and apply it.

Before saving an SMTP password, create the encryption key once on the server:

```powershell
py tools\init_encryption_key.py
```

The script writes `AWSUM_SYSTEM_SETTINGS_ENCRYPTION_KEY` to the Git-ignored
`.env` in the project root without printing it. It never overwrites an existing
key, and refuses to create a new one if encrypted SMTP data already exists
(restore the original key instead). A real environment variable or secret
manager value takes precedence over `.env`. The key is read on demand, so no
restart is needed after creating it. Back up this key separately from the
database in the organization's secret-recovery system; the saved password
cannot be recovered without the original key. Never regenerate or replace the
key without re-encrypting the saved SMTP password first. No SMTP host or
credentials are bundled with the application.

## UI development guide: Add/Edit modals

All new Add/Edit modals in the platform area must use the shared stylesheet
`static/css/platform-modal.css` (loaded by `platform_base.html`). Do not copy modal styles into pages.

- Wrap with `<div class="modal fade pm-modal">`; add `pm-modal-lg` (max 1000px) for wide forms such as price tables. Default width is 560px.
- Structure: `.modal-header` (`.pm-head-icon` + `.pm-head-text` with `.modal-title` and `.pm-head-sub`), `.modal-body`, `.modal-footer` inside one `<form>`.
- Header and footer stay fixed; only `.modal-body` scrolls (max height 90dvh).
- Labels use `.form-label` with an optional `.pm-lbl-icon` SVG and `.pm-req` for required fields. Inputs are 44px high.
- Errors: put `.invalid-feedback` under the field; right-align numbers with `.pm-num`.
- Group sections with `.pm-section` / `.pm-section-head`. Footer buttons: Cancel + Create (add) or Save Changes (edit).
- Reference implementation: `templates/platform/master_group.html` and `static/js/master_group.js`.

## Global report / print standard

The report standard applies across the application, not only Master Group.
`GlobalReportMiddleware` in `backend/services/reporting.py` automatically adds
the shared print assets to all successful HTML responses (including raw
Invoice/Contract HTML and future routes). Static assets and binary/CSV/JSON
downloads are not modified. Keep this middleware inside any compression
middleware so it sees uncompressed HTML.

- Context is resolved on the server from the authenticated DB user. HQ uses
  System Settings Company Name (then Display Name); other users use the
  Merchant linked to their own Store, then the Store name. Report filters and
  the target customer never override the printing user's organization.
- Names use the authenticated user's first/last name, then email, then
  `Unknown user`. Missing company details fall back to `APP_NAME`.
- `static/css/report-print.css` owns print margins, repeated margin footers,
  the separator, and print-only removal of application navigation. Printed
  timestamps are explicitly UTC, captured when the document is generated.
- HTML browser Print / Save as PDF uses real CSS `counter(page)` and
  `counter(pages)` in page-margin boxes. Use current Chrome/Edge (131+) on
  A4/Letter paper. Turn off the browser's own Headers and Footers to avoid its
  URL/date/page decorations. Preserve the document's default print margins.
  Firefox/Safari and browser preview engines without page-margin-box support
  cannot guarantee these footers or totals: print through Chrome/Edge instead.
- Long names are centrally shortened with ellipses; the page counter keeps
  its own column. No report may implement a local page footer or hard-code
  page totals. Full names remain unchanged in authentication and settings.
- The footer is a compact bottom bar: approximately 4-6px between the
  separator and text, with minimal padding beneath. This is shared by browser
  print/PDF and the server PDF canvas; report content margins stay unchanged.
- Browser reports supply title/headers/rows to
  `AwsumReports.printTable(title, headers, rows)`. For trusted server-generated
  document HTML use `AwsumReports.printHtml(html)`. Both load styles before
  invoking print. Direct Ctrl+P / `window.print()` inherits the same margins
  and footer through the middleware. Build server-side table HTML with
  `table_report_html()`; add `data-report-auto-print` for automatic preview.
- Server PDF generators **must** use `GlobalReportCanvas(...,
  report_context=report_context(db, user))` from `backend/services/report_pdf.py`,
  including as the `canvasmaker` for ReportLab document templates. It handles
  all pages and actual totals in a final pass. Keep content above the reserved
  48pt bottom area and below the timestamp area. Never use a raw ReportLab
  canvas or add footer code in a report.
- Legacy HTML `.xls` exports inherit Office `mso-footer-data` with Excel's
  `&P`/`&N` pagination where supported. Client-generated Excel HTML includes
  `AwsumReports.spreadsheetStyle()` in its head. Office controls print preview
  and page breaks; this compatibility path is not a PDF renderer.
  CSV and on-screen grids are data-only, have no pages, and do not get footer
  rows that could corrupt exported data.
- Existing permission/filter checks stay with the data provider. Never send
  credentials, entire session objects, or client-provided actor names to a
  report. The global layer adds no new access permissions.

Install runtime dependencies with `py -m pip install -r requirements.txt`
(`reportlab` is now required for server PDFs). Restart an existing server
after installing dependencies.

Report validation:

```powershell
py -m pip install -r tools\requirements-report-tests.txt
py -W ignore -m unittest tools.test_reporting tools.test_reporting_browser
```

The browser tests use an isolated headless Chrome profile, generate real
three-page portrait/landscape PDFs, and inspect every page with `pypdf`.
Set `AWSUM_REPORT_CHROME` to an Edge/Chrome executable if Chrome is not at
the standard Windows installation path. Test documents and profiles are
temporary; no production records or emails are changed.

## Global single active web session

All web-user authentication uses `backend/services/web_sessions.py`. A valid
JWT is insufficient: each authenticated request must also match a persistent
ACTIVE row in `tb_session`. Session ID, user, Store, role, absolute expiry,
and idle timeout are checked server-side. The HTTP middleware rejects stale
cookies independently of module routes; all existing authentication helpers
delegate to this service. New protected modules must use `authenticate()` (or
an existing common `get_current_user` dependency); never validate JWTs alone.

After password and login-permission checks, login must call `create_session()`.
It locks the user row and expires abandoned sessions. Platform, Agency, Merchant and Store
share the same policy: successful login from the same browser terminates its
ACTIVE session and creates a replacement atomically; the old token is invalid.
The existing persistent HttpOnly `awsum_web_client` cookie identifies the browser,
not IP or User-Agent. It survives browser/PC restart without logout. If the cookie
is cleared, a private window or another browser is treated as a different client.
Only another client's unexpired ACTIVE session blocks login with HTTP 409.
The common login form recognizes `ACTIVE_SESSION_EXISTS` and asks whether to
sign out the other device. Cancel makes no replacement request. Approval resubmits
credentials with `replace_active_session=true`; credentials and scope permissions
are checked again before the same locked transaction terminates old sessions and
creates the new one. Terminated devices redirect to their login on the next
protected page request, or through the common Fetch 401 handler.
Cross-client replacement records `REPLACED` (retaining termination audit fields).
Its login redirect uses `reason=signed_in_elsewhere` and the common Signed Out
notice. Logout remains `LOGGED_OUT`, timeout `EXPIRED`, and administrative
termination `TERMINATED`; these do not show the other-device notice.
A wrong password never terminates an existing session. Session changes and
login timestamp commit together. A PostgreSQL partial unique index
also enforces one ACTIVE row per user across concurrent processes.

Runtime configuration (positive integer minutes):

- System Settings > Security > Web Session Idle Timeout: **5-480 minutes**.
  Priority is saved DB setting, then `AWSUM_WEB_SESSION_IDLE_MINUTES`, then **30**.
  Active Platform/Agency/Merchant/Store web sessions use the new value at their
  next authentication request. POS/KIOSK device authentication is excluded.
- `AWSUM_WEB_SESSION_MAX_MINUTES`: **480** by default (absolute lifetime).

UTC login/activity/logout/termination timestamps are stored in the existing
timestamp columns. Activity extends only the idle deadline, not the absolute
lifetime. Expiry is evaluated on authenticated requests, attempted logins, and
the Sessions list; an abandoned session needs no browser-close notification.
All web requests using the common session count as activity; new background
polling features should not be used as artificial session keepalives.

The migration is additive and repeatable:

```powershell
py -m tools.migrate_web_sessions
```

Startup runs the same migration. It adds identity, Store, client/IP/browser,
expiry/logout and termination-audit columns and indexes to `tb_session`.
Session history and the legacy token column are retained; **new bearer JWTs
are never stored in that column or returned by the Sessions API**. Legacy
ACTIVE rows without a verifiable session identity/expiry become EXPIRED.
Duplicate modern ACTIVE rows cause migration failure for administrator
review, not arbitrary deletion. Old JWTs without `sid` require one fresh
sign-in after rollout; they cannot be silently promoted to new sessions.
Back up the headquarters DB before deployment and run all workers on the new
version together. Do not mix old JWT-only workers with this policy.

Security > Sessions (`/platform/master/session`) is a security list, not CRUD:
Sessions persist `c_login_context` (PLATFORM / AGENCY / MERCHANT / STORE)
and explicit organization IDs. PLATFORM sessions have no customer Store,
Merchant, or Agency reference, even though `tb_platform_user.i_store_id`
retains the legacy technical `ADMIN_STORE_ID` for authentication compatibility.
Their organization uses System Settings Company Name, then Display Name,
then AWSUM PLATFORM. Other scopes resolve only their explicitly recorded
Agency/Merchant/Store IDs, never an administrator's technical Store.
Agency and future Merchant login handlers must pass their server-authorized
`scope_type` and `agency_id`/`merchant_id` to `create_session()` after checking
membership; these must never come directly from an unchecked login payload.
The repeatable migration backfills PLATFORM only from the recorded
`/platform-admin` login path and clears its customer references without
changing session IDs, status, timestamps, or JWTs. Historical rows with no
provable login context remain unknown rather than guessing from Store IDs;
such tokens require a fresh sign-in.

The screen provides:
Search, status filter, Refresh, View Details, Terminate, and CSV/PDF Export.
Only Super Admin can terminate an ACTIVE session. Termination records the
actor and time, and the previous browser is denied on its next request.
The common browser fetch handler sends it to the correct login page.
Normal logout changes ACTIVE to LOGGED_OUT and records `dt_logout`; it never
deletes history. Do not reactivate historical sessions or manually insert
sessions through master-data APIs.

POS/KIOSK activation and device validation are explicitly excluded. Multiple
properly licensed devices can operate simultaneously, regardless of a web
user being logged out, expired, or terminated. Do not put user-session checks
in the device licensing service. Currently only the HQ login is enabled by
this application's login routes; Store-user login remains directed to the
operation-DB flow rather than being newly enabled by this change.

Tests:

```powershell
py -m pip install -r tools\requirements-session-tests.txt
py -W ignore -m unittest tools.test_web_sessions
# Opt in to real PostgreSQL locking/index tests:
$env:AWSUM_TEST_POSTGRES_SESSIONS = "1"
py -W ignore -m unittest tools.test_web_sessions
```

PostgreSQL tests use and clean up uniquely named `test_web_sessions_*` schemas
in the configured database. They do not create production-user sessions or
modify production users, licenses, or Store data. Normal tests use in-memory
SQLite and do not execute the application's database-seeding startup.

### Global session network and client metadata

`backend/services/session_metadata.py` collects audit metadata only when a new
session is created. The common login service handles this for every web scope.
It reuses `c_ip_address` as the canonical public/transport client IP,
`c_user_agent` as the bounded raw audit string, and `c_client_id` as the
persistent browser Client ID. New nullable columns hold a separate local IP,
country/region/city, parsed browser/OS versions, and native device name/UUID/app
version. Browser Client ID is not a hardware UUID. None of these fields grants
access or changes the one-ACTIVE-session rule or POS/KIOSK licensing.

**Proxy configuration is security-sensitive:**

- Run Uvicorn with **`--no-proxy-headers`**. Both server batch scripts include
  this flag. Restart an already running server to apply it; reload does not
  change Uvicorn's launch flags. The app must receive the original socket peer,
  not an IP already rewritten by Uvicorn/Gunicorn or another ASGI wrapper.
- `AWSUM_TRUSTED_PROXY_CIDRS` is a comma-separated list of exact proxy IPs or
  CIDRs, empty by default. Example: `10.20.0.10/32,10.20.0.11/32`.
  Configure only infrastructure you control; never `0.0.0.0/0`, `::/0`, or
  arbitrary customer networks. Invalid CIDRs fail startup.
- Only a trusted socket peer may supply X-Forwarded-For or X-Real-IP.
  X-Forwarded-For is traversed right-to-left to the first untrusted hop, so
  client-prepended spoofed addresses are ignored. X-Real-IP is used only when
  X-Forwarded-For is absent. For a trusted proxy, missing/malformed/oversized
  forwarding headers or an all-trusted-proxy chain leave the original client
  IP NULL with a warning. Never geolocate the proxy as a substitute.
  A proxy must overwrite/append the real connecting IP
  correctly and access to the backend should be restricted to trusted proxies.
- Direct connections use the real socket client. `127.0.0.1`, `::1`, and private
  IPs remain unchanged when that is all the transport can reliably establish.
  “Client Public IP” does not guarantee a globally routable WAN address.
  No browser-supplied public/local IP or device header/form value is accepted.

**Approximate location (optional, local-only by default):**

Set `AWSUM_GEOIP_DATABASE_PATH` in the server environment or Git-ignored `.env`
to an absolute path to an operator-provided GeoIP2/GeoLite2 City `.mmdb` file.
Obtain/update that database separately under its provider's license and keep
it outside source control. No database or credentials are bundled or downloaded
automatically. The `IpGeolocationProvider` interface allows provider replacement
without changing login/session code. The built-in provider reads locally and
does not transmit client IPs to a third party. Only globally routable IPs are
looked up. No configuration, no match, or a provider failure leaves location
NULL; failures are logged without the client IP/provider payload, and login
still succeeds. Lookup occurs once per accepted login, not on normal activity
or denied duplicate logins. Country, region, and city are approximate IP-based
estimates, never GPS coordinates or a verified physical address.

Ordinary browsers cannot reliably provide local IP, OS version, device name,
or hardware UUID; unavailable values stay NULL. User-Agent is self-reported
and parsed for a compact Browser / OS label (Windows 10 and 11 may be
indistinguishable from browser UA). Full UA remains in View Details.
`TrustedClientMetadata` is an explicit server-side extension point for a future
verified native-client login handler. That handler must authenticate/verify
the client before passing local IP/device/OS/app metadata to `create_session`;
no native trust mechanism or device-license linkage is introduced here.

The existing migration adds nullable columns and parses stored historical
User-Agents without changing session IDs/status/times or inventing historical
location/local-IP/device data. It does not geolocate historical sessions.
Run `py -m tools.migrate_web_sessions` (safe to repeat); startup also runs it.
Install declared runtime dependencies with `py -m pip install -r requirements.txt`.

The list, CSV, and PDF use compact client/IP/location fields. Details group
Session / Network / Client / Activity / Technical, including raw UA and separate
Browser Client ID / Device UUID. Timestamps remain UTC in storage/API; the
shared UI formatter interprets naive API timestamps as UTC and reads
`data-display-timezone` (currently UTC), ready for a future server-resolved
System Settings/user preference without changing stored dates.

```powershell
py -W ignore -m unittest tools.test_session_metadata tools.test_web_sessions
$env:AWSUM_TEST_POSTGRES_SESSIONS = "1"
py -W ignore -m unittest tools.test_session_metadata tools.test_web_sessions
```

### Global client location standard

Session location means **the approximate network location of the client that
initiated the authenticated login session**. It never means the FastAPI,
database, cloud host, or reverse proxy's location. There is no external
"what is my IP" call, server-interface discovery, server-IP fallback, browser
IP claim, or hard-coded geographic default in the collector.

The only geolocation input is the original client IP resolved from the socket
peer (direct) or the first untrusted hop in the trusted forwarding chain.
If the original client cannot be established behind a configured trusted proxy,
login still succeeds but client IP/location remain unavailable. The transport
peer is stored separately for diagnostics and is never substituted for the
client. Trusted proxy CIDRs must include every proxy hop, including CDN egress
when using a CDN and a load balancer together; keep these ranges maintained.

API names `client_public_ip`, `client_local_ip`, `client_country_code`,
`client_region`, `client_city`, `client_location`, `client_location_status`,
`client_ip_source`, and `connection_type` make the meaning explicit.
Existing generic API names remain compatibility aliases. ORM client-oriented
synonyms reuse existing network columns instead of duplicating stored values.
The additive migration adds `c_transport_peer_ip`, `c_client_ip_source`
(DIRECT / FORWARDED / UNAVAILABLE), and `c_connection_type` (Web / Desktop).
Historical source/transport/type values remain NULL (API LEGACY / Unknown)
because the old data cannot prove them. No session identity/status/timestamps
or historical geographic values are rewritten; historical non-public IPs do
not expose an invented geographic location in the API.

List, Details, CSV, and PDF use the same server-resolved location status:

- Direct/legacy loopback (`127.0.0.1`, `::1`): **Local Development**.
- Routable client IP with geolocation: approximate city, region, country.
- Routable client IP with no match/provider: **Location unavailable**.
- Private client IP: **Location unavailable (non-public client IP)**.
- Trusted proxy without a provable original client: client IP **Not Available**
  and **Location unavailable**; transport peer is Technical diagnostic data only.

**Development testing without falsifying session data:**

Local browser access to `http://127.0.0.1:8000` intentionally displays Local
Development. To test connectivity from another authorized device, run a
development instance with `--host 0.0.0.0 --no-proxy-headers` on a controlled
network and browse to its actual LAN address; this normally yields the
client's private IP, not a public geographic location. Public geolocation
testing requires a genuinely remote client reaching a safely deployed
instance directly or through correctly configured trusted proxies, plus a
local City MMDB. Do not expose a development instance to the Internet merely
to obtain an IP.

For repeatable development-only simulation, use the isolated automated tests:
`test_same_server_two_client_countries_use_only_original_client_ip` and
`test_two_users_same_host_have_separate_persisted_client_locations`. Mock
provider results simulate two countries behind the same server/proxy and
never write production sessions or make live claims about those IPs.
No runtime `AWSUM_DEV_TEST_CLIENT_IP` override is implemented or read: this
avoids silently replacing real login metadata or enabling test data in
production. Location remains approximate audit metadata, never authorization
or licensing input.

## Global login-context branding

`backend/services/branding.py` is the common public-safe resolver. Entry paths,
not the current/previous user cookie, select the login organization:

- `/platform-admin` always resolves PLATFORM and System Settings platform
  Display Name/Company Name and logo. The configured logo falls back to the
  shared platform asset, then the bundled AWSUM `logo_full.png`; if no asset
  exists, the organization name is shown. `?reason=session_expired` changes
  only the notice/modal state, never the branding context.
- `/login/agency/{agency_code}`, `/login/merchant/{merchant_code}`, and
  `/login/store/{store_code}` are explicit, public organization login entries.
  Their Access Code is fixed to the resolved entry; an unrelated remembered
  code cannot replace it.
- `/login` and the homepage's general modal can resolve a typed Access Code
  through public `GET /auth/branding?access_code=...`. Only context, public
  organization/brand name, versioned logo, entry URL, code, and description
  are returned. Invalid/inactive codes are rejected; ambiguous codes require
  the explicit context route or `context_type` parameter. No email, password,
  permissions, SMTP settings, membership, or other private fields are exposed.
  Resolution never authenticates anyone.

The inheritance chain is Store logo -> linked Merchant logo -> Merchant's
Agency logo -> platform logo. Agency/ Merchant/Store names always remain the
entry organization's name, even when its logo is inherited. Store's installing
Agency is not treated as its Merchant's Agency. Local image URLs are validated,
must exist in the common context-first image store (or compatible legacy roots), and are versioned
with file modification time. External/script/traversal image references are
not accepted by this resolver. Logos use `object-fit: contain`, preserve aspect
ratio, and fall back to text on image failure.

System Settings already owns the platform logo reference. The additive
`py -m tools.migrate_branding` migration adds nullable `c_logo_url` only to
Agency/Merchant/Store, backfills existing code-based Merchant/Store logo files
without moving or deleting them, and preserves existing non-null references.
Startup runs the same schema migration and then the image-storage migration below.
Legacy file discovery runs only when a logo column is first added; restarting
cannot resurrect a deliberately reset logo from retained legacy files.
Merchant/Store/Agency POST/DELETE logo endpoints remain compatible, but all new
writes use the common context-first store. Reset clears the reference without
deleting historical images. System Settings uses the same storage. New login
pages consume `BrandingContext`; do not construct asset paths in components.

The session creation service records its canonical organization login entry
in `c_login_path` and the signed token; logout and expiry/termination errors
return to that entry. Client redirects accept only same-origin allowlisted
login paths. Migration updates older generic `/` session entries only where
an explicit stored AGENCY/MERCHANT/STORE context and organization relation
prove the correct entry. Existing IDs, JWTs, state, and timestamps are retained.

HQ and Agency Admin login use the existing authentication and common session
service. Merchant/Store operation-user login remains rejected by `/auth/login`;
those entry routes only provide pre-authentication branding. Agency membership
is validated against the user's Agency FK and active Agency Admin Role.

```powershell
py -m tools.migrate_branding
py -W ignore -m unittest tools.test_branding tools.test_web_sessions tools.test_system_settings
# Include isolated PostgreSQL migration/locking tests:
$env:AWSUM_TEST_POSTGRES_SESSIONS = "1"
py -W ignore -m unittest tools.test_branding tools.test_web_sessions
```

## AWSUM PLATFORM / TUXEDO POS Global Path Standard

The permanent developer specification is
[AWSUM GLOBAL PATH STANDARD](docs/AWSUM_GLOBAL_PATH_STANDARD.md).
Store resources always belong underneath their Merchant; Agency associations
remain database relationships, not physical parents.

All application image ownership follows this permanent logical hierarchy:

```text
/platform-admin/images/{category}/{file}
/agency/{agency_code}/images/{category}/{file}
/merchant/{merchant_code}/images/{category}/{file}
/merchant/{merchant_code}/store/{store_code}/images/{category}/{file}
/platform-admin/data/{category}/
/agency/{agency_code}/data/{category}/
/merchant/{merchant_code}/data/{category}/
/merchant/{merchant_code}/store/{store_code}/data/{category}/
```

Existing business codes are preserved (for example `AGT00001`, `CLT11001`,
and `CLT00001`); example AG/MC/ST prefixes do not change business-code issuance.
Categories are singular, lowercase `logo`, `master`, `employee`, `background`,
and `common`. `master` optionally accepts `item`, `category`, `modifier`,
`department`, `group`, or `other`. Do not add unused future categories.

`backend.services.paths.AwsumPathResolver` is the central image/data resolver;
`ImagePath` delegates to it. Store paths require both Merchant and Store codes:

```python
from backend.services.images import ImagePath, image_url

image = ImagePath("STORE", "CLT00001", "master", "IT000025.jpg", "item",
                  merchant_code="CLT11001")
assert image.logical_path == "/merchant/CLT11001/store/CLT00001/images/master/item/IT000025.jpg"
```

`image_url(reference)` supplies a usable, versioned URL; templates have the same
helper. `resolve_image_file(reference)` resolves local bytes for email/CID
consumers without outbound URL fetches. Browser previews consume the branding
API, not code-based path guesses.
Platform management previews use authenticated `GET /api/images/branding` with
explicit context; inactive organizations may be previewed there but remain
unavailable through public login-code resolution.

### Physical storage and ownership

Business tables retain only logical image references in the existing nullable
`c_logo_url` columns; no duplicate logo fields or image tables are added.
Physical storage maps directly beneath the effective storage root, without
a `media` container:

```text
AWSUM_STORAGE_ROOT\merchant\CLT11001\store\CLT00001\images\employee\EMP000007.webp
AWSUM_STORAGE_ROOT\platform-admin\images\logo\logo.webp
AWSUM_STORAGE_ROOT\merchant\CLT11001\store\CLT00001\data\master\main.db
```

Root selection is environment `AWSUM_STORAGE_ROOT`, then saved System Settings
root, then sibling `Awsum_Data`. For the final cutover, use the reviewed migration
below to separate program assets and map legacy runtime files before switching
configuration; the root setting does not silently move data. Never modify
PostgreSQL's data files.
The common storage functions are the replacement point for future S3/CDN storage;
business references and frontend components need not change.

Public image GETs serve `logo`, `background`, and `common` so login/expiry pages
work even with stale session cookies. These categories are explicitly public:
never put private documents or employee information in them. `master` and
`employee` require an active session and the same server-verified owning context
and organization, or authorized Platform administration. They use
`private, no-store`; the data root is **not** mounted publicly. No browser context,
path, branding result, or owner code grants authorization.

The common owner-scoped write API is:

```text
POST /api/images/{context}/{owner_code}/{category}?sub_category=item
POST /api/images/merchant/{merchant_code}/store/{store_code}/{category}?sub_category=item
  multipart: file
DELETE /api/images?image_path={logical_path}
```

Context values are `platform-admin`, `agency`, `merchant`, `store`; Platform writes
use `platform-admin` as the owner placeholder (it is absent from the logical path).
Store writes use the nested API above. The old generic Store write endpoint now
requires explicit `merchant_code`; no standalone Store namespace can be created.
Only authorized Platform administrators or an active matching organization-admin
session may write/delete. Store sessions cannot manage their parent Merchant or
other Stores. Cross-origin writes are rejected. Merchant/Store operation-user
login availability is unchanged; Agency Admin login establishes server-validated
membership through the common session service.

Uploads require safe leaf filenames (spaces/non-ASCII and uppercase extensions
are allowed; directories, drive/URL syntax and traversal are not), matching
PNG/JPEG/WebP extension, MIME and decoded content, at most 5 MiB and 20 megapixels.
All image categories use the same configured square WEBP processor.
Generic uploads require a separate stable `entity_key` and return a `.webp` logical
path. Managed logos use the owner-scoped `logo.webp` singleton and transactional storage.
Generic writes are create-only (409 on an existing path); deletion rejects active
logo references. Employee business-image fields/UIs are not yet implemented.

Platform User photos use a reusable local square cropper with drag/zoom and
deferred Apply/Save, then canonical 512 x 512 WEBP storage at
`/platform-admin/images/users/{USER_ID}.webp`. Existing JPG/PNG photos remain
unchanged on account-only edits. See the authoritative
[common image standard](docs/AWSUM_STORAGE_STANDARD.md#awsum-common-image-standard).

Platform Admin **MD Master** Add/Edit now supports local image preview,
Upload/Replace and deferred Remove. Save persists the item and its canonical image
at `/platform-admin/images/master/{MD_CODE}.webp`; Cancel never mutates the saved
image. Existing `MD` + five-digit code issuance is unchanged. The backend reuses
the same uploader, Adjust Image, processor and image service as Users.
Both normalize to configured 512 x 512 WEBP; legacy PNG/JPG references are preserved
until an actual replacement Save. The
additive, repeatable startup migration adds nullable `tb_md_master.c_image_path`
without changing existing rows. Responses include a versioned `image_url` and
the list shows a 40px thumbnail. CSV/Excel/PDF/report exports are unchanged.

Multipart `POST /platform/api/master-group/md/with-image` and
`PUT /platform/api/master-group/md/{id}/with-image` accept `payload` JSON,
optional `image`, and `remove_image`; existing JSON CRUD remains compatible.
Only existing MD administrators may save images. The server derives destination
from the generated code; generic uploads/deletes cannot modify MD-managed files.
OS/SQL locking and compensating file backups restore old bytes on save failure.
See [MD image storage and recovery](docs/AWSUM_GLOBAL_PATH_STANDARD.md#md-master-images)
for operation/cleanup details and owner-context reuse.

```powershell
py -W ignore -m unittest tools.test_master_images tools.test_master_group
# Optional isolated PostgreSQL concurrency/migration coverage:
$env:AWSUM_TEST_POSTGRES_SESSIONS = "1"
py -W ignore -m unittest tools.test_master_images
```

### Automatic owner storage initialization

Basic owner storage now initializes **automatically after successful registration**
and during normal application startup. Owner roots get `images/` and `data/`;
Merchant roots also get `store/`. Category folders remain lazy. Store roots are
always under their saved Merchant. No registration path fields are needed:
all owners use `AWSUM_STORAGE_ROOT`/System Settings and the common path resolver.
Disk errors retain the committed business record and return a visible
`storage_warning`; correct the storage problem and repair instead of registering
the organization twice. Existing files are never overwritten/deleted by repair.
Organization deletion/deactivation does not delete directories.

To preview/repair existing owners independently of a server restart:

```powershell
py -m tools.initialize_storage --dry-run
py -m tools.initialize_storage
```

These commands do not migrate/rename files or update business rows. See
[automatic storage initialization](docs/AWSUM_GLOBAL_PATH_STANDARD.md#automatic-initialization-and-repair)
for APIs, physical mappings, failures/logging and the technical HQ Store exception.

### Final program/data separation

The common image components are Svelte/Vite, not SvelteKit. Built-in Platform
logos, favicon, decorative images and shared defaults are Central Server program
assets under root
`static/images/platform/{logo,background,favicon,defaults,ui}` and served at
`/images/platform/...`. Templates use the common `builtin_asset_url()` resolver.
Custom uploaded logos retain priority and stay in owner-scoped runtime storage;
no tenant defaults are copied into source directories.

The configured runtime root for this installation is:

```text
AWSUM_STORAGE_ROOT=D:\01_Development\Awsum_Projects\Awsum_Data
```

It contains direct `platform-admin`, `agency`, and `merchant` owner trees,
with Stores nested beneath their Merchant. Existing `PostgreSQL/Data` is an
independently managed cluster and is excluded from file migration/cleanup.

```powershell
py -m tools.migrate_final_storage --destination "D:\01_Development\Awsum_Projects\Awsum_Data"
py -m tools.migrate_final_storage --destination "D:\01_Development\Awsum_Projects\Awsum_Data" --apply --pg-dump "C:\Program Files\PostgreSQL\16\bin\pg_dump.exe"
# Quiesce writers before applying; configure the new root and verify the app.
py -m tools.migrate_final_storage --manifest "<recorded manifest.json>" --verify
py -m tools.migrate_final_storage --manifest "<recorded manifest.json>" --cleanup --app-verified
```

Reuse the recorded manifest; cleanup requires successful file/DB verification
and explicit application verification. Backups and unknown historical assets
remain private under `platform-admin/data/audit/storage-migrations/`.
No DB business values are changed; SQLite is copied and integrity-checked and
PostgreSQL is backed up through `pg_dump`, never by editing its cluster files.
Layout version 2 startup validates current references instead of replaying old
source archives. See the [permanent storage standard](docs/AWSUM_GLOBAL_PATH_STANDARD.md#final-storage-migration-and-recovery)
for safeguards/recovery and the [actual migration report](docs/FINAL_STORAGE_MIGRATION_REPORT.md)
for this installation's paths, counts and verification.

### Earlier logical-path migration (legacy installations only)

Back up the platform database and data root as usual, then preflight and apply:

```powershell
py -m tools.migrate_global_paths --dry-run
py -m tools.migrate_global_paths
py -W ignore -m unittest tools.test_global_paths tools.test_images tools.test_branding tools.test_system_settings tools.test_license_email
```

Before the final-layout marker exists, startup performs the same migration;
preview manually before deploying against legacy data. It:

1. Preflights every source/destination hash and stored owner reference.
2. Refuses differing destinations, missing referenced files, invalid ownership,
   or concurrent reference edits; never overwrites/deletes another asset.
3. Writes `platform-admin\data\audit\image-migrations\{timestamp}-{id}.json` **outside public assets** before
   copying. It records source, destination, logical path, SHA-256, DB row keys,
   before/after references, and migration state.
4. Copies files create-only, verifies checksums, and updates references in one DB
   transaction. Numeric IDs, foreign keys, credentials and session state do not change.
5. Preserves historical/orphan assets until the final reviewed cutover. Final
   cutover separates bundled Platform assets, archives unknown files and removes
   only verified old sources; legacy static URLs are not a permanent interface.
   Store paths always nest under their DB-verified Merchant.

The data migration records corresponding maps in private
`platform-admin/data/audit/path-migrations`, copies
recognized legacy Store SQLite/files and contract references into nested
`data/{category}/` paths, and leaves PostgreSQL database identifiers/internal files
unchanged. Stop/checkpoint SQLite writers before migrating; active journals are
rejected. Legacy standalone Store image GETs redirect through the current DB
relationship, but those old paths cannot be used for writes/deletes.

Rerunning safely resumes from identical copies and existing logical references;
an unchanged run creates no new manifest. DB update failure rolls back references
and retains recoverable copies. `COPIED_PENDING_DB_COMMIT` in a map means verify
the recorded references before retrying (for example after power/commit failure).
To revert, restore the backed-up database or the exact recorded `before` values
in a transaction only while the matching `after` values still apply; legacy
sources were never removed. Do not delete the whole media directory or overwrite
newer references during recovery.