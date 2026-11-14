# AWSUM Form Development Standard

Recorded 2026-11-14 (base commit `cbdb362`, Common Framework P0, approved direction by Nova). Parts referenced here
are listed with their files and APIs in the [AWSUM Common Component Inventory](AWSUM_COMMON_COMPONENT_INVENTORY.md).
The Money, Date, Architecture, Storage and Installation standards stay authoritative (see `AGENTS.md`).

## 1. Principles

1. **One Function, One Common Implementation, Multiple Scopes.** A screen supplies fields, data, permissions and
   options; behaviour comes from the common parts. No screen-specific copy of a common function.
2. **New Merchant / Store Masters use the Master Group engine (B)**: `services/master_group.py` `ENTITIES`,
   `routers/master_group.py`, `AppScope`, `FieldErrors`, `COLUMNS` and the common API. No new form engine.
3. **Platform Admin (engine A, `master_management.html`) is not rewritten.** It changes only through approved,
   step-by-step replacement with common parts (Date, Confirm, error contract).
4. **The server decides**: validation, permission, read-only state and feature entitlement are enforced by the API;
   the screen follows the flags it receives (`read_only`, `can_edit`, `can_delete`, `can_add`, `can_view_cost`).
5. **Layout is agreed before building**: tabs, sections, column widths and the list columns are part of the design
   approval, so layout is not reworked after implementation.
6. **Step approval**: design -> implementation -> tests / screen check -> report -> commit / push, each approved.
   Migrations are separate approvals; `AWSUM_AUTO_MIGRATE` stays OFF; real data is never changed by tests.

## 2. Required common parts (use, never re-create)

| Need | Use | Never |
|---|---|---|
| Date input / display | `AwsumDateInput` (`data-awsum-date`), `services/dates.py` | native `type="date"`, own picker / mask / formatter |
| Money | `AwsumMoneyInput` (`data-awsum-money`), `AwsumMoneyKeypad`, `services/money.py` | own money mask |
| Codes / decimals | `AwsumCodeInput` (`data-awsum-code`, `data-awsum-decimal`) | own upper-case handlers |
| Barcode | `AwsumBarcodeInput`, `services/barcode.py` | own scanner handling |
| Choices | `AwsumOptionGroup` or a `select` filled from options | |
| Address / ZIP / Map | `AwsumAddress`, `AwsumMap` | own ZIP lookup, own map frame |
| Images | `AwsumImages.createUploader`, `services/images.py`, `image_service.commit_entity_image` | own uploader / save path |
| Confirm | `AwsumConfirm.show` | `window.confirm`, own modal |
| Result message | `AwsumToast` (+ in-form message slot for errors) | `alert()` |
| Lookup list | `AwsumLookup.open` | own selection modal |
| Header buttons | `page_header(..., actions_id)` + `AwsumPageActions.mount` | own button state code |
| Split layout | `awsum-master-detail.css` (Equal Height standard), `AwsumMasterDetail` for trees | own height scripts |
| Tabbed body | `.mg-md-pageframe` + Bootstrap Tab (MD / Employee markup) until the common Tab part exists | a new tab implementation |
| Child records | Common Child Grid (`childGrid(cfg)` in `master_group.js`) | a new inline list editor |
| Errors | server `FieldErrors` -> 422 `{detail, errors:{field: message}}`; screen `showFieldErrors` (+ `showTabOf`) | client-only validation, text-only errors |
| Scope / permission | `resolve_app_scope` + `scope.require(action)`; features via `scope.feature_on` | role-name checks in a screen |

## 3. Master Group engine: where a Master is defined today

A Master on engine B is described in these places (Tax shown as the reference):

| Layer | File | Item | Tax example |
|---|---|---|---|
| Model | `backend/models_admin/master_group.py` | SQLAlchemy model | `Tax` |
| Registry | `backend/services/master_group.py` | `ENTITIES[entity]` | `dict(model=Tax, pk="i_tax_id", code="c_tax_code", name="c_tax_name", prefix="TAX")` |
| Validation | same | `_validate_payload` branch with `_text / _decimal / _int / _id / _validate_common` | `name` (100, required), `rate` (4 places, max 100), common `active / notes` |
| Serialization | same | `serialize` branch | `name`, `rate` |
| Sort | same | `SORT_COLUMNS[entity]`, `DEFAULT_SORT[entity]` | code / name / rate / active; default `code` |
| Export | same | `export_table` branch | Tax Code, Tax Name, Tax Rate (%), Status, Notes |
| In use | same | `is_in_use` branch | referenced by MD default tax / Category III taxes |
| Page / scope | `backend/routers/master_group.py` | `PAGES[slug]`, `RECORD_TITLES`, scope sets (`PLATFORM_ONLY_ENTITIES`, `MERCHANT_ONLY_ENTITIES`, `STORE_VIEW_ONLY_ENTITIES`, `SUPER_ADMIN_ENTITIES`) | `"tax": ("tax", "Tax")` |
| Menu | `backend/services/app_navigation.py` `MASTER_SLUGS` (+ Platform menu in `templates/platform/platform_base.html`, order guarded by tests) | slug, label, available | `("tax", "Tax", True)` |
| Screen text | `static/js/master_group.js` | `SUBTITLES[entity]` | "Tax rates that items can use as their default tax." |
| Layout | same | `TREE_LEVELS[entity]` (split layout; parent levels for a tree) | `[]` |
| List | same | `COLUMNS[entity]` `[key, label, render, sortable, flag]` | `nameColumn('Tax Name')` |
| Form | same | `formHtml()` case using `field()` / `input()` / `select()` | name, rate, status, notes |
| Fill / collect | same | `fillForm(row)` per field; `collect()` generic over `[name]` | `name`, `rate` |

