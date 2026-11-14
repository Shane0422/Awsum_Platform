# AWSUM Regression Test Baseline (STEP 2 / STEP 3-3C)

Recorded 2026-11-12 after the STEP 2 Merchant MD Master approval, before the next development step.
Full suite (`.venv/Scripts/python -m unittest $(ls tools/test_*.py | sed 's#/#.#; s#\.py$##')`):
**612 tests: 26 failures, 10 errors, 11 skipped** (existing baseline, unchanged by STEP 2 and STEP 3-3C).

A new failure is anything not listed here. These are pre-existing and not caused by STEP 2 / STEP 3-3C; none is in the
Merchant MD, Feature, Location or Store Item Feature modules. They are tracked separately and are not fixed yet.

**Current count (2026-11-14, Common Framework P0, base `cbdb362`): 710 tests, 27 failures, 10 errors, 11 skipped.**
The failure / error list is exactly the list below; the 27th failure is the flaky MD browser test (section
"Unstable"). New tests since the first record pass.

## Classification (Common Framework P0, 2026-11-14)

Recorded from the failure messages of the 2026-11-14 full run. Nothing was fixed and no expected result was changed;
each group needs its own approved step. "Stale test" means the application changed on purpose and the test still
expects the earlier version; the fix is to update the test to the approved behaviour after review, not to change
the application.

| Group | Count | Tests | Message | Cause |
|---|---|---|---|---|
| S1. Stale test: stylesheet version | 19 failures | `test_platform_admin_ui...test_every_management_module_reuses_single_shared_stylesheet` (all subtests) | `0 != 1` | the test looks for `awsum-list.css?v=3`; the bases now load `?v=7` (`platform_base.html`) / `?v=8` (`master_group.html`) |
| S2. Stale test: extracted functions | 3 errors + 4 failures | `test_agency_activation_browser` (2 errors, 1 failure), `test_master_form_modal` (1 error, 1 failure), `test_store_logo...test_both_store_forms_use_common_pending_preview_cancel_and_save`, `test_platform_admin_ui...test_shared_visuals_and_preserved_layouts` | `'NoneType' object has no attribute 'group'`; `ReferenceError: contactOwner / bindRegistrationEmailButton is not defined`; `storeDevicesPanel.init is not a function` | these tests copy single functions out of `master_management.html` by regex; functions were renamed (`openAgencyRegistrationEmail` -> `openRegistrationEmail`) or gained new helpers / panel APIs the copied set does not include |
| S3. Stale test: template harness | 2 errors | `test_money_browser` (2) | `'page_header' is undefined` | the test renders a template without the `management_layout.html` macro import that the page now uses |
| R1. Business rule changed | 3 errors + 2 failures | `test_storage_initialization` (3), `test_business_codes...(url='/platform/merchant', '/platform/merchant/with-image')` (2) | `400: Save the Merchant as Inactive, add an active Primary Contact, then activate it.` | the tests create an Active Merchant directly; the registration rule now requires Inactive -> Primary Contact -> Active |
| R2. Login flow changed (needs review) | 2 errors + 1 failure | `test_branding...test_every_context_preserves_its_entry_on_logout_and_termination (agency, merchant)`, `test_branding...test_platform_login_logout_and_nonplatform_endpoints_remain_restricted` | `401: A valid authenticated login context is required.`; `303 != 400` (Store login without an operation DB) | session / login-context rules changed after the tests were written; whether the test or the behaviour is right must be confirmed before changing either |
| F. Flaky | 1 failure | `test_master_group_ui...test_md_store_item_features_off_hide_fields_and_keep_saved_values` | `Timed out waiting for UI update` | timing; see "Unstable" |

Totals: S1 19 + S2 7 + S3 2 + R1 5 + R2 3 + F 1 = 37 = 27 failures + 10 errors (the flaky test is one of the
27 failures). No group touches Employee, Map, Master Group CRUD or the Split Layout standard.

## Unstable (flaky) browser tests

- `tools.test_master_group_ui.MasterGroupListBrowserTests.test_md_store_item_features_off_hide_fields_and_keep_saved_values`
  fails intermittently in the full suite (timing) and passes when run alone (2/2 on 2026-11-12). Rerun it in isolation
  before treating it as a regression. It also fails alone at times on committed code (1/6 at `5c16192`, 2026-11-13;
  1/2 on 2026-11-14); the message is always "Timed out waiting for UI update".
- `tools.test_image_components.SharedImageComponentTests.test_local_preview_validation_cancel_form_payload_and_avatar_fallback`
  has failed intermittently in earlier full runs (avatar fallback timing) and passes when rerun; it passed in the
  2026-11-14 run.

## Failures (26)

- `tools.test_agency_activation_browser.AgencyActivationBrowserTests.test_agency_save_never_opens_preview_or_displays_password`
- `tools.test_store_logo.StoreLogoBrowserTests.test_both_store_forms_use_common_pending_preview_cancel_and_save`
- `tools.test_platform_admin_ui.PlatformAdminTemplateTests.test_every_management_module_reuses_single_shared_stylesheet` (page='master_management.html', module='agency')
- `tools.test_platform_admin_ui.PlatformAdminTemplateTests.test_every_management_module_reuses_single_shared_stylesheet` (page='master_management.html', module='agency-type')
- `tools.test_platform_admin_ui.PlatformAdminTemplateTests.test_every_management_module_reuses_single_shared_stylesheet` (page='master_management.html', module='business-type')
- `tools.test_platform_admin_ui.PlatformAdminTemplateTests.test_every_management_module_reuses_single_shared_stylesheet` (page='master_management.html', module='contract')
- `tools.test_platform_admin_ui.PlatformAdminTemplateTests.test_every_management_module_reuses_single_shared_stylesheet` (page='master_management.html', module='device-category')
- `tools.test_platform_admin_ui.PlatformAdminTemplateTests.test_every_management_module_reuses_single_shared_stylesheet` (page='master_management.html', module='device-type')
- `tools.test_platform_admin_ui.PlatformAdminTemplateTests.test_every_management_module_reuses_single_shared_stylesheet` (page='master_management.html', module='invoice')
- `tools.test_platform_admin_ui.PlatformAdminTemplateTests.test_every_management_module_reuses_single_shared_stylesheet` (page='master_management.html', module='license')
- `tools.test_platform_admin_ui.PlatformAdminTemplateTests.test_every_management_module_reuses_single_shared_stylesheet` (page='master_management.html', module='merchant')
- `tools.test_platform_admin_ui.PlatformAdminTemplateTests.test_every_management_module_reuses_single_shared_stylesheet` (page='master_management.html', module='payment-method')
- `tools.test_platform_admin_ui.PlatformAdminTemplateTests.test_every_management_module_reuses_single_shared_stylesheet` (page='master_management.html', module='pricing-plan')
- `tools.test_platform_admin_ui.PlatformAdminTemplateTests.test_every_management_module_reuses_single_shared_stylesheet` (page='master_management.html', module='role')
- `tools.test_platform_admin_ui.PlatformAdminTemplateTests.test_every_management_module_reuses_single_shared_stylesheet` (page='master_management.html', module='store')
- `tools.test_platform_admin_ui.PlatformAdminTemplateTests.test_every_management_module_reuses_single_shared_stylesheet` (page='master_management.html', module='subscription')
- `tools.test_platform_admin_ui.PlatformAdminTemplateTests.test_every_management_module_reuses_single_shared_stylesheet` (page='merchants_tree.html', module='')
- `tools.test_platform_admin_ui.PlatformAdminTemplateTests.test_every_management_module_reuses_single_shared_stylesheet` (page='platform_stores.html', module='')
- `tools.test_platform_admin_ui.PlatformAdminTemplateTests.test_every_management_module_reuses_single_shared_stylesheet` (page='sessions.html', module='session')
- `tools.test_platform_admin_ui.PlatformAdminTemplateTests.test_every_management_module_reuses_single_shared_stylesheet` (page='system_settings.html', module='')
- `tools.test_platform_admin_ui.PlatformAdminTemplateTests.test_every_management_module_reuses_single_shared_stylesheet` (page='users.html', module='user')
- `tools.test_branding.BrandingTests.test_platform_login_logout_and_nonplatform_endpoints_remain_restricted`
- `tools.test_business_codes.BusinessCodeTests.test_registration_entry_points_use_common_codes_and_keep_existing_records` (url='/platform/merchant')
- `tools.test_business_codes.BusinessCodeTests.test_registration_entry_points_use_common_codes_and_keep_existing_records` (url='/platform/merchant/with-image')
- `tools.test_master_form_modal.MasterFormModalTests.test_shared_image_layout_keeps_fields_left_and_uploader_top_right`
- `tools.test_platform_admin_ui.PlatformAdminBrowserTests.test_shared_visuals_and_preserved_layouts`

## Errors (10)

- `tools.test_agency_activation_browser.AgencyActivationBrowserTests.test_agency_form_shows_email_button_only_for_saved_admin`
- `tools.test_storage_initialization.StorageInitializationTests.test_all_registration_entry_points_initialize_only_after_commit`
- `tools.test_money_browser.MoneyBrowserTests.test_current_metadata_controls_serialization_dirty_state_and_sorting`
- `tools.test_branding.BrandingTests.test_every_context_preserves_its_entry_on_logout_and_termination` (context='agency')
- `tools.test_branding.BrandingTests.test_every_context_preserves_its_entry_on_logout_and_termination` (context='merchant')
- `tools.test_money_browser.MoneyBrowserTests.test_md_form_common_money_discount_and_comma_free_multipart`
- `tools.test_master_form_modal.MasterFormModalTests.test_module_modes_scroll_and_existing_actions`
- `tools.test_storage_initialization.StorageInitializationTests.test_registration_disk_failure_keeps_record_and_returns_warning`
- `tools.test_agency_activation_browser.AgencyActivationBrowserTests.test_registration_reuses_email_preview_recipient_edit_and_retry`
- `tools.test_storage_initialization.StorageInitializationTests.test_storage_failure_is_reported_by_agency_merchant_and_both_store_forms` (context='MERCHANT')