Common Framework P1 reduces the screen part to one declarative definition per entity (see the P1 design); until
then, follow this table and keep each new Master's code inside these existing places.

## 4. Standard procedure for a new Master

1. **Design approval**: scope (Platform / Merchant / Store), fields (type, required, limits, defaults), list columns,
   tabs / sections and widths, permissions per action, feature gates, child records, export columns.
2. **Model and migration** (separate approval): table, columns, indexes; `tools.migrate_platform` path.
3. **Backend**: register in `ENTITIES`; validation in `_validate_payload` (simple) or a `custom` service (complex,
   like `services/employees.py`); `serialize`, `SORT_COLUMNS`, `DEFAULT_SORT`, `export_table`, `is_in_use`;
   options in `lookup_options` if the form needs lists; route registration in `PAGES` and the scope sets;
   menu in `MASTER_SLUGS`.
4. **Screen**: `SUBTITLES`, `TREE_LEVELS`, `COLUMNS`, form fields with `field()` helpers and the common inputs,
   `fillForm`; child lists through `childGrid`; nothing outside `master_group.js` / `master_group.html` unless approved.
5. **Tests** (section 6) and the real-app screen check on a temporary DB copy.
6. **Report** with the checklist (section 5), then commit / push after approval.

## 5. New Master checklist

Design
- [ ] Scope and menu position approved; Platform-only / Merchant-only / Store view-only decided
- [ ] Field list with types, limits, required, defaults; dates `MM-DD-YYYY`; money via `AwsumMoneyInput`
- [ ] Tabs / sections / widths and list columns approved before building
- [ ] Permissions per action (read / create / update / delete / export) and feature gates
- [ ] Delete policy (delete, deactivate, in-use rule) and child records

Backend
- [ ] `ENTITIES` entry (code prefix or manual code, scope flags)
- [ ] Validation with the common helpers; every error returned as `FieldErrors` (field name -> message)
- [ ] `serialize`, `SORT_COLUMNS`, `DEFAULT_SORT`, `export_table`, `is_in_use`, `lookup_options`
- [ ] `PAGES`, scope sets, `MASTER_SLUGS`
- [ ] Migration script approved separately; no automatic migration

Screen
- [ ] `SUBTITLES`, `TREE_LEVELS`, `COLUMNS`, form definition, `fillForm`
- [ ] Only common parts (section 2); no `window.confirm`, native date input, own picker or own modal
- [ ] Read-only and permission flags followed (`read_only`, `can_*`); Save disabled when read-only
- [ ] Equal Height split layout; no sideways scroll at 420px; stacked below 992px

Verification
- [ ] API tests: each scope, signed out 401 / wrong scope 403, list / create / update / delete / export, 422 field map
- [ ] Browser test: open, fill, field errors, save, permissions, layout checks
- [ ] Full suite compared with [the baseline](AWSUM_TEST_BASELINE.md); no new failure
- [ ] Real-app screen check on a temporary DB copy (never the real data); screenshots attached to the report

## 6. Test strategy

- **Component tests** stay per component (`tools/test_awsum_map.py`, money, date, image components, ...).
- **API tests** per Master (pattern: `tools/test_master_group.py`, `tools/test_employee_master.py`): scopes,
  permissions, validation map, export, in-use rules.
- **Browser tests** use the shared harness `SharedImageComponentTests.run_component_script`
  (`tools/test_image_components.py`) with mocked APIs (pattern: `tools/test_master_group_ui.py`).
- **Baseline**: the full suite is compared with `docs/AWSUM_TEST_BASELINE.md`; known failures are listed and
  classified there. A failure not listed is a regression until shown otherwise; a flaky test is rerun alone first.
- **Expected results are never edited just to make a test pass**; a test changes only when the approved behaviour
  changes, and the report says so.
- Planned (P1+): one parameterized entity contract test (scopes, CRUD, 422 format, export) and one parameterized
  screen smoke test (Equal Height, page actions, field errors) so a new Master is covered by registration.
