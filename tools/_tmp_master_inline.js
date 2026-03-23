
  const activeModuleRaw = "account";
  const activeModule = activeModuleRaw === 'client' ? 'account' : activeModuleRaw;
  const apiBase = "/platform";

  const masterListHeader = document.getElementById('masterListHeader');
  const masterListBody = document.getElementById('masterListBody');
  const masterListTable = document.getElementById('masterListTable');
  const masterSearch = document.getElementById('masterSearch');
  const masterStatusFilter = document.getElementById('masterStatusFilter');
  const masterForm = document.getElementById('masterForm');
  const masterFormModal = document.getElementById('masterFormModal');
  const selectedMasterId = document.getElementById('selectedMasterId');
  const masterListCol = document.getElementById('masterListCol');
  const masterEditorCol = document.getElementById('masterEditorCol');
  const masterListToolbar = document.getElementById('masterListToolbar');
  const masterListTitle = document.getElementById('masterListTitle');
  const masterEditorTitle = document.getElementById('masterEditorTitle');
  const masterEditorSubtitle = document.getElementById('masterEditorSubtitle');
  const clientDetailHero = document.getElementById('clientDetailHero');
  const clientDetailCode = document.getElementById('clientDetailCode');
  const clientDetailLogo = document.getElementById('clientDetailLogo');
  const clientDetailName = document.getElementById('clientDetailName');
  const clientDetailStatus = document.getElementById('clientDetailStatus');
  const clientDetailHint = document.getElementById('clientDetailHint');
  const storeDetailHero = document.getElementById('storeDetailHero');
  const storeDetailCode = document.getElementById('storeDetailCode');
  const storeDetailName = document.getElementById('storeDetailName');
  const storeDetailStatus = document.getElementById('storeDetailStatus');
  const storeDetailHint = document.getElementById('storeDetailHint');
  const moduleDetailHero = document.getElementById('moduleDetailHero');
  const moduleDetailMeta = document.getElementById('moduleDetailMeta');
  const moduleDetailCode = document.getElementById('moduleDetailCode');
  const moduleDetailName = document.getElementById('moduleDetailName');
  const moduleDetailStatus = document.getElementById('moduleDetailStatus');
  const moduleDetailHint = document.getElementById('moduleDetailHint');
  const masterSortLive = document.getElementById('masterSortLive');
  const deviceSortLive = document.getElementById('deviceSortLive');

  const btnNewRecord = document.getElementById('btnNewRecord');
  const btnSaveRecord = document.getElementById('btnSaveRecord');
  const btnCancelRecord = document.getElementById('btnCancelRecord');
  const btnDeleteRecord = document.getElementById('btnDeleteRecord');
  const btnListAdd = document.getElementById('btnListAdd');
  const btnListEdit = document.getElementById('btnListEdit');
  const btnListDelete = document.getElementById('btnListDelete');
  const btnModalSave = document.getElementById('btnModalSave');
  const btnModalDelete = document.getElementById('btnModalDelete');
  const masterEditModalElement = document.getElementById('masterEditModal');
  const masterEditModalTitle = document.getElementById('masterEditModalTitle');
  const btnExportRecord = document.getElementById('btnExportRecord');
  const btnMasterRefresh = document.getElementById('btnMasterRefresh');
  const exportMenuItems = document.querySelectorAll('[data-export-format]');
  const clientStoreModalElement = document.getElementById('clientStoreModal');
  const clientStoreDeviceModalElement = document.getElementById('clientStoreDeviceModal');
  const appConfirmModalElement = document.getElementById('appConfirmModal');
  const appConfirmModalTitle = document.getElementById('appConfirmModalTitle');
  const appConfirmModalMessage = document.getElementById('appConfirmModalMessage');
  const appConfirmModalOk = document.getElementById('appConfirmModalOk');
  const appConfirmModalCancel = document.getElementById('appConfirmModalCancel');
  const appConfirmModalIcon = document.getElementById('appConfirmModalIcon');
  const clientStoreModalTitle = document.getElementById('clientStoreModalTitle');
  const clientStoreModalHint = document.getElementById('clientStoreModalHint');
  const clientStoreModalClientMeta = document.getElementById('clientStoreModalClientMeta');
  const clientStoreDeviceModalTitle = document.getElementById('clientStoreDeviceModalTitle');

  const masterSeedData = JSON.parse(document.getElementById('masterSeedData')?.textContent || '{}');
  const masterRoleOptions = masterSeedData.roles || [];
  const masterBusinessTypeOptions = masterSeedData.businessTypes || [];
  const masterClientOptions = masterSeedData.clients || [];
  const masterAccountOptions = masterSeedData.accounts || masterClientOptions;  // new name, fallback to legacy
  const masterAgentOptions = masterSeedData.agents || [];
  const masterAgentTypeOptions = masterSeedData.agentTypes || [];
  const masterStoreOptions = masterSeedData.stores || [];

  const masterConfig = {
    role: {
      endpoint: 'role',
      headers: ['ID', 'Name', 'Description', 'Status'],
      fields: [
        { name: 'name', label: 'Name', type: 'text' },
        { name: 'description', label: 'Description', type: 'text' },
        { name: 'status', label: 'Status', type: 'select', options: ['active', 'inactive'] },
      ],
    },
    'business-type': {
      endpoint: 'business-type',
      headers: ['ID', 'Name', 'Description', 'Status'],
      fields: [
        { name: 'name', label: 'Name', type: 'text' },
        { name: 'code', label: 'Code', type: 'text' },
        { name: 'status', label: 'Status', type: 'select', options: ['active', 'inactive'] },
      ],
    },
    'agent-type': {
      endpoint: 'agent-type',
      headers: ['ID', 'Type Code', 'Type Name', 'Status'],
      fields: [
        { name: 'agent_type_code', label: 'Agent Type Code', type: 'text' },
        { name: 'agent_type_name', label: 'Agent Type Name', type: 'text' },
        { name: 'status', label: 'Status', type: 'select', options: ['active', 'inactive'] },
      ],
    },
    account: {
      endpoint: 'client',
      headers: ['ID', 'code', 'Account Name', 'Primary Agent', 'Status'],
      statusIndex: 4,
      fields: [
        { name: 'c_account_code', label: 'Account Code', type: 'text', readonly: true, placeholder: 'Auto-generated on save (CLT_11001)' },
        { name: 'account_name', label: 'Account Name', type: 'text' },
        { name: 'primary_agent_id', label: 'Primary Agent', type: 'select', options: [{ value: '', label: '' }, ...masterAgentOptions] },
        { name: 'business_type', label: 'Business Type', type: 'select', options: ['', ...masterBusinessTypeOptions] },
        { name: 'status', label: 'Status', type: 'select', options: ['active', 'inactive'] },
        { name: 'channel_type', label: 'Channel', type: 'select', options: ['', 'Direct', 'Reseller', 'Partner', 'Online', 'Franchise', 'Internal'] },
        { name: 'first_name', label: 'Contact First Name', type: 'text' },
        { name: 'last_name', label: 'Contact Last Name', type: 'text' },
        { name: 'email', label: 'Email', type: 'email' },
        { name: 'phone', label: 'Phone', type: 'text' },
        { name: 'memo', label: 'Memo', type: 'textarea', rows: 4, colClass: 'col-12' },
        { name: 'address_line1', label: 'Address Line 1', type: 'text', colClass: 'col-12' },
        { name: 'address_line2', label: 'Address Line 2', type: 'text', colClass: 'col-12' },
        { name: 'zip', label: 'ZIP Code', type: 'text', placeholder: '12345 or 12345-6789', pattern: '^\\d{5}(-\\d{4})?$', title: 'Use US ZIP format: 12345 or 12345-6789', colClass: 'col-12 col-md-6 col-lg-3' },
        { name: 'city', label: 'City', type: 'text', colClass: 'col-12 col-md-6 col-lg-3' },
        { name: 'state', label: 'State', type: 'text', colClass: 'col-12 col-md-6 col-lg-3' },
        { name: 'country', label: 'Country', type: 'text', placeholder: 'USA', colClass: 'col-12 col-md-6 col-lg-3' },
      ],
      tabs: [
        { id: 'overview', label: 'Overview', summary: 'Core identity, classification, and lifecycle status.', fields: ['c_account_code', 'account_name', 'primary_agent_id', 'business_type', 'status', 'channel_type'] },
        { id: 'contact', label: 'Contact', summary: 'Primary contact details for day-to-day communication.', fields: ['first_name', 'last_name', 'email', 'phone', 'memo'] },
        { id: 'address', label: 'Address', summary: 'Structured mailing address with US ZIP code support.', fields: ['zip', 'city', 'state', 'country', 'address_line1', 'address_line2'] },
        { id: 'stores', label: 'Stores', summary: 'Manage stores that belong to this account.', fields: [] },
      ],
    },
    // Legacy compatibility
    client: null,  // Will be mapped to 'account' at runtime
    agent: {
      endpoint: 'agent',
      headers: ['Code', 'Agent Type', 'Company', 'Contact Name', 'Phone', 'Commission', 'Status'],
      statusIndex: 6,
      fields: [
        { name: 'agent_code', label: 'Agent Code', type: 'text', readonly: true, placeholder: 'Auto-generated on save' },
        { name: 'agent_type_id', label: 'Agent Type', type: 'select', options: [{ value: '', label: '' }, ...masterAgentTypeOptions] },
        { name: 'company_name', label: 'Company Name', type: 'text', colClass: 'col-12' },
        { name: 'contact_name', label: 'Contact Name', type: 'text', colClass: 'col-12 col-md-6' },
        { name: 'phone', label: 'Phone', type: 'text', colClass: 'col-12 col-md-6' },
        { name: 'email', label: 'Email', type: 'email', colClass: 'col-12 col-md-6' },
        { name: 'commission_rate', label: 'Commission Rate', type: 'number', colClass: 'col-12 col-md-6' },
        { type: 'section', label: 'Address', colClass: 'col-12' },
        { name: 'zip', label: 'Zip Code', type: 'text', placeholder: '12345 or 12345-6789', pattern: '^\\d{5}(-\\d{4})?$', title: 'Use US ZIP format: 12345 or 12345-6789', colClass: 'col-12 col-md-6 col-lg-3' },
        { name: 'city', label: 'City', type: 'text', colClass: 'col-12 col-md-6 col-lg-3' },
        { name: 'state', label: 'State', type: 'text', colClass: 'col-12 col-md-6 col-lg-3' },
        { name: 'country', label: 'Country', type: 'text', colClass: 'col-12 col-md-6 col-lg-3' },
        { name: 'address_line1', label: 'Address Line 1', type: 'text', colClass: 'col-12' },
        { name: 'address_line2', label: 'Address Line 2', type: 'text', colClass: 'col-12' },
        { name: 'memo', label: 'Memo', type: 'textarea', rows: 6, colClass: 'col-12' },
        { name: 'status', label: 'Status', type: 'select', options: ['active', 'inactive'] },
        { name: 'created_at', label: 'Created At', type: 'text', readonly: true, colClass: 'col-12' },
      ],
    },
    license: {
      endpoint: 'license',
      headers: ['ID', 'License Key', 'Plan', 'Store', 'Status'],
      statusIndex: 4,
      fields: [
        { name: 'license_key', label: 'License Key', type: 'text' },
        { name: 'plan_name', label: 'Plan Name', type: 'text' },
        { name: 'license_type', label: 'License Type', type: 'select', options: ['', 'monthly', 'yearly', 'trial', 'enterprise'] },
        { name: 'store_id', label: 'Store', type: 'select', options: [{ value: '', label: '' }, ...masterStoreOptions] },
        { name: 'account_id', label: 'Account', type: 'select', options: [{ value: '', label: '' }, ...masterAccountOptions] },
        { name: 'agent_id', label: 'Agent', type: 'select', options: [{ value: '', label: '' }, ...masterAgentOptions] },
        { name: 'max_devices', label: 'Max Devices', type: 'number' },
        { name: 'max_users', label: 'Max Users', type: 'number' },
        { name: 'start_date', label: 'Start Date', type: 'datetime-local' },
        { name: 'end_date', label: 'End Date', type: 'datetime-local' },
        { name: 'monthly_fee', label: 'Monthly Fee', type: 'number' },
        { name: 'agent_commission', label: 'Agent Commission', type: 'number' },
        { name: 'platform_fee', label: 'Platform Fee', type: 'number' },
        { name: 'status', label: 'Status', type: 'select', options: ['active', 'inactive', 'suspended', 'trial', 'expired'] },
      ],
    },
    'payment-method': {
      endpoint: 'payment-method',
      headers: ['ID', 'Account', 'Payment Type', 'Billing Cycle', 'Next Billing', 'Status'],
      statusIndex: 5,
      fields: [
        { name: 'account_id', label: 'Account', type: 'select', options: [{ value: '', label: '' }, ...masterAccountOptions] },
        { name: 'payment_type', label: 'Payment Type', type: 'select', options: ['card', 'bank_transfer', 'cash', 'other'] },
        { name: 'card_token', label: 'Card Token', type: 'text', colClass: 'col-12' },
        { name: 'bank_account', label: 'Bank Account', type: 'text', colClass: 'col-12' },
        { name: 'billing_cycle', label: 'Billing Cycle', type: 'select', options: ['', 'monthly', 'quarterly', 'yearly', 'manual'] },
        { name: 'next_billing', label: 'Next Billing', type: 'datetime-local' },
        { name: 'status', label: 'Status', type: 'select', options: ['active', 'inactive'] },
      ],
    },
    store: {
      endpoint: 'store',
      headers: ['ID', 'Store Code', 'Name', 'Status'],
      fields: [
        { name: 'store_code', label: 'Store Code', type: 'text', readonly: true, placeholder: 'Auto-generated on save' },
        { name: 'store_name', label: 'Store Name', type: 'text' },
        { name: 'status', label: 'Status', type: 'select', options: ['active', 'inactive'] },
        { name: 'business_type', label: 'Business Type', type: 'select', options: ['', ...masterBusinessTypeOptions] },
        { name: 'operation_type', label: 'Operation Type', type: 'select', options: ['', 'Single Store', 'Branch', 'Franchise', 'HQ', 'Warehouse', 'Online Only', 'Other'] },
        { name: 'contact_name', label: 'Contact Name', type: 'text', colClass: 'col-12' },
        { name: 'email', label: 'Contact Email', type: 'email', colClass: 'col-12 col-md-6' },
        { name: 'phone', label: 'Contact Phone', type: 'text', colClass: 'col-12 col-md-6' },
        { name: 'zip', label: 'ZIP Code', type: 'text', colClass: 'col-12 col-md-6 col-lg-3' },
        { name: 'address_line1', label: 'Address Line 1', type: 'text', colClass: 'col-12' },
        { name: 'address_line2', label: 'Address Line 2', type: 'text', colClass: 'col-12' },
        { name: 'city', label: 'City', type: 'text', colClass: 'col-12 col-md-4' },
        { name: 'state', label: 'State', type: 'text', colClass: 'col-12 col-md-4' },
        { name: 'country', label: 'Country', type: 'text', colClass: 'col-12 col-md-4' },
        { name: 'default_tax_rate', label: 'Sales Tax Rate (%)', type: 'text', placeholder: '0.0000', colClass: 'col-12 col-md-4' },
        { name: 'timezone', label: 'Time Zone', type: 'text', readonly: true, placeholder: 'America/New_York', colClass: 'col-12 col-md-4' },
        { name: 'tax_source', label: 'Tax Source', type: 'text', readonly: true, placeholder: 'auto', colClass: 'col-12 col-md-4' },
        { name: 'receipt_store_name', label: 'Receipt Store Name', type: 'text', colClass: 'col-12' },
        { name: 'receipt_phone', label: 'Receipt Phone', type: 'text', colClass: 'col-12 col-md-6' },
        { name: 'receipt_email', label: 'Receipt Email', type: 'email', colClass: 'col-12 col-md-6' },
        { name: 'receipt_website_url', label: 'Receipt Website URL', type: 'text', colClass: 'col-12' },
        { name: 'receipt_message', label: 'Receipt Message', type: 'textarea', rows: 3, colClass: 'col-12' },
      ],
      tabs: [
        { id: 'basic', label: 'Overview', summary: 'Core store identity and operating profile.', fields: ['store_code', 'store_name', 'status', 'business_type', 'operation_type'] },
        { id: 'contact', label: 'Contact', summary: 'Store contact person and communication details.', fields: ['contact_name', 'email', 'phone'] },
        { id: 'address', label: 'Address', summary: 'Store location details. Auto-filled from address. You can adjust if needed.', fields: ['zip', 'address_line1', 'address_line2', 'city', 'state', 'country', 'default_tax_rate', 'timezone'] },
        { id: 'receipt', label: 'Receipt Info', summary: 'Receipt header and footer information for printed output.', fields: ['receipt_store_name', 'receipt_phone', 'receipt_email', 'receipt_website_url', 'receipt_message'] },
      ],
    },
    user: {
      endpoint: 'user',
      headers: ['ID', 'User', 'Email', 'Role', 'Status'],
      statusIndex: 4,
      fields: [
        { name: 'email', label: 'Email', type: 'email' },
        { name: 'first_name', label: 'First Name', type: 'text' },
        { name: 'last_name', label: 'Last Name', type: 'text' },
        { name: 'store_id', label: 'Store', type: 'select', options: [{ value: '', label: '' }, ...masterStoreOptions] },
        { name: 'role', label: 'Role', type: 'select', options: masterRoleOptions },
        { name: 'status', label: 'Status', type: 'select', options: ['active', 'inactive'] },
      ],
    },
    session: {
      endpoint: 'session',
      headers: ['ID', 'User', 'Store', 'Client', 'Status'],
      statusIndex: 4,
      fields: [
        { name: 'user_email', label: 'User Email', type: 'text', readonly: true, colClass: 'col-12' },
        { name: 'store_name', label: 'Store', type: 'text', readonly: true },
        { name: 'client_name', label: 'Client', type: 'text', readonly: true },
        { name: 'login_at', label: 'Login At', type: 'text', readonly: true, colClass: 'col-12 col-lg-4' },
        { name: 'last_active_at', label: 'Last Active At', type: 'text', readonly: true, colClass: 'col-12 col-lg-4' },
        { name: 'terminated_at', label: 'Terminated At', type: 'text', readonly: true, colClass: 'col-12 col-lg-4' },
        { name: 'status', label: 'Status', type: 'select', options: ['active', 'terminated'] },
      ],
      allowCreate: false,
      deleteLabel: 'Terminate',
      tabs: [
        { id: 'overview', label: 'Overview', summary: 'Session ownership and current state.', fields: ['user_email', 'store_name', 'client_name', 'status'] },
        { id: 'timeline', label: 'Timeline', summary: 'Read-only audit timestamps for this session.', fields: ['login_at', 'last_active_at', 'terminated_at'] },
      ],
    },
  };

  const config = masterConfig[activeModule] || masterConfig.role;
  const isListCentricModule = true;
  const reportMeta = {
    companyName: "AwsumSolution",
    printedBy: "Platform Admin",
  };
  const moduleLabels = {
    role: { list: 'Roles', singular: 'Role' },
    'business-type': { list: 'Business Types', singular: 'Business Type' },
    'agent-type': { list: 'Agent Types', singular: 'Agent Type' },
    account: { list: 'Accounts', singular: 'Account' },
    client: { list: 'Clients', singular: 'Client' },
    agent: { list: 'Agents', singular: 'Agent' },
    license: { list: 'Licenses', singular: 'License' },
    'payment-method': { list: 'Payment Methods', singular: 'Payment Method' },
    store: { list: 'Stores', singular: 'Store' },
    user: { list: 'Users', singular: 'User' },
    session: { list: 'Sessions', singular: 'Session' },
  };
  const activeLabel = moduleLabels[activeModule] || { list: 'Records', singular: 'Record' };

  let currentItems = [];
  let formBaselineSnapshot = null;
  let selectedOriginalRecord = null;
  let clientStoreItems = [];
  let selectedClientStoreId = null;
  let clientStoreDeviceItems = [];
  let selectedClientStoreDeviceId = null;
  let selectedClientStoreDeviceDetail = null;
  let clientStoreDeviceLogs = [];
  let masterSortState = { columnIndex: 0, direction: 'asc' };
  let deviceSortState = { key: 'device_id', direction: 'asc' };
  let suppressClientStoreRowClickUntil = 0;
  let clientStoreModalPinnedPosition = null;
  let clientStoreModalMode = 'add';
  const isReadOnlyModule = config.allowCreate === false;

  async function showCenteredConfirm(message, title = 'Confirm', options = {}) {
    if (!appConfirmModalElement || !window.bootstrap?.Modal || !appConfirmModalOk) {
      return confirm(message);
    }

    const variant = String(options.variant || 'info').toLowerCase();
    const iconMap = { info: '!', warning: '!', danger: '!', success: '\u2713' };
    const iconText = iconMap[variant] || '!';
    const okText = String(options.okText || 'OK');
    const cancelText = String(options.cancelText || 'Cancel');

    appConfirmModalElement.dataset.variant = ['danger', 'warning', 'success'].includes(variant) ? variant : 'info';
    appConfirmModalTitle.textContent = title;
    appConfirmModalMessage.textContent = message;
    if (appConfirmModalIcon) appConfirmModalIcon.textContent = iconText;
    appConfirmModalOk.textContent = okText;
    if (appConfirmModalCancel) appConfirmModalCancel.textContent = cancelText;
    const modal = bootstrap.Modal.getOrCreateInstance(appConfirmModalElement);

    return new Promise((resolve) => {
      let done = false;
      const finalize = (value) => {
        if (done) return;
        done = true;
        appConfirmModalElement.removeEventListener('hidden.bs.modal', onHidden);
        appConfirmModalOk.removeEventListener('click', onOk);
        appConfirmModalElement.dataset.variant = 'info';
        appConfirmModalOk.textContent = 'OK';
        if (appConfirmModalCancel) appConfirmModalCancel.textContent = 'Cancel';
        resolve(value);
      };
      const onHidden = () => finalize(false);
      const onOk = () => {
        finalize(true);
        modal.hide();
      };

      appConfirmModalElement.addEventListener('hidden.bs.modal', onHidden, { once: true });
      appConfirmModalOk.addEventListener('click', onOk, { once: true });
      modal.show();
    });
  }

  function snapshotFormState() {
    const snap = {
      id: String(selectedMasterId.value || ""),
    };
    config.fields.forEach(field => {
      const input = document.getElementById(`field_${field.name}`);
      if (!input) return;
      snap[field.name] = String(input.value ?? "").trim();
    });
    return snap;
  }

  function isFormDirty() {
    if (!formBaselineSnapshot) return false;
    return JSON.stringify(snapshotFormState()) !== JSON.stringify(formBaselineSnapshot);
  }

  function updateActionButtonsState() {
    if (isReadOnlyModule) {
      btnSaveRecord.disabled = !selectedMasterId.value;
      if (btnCancelRecord) btnCancelRecord.disabled = true;
      if (btnModalSave) btnModalSave.disabled = !selectedMasterId.value;
      return;
    }
    const dirty = isFormDirty();
    btnSaveRecord.disabled = !dirty;
    if (btnModalSave) btnModalSave.disabled = !dirty;
    if (btnCancelRecord) btnCancelRecord.disabled = !dirty;
  }

  function updateListCentricActionButtons() {
    if (!isListCentricModule) return;
    const hasSelection = Boolean(String(selectedMasterId.value || '').trim());
    if (btnListEdit) btnListEdit.disabled = !hasSelection;
    if (btnListDelete) btnListDelete.disabled = !hasSelection;
    if (btnModalDelete) btnModalDelete.disabled = !hasSelection;
  }

  function getMasterEditModalInstance() {
    if (!masterEditModalElement || !window.bootstrap?.Modal) return null;
    return bootstrap.Modal.getOrCreateInstance(masterEditModalElement);
  }

  function openMasterEditModal(mode = 'edit') {
    if (!isListCentricModule) return;
    const modal = getMasterEditModalInstance();
    if (!modal) return;
    if (masterEditModalTitle) {
      masterEditModalTitle.textContent = mode === 'create'
        ? `Add ${activeLabel.singular}`
        : `Edit ${activeLabel.singular}`;
    }
    modal.show();
  }

  function openSelectedMasterEditFromList() {
    if (!isListCentricModule) return;
    if (!String(selectedMasterId.value || '').trim()) {
      alert('Please select an item first.');
      return;
    }
    updateSectionTitles('edit');
    openMasterEditModal('edit');
  }

  function closeMasterEditModal() {
    const modal = getMasterEditModalInstance();
    if (!modal) return;
    modal.hide();
  }

  function applyMasterLayoutMode() {
    if (!masterListCol || !masterEditorCol) return;
    if (isListCentricModule) {
      masterListCol.classList.remove('col-xl-3');
      masterListCol.classList.add('col-xl-12');
      masterEditorCol.classList.add('d-none');
      if (masterListToolbar) masterListToolbar.classList.remove('d-none');
      return;
    }

    masterListCol.classList.remove('col-xl-12');
    masterListCol.classList.add('col-xl-3');
    masterEditorCol.classList.remove('d-none');
    if (masterListToolbar) masterListToolbar.classList.add('d-none');
  }

  function configureMasterStatusFilter() {
    if (!masterStatusFilter) return;
    const optionSets = {
      session: ['', 'active', 'terminated'],
      license: ['', 'active', 'inactive', 'suspended', 'trial', 'expired'],
      'payment-method': ['', 'active', 'inactive'],
      default: ['', 'active', 'inactive'],
    };
    const values = optionSets[activeModule] || optionSets.default;
    const current = String(masterStatusFilter.value || '');

    masterStatusFilter.innerHTML = values.map((value) => {
      const label = value ? value : 'All Status';
      return `<option value="${value}">${label}</option>`;
    }).join('');

    masterStatusFilter.value = values.includes(current) ? current : '';
  }

  function applyListCentricLabels() {
    if (!isListCentricModule) return;

    if (btnListAdd) btnListAdd.textContent = `Add ${activeLabel.singular}`;
    if (btnListEdit) btnListEdit.textContent = 'Edit';
    if (btnListDelete) btnListDelete.textContent = config.deleteLabel || 'Delete';
    if (btnModalSave) btnModalSave.textContent = 'Save';
    if (btnModalDelete) btnModalDelete.textContent = config.deleteLabel || 'Delete';
    if (masterEditModalTitle) masterEditModalTitle.textContent = `Edit ${activeLabel.singular}`;
  }

  function setBaselineSnapshot() {
    formBaselineSnapshot = snapshotFormState();
    updateActionButtonsState();
  }

  function attachFormChangeWatchers() {
    const watched = [];
    const idInput = document.getElementById('field_record_id');
    if (idInput) watched.push(idInput);
    config.fields.forEach(field => {
      const input = document.getElementById(`field_${field.name}`);
      if (input) watched.push(input);
    });
    watched.forEach(input => {
      const eventName = input.tagName === 'SELECT' ? 'change' : 'input';
      input.addEventListener(eventName, updateActionButtonsState);
    });

    if (activeModule === 'account') {
      bindZipAutoFill('field_zip', 'field_city', 'field_state', 'field_country');
    }
    if (activeModule === 'store') {
      bindZipAutoFill('field_zip', 'field_city', 'field_state', 'field_country', 'field_default_tax_rate', 'field_timezone', 'field_tax_source');
      bindTaxSourceManualOverride('field_default_tax_rate', 'field_tax_source');
    }
    if (activeModule === 'agent-type') {
      bindAgentTypeCodeValidation();
    }
  }

  function normalizeAgentTypeCode(raw) {
    return String(raw || '')
      .toUpperCase()
      .replace(/\s+/g, '_')
      .replace(/[^A-Z0-9_]/g, '')
      .trim();
  }

  function isDuplicateAgentTypeCode(code) {
    const normalized = normalizeAgentTypeCode(code);
    if (!normalized) return false;

    const currentId = String(selectedMasterId.value || '').trim();
    return (currentItems || []).some((item) => {
      const rowCode = normalizeAgentTypeCode(item?.agent_type_code || item?.code || '');
      const rowId = String(item?.id ?? item?.store_id ?? item?.i_store_id ?? '').trim();
      if (!rowCode) return false;
      if (currentId && rowId === currentId) return false;
      return rowCode === normalized;
    });
  }

  function applyAgentTypeCodeValidation() {
    if (activeModule !== 'agent-type') return true;
    const codeInput = document.getElementById('field_agent_type_code');
    if (!codeInput) return true;

    const normalized = normalizeAgentTypeCode(codeInput.value);
    if (codeInput.value !== normalized) {
      codeInput.value = normalized;
    }

    if (!normalized) {
      codeInput.setCustomValidity('Agent Type Code is required.');
      return false;
    }

    if (isDuplicateAgentTypeCode(normalized)) {
      codeInput.setCustomValidity('Agent Type Code already exists.');
      return false;
    }

    codeInput.setCustomValidity('');
    return true;
  }

  function bindAgentTypeCodeValidation() {
    const codeInput = document.getElementById('field_agent_type_code');
    if (!codeInput || codeInput.dataset.boundAgentTypeValidation === '1') return;
    codeInput.dataset.boundAgentTypeValidation = '1';

    codeInput.addEventListener('input', () => {
      applyAgentTypeCodeValidation();
      updateActionButtonsState();
    });

    codeInput.addEventListener('blur', () => {
      const ok = applyAgentTypeCodeValidation();
      if (!ok) codeInput.reportValidity();
    });
  }

  const STATE_DEFAULT_TAX_RATE = {
    AL: '4.0000', AK: '0.0000', AZ: '5.6000', AR: '6.5000', CA: '7.2500', CO: '2.9000', CT: '6.3500', DE: '0.0000',
    FL: '6.0000', GA: '4.0000', HI: '4.0000', ID: '6.0000', IL: '6.2500', IN: '7.0000', IA: '6.0000', KS: '6.5000',
    KY: '6.0000', LA: '4.4500', ME: '5.5000', MD: '6.0000', MA: '6.2500', MI: '6.0000', MN: '6.8750', MS: '7.0000',
    MO: '4.2250', MT: '0.0000', NE: '5.5000', NV: '6.8500', NH: '0.0000', NJ: '6.6250', NM: '5.1250', NY: '4.0000',
    NC: '4.7500', ND: '5.0000', OH: '5.7500', OK: '4.5000', OR: '0.0000', PA: '6.0000', RI: '7.0000', SC: '6.0000',
    SD: '4.5000', TN: '7.0000', TX: '6.2500', UT: '6.1000', VT: '6.0000', VA: '5.3000', WA: '6.5000', WV: '6.0000',
    WI: '5.0000', WY: '4.0000', DC: '6.0000'
  };

  const STATE_TIMEZONE = {
    AL: 'America/Chicago', AK: 'America/Anchorage', AZ: 'America/Phoenix', AR: 'America/Chicago', CA: 'America/Los_Angeles',
    CO: 'America/Denver', CT: 'America/New_York', DE: 'America/New_York', FL: 'America/New_York', GA: 'America/New_York',
    HI: 'Pacific/Honolulu', ID: 'America/Denver', IL: 'America/Chicago', IN: 'America/Indiana/Indianapolis', IA: 'America/Chicago',
    KS: 'America/Chicago', KY: 'America/New_York', LA: 'America/Chicago', ME: 'America/New_York', MD: 'America/New_York',
    MA: 'America/New_York', MI: 'America/Detroit', MN: 'America/Chicago', MS: 'America/Chicago', MO: 'America/Chicago',
    MT: 'America/Denver', NE: 'America/Chicago', NV: 'America/Los_Angeles', NH: 'America/New_York', NJ: 'America/New_York',
    NM: 'America/Denver', NY: 'America/New_York', NC: 'America/New_York', ND: 'America/Chicago', OH: 'America/New_York',
    OK: 'America/Chicago', OR: 'America/Los_Angeles', PA: 'America/New_York', RI: 'America/New_York', SC: 'America/New_York',
    SD: 'America/Chicago', TN: 'America/Chicago', TX: 'America/Chicago', UT: 'America/Denver', VT: 'America/New_York',
    VA: 'America/New_York', WA: 'America/Los_Angeles', WV: 'America/New_York', WI: 'America/Chicago', WY: 'America/Denver',
    DC: 'America/New_York'
  };

  function applyAddressDerivedDefaults(stateId, countryId, taxRateId, timezoneId, taxSourceId, hintId) {
    const stateInput = document.getElementById(stateId);
    const countryInput = document.getElementById(countryId);
    const taxRateInput = taxRateId ? document.getElementById(taxRateId) : null;
    const timezoneInput = timezoneId ? document.getElementById(timezoneId) : null;
    const taxSourceInput = taxSourceId ? document.getElementById(taxSourceId) : null;
    const hintEl = hintId ? document.getElementById(hintId) : null;

    if (!stateInput || !countryInput || !taxRateInput || !timezoneInput || !taxSourceInput) return;

    const country = String(countryInput.value || '').trim().toUpperCase();
    const state = String(stateInput.value || '').trim().toUpperCase();
    if (!state || (country && country !== 'USA' && country !== 'US')) return;
    if (String(taxSourceInput.value || '').toLowerCase() === 'manual') return;

    const defaultTax = STATE_DEFAULT_TAX_RATE[state];
    const timezone = STATE_TIMEZONE[state];
    if (!defaultTax || !timezone) return;

    taxRateInput.dataset.autoUpdating = '1';
    taxRateInput.value = defaultTax;
    taxRateInput.dataset.autoUpdating = '0';
    timezoneInput.value = timezone;
    taxSourceInput.value = 'auto';
    if (hintEl) {
      hintEl.textContent = 'Auto-filled from address. You can adjust if needed.';
    }
    updateActionButtonsState();
  }

  function bindTaxSourceManualOverride(taxRateId, taxSourceId) {
    const taxRateInput = document.getElementById(taxRateId);
    const taxSourceInput = document.getElementById(taxSourceId);
    if (!taxRateInput || !taxSourceInput || taxRateInput.dataset.taxManualBound === '1') return;
    taxRateInput.dataset.taxManualBound = '1';
    taxRateInput.addEventListener('input', () => {
      if (taxRateInput.dataset.autoUpdating === '1') return;
      taxSourceInput.value = 'manual';
      updateActionButtonsState();
    });
  }

  function bindZipAutoFill(zipId, cityId, stateId, countryId, taxRateId, timezoneId, taxSourceId, hintId) {
    const zipInput = document.getElementById(zipId);
    if (!zipInput || zipInput.dataset.zipAutofillBound === '1') return;
    zipInput.dataset.zipAutofillBound = '1';
      const handleZipLookup = () => autoFillLocationFromZip(zipId, cityId, stateId, countryId, taxRateId, timezoneId, taxSourceId, hintId);
      zipInput.addEventListener('blur', handleZipLookup);
      zipInput.addEventListener('change', handleZipLookup);
      zipInput.addEventListener('keydown', event => {
        if (event.key !== 'Enter') return;
        event.preventDefault();
        handleZipLookup();
      });

      const stateInput = document.getElementById(stateId);
      const countryInput = document.getElementById(countryId);
      const applyDefaults = () => applyAddressDerivedDefaults(stateId, countryId, taxRateId, timezoneId, taxSourceId, hintId);
      if (stateInput && stateInput.dataset.stateAutofillBound !== '1') {
        stateInput.dataset.stateAutofillBound = '1';
        stateInput.addEventListener('blur', applyDefaults);
        stateInput.addEventListener('change', applyDefaults);
      }
      if (countryInput && countryInput.dataset.countryAutofillBound !== '1') {
        countryInput.dataset.countryAutofillBound = '1';
        countryInput.addEventListener('blur', applyDefaults);
        countryInput.addEventListener('change', applyDefaults);
      }
  }

  async function autoFillLocationFromZip(zipId, cityId, stateId, countryId, taxRateId, timezoneId, taxSourceId, hintId) {
    const zipInput = document.getElementById(zipId);
    const cityInput = document.getElementById(cityId);
    const stateInput = document.getElementById(stateId);
    const countryInput = document.getElementById(countryId);

      if (!zipInput || !cityInput || !stateInput) return;

    const rawZip = String(zipInput.value || '').trim();
    if (!rawZip) return;

    if (!/^\d{5}(-\d{4})?$/.test(rawZip)) {
      alert('Invalid ZIP code. Use US format: 12345 or 12345-6789.');
      return;
    }

    const lookupZip = rawZip.slice(0, 5);

    try {
      const res = await fetch(`https://api.zippopotam.us/us/${encodeURIComponent(lookupZip)}`);
      if (!res.ok) {
        alert('ZIP code not found. Please check and try again.');
        return;
      }

      const data = await res.json();
      const place = data?.places?.[0];
      if (!place) {
        alert('ZIP code lookup failed. Please enter City/State manually.');
        return;
      }

      cityInput.value = place['place name'] || '';
      stateInput.value = place['state abbreviation'] || '';
      if (countryInput) countryInput.value = 'USA';
      applyAddressDerivedDefaults(stateId, countryId, taxRateId, timezoneId, taxSourceId, hintId);
      updateActionButtonsState();
    } catch (err) {
      alert('Unable to fetch ZIP code information right now. Please try again.');
    }
  }

  function cancelEdit() {
    if (isReadOnlyModule) return;
    if (selectedMasterId.value && selectedOriginalRecord) {
      populateForm(selectedOriginalRecord);
      setBaselineSnapshot();
      return;
    }
    clearForm();
  }

  function updateSectionTitles(mode) {
    masterListTitle.textContent = activeLabel.list;
    masterEditorTitle.textContent = mode === 'create' ? `Create ${activeLabel.singular}` : `Edit ${activeLabel.singular}`;
    masterEditorSubtitle.textContent = mode === 'create'
      ? `Add a new ${activeLabel.singular.toLowerCase()} record.`
      : `Update the selected ${activeLabel.singular.toLowerCase()} record.`;

    if (activeModule === 'account') {
      masterEditorTitle.classList.add('d-none');
      masterEditorSubtitle.classList.add('d-none');
      if (clientDetailHero) clientDetailHero.classList.add('active');
      if (storeDetailHero) storeDetailHero.classList.remove('active');
      if (moduleDetailHero) moduleDetailHero.classList.remove('active');
      return;
    }

    if (activeModule === 'store') {
      masterEditorTitle.classList.add('d-none');
      masterEditorSubtitle.classList.add('d-none');
      if (storeDetailHero) storeDetailHero.classList.add('active');
      if (clientDetailHero) clientDetailHero.classList.remove('active');
      if (moduleDetailHero) moduleDetailHero.classList.remove('active');
      return;
    }

    masterEditorTitle.classList.add('d-none');
    masterEditorSubtitle.classList.add('d-none');
    if (moduleDetailHero) moduleDetailHero.classList.add('active');
    if (clientDetailHero) clientDetailHero.classList.remove('active');
    if (storeDetailHero) storeDetailHero.classList.remove('active');
    return;
  }

  function renderClientDetailHero(mode, item) {
    if (activeModule !== 'account') return;

    const statusValue = String(item?.status || (mode === 'edit' ? 'inactive' : 'pending')).toLowerCase();
    const statusClass = statusValue === 'active' ? 'active' : (statusValue === 'inactive' ? 'inactive' : 'pending');

    if (clientDetailCode) {
      clientDetailCode.textContent = item?.c_client_code || item?.client_code || 'CLT_----';
    }
    if (clientDetailLogo) {
      const logoUrl = buildClientLogoUrl(item?.c_client_code || item?.client_code || '');
      clientDetailLogo.src = `${logoUrl}?t=${Date.now()}`;
    }
    if (clientDetailName) {
      clientDetailName.textContent = item?.client_name || 'New Client';
    }
    if (clientDetailStatus) {
      clientDetailStatus.className = `client-header-status ${statusClass}`;
      clientDetailStatus.textContent = statusValue;
    }
    if (clientDetailHint) {
      clientDetailHint.textContent = mode === 'edit' ? 'Edit Client' : 'Create Client';
    }
  }

  function renderStoreDetailHero(mode, item) {
    if (activeModule !== 'store') return;

    const statusValue = String(item?.status || item?.store_status || (mode === 'edit' ? 'inactive' : 'pending')).toLowerCase();
    const statusClass = statusValue === 'active' ? 'active' : (statusValue === 'inactive' ? 'inactive' : 'pending');

    if (storeDetailCode) {
      storeDetailCode.textContent = item?.store_code || 'STR_----';
    }
    if (storeDetailName) {
      storeDetailName.textContent = item?.store_name || 'New Store';
    }
    if (storeDetailStatus) {
      storeDetailStatus.className = `store-header-status ${statusClass}`;
      storeDetailStatus.textContent = statusValue;
    }
    if (storeDetailHint) {
      storeDetailHint.textContent = mode === 'edit' ? 'Edit Store' : 'Create Store';
    }
  }

  function renderModuleDetailHero(mode, item) {
    if (activeModule === 'account' || activeModule === 'store') return;

    const statusRaw = item?.status || (activeModule === 'payment-method' ? 'active' : (mode === 'edit' ? 'inactive' : 'pending'));
    const statusValue = String(statusRaw).toLowerCase();
    const statusClass = statusValue === 'active'
      ? 'active'
      : ((statusValue === 'inactive' || statusValue === 'terminated') ? statusValue : 'pending');

    const metaByModule = {
      role: item?.name || 'Role',
      'business-type': item?.code || 'Business Type',
      'agent-type': item?.agent_type_code || 'Agent Type',
      agent: item?.company_name || 'Agent Partner',
      license: item?.license_type || 'License',
      'payment-method': item?.payment_type || 'Payment Method',
      user: 'User Account',
      session: 'Session Log',
    };

    const resolvedId = item?.id ?? item?.store_id ?? item?.i_store_id ?? '';
    const numericId = Number.parseInt(String(resolvedId || '0'), 10);
    const pad = (value) => String(Number.isFinite(value) ? Math.max(0, value) : 0).padStart(4, '0');

    const codeByModule = {
      role: `ROLE_${pad(numericId)}`,
      'business-type': `BTYPE_${pad(numericId)}`,
      'agent-type': item?.agent_type_code || `ATYPE_${pad(numericId)}`,
      agent: item?.agent_code || `AGT_${pad(numericId)}`,
      license: item?.license_key || `LIC_${pad(numericId)}`,
      'payment-method': `PAY_${pad(numericId)}`,
      user: `USER_${pad(numericId)}`,
      session: `SESS_${pad(numericId)}`,
    };

    const nameByModule = {
      role: item?.name || 'New Role',
      'business-type': item?.name || 'New Business Type',
      'agent-type': item?.agent_type_name || 'New Agent Type',
      agent: item?.contact_name || 'New Agent Contact',
      license: item?.plan_name || item?.license_key || 'New License',
      'payment-method': item?.client_name || 'New Payment Method',
      user: (`${item?.first_name || ''} ${item?.last_name || ''}`.trim() || item?.email || 'New User'),
      session: [item?.store_name || '', item?.client_name || ''].filter(Boolean).join(' / ') || 'Session Record',
    };

    if (moduleDetailMeta) {
      moduleDetailMeta.textContent = String(metaByModule[activeModule] || activeLabel.singular || 'Record');
    }
    if (moduleDetailCode) {
      moduleDetailCode.textContent = codeByModule[activeModule] || 'MOD_----';
    }
    if (moduleDetailName) {
      moduleDetailName.textContent = nameByModule[activeModule] || `New ${activeLabel.singular}`;
    }
    if (moduleDetailStatus) {
      moduleDetailStatus.className = `module-header-status ${statusClass}`;
      moduleDetailStatus.textContent = statusValue;
    }
    if (moduleDetailHint) {
      moduleDetailHint.textContent = mode === 'edit' ? `Edit ${activeLabel.singular}` : `Create ${activeLabel.singular}`;
    }
  }

  function getRowValues(item) {
    const resolvedId = item.id ?? item.store_id ?? item.i_store_id ?? '';
    const name = item.name || item.client_name || item.store_name || item.email || item.user_email || `${item.first_name || ''} ${item.last_name || ''}`.trim() || '';
    const description = item.description || item.code || item.role || item.business_type || item.operation_type || item.channel_type || item.store_name || item.store_code || item.client_name || item.store_name || '';
    if (activeModule === 'role') {
      return [resolvedId, item.name || '', item.description || '', item.status || ''];
    }
    if (activeModule === 'business-type') {
      return [resolvedId, item.name || '', item.code || '', item.status || ''];
    }
    if (activeModule === 'agent-type') {
      return [resolvedId, item.agent_type_code || '', item.agent_type_name || '', item.status || ''];
    }
    if (activeModule === 'account') {
      return [resolvedId, item.c_client_code || item.client_code || '', item.client_name || '', item.primary_agent_name || '', item.status || ''];
    }
    if (activeModule === 'agent') {
      const commissionText = item.commission_rate === null || item.commission_rate === undefined || item.commission_rate === ''
        ? ''
        : `${item.commission_rate}%`;
      return [
        item.agent_code || '',
        item.agent_type_name || '',
        item.company_name || '',
        item.contact_name || '',
        item.phone || '',
        commissionText,
        item.status || '',
      ];
    }
    if (activeModule === 'license') {
      return [resolvedId, item.license_key || '', item.plan_name || item.license_type || '', item.store_name || '', item.status || ''];
    }
    if (activeModule === 'payment-method') {
      return [resolvedId, item.client_name || '', item.payment_type || '', item.billing_cycle || '', item.next_billing || '', item.status || ''];
    }
    if (activeModule === 'store') {
      return [resolvedId, item.store_code || '', item.store_name || '', item.status || ''];
    }
    if (activeModule === 'user') {
      const fullName = `${item.first_name || ''} ${item.last_name || ''}`.trim() || item.email || '';
      return [resolvedId, fullName, item.email || '', item.role || '', item.status || ''];
    }
    if (activeModule === 'session') {
      return [resolvedId, item.user_email || '', item.store_name || '', item.client_name || '', item.status || ''];
    }
    return [resolvedId, name, description, item.status || ''];
  }

  function getReportRowValues(item) {
    const resolvedId = item.id ?? item.store_id ?? item.i_store_id ?? '';
    const name = item.name || item.contact_name || item.agent_name || item.client_name || item.store_name || item.email || item.user_email || `${item.first_name || ''} ${item.last_name || ''}`.trim() || '';
    const description = item.description || item.license_key || item.payment_type || item.agent_code || item.code || item.role || item.business_type || item.operation_type || item.channel_type || '';
    return [resolvedId, name, description, item.status || ''];
  }

  function toComparableValue(value) {
    const raw = value ?? '';
    const text = String(raw).trim();
    if (!text) return { type: 'empty', value: '' };

    const asNumber = Number(text);
    if (!Number.isNaN(asNumber) && /^-?\d+(\.\d+)?$/.test(text)) {
      return { type: 'number', value: asNumber };
    }

    const asDate = Date.parse(text);
    if (!Number.isNaN(asDate) && /\d{4}-\d{2}-\d{2}/.test(text)) {
      return { type: 'date', value: asDate };
    }

    return { type: 'text', value: text.toLowerCase() };
  }

  function compareComparableValues(left, right) {
    if (left.type === 'empty' && right.type === 'empty') return 0;
    if (left.type === 'empty') return -1;
    if (right.type === 'empty') return 1;

    if (left.type === right.type) {
      if (left.value < right.value) return -1;
      if (left.value > right.value) return 1;
      return 0;
    }

    const leftText = String(left.value);
    const rightText = String(right.value);
    return leftText.localeCompare(rightText, undefined, { numeric: true, sensitivity: 'base' });
  }

  function getSortedMasterItems(items) {
    const directionMultiplier = masterSortState.direction === 'asc' ? 1 : -1;
    const columnIndex = masterSortState.columnIndex;
    const firstHeader = String((config.headers || [])[0] || '').trim().toLowerCase();
    const isIdFirstColumn = firstHeader === 'id';

    return [...(items || [])].sort((a, b) => {
      if (columnIndex === 0 && isIdFirstColumn) {
        const leftId = Number(a?.id ?? a?.store_id ?? a?.i_store_id ?? 0);
        const rightId = Number(b?.id ?? b?.store_id ?? b?.i_store_id ?? 0);
        if (leftId === rightId) return 0;
        return (leftId < rightId ? -1 : 1) * directionMultiplier;
      }

      const leftRowValues = getRowValues(a);
      const rightRowValues = getRowValues(b);
      const leftComparable = toComparableValue(leftRowValues[columnIndex]);
      const rightComparable = toComparableValue(rightRowValues[columnIndex]);
      const result = compareComparableValues(leftComparable, rightComparable);
      return result * directionMultiplier;
    });
  }

  function updateMasterSortIndicators() {
    const headers = masterListHeader.querySelectorAll('th');
    headers.forEach((th, idx) => {
      const indicator = th.querySelector('.sort-indicator');
      th.classList.toggle('is-sorted', idx === masterSortState.columnIndex);
      th.setAttribute(
        'aria-sort',
        idx === masterSortState.columnIndex
          ? (masterSortState.direction === 'asc' ? 'ascending' : 'descending')
          : 'none'
      );
      if (!indicator) return;
      if (idx === masterSortState.columnIndex) {
        indicator.textContent = masterSortState.direction === 'asc' ? 'ASC' : 'DESC';
      } else {
        indicator.textContent = '';
      }
    });
  }

  function toggleMasterSort(columnIndex) {
    if (masterSortState.columnIndex === columnIndex) {
      masterSortState.direction = masterSortState.direction === 'asc' ? 'desc' : 'asc';
    } else {
      masterSortState.columnIndex = columnIndex;
      masterSortState.direction = 'asc';
    }
    updateMasterSortIndicators();
    announceMasterSortChange();
    renderList(currentItems, selectedMasterId.value || null);
  }

  function announceMasterSortChange() {
    if (!masterSortLive) return;
    const headers = config.headers || [];
    const label = headers[masterSortState.columnIndex] || 'Column';
    const direction = masterSortState.direction === 'asc' ? 'ascending' : 'descending';
    masterSortLive.textContent = `${label} sorted ${direction}`;
  }

  function renderListHeader() {
    masterListHeader.innerHTML = '';
    const statusIdx = (typeof config.statusIndex === 'number') ? config.statusIndex : (config.headers.length - 1);
    config.headers.forEach((col, idx) => {
      const th = document.createElement('th');
      th.classList.add('sortable-col');
      th.innerHTML = `${col}<span class="sort-indicator"></span>`;
      th.setAttribute('tabindex', '0');
      th.setAttribute('role', 'button');
      th.setAttribute('aria-label', `${col} sort`);
      th.setAttribute('aria-sort', 'none');
      if (idx === 0) {
        th.classList.add('role-col-id');
      }
      if (idx === statusIdx) {
        th.classList.add('status-col');
      }
      if (activeModule === 'store' && idx === 3) {
        th.classList.add('store-col-status');
      }
      if (activeModule === 'account' && idx === statusIdx) {
        th.classList.add('client-col-status');
      }
      if (activeModule === 'user' && idx === 1) th.classList.add('user-col-name');
      if (activeModule === 'user' && idx === 2) th.classList.add('user-col-email');
      if (activeModule === 'user' && idx === 3) th.classList.add('user-col-role');
      if (activeModule === 'session' && idx === 1) th.classList.add('session-col-user');
      if (activeModule === 'session' && idx === 2) th.classList.add('session-col-store');
      if (activeModule === 'session' && idx === 3) th.classList.add('session-col-client');
      th.addEventListener('click', () => toggleMasterSort(idx));
      th.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        toggleMasterSort(idx);
      });
      masterListHeader.appendChild(th);
    });
    updateMasterSortIndicators();
  }

  function buildQuery() {
    const params = new URLSearchParams();
    if (masterSearch.value.trim()) params.set('q', masterSearch.value.trim());
    if (masterStatusFilter && masterStatusFilter.value) params.set('status', masterStatusFilter.value);
    return params.toString();
  }

  let lastSavedId = null;

  async function loadList(highlightId = null) {
    const query = buildQuery();
    const url = `${apiBase}/${config.endpoint}s?${query}`;
    try {
      const res = await fetch(url, {
        cache: 'no-store',
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
      });

      if (!res.ok) {
        throw new Error(`Request failed (${res.status})`);
      }

      const contentType = (res.headers.get('content-type') || '').toLowerCase();
      if (!contentType.includes('application/json')) {
        throw new Error('Session expired or invalid response. Please sign in again.');
      }

      const items = await res.json();
      renderList(items, highlightId);
    } catch (error) {
      console.error('Failed to load master list:', error);
      masterListBody.innerHTML = `<tr><td class="text-center text-danger py-4" colspan="${config.headers.length}">Failed to load data. Refresh or sign in again.</td></tr>`;
    }
  }

  function renderList(items, highlightId) {
    currentItems = items || [];
    masterListBody.innerHTML = '';
    if (!items || items.length === 0) {
      masterListBody.innerHTML = `<tr><td class="text-center text-muted py-4" colspan="${config.headers.length}">No items found.</td></tr>`;
      return;
    }

    const targetHighlightId = highlightId || selectedMasterId.value || null;
    const sortedItems = getSortedMasterItems(items);
    const firstHeader = String((config.headers || [])[0] || '').trim().toLowerCase();
    const isIdFirstColumn = firstHeader === 'id';

    sortedItems.forEach(item => {
      const tr = document.createElement('tr');
      const resolvedId = item.id ?? item.store_id ?? item.i_store_id ?? '';
      tr.dataset.itemId = resolvedId;
      tr.classList.add('cursor-pointer');

      const statusIdx = (typeof config.statusIndex === 'number') ? config.statusIndex : (config.headers.length - 1);
      const cells = getRowValues(item).map((value, idx) => {
        if (idx === 0 && isIdFirstColumn) {
          const n = Number.parseInt(String(resolvedId || "0"), 10);
          const roleIdLabel = Number.isFinite(n)
            ? `#${String(Math.max(0, n)).padStart(3, '0')}`
            : `#${String(resolvedId || '').padStart(3, '0')}`;
          if (activeModule === 'store') {
            return `<td class="role-col-id"><span class="store-row-id">${roleIdLabel}</span></td>`;
          }
          if (activeModule === 'account') {
            return `<td class="role-col-id"><span class="client-row-id">${roleIdLabel}</span></td>`;
          }
          return `<td class="role-col-id"><span class="role-row-id">${roleIdLabel}</span></td>`;
        }
        if (activeModule === 'store' && idx === 1) {
          return `<td><span class="store-row-code">${value ?? ''}</span></td>`;
        }
        if (activeModule === 'store' && idx === 2) {
          return `<td><span class="store-row-name">${value ?? ''}</span></td>`;
        }
        if (activeModule === 'store' && idx === 3) {
          const normalized = String(value ?? '').toLowerCase() === 'active' ? 'active' : 'inactive';
          return `<td class="store-col-status"><span class="store-status-badge ${normalized}">${normalized}</span></td>`;
        }
        if (activeModule === 'account' && idx === 1) {
          return `<td><span class="client-row-code">${value ?? ''}</span></td>`;
        }
        if (activeModule === 'account' && idx === 2) {
          return `<td><span class="client-row-name">${value ?? ''}</span></td>`;
        }
        if (activeModule === 'account' && idx === statusIdx) {
          const normalized = String(value ?? '').toLowerCase() === 'active' ? 'active' : 'inactive';
          return `<td class="client-col-status"><span class="client-status-badge ${normalized}">${normalized}</span></td>`;
        }
        if (idx === statusIdx) {
          const lowered = String(value ?? '').toLowerCase();
          const normalized = (lowered === 'active' || lowered === 'inactive' || lowered === 'terminated') ? lowered : 'pending';
          return `<td class="status-col"><span class="module-status-badge ${normalized}">${lowered || normalized}</span></td>`;
        }
        if (activeModule === 'user' && idx === 1) {
          return `<td class="user-col-name"><span class="cell-ellipsis">${value ?? ''}</span></td>`;
        }
        if (activeModule === 'user' && idx === 2) {
          return `<td class="user-col-email"><span class="cell-ellipsis user-email-text">${value ?? ''}</span></td>`;
        }
        if (activeModule === 'user' && idx === 3) {
          return `<td class="user-col-role"><span class="cell-ellipsis">${value ?? ''}</span></td>`;
        }
        if (activeModule === 'session' && idx === 1) {
          return `<td class="session-col-user"><span class="cell-ellipsis">${value ?? ''}</span></td>`;
        }
        if (activeModule === 'session' && idx === 2) {
          return `<td class="session-col-store"><span class="cell-ellipsis">${value ?? ''}</span></td>`;
        }
        if (activeModule === 'session' && idx === 3) {
          return `<td class="session-col-client"><span class="cell-ellipsis">${value ?? ''}</span></td>`;
        }
        if (idx === 1) {
          return `<td><span class="role-row-title">${value ?? ''}</span></td>`;
        }
        return `<td>${value ?? ''}</td>`;
      });

      tr.innerHTML = cells.join('');
      tr.addEventListener('click', () => selectItem(item, tr));
      tr.addEventListener('dblclick', () => {
        selectItem(item, tr);
        openSelectedMasterEditFromList();
      });
      masterListBody.appendChild(tr);

      if (targetHighlightId && String(resolvedId) === String(targetHighlightId)) {
        selectItem(item, tr);
      }
    });
  }

  function createField(field) {
    const wrapper = document.createElement('div');
    wrapper.className = field.colClass || 'col-12 col-md-6';

    if (field.type === 'section') {
      const title = document.createElement('div');
      title.className = 'master-form-section-title';
      title.textContent = field.label || '';
      wrapper.appendChild(title);
      return wrapper;
    }

    const label = document.createElement('label');
    label.className = 'form-label';
    label.textContent = field.label;

    let input;
    if (field.type === 'select') {
      input = document.createElement('select');
      input.className = 'form-select';
      field.options.forEach(opt => {
        const o = document.createElement('option');
        if (typeof opt === 'object') {
          o.value = opt.value;
          o.textContent = opt.label;
        } else {
          o.value = opt;
          o.textContent = opt;
        }
        input.appendChild(o);
      });
      if (field.disabled) {
        input.disabled = true;
      }
    } else if (field.type === 'textarea') {
      input = document.createElement('textarea');
      input.className = 'form-control';
      input.rows = field.rows || 3;
      if (field.readonly) {
        input.readOnly = true;
      }
      if (field.placeholder) {
        input.placeholder = field.placeholder;
      }
    } else {
      input = document.createElement('input');
      input.type = field.type;
      input.className = 'form-control';
      if (field.readonly) {
        input.readOnly = true;
      }
      if (field.placeholder) {
        input.placeholder = field.placeholder;
      }
      if (field.pattern) {
        input.pattern = field.pattern;
      }
      if (field.title) {
        input.title = field.title;
      }
    }
    input.id = `field_${field.name}`;

    wrapper.appendChild(label);
    wrapper.appendChild(input);
    return wrapper;
  }

  function createIdField() {
    const wrapper = document.createElement('div');
    wrapper.className = 'col-12 col-md-6 id-field-wrap';

    const label = document.createElement('label');
    label.className = 'form-label';
    label.textContent = 'ID';

    const input = document.createElement('input');
    input.type = 'text';
    input.id = 'field_record_id';
    input.className = 'form-control';
    input.readOnly = true;
    input.placeholder = 'Assigned automatically';

    wrapper.appendChild(label);
    wrapper.appendChild(input);
    return wrapper;
  }

  function createClientStoresPanel() {
    const wrapper = document.createElement('div');
    wrapper.className = 'client-store-panel';

    wrapper.innerHTML = `
      <div class="client-store-panel-head">
        <h6 class="client-store-title">Stores</h6>
        <button type="button" id="btnClientStoreAdd" class="btn btn-sm btn-outline-secondary role-action-btn">Add Store</button>
      </div>

      <div id="clientStoreList" class="client-store-list">
        <div class="text-muted px-2 py-2 small">Select a client to load stores.</div>
      </div>

      <div id="clientStorePanelHint" class="text-muted small mb-2">Store is auto-linked to selected Client.</div>
    `;

    return wrapper;
  }

  function normalizeBrandCode(value) {
    return String(value || '').trim().toUpperCase().replace(/[^A-Z0-9_-]/g, '');
  }

  function buildClientLogoUrl(clientCode) {
    const normalizedClientCode = normalizeBrandCode(clientCode);
    if (!normalizedClientCode) return '/static/images/platform/icon.png';
    return `/static/images/brands/clients/${encodeURIComponent(normalizedClientCode)}/logo.png`;
  }

  function buildStoreLogoUrl(clientCode, storeCode) {
    const normalizedClientCode = normalizeBrandCode(clientCode);
    const normalizedStoreCode = normalizeBrandCode(storeCode);
    if (!normalizedClientCode || !normalizedStoreCode) return '/static/images/platform/icon.png';
    return `/static/images/brands/clients/${encodeURIComponent(normalizedClientCode)}/stores/${encodeURIComponent(normalizedStoreCode)}/logo.png`;
  }

  function swapLogoWithFade(imageEl, baseUrl) {
    if (!imageEl || !baseUrl) return;
    const nextSrc = `${baseUrl}?t=${Date.now()}`;
    imageEl.classList.add('logo-fade-out');
    window.setTimeout(() => {
      imageEl.src = nextSrc;
      imageEl.classList.remove('logo-fade-out');
    }, 110);
  }

  function createBrandLogoUploadPanel(kind) {
    const col = document.createElement('div');
    col.className = 'col-12';

    if (kind === 'client') {
      col.innerHTML = `
        <div class="brand-logo-upload-card">
          <div class="brand-logo-upload-head">
            <h6 class="brand-logo-upload-title">Client Logo (Dashboard)</h6>
            <span class="text-muted small">PNG/JPG/WebP</span>
          </div>
          <div class="d-flex flex-column flex-md-row gap-3 align-items-start">
            <img id="clientLogoPreview" class="brand-logo-preview" src="/static/images/platform/icon.png" alt="Client logo preview">
            <div class="w-100">
              <div class="brand-file-picker mb-2">
                <input id="clientLogoFile" type="file" class="d-none" accept="image/*">
                <button type="button" id="btnBrowseClientLogoFile" class="btn btn-sm btn-outline-secondary role-action-btn">Choose File</button>
                <span id="clientLogoFileName" class="brand-file-name">No file selected</span>
              </div>
              <div class="d-flex gap-2 flex-wrap">
                <button type="button" id="btnUploadClientLogo" class="btn btn-sm btn-outline-secondary role-action-btn">Upload Client Logo</button>
                <button type="button" id="btnResetClientLogo" class="btn btn-sm btn-outline-danger role-action-btn">Reset Logo</button>
              </div>
              <div id="clientLogoPathHint" class="brand-logo-hint mt-2">Path: /static/images/brands/clients/CLT_XXXXX/logo.png</div>
            </div>
          </div>
        </div>
      `;
      return col;
    }

    col.innerHTML = `
      <div class="brand-logo-upload-card">
        <div class="brand-logo-upload-head">
          <h6 class="brand-logo-upload-title">Store Logo (Dashboard)</h6>
          <span class="text-muted small">PNG/JPG/WebP</span>
        </div>
        <div class="d-flex flex-column flex-md-row gap-3 align-items-start">
          <img id="storeLogoPreview" class="brand-logo-preview" src="/static/images/platform/icon.png" alt="Store logo preview">
          <div class="w-100">
            <div class="brand-file-picker mb-2">
              <input id="storeLogoFile" type="file" class="d-none" accept="image/*">
              <button type="button" id="btnBrowseStoreLogoFile" class="btn btn-sm btn-outline-secondary role-action-btn">Choose File</button>
              <span id="storeLogoFileName" class="brand-file-name">No file selected</span>
            </div>
            <div class="d-flex gap-2 flex-wrap">
              <button type="button" id="btnUploadStoreLogo" class="btn btn-sm btn-outline-secondary role-action-btn">Upload Store Logo</button>
              <button type="button" id="btnResetStoreLogo" class="btn btn-sm btn-outline-danger role-action-btn">Reset Logo</button>
            </div>
            <div id="storeLogoPathHint" class="brand-logo-hint mt-2">Path: /static/images/brands/clients/CLT_XXXXX/stores/STR_XXXXX/logo.png</div>
          </div>
        </div>
      </div>
    `;
    return col;
  }

  function refreshClientLogoPreview() {
    const preview = document.getElementById('clientLogoPreview');
    const hint = document.getElementById('clientLogoPathHint');
    if (!preview || !hint) return;
    const clientCode = normalizeBrandCode(document.getElementById('field_c_client_code')?.value || '');
    const baseUrl = buildClientLogoUrl(clientCode);
    swapLogoWithFade(preview, baseUrl);
    if (clientDetailLogo) {
      swapLogoWithFade(clientDetailLogo, baseUrl);
    }
    hint.textContent = clientCode
      ? `Path: /static/images/brands/clients/${clientCode}/logo.png`
      : 'Save Client first to generate code (CLT_XXXXX)';
  }

  function refreshStoreLogoPreview(item = null) {
    const preview = document.getElementById('storeLogoPreview');
    const hint = document.getElementById('storeLogoPathHint');
    if (!preview || !hint) return;

    const source = item || selectedOriginalRecord || {};
    const clientCode = normalizeBrandCode(source.client_code || source.c_client_code || '');
    const storeCode = normalizeBrandCode(document.getElementById('field_store_code')?.value || source.store_code || '');
    const baseUrl = buildStoreLogoUrl(clientCode, storeCode);
    swapLogoWithFade(preview, baseUrl);
    hint.textContent = (clientCode && storeCode)
      ? `Path: /static/images/brands/clients/${clientCode}/stores/${storeCode}/logo.png`
      : 'Select a saved Store linked to Client to enable logo path.';
  }

  function refreshClientStoreModalLogoPreview(item = null) {
    const preview = document.getElementById('clientStoreLogoPreview');
    const hint = document.getElementById('clientStoreLogoPathHint');
    const fileInput = document.getElementById('clientStoreLogoFile');
    const browseBtn = document.getElementById('btnBrowseClientStoreLogoFile');
    const fileName = document.getElementById('clientStoreLogoFileName');
    const uploadBtn = document.getElementById('btnUploadClientStoreLogo');
    const resetBtn = document.getElementById('btnResetClientStoreLogo');
    if (!preview || !hint) return;

    const currentItem = item
      || clientStoreItems.find((store) => String(store.store_id) === String(selectedClientStoreId))
      || {};
    const { clientCode } = getSelectedClientContext();
    const normalizedClientCode = normalizeBrandCode(currentItem.client_code || clientCode || '');
    const storeCode = normalizeBrandCode(document.getElementById('field_client_store_code')?.value || currentItem.store_code || '');
    const hasSavedStore = Boolean(String(selectedClientStoreId || '').trim() && normalizedClientCode && storeCode);

    swapLogoWithFade(preview, buildStoreLogoUrl(normalizedClientCode, storeCode));
    hint.textContent = hasSavedStore
      ? `Path: /static/images/brands/clients/${normalizedClientCode}/stores/${storeCode}/logo.png`
      : 'Save Store first to enable logo path.';

    if (fileInput) {
      fileInput.disabled = !hasSavedStore;
      if (!hasSavedStore) fileInput.value = '';
    }
    if (fileName && !hasSavedStore) fileName.textContent = 'No file selected';
    if (browseBtn) browseBtn.disabled = !hasSavedStore;
    if (uploadBtn) uploadBtn.disabled = !hasSavedStore;
    if (resetBtn) resetBtn.disabled = !hasSavedStore;
  }

  function bindEnglishFilePicker(fileInputId, browseButtonId, fileNameId) {
    const fileInput = document.getElementById(fileInputId);
    const browseButton = document.getElementById(browseButtonId);
    const fileName = document.getElementById(fileNameId);
    if (!fileInput || !browseButton || !fileName) return;

    const syncName = () => {
      fileName.textContent = fileInput.files && fileInput.files.length > 0
        ? fileInput.files[0].name
        : 'No file selected';
    };

    if (browseButton.dataset.bound !== '1') {
      browseButton.dataset.bound = '1';
      browseButton.addEventListener('click', () => {
        if (browseButton.disabled || fileInput.disabled) return;
        fileInput.click();
      });
    }

    if (fileInput.dataset.boundName !== '1') {
      fileInput.dataset.boundName = '1';
      fileInput.addEventListener('change', syncName);
    }

    syncName();
  }

  function bindBrandLogoUploadHandlers() {
    bindEnglishFilePicker('clientLogoFile', 'btnBrowseClientLogoFile', 'clientLogoFileName');
    bindEnglishFilePicker('storeLogoFile', 'btnBrowseStoreLogoFile', 'storeLogoFileName');
    bindEnglishFilePicker('clientStoreLogoFile', 'btnBrowseClientStoreLogoFile', 'clientStoreLogoFileName');

    const clientBtn = document.getElementById('btnUploadClientLogo');
    if (clientBtn && clientBtn.dataset.bound !== '1') {
      clientBtn.dataset.bound = '1';
      clientBtn.addEventListener('click', async () => {
        const clientId = getSelectedClientId();
        if (!clientId) {
          alert('Please save the Client first to upload a logo.');
          return;
        }
        const fileInput = document.getElementById('clientLogoFile');
        const file = fileInput?.files?.[0];
        if (!file) {
          alert('Choose an image file first.');
          return;
        }

        const formData = new FormData();
        formData.append('file', file);
        const res = await fetch(`${apiBase}/client/${clientId}/logo`, {
          method: 'POST',
          body: formData,
          credentials: 'same-origin',
        });
        if (res.status === 401) {
          alert('Session expired. Please refresh the page and login again.');
          return;
        }
        if (!res.ok) {
          const text = await res.text();
          alert('Client logo upload failed: ' + text);
          return;
        }
        fileInput.value = '';
        refreshClientLogoPreview();
        alert('Client logo uploaded successfully.');
      });
    }

    const resetClientBtn = document.getElementById('btnResetClientLogo');
    if (resetClientBtn && resetClientBtn.dataset.bound !== '1') {
      resetClientBtn.dataset.bound = '1';
      resetClientBtn.addEventListener('click', async () => {
        const clientId = getSelectedClientId();
        if (!clientId) {
          alert('Please save the Client first to reset the logo.');
          return;
        }
        if (!(await showCenteredConfirm('Reset the Client logo to the default image?', 'Reset Logo', { variant: 'warning', okText: 'Reset', cancelText: 'No' }))) return;
        const res = await fetch(`${apiBase}/client/${clientId}/logo`, {
          method: 'DELETE',
          credentials: 'same-origin',
        });
        if (res.status === 401) {
          alert('Session expired. Please refresh the page and login again.');
          return;
        }
        if (!res.ok) {
          const text = await res.text();
          alert('Unable to reset the Client logo: ' + text);
          return;
        }
        refreshClientLogoPreview();
        alert('Client logo reset successfully.');
      });
    }

    const storeBtn = document.getElementById('btnUploadStoreLogo');
    if (storeBtn && storeBtn.dataset.bound !== '1') {
      storeBtn.dataset.bound = '1';
      storeBtn.addEventListener('click', async () => {
        const storeId = String(selectedMasterId.value || '').trim();
        if (!storeId) {
          alert('Please select and save the Store first to upload a logo.');
          return;
        }
        const fileInput = document.getElementById('storeLogoFile');
        const file = fileInput?.files?.[0];
        if (!file) {
          alert('Choose an image file first.');
          return;
        }

        const formData = new FormData();
        formData.append('file', file);
        const res = await fetch(`${apiBase}/store/${encodeURIComponent(storeId)}/logo`, {
          method: 'POST',
          body: formData,
          credentials: 'same-origin',
        });
        if (res.status === 401) {
          alert('Session expired. Please refresh the page and login again.');
          return;
        }
        if (!res.ok) {
          const text = await res.text();
          alert('Unable to upload the Store logo: ' + text);
          return;
        }
        fileInput.value = '';
        refreshStoreLogoPreview();
        alert('Store logo uploaded successfully.');
      });
    }

    const resetStoreBtn = document.getElementById('btnResetStoreLogo');
    if (resetStoreBtn && resetStoreBtn.dataset.bound !== '1') {
      resetStoreBtn.dataset.bound = '1';
      resetStoreBtn.addEventListener('click', async () => {
        const storeId = String(selectedMasterId.value || '').trim();
        if (!storeId) {
          alert('Please select and save the Store first to reset the logo.');
          return;
        }
        if (!(await showCenteredConfirm('Reset the Store logo to the default image?', 'Reset Logo', { variant: 'warning', okText: 'Reset', cancelText: 'No' }))) return;
        const res = await fetch(`${apiBase}/store/${encodeURIComponent(storeId)}/logo`, {
          method: 'DELETE',
          credentials: 'same-origin',
        });
        if (res.status === 401) {
          alert('Session expired. Please refresh the page and login again.');
          return;
        }
        if (!res.ok) {
          const text = await res.text();
          alert('Unable to reset the Store logo: ' + text);
          return;
        }
        refreshStoreLogoPreview();
        alert('Store logo reset successfully.');
      });
    }
  }

  function bindClientStoreModalLogoHandlers() {
    const uploadBtn = document.getElementById('btnUploadClientStoreLogo');
    if (uploadBtn && uploadBtn.dataset.bound !== '1') {
      uploadBtn.dataset.bound = '1';
      uploadBtn.addEventListener('click', async () => {
        const storeId = String(selectedClientStoreId || document.getElementById('field_client_store_id')?.value || '').trim();
        if (!storeId) {
          alert('Please save the Store first to upload a logo.');
          return;
        }

        const fileInput = document.getElementById('clientStoreLogoFile');
        const file = fileInput?.files?.[0];
        if (!file) {
          alert('Choose an image file first.');
          return;
        }

        const formData = new FormData();
        formData.append('file', file);
        const res = await fetch(`${apiBase}/store/${encodeURIComponent(storeId)}/logo`, {
          method: 'POST',
          body: formData,
          credentials: 'same-origin',
        });
        if (res.status === 401) {
          alert('Session expired. Please refresh the page and login again.');
          return;
        }
        if (!res.ok) {
          const text = await res.text();
          alert('Unable to upload the Store logo: ' + text);
          return;
        }

        fileInput.value = '';
        refreshClientStoreModalLogoPreview();
        alert('Store logo uploaded successfully.');
      });
    }

    const resetBtn = document.getElementById('btnResetClientStoreLogo');
    if (resetBtn && resetBtn.dataset.bound !== '1') {
      resetBtn.dataset.bound = '1';
      resetBtn.addEventListener('click', async () => {
        const storeId = String(selectedClientStoreId || document.getElementById('field_client_store_id')?.value || '').trim();
        if (!storeId) {
          alert('Please save the Store first to reset the logo.');
          return;
        }
        if (!(await showCenteredConfirm('Reset the Store logo to the default image?', 'Reset Logo', { variant: 'warning', okText: 'Reset', cancelText: 'No' }))) return;

        const res = await fetch(`${apiBase}/store/${encodeURIComponent(storeId)}/logo`, {
          method: 'DELETE',
          credentials: 'same-origin',
        });
        if (res.status === 401) {
          alert('Session expired. Please refresh the page and login again.');
          return;
        }
        if (!res.ok) {
          const text = await res.text();
          alert('Unable to reset the Store logo: ' + text);
          return;
        }

        refreshClientStoreModalLogoPreview();
        alert('Store logo reset successfully.');
      });
    }
  }

  function getSelectedClientId() {
    const value = String(selectedMasterId.value || '').trim();
    if (!value) return null;
    const parsed = Number.parseInt(value, 10);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function getSelectedClientContext() {
    const clientId = getSelectedClientId();
    const nameInput = document.getElementById('field_client_name');
    const codeInput = document.getElementById('field_c_client_code');
    const clientName = String(nameInput?.value || '').trim();
    const clientCode = String(codeInput?.value || '').trim();
    const isReadyForStoreRegistration = Boolean(clientId && clientCode);
    return { clientId, clientName, clientCode, isReadyForStoreRegistration };
  }

  function getClientStoreModalInstance() {
    if (!clientStoreModalElement || !window.bootstrap?.Modal) return null;
    return bootstrap.Modal.getOrCreateInstance(clientStoreModalElement);
  }

  function getClientStoreDeviceModalInstance() {
    if (!clientStoreDeviceModalElement || !window.bootstrap?.Modal) return null;
    return bootstrap.Modal.getOrCreateInstance(clientStoreDeviceModalElement);
  }

  function openClientStoreDeviceEditor(mode = 'edit') {
    const modal = getClientStoreDeviceModalInstance();
    if (!modal) return;

    const normalizedMode = mode === 'add' ? 'add' : 'edit';
    if (clientStoreDeviceModalTitle) {
      clientStoreDeviceModalTitle.textContent = normalizedMode === 'add' ? 'Add Device' : 'Edit Device';
    }
    setClientStoreDeviceInfoTab('detail');
    modal.show();

    window.setTimeout(() => {
      const editorNameInput = document.getElementById('field_client_store_device_name');
      if (!editorNameInput) return;
      editorNameInput.focus();
      if (normalizedMode === 'edit') editorNameInput.select();
    }, 120);
  }

  function lockClientStoreModalPosition() {
    const dialog = clientStoreModalElement?.querySelector('.modal-dialog');
    if (!dialog) return;
    dialog.classList.add('position-locked');
    clientStoreModalPinnedPosition = { locked: true };
  }

  function reapplyClientStoreModalPosition() {
    const dialog = clientStoreModalElement?.querySelector('.modal-dialog');
    if (!dialog || !clientStoreModalPinnedPosition) return;
    dialog.classList.add('position-locked');
  }

  function resetClientStoreModalPosition() {
    const dialog = clientStoreModalElement?.querySelector('.modal-dialog');
    if (!dialog) return;
    dialog.style.left = '';
    dialog.style.top = '';
    dialog.style.width = '';
    dialog.style.transform = '';
    dialog.style.marginTop = '';
    dialog.classList.remove('position-locked');
    clientStoreModalPinnedPosition = null;
  }

  function applyClientStoreModalInitialOffset() {
    // ?대룞 湲곕뒫 鍮꾪솢?깊솕: 珥덇린 ?ㅽ봽??誘몄궗??
    return;
  }

  function initClientStoreModalDrag() {
    // ?대룞 湲곕뒫 鍮꾪솢?깊솕
    return;
  }

  function setClientStoreModalTab(tabId) {
    const tabsWrap = document.getElementById('clientStoreModalTabs');
    const formWrap = document.getElementById('clientStoreForm');
    if (!tabsWrap || !formWrap) return;

    tabsWrap.querySelectorAll('[data-store-modal-tab]').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.storeModalTab === tabId);
    });
    formWrap.querySelectorAll('[data-store-modal-panel]').forEach((panel) => {
      panel.classList.toggle('active', panel.dataset.storeModalPanel === tabId);
    });

    const modalBody = clientStoreModalElement?.querySelector('.modal-body');
    if (modalBody) {
      modalBody.scrollTop = 0;
    }
    formWrap.scrollTop = 0;

    window.requestAnimationFrame(() => {
      reapplyClientStoreModalPosition();
    });

    if (tabId === 'devices' && selectedClientStoreId) {
      loadClientStoreDevices();
    }
  }

  function initClientStoreModalTabs() {
    const tabsWrap = document.getElementById('clientStoreModalTabs');
    if (!tabsWrap || tabsWrap.dataset.boundTabs === '1') return;
    tabsWrap.dataset.boundTabs = '1';
    tabsWrap.querySelectorAll('[data-store-modal-tab]').forEach((btn) => {
      btn.addEventListener('click', () => {
        setClientStoreModalTab(btn.dataset.storeModalTab || 'basic');
      });
    });

    if (clientStoreModalElement && !clientStoreModalElement.dataset.boundPositionLock) {
      clientStoreModalElement.dataset.boundPositionLock = '1';
      clientStoreModalElement.addEventListener('show.bs.modal', () => {
        lockClientStoreModalPosition();
      });
      clientStoreModalElement.addEventListener('shown.bs.modal', () => {
        window.requestAnimationFrame(() => {
          reapplyClientStoreModalPosition();
        });
      });
      clientStoreModalElement.addEventListener('hidden.bs.modal', () => {
        resetClientStoreModalPosition();
      });
    }
  }

  function updateClientStoreModalChrome(mode) {
    clientStoreModalMode = mode;
    setClientStoreModalTab('basic');
    if (clientStoreModalTitle) {
      clientStoreModalTitle.textContent = mode === 'edit' ? 'Edit Store' : 'Add Store';
    }
    if (clientStoreModalClientMeta) {
      const { clientName, clientCode, isReadyForStoreRegistration } = getSelectedClientContext();
      clientStoreModalClientMeta.textContent = isReadyForStoreRegistration
        ? `Client: ${clientName || 'Unnamed Client'} (${clientCode})`
        : 'Client: Save Client first';
    }
    if (clientStoreModalHint) {
      const { clientId, clientName, clientCode, isReadyForStoreRegistration } = getSelectedClientContext();
      clientStoreModalHint.textContent = isReadyForStoreRegistration
        ? `Store will be linked to ${clientName || 'Client'} ${clientCode} (ID ${clientId}).`
        : 'Save Client first to enable Store registration';
    }
  }

  function setClientStoreFormVisible(isVisible) {
    const modal = getClientStoreModalInstance();
    if (!modal) return;
    if (isVisible) {
      modal.show();
      return;
    }
    modal.hide();
  }

  function resetClientStoreEditor() {
    selectedClientStoreId = null;
    const fields = {
      id: document.getElementById('field_client_store_id'),
      code: document.getElementById('field_client_store_code'),
      name: document.getElementById('field_client_store_name'),
      status: document.getElementById('field_client_store_status'),
      business_type: document.getElementById('field_client_store_business_type'),
      operation_type: document.getElementById('field_client_store_operation_type'),
      contact_name: document.getElementById('field_client_store_contact_name'),
      email: document.getElementById('field_client_store_email'),
      phone: document.getElementById('field_client_store_phone'),
      zip: document.getElementById('field_client_store_zip'),
      address_line1: document.getElementById('field_client_store_address_line1'),
      address_line2: document.getElementById('field_client_store_address_line2'),
      city: document.getElementById('field_client_store_city'),
      state: document.getElementById('field_client_store_state'),
      country: document.getElementById('field_client_store_country'),
      default_tax_rate: document.getElementById('field_client_store_default_tax_rate'),
      timezone: document.getElementById('field_client_store_timezone'),
      tax_source: document.getElementById('field_client_store_tax_source'),
      receipt_store_name: document.getElementById('field_client_store_receipt_store_name'),
      receipt_phone: document.getElementById('field_client_store_receipt_phone'),
      receipt_email: document.getElementById('field_client_store_receipt_email'),
      receipt_website_url: document.getElementById('field_client_store_receipt_website_url'),
      receipt_message: document.getElementById('field_client_store_receipt_message'),
      memo: document.getElementById('field_client_store_memo'),
      installed_by_agent_id: document.getElementById('field_client_store_installed_by_agent_id'),
    };
    if (fields.id) fields.id.value = '';
    if (fields.code) fields.code.value = '';
    if (fields.name) fields.name.value = '';
    if (fields.status) fields.status.value = 'active';
    if (fields.business_type) fields.business_type.value = '';
    if (fields.operation_type) fields.operation_type.value = '';
    if (fields.contact_name) fields.contact_name.value = '';
    if (fields.email) fields.email.value = '';
    if (fields.phone) fields.phone.value = '';
    if (fields.zip) fields.zip.value = '';
    if (fields.address_line1) fields.address_line1.value = '';
    if (fields.address_line2) fields.address_line2.value = '';
    if (fields.city) fields.city.value = '';
    if (fields.state) fields.state.value = '';
    if (fields.country) fields.country.value = '';
    if (fields.default_tax_rate) fields.default_tax_rate.value = '';
    if (fields.timezone) fields.timezone.value = '';
    if (fields.tax_source) fields.tax_source.value = 'auto';
    if (fields.receipt_store_name) fields.receipt_store_name.value = '';
    if (fields.receipt_phone) fields.receipt_phone.value = '';
    if (fields.receipt_email) fields.receipt_email.value = '';
    if (fields.receipt_website_url) fields.receipt_website_url.value = '';
    if (fields.receipt_message) fields.receipt_message.value = '';
    if (fields.memo) fields.memo.value = '';
    if (fields.installed_by_agent_id) fields.installed_by_agent_id.value = '';
    resetClientStoreDeviceEditor();
    refreshClientStoreModalLogoPreview();
  }

  function resetClientStoreDeviceEditor() {
    selectedClientStoreDeviceId = null;
    selectedClientStoreDeviceDetail = null;
    clientStoreDeviceItems = [];
    clientStoreDeviceLogs = [];

    const idInput = document.getElementById('field_client_store_device_id');
    const nameInput = document.getElementById('field_client_store_device_name');
    const typeInput = document.getElementById('field_client_store_device_type');
    const statusInput = document.getElementById('field_client_store_device_status');
    const installedByAgentInput = document.getElementById('field_client_store_device_installed_by_agent_id');
    const noteInput = document.getElementById('field_client_store_device_note');
    const hintEl = document.getElementById('clientStoreDevicesHint');
    const listEl = document.getElementById('clientStoreDeviceList');
    const saveBtn = document.getElementById('btnClientStoreDeviceSave');
    const deleteBtn = document.getElementById('btnClientStoreDeviceDelete');

    if (idInput) idInput.value = '';
    if (nameInput) nameInput.value = '';
    if (typeInput) typeInput.value = 'POS';
    if (statusInput) statusInput.value = 'active';
    if (installedByAgentInput) installedByAgentInput.value = '';
    if (noteInput) noteInput.value = '';
    if (hintEl) hintEl.textContent = 'Save Store first to manage devices.';
    if (saveBtn) saveBtn.disabled = true;
    if (deleteBtn) deleteBtn.disabled = true;
    if (listEl) {
      listEl.innerHTML = '<div class="text-muted px-2 py-2 small">No devices loaded.</div>';
    }
    setClientStoreDeviceInfoTab('detail');
    renderClientStoreDeviceDetail();
  }

  function setClientStoreDeviceInfoTab(tabId) {
    const tabsWrap = document.getElementById('clientStoreDeviceInfoTabs');
    const validTab = tabId === 'logs' ? 'logs' : 'detail';
    if (!tabsWrap) return;

    tabsWrap.querySelectorAll('[data-device-info-tab]').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.deviceInfoTab === validTab);
    });

    document.querySelectorAll('[data-device-info-panel]').forEach((panel) => {
      panel.classList.toggle('active', panel.dataset.deviceInfoPanel === validTab);
    });
  }

  function initClientStoreDeviceInfoTabs() {
    const tabsWrap = document.getElementById('clientStoreDeviceInfoTabs');
    if (!tabsWrap || tabsWrap.dataset.boundTabs === '1') return;
    tabsWrap.dataset.boundTabs = '1';

    tabsWrap.querySelectorAll('[data-device-info-tab]').forEach((btn) => {
      btn.addEventListener('click', () => {
        setClientStoreDeviceInfoTab(btn.dataset.deviceInfoTab || 'detail');
      });
    });

    setClientStoreDeviceInfoTab('detail');
  }

  function escapeClientStoreDeviceHtml(value) {
    return String(value ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function formatClientStoreDeviceDateTime(value) {
    if (!value) return '-';
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? '-' : parsed.toLocaleString();
  }

  function populateClientStoreDeviceEditor(device) {
    const idInput = document.getElementById('field_client_store_device_id');
    const nameInput = document.getElementById('field_client_store_device_name');
    const typeInput = document.getElementById('field_client_store_device_type');
    const statusInput = document.getElementById('field_client_store_device_status');
    const installedByAgentInput = document.getElementById('field_client_store_device_installed_by_agent_id');
    const noteInput = document.getElementById('field_client_store_device_note');
    const saveBtn = document.getElementById('btnClientStoreDeviceSave');
    const deleteBtn = document.getElementById('btnClientStoreDeviceDelete');

    if (idInput) idInput.value = String(device?.device_id || '');
    if (nameInput) nameInput.value = device?.device_name || '';
    if (typeInput) typeInput.value = device?.device_type || 'POS';
    if (statusInput) statusInput.value = String(device?.status || 'active').toLowerCase();
    if (installedByAgentInput) installedByAgentInput.value = device?.installed_by_agent_id ? String(device.installed_by_agent_id) : '';
    if (noteInput) noteInput.value = device?.note || '';
    if (saveBtn) saveBtn.disabled = !selectedClientStoreId;
    if (deleteBtn) deleteBtn.disabled = !selectedClientStoreDeviceId;
  }

  function renderClientStoreDeviceDetail() {
    const detailBody = document.getElementById('clientStoreDeviceDetailBody');
    const logBody = document.getElementById('clientStoreDeviceLogBody');
    if (!detailBody || !logBody) return;

    if (!selectedClientStoreDeviceId || !selectedClientStoreDeviceDetail) {
      detailBody.innerHTML = '<div class="store-device-detail-empty">Select a device from the list to view detail.</div>';
      logBody.innerHTML = '<tr><td colspan="7" class="store-device-log-empty">Select a device from the list to view logs.</td></tr>';
      return;
    }

    const device = selectedClientStoreDeviceDetail;
    const badgeClass = (value, fallback = 'inactive') => String(value || fallback).trim().toLowerCase().replace(/\s+/g, '-');

    detailBody.innerHTML = `
      <div class="store-device-detail-summary">
        <div>
          <div class="store-device-detail-name">${escapeClientStoreDeviceHtml(device.device_name || 'Unnamed Device')}</div>
          <div class="store-device-detail-code">${escapeClientStoreDeviceHtml(device.device_code || '-')} 쨌 ${escapeClientStoreDeviceHtml(device.device_type || 'Unknown')}</div>
        </div>
        <div class="store-device-badge-group">
          <span class="device-license-badge ${badgeClass(device.license_status, 'unassigned')}">${escapeClientStoreDeviceHtml(device.license_status || 'Unassigned')}</span>
          <span class="device-state-badge ${badgeClass(device.status, 'inactive')}">${escapeClientStoreDeviceHtml(String(device.status || 'inactive').replace(/_/g, ' ').replace(/\b\w/g, (ch) => ch.toUpperCase()))}</span>
        </div>
      </div>
      <div class="store-device-detail-fields">
        <div class="store-device-detail-item">
          <div class="store-device-detail-label">Installed By Agent</div>
          <div class="store-device-detail-value">${escapeClientStoreDeviceHtml(device.installed_by_agent_name || '-')}</div>
        </div>
        <div class="store-device-detail-item">
          <div class="store-device-detail-label">Activation Code</div>
          <div class="store-device-detail-value">${escapeClientStoreDeviceHtml(device.activation_code || '-')}</div>
        </div>
        <div class="store-device-detail-item">
          <div class="store-device-detail-label">First Activated</div>
          <div class="store-device-detail-value">${escapeClientStoreDeviceHtml(formatClientStoreDeviceDateTime(device.first_activated_at))}</div>
        </div>
        <div class="store-device-detail-item">
          <div class="store-device-detail-label">Activated By</div>
          <div class="store-device-detail-value">${escapeClientStoreDeviceHtml(device.activated_by || '-')}</div>
        </div>
        <div class="store-device-detail-item">
          <div class="store-device-detail-label">Bound Hardware ID</div>
          <div class="store-device-detail-value">${escapeClientStoreDeviceHtml(device.bound_hardware_id || '-')}</div>
        </div>
        <div class="store-device-detail-item">
          <div class="store-device-detail-label">Last IP</div>
          <div class="store-device-detail-value">${escapeClientStoreDeviceHtml(device.last_ip || '-')}</div>
        </div>
        <div class="store-device-detail-item">
          <div class="store-device-detail-label">Last Seen</div>
          <div class="store-device-detail-value">${escapeClientStoreDeviceHtml(formatClientStoreDeviceDateTime(device.last_seen))}</div>
        </div>
        <div class="store-device-detail-item">
          <div class="store-device-detail-label">OS</div>
          <div class="store-device-detail-value">${escapeClientStoreDeviceHtml(device.os || '-')}</div>
        </div>
        <div class="store-device-detail-item">
          <div class="store-device-detail-label">Version</div>
          <div class="store-device-detail-value">${escapeClientStoreDeviceHtml(device.app_version || '-')}</div>
        </div>
        <div class="store-device-detail-item full">
          <div class="store-device-detail-label">Note</div>
          <div class="store-device-detail-value">${escapeClientStoreDeviceHtml(device.note || '-')}</div>
        </div>
      </div>
    `;

    if (!clientStoreDeviceLogs.length) {
      logBody.innerHTML = '<tr><td colspan="7" class="text-muted text-center py-3">No event logs for this device.</td></tr>';
      return;
    }

    logBody.innerHTML = clientStoreDeviceLogs.map((log) => {
      const runtime = [log.os, log.version].filter(Boolean).join(' / ') || '-';
      const badgeClassName = badgeClass(log.event_type, 'updated');
      return `
        <tr>
          <td><span class="device-event-badge ${badgeClassName}">${escapeClientStoreDeviceHtml(log.event_type || 'UPDATED')}</span></td>
          <td>${escapeClientStoreDeviceHtml(formatClientStoreDeviceDateTime(log.event_time))}</td>
          <td>${escapeClientStoreDeviceHtml(log.hardware_id || '-')}</td>
          <td>${escapeClientStoreDeviceHtml(log.ip_address || '-')}</td>
          <td>${escapeClientStoreDeviceHtml(runtime)}</td>
          <td>${escapeClientStoreDeviceHtml(log.action_by || '-')}</td>
          <td>${escapeClientStoreDeviceHtml(log.note || '-')}</td>
        </tr>
      `;
    }).join('');
  }

  async function loadClientStoreDeviceDetail(deviceId) {
    const { clientId } = getSelectedClientContext();
    if (!clientId || !selectedClientStoreId || !deviceId) {
      selectedClientStoreDeviceDetail = null;
      clientStoreDeviceLogs = [];
      renderClientStoreDeviceDetail();
      return;
    }

    const res = await fetch(
      `${apiBase}/client/${encodeURIComponent(clientId)}/stores/${encodeURIComponent(selectedClientStoreId)}/devices/${encodeURIComponent(deviceId)}?t=${Date.now()}`,
      {
        credentials: 'same-origin',
        cache: 'no-store',
        headers: { Accept: 'application/json' },
      }
    );

    if (!res.ok) {
      selectedClientStoreDeviceDetail = null;
      clientStoreDeviceLogs = [];
      renderClientStoreDeviceDetail();
      return;
    }

    const payload = await res.json();
    selectedClientStoreDeviceDetail = payload?.device || null;
    clientStoreDeviceLogs = Array.isArray(payload?.logs) ? payload.logs : [];
    if (selectedClientStoreDeviceDetail) {
      populateClientStoreDeviceEditor(selectedClientStoreDeviceDetail);
    }
    renderClientStoreDeviceDetail();
  }

  function renderClientStoreDeviceList() {
    const listEl = document.getElementById('clientStoreDeviceList');
    if (!listEl) return;

    const formatDeviceStatusLabel = (value) => String(value || 'inactive')
      .replace(/_/g, ' ')
      .replace(/\b\w/g, (ch) => ch.toUpperCase());
    const toBadgeClass = (value, fallback = 'unassigned') => String(value || fallback)
      .trim()
      .toLowerCase()
      .replace(/\s+/g, '-');

    if (!clientStoreDeviceItems.length) {
      listEl.innerHTML = '<div class="text-muted px-2 py-2 small">No devices for this store.</div>';
      return;
    }

    const sortedDevices = getSortedClientStoreDevices(clientStoreDeviceItems);

    listEl.innerHTML = sortedDevices.map((device) => {
      const isActive = String(device.device_id) === String(selectedClientStoreDeviceId);
      const deviceCode = device.device_code || `DEV-${String(device.device_id || '').trim() || 'NEW'}`;
      const lastSeenLabel = formatClientStoreDeviceDateTime(device.last_seen);
      const detailLabel = [device.device_type || '-', lastSeenLabel === '-' ? 'Never seen' : `Seen ${lastSeenLabel}`].join(' 쨌 ');
      return `
        <div class="store-device-row ${isActive ? 'active' : ''}" data-device-id="${device.device_id}">
          <div class="store-device-main">
            <div class="store-device-code">${escapeClientStoreDeviceHtml(deviceCode)}</div>
            <div class="store-device-name">${escapeClientStoreDeviceHtml(device.device_name || 'Unnamed Device')}</div>
            <div class="store-device-sub">${escapeClientStoreDeviceHtml(detailLabel)}</div>
          </div>
          <div class="store-device-actions">
            <span class="device-state-badge ${toBadgeClass(device.status, 'inactive')}">${escapeClientStoreDeviceHtml(formatDeviceStatusLabel(device.status))}</span>
            <button type="button" class="store-device-edit-btn" data-device-edit-id="${device.device_id}" aria-label="Edit device" title="Edit device">
              <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M4 20h4l10-10-4-4L4 16v4z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"></path>
                <path d="M12.5 5.5l4 4" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"></path>
              </svg>
            </button>
          </div>
        </div>
      `;
    }).join('');

    listEl.querySelectorAll('.store-device-row[data-device-id]').forEach((row) => {
      row.addEventListener('click', async () => {
        const deviceId = row.dataset.deviceId;
        const found = clientStoreDeviceItems.find((item) => String(item.device_id) === String(deviceId));
        if (!found) return;

        selectedClientStoreDeviceId = found.device_id;
        selectedClientStoreDeviceDetail = found;
        populateClientStoreDeviceEditor(found);
        renderClientStoreDeviceList();
        await loadClientStoreDeviceDetail(found.device_id);
        openClientStoreDeviceEditor('edit');
      });
    });

    listEl.querySelectorAll('.store-device-edit-btn[data-device-edit-id]').forEach((btn) => {
      btn.addEventListener('click', async (event) => {
        event.preventDefault();
        event.stopPropagation();
        const deviceId = btn.dataset.deviceEditId;
        const found = clientStoreDeviceItems.find((item) => String(item.device_id) === String(deviceId));
        if (!found) return;

        selectedClientStoreDeviceId = found.device_id;
        selectedClientStoreDeviceDetail = found;
        populateClientStoreDeviceEditor(found);
        renderClientStoreDeviceList();
        await loadClientStoreDeviceDetail(found.device_id);
        openClientStoreDeviceEditor('edit');
      });
    });
  }

  function getSortedClientStoreDevices(items) {
    const directionMultiplier = deviceSortState.direction === 'asc' ? 1 : -1;
    const key = deviceSortState.key;

    return [...(items || [])].sort((a, b) => {
      if (key === 'device_id') {
        const leftId = Number(a?.device_id ?? 0);
        const rightId = Number(b?.device_id ?? 0);
        if (leftId === rightId) return 0;
        return (leftId < rightId ? -1 : 1) * directionMultiplier;
      }

      const leftComparable = toComparableValue(a?.[key]);
      const rightComparable = toComparableValue(b?.[key]);
      const result = compareComparableValues(leftComparable, rightComparable);
      return result * directionMultiplier;
    });
  }

  function updateClientStoreDeviceSortIndicators() {
    const headers = document.querySelectorAll('[data-device-sort-key]');
    headers.forEach((th) => {
      th.classList.add('sortable-col');
      const key = th.dataset.deviceSortKey;
      th.classList.toggle('is-sorted', key === deviceSortState.key);
      th.setAttribute(
        'aria-sort',
        key === deviceSortState.key
          ? (deviceSortState.direction === 'asc' ? 'ascending' : 'descending')
          : 'none'
      );
      const indicator = th.querySelector('.sort-indicator');
      if (!indicator) return;
      if (key === deviceSortState.key) {
        indicator.textContent = deviceSortState.direction === 'asc' ? 'ASC' : 'DESC';
      } else {
        indicator.textContent = '';
      }
    });
  }

  function toggleClientStoreDeviceSort(key) {
    if (!key) return;
    if (deviceSortState.key === key) {
      deviceSortState.direction = deviceSortState.direction === 'asc' ? 'desc' : 'asc';
    } else {
      deviceSortState.key = key;
      deviceSortState.direction = 'asc';
    }
    announceDeviceSortChange();
    renderClientStoreDeviceList();
  }

  function announceDeviceSortChange() {
    if (!deviceSortLive) return;
    const activeHeader = document.querySelector(`[data-device-sort-key="${deviceSortState.key}"]`);
    const label = String(activeHeader?.textContent || 'Column').replace(/[?꿎뼹]/g, '').trim();
    const direction = deviceSortState.direction === 'asc' ? 'ascending' : 'descending';
    deviceSortLive.textContent = `${label} sorted ${direction}`;
  }

  function initClientStoreDeviceSortHandlers() {
    const headers = document.querySelectorAll('[data-device-sort-key]');
    headers.forEach((th) => {
      th.setAttribute('tabindex', '0');
      th.setAttribute('role', 'button');
      th.setAttribute('aria-label', `${(th.textContent || '').replace(/[?꿎뼹]/g, '').trim()} sort`);
      th.setAttribute('aria-sort', 'none');
      if (th.dataset.sortBound === '1') return;
      th.dataset.sortBound = '1';
      th.addEventListener('click', () => toggleClientStoreDeviceSort(th.dataset.deviceSortKey));
      th.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        toggleClientStoreDeviceSort(th.dataset.deviceSortKey);
      });
    });
    updateClientStoreDeviceSortIndicators();
  }

  async function loadClientStoreDevices() {
    const { clientId } = getSelectedClientContext();
    const hintEl = document.getElementById('clientStoreDevicesHint');
    const saveBtn = document.getElementById('btnClientStoreDeviceSave');
    const addBtn = document.getElementById('btnClientStoreDeviceAdd');
    const deleteBtn = document.getElementById('btnClientStoreDeviceDelete');

    if (!clientId || !selectedClientStoreId) {
      resetClientStoreDeviceEditor();
      if (addBtn) addBtn.disabled = true;
      return;
    }

    if (hintEl) hintEl.textContent = 'Manage devices for this store.';
    if (addBtn) addBtn.disabled = false;
    if (saveBtn) saveBtn.disabled = true;
    if (deleteBtn) deleteBtn.disabled = true;

    const res = await fetch(`${apiBase}/client/${encodeURIComponent(clientId)}/stores/${encodeURIComponent(selectedClientStoreId)}/devices?t=${Date.now()}`, {
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { Accept: 'application/json' },
    });

    if (!res.ok) {
      if (hintEl) hintEl.textContent = 'Failed to load devices.';
      clientStoreDeviceItems = [];
      selectedClientStoreDeviceDetail = null;
      clientStoreDeviceLogs = [];
      renderClientStoreDeviceList();
      renderClientStoreDeviceDetail();
      return;
    }

    clientStoreDeviceItems = await res.json();
    if (!clientStoreDeviceItems.some((item) => String(item.device_id) === String(selectedClientStoreDeviceId))) {
      selectedClientStoreDeviceId = null;
      selectedClientStoreDeviceDetail = null;
      clientStoreDeviceLogs = [];
    }
    renderClientStoreDeviceList();
    if (selectedClientStoreDeviceId) {
      await loadClientStoreDeviceDetail(selectedClientStoreDeviceId);
    } else {
      renderClientStoreDeviceDetail();
    }
  }

  function collectClientStoreDevicePayload() {
    return {
      device_name: document.getElementById('field_client_store_device_name')?.value || '',
      device_type: document.getElementById('field_client_store_device_type')?.value || 'POS',
      status: document.getElementById('field_client_store_device_status')?.value || 'active',
      installed_by_agent_id: document.getElementById('field_client_store_device_installed_by_agent_id')?.value || '',
      note: document.getElementById('field_client_store_device_note')?.value || '',
    };
  }

  async function saveClientStoreDevice() {
    const { clientId } = getSelectedClientContext();
    if (!clientId || !selectedClientStoreId) {
      alert('Please save and select a Store first.');
      return;
    }

    const payload = collectClientStoreDevicePayload();
    if (!String(payload.device_name || '').trim()) {
      alert('Device name is required.');
      return;
    }

    const isEditing = Boolean(selectedClientStoreDeviceId);
    const method = isEditing ? 'PUT' : 'POST';
    const url = isEditing
      ? `${apiBase}/client/${encodeURIComponent(clientId)}/stores/${encodeURIComponent(selectedClientStoreId)}/devices/${encodeURIComponent(selectedClientStoreDeviceId)}`
      : `${apiBase}/client/${encodeURIComponent(clientId)}/stores/${encodeURIComponent(selectedClientStoreId)}/devices`;

    const res = await fetch(url, {
      method,
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      const text = await res.text();
      alert('Unable to save device: ' + text);
      return;
    }

    const saved = await res.json();
    selectedClientStoreDeviceId = saved?.device_id || selectedClientStoreDeviceId;
    await loadClientStoreDevices();
    const modal = getClientStoreDeviceModalInstance();
    if (modal) modal.hide();
  }

  async function deleteClientStoreDevice() {
    const { clientId } = getSelectedClientContext();
    if (!clientId || !selectedClientStoreId || !selectedClientStoreDeviceId) {
      alert('Please select a device first.');
      return;
    }

    if (!(await showCenteredConfirm('Delete this device?', 'Delete Device', { variant: 'danger', okText: 'Delete', cancelText: 'No' }))) {
      return;
    }

    const res = await fetch(
      `${apiBase}/client/${encodeURIComponent(clientId)}/stores/${encodeURIComponent(selectedClientStoreId)}/devices/${encodeURIComponent(selectedClientStoreDeviceId)}`,
      { method: 'DELETE', credentials: 'same-origin' }
    );

    if (!res.ok) {
      const text = await res.text();
      alert('Unable to delete device: ' + text);
      return;
    }

    selectedClientStoreDeviceId = null;
    await loadClientStoreDevices();
    const modal = getClientStoreDeviceModalInstance();
    if (modal) modal.hide();
  }

  function populateClientStoreEditor(store) {
    selectedClientStoreId = store.store_id;
    const saveBtn = document.getElementById('btnClientStoreSave');
    const deleteBtn = document.getElementById('btnClientStoreDelete');
    const idInput = document.getElementById('field_client_store_id');
    const codeInput = document.getElementById('field_client_store_code');
    const nameInput = document.getElementById('field_client_store_name');
    const statusInput = document.getElementById('field_client_store_status');
    const btInput = document.getElementById('field_client_store_business_type');
    const opInput = document.getElementById('field_client_store_operation_type');
    const contactNameInput = document.getElementById('field_client_store_contact_name');
    const emailInput = document.getElementById('field_client_store_email');
    const phoneInput = document.getElementById('field_client_store_phone');
    const zipInput = document.getElementById('field_client_store_zip');
    const addr1Input = document.getElementById('field_client_store_address_line1');
    const addr2Input = document.getElementById('field_client_store_address_line2');
    const cityInput = document.getElementById('field_client_store_city');
    const stateInput = document.getElementById('field_client_store_state');
    const countryInput = document.getElementById('field_client_store_country');
    const taxRateInput = document.getElementById('field_client_store_default_tax_rate');
    const timezoneInput = document.getElementById('field_client_store_timezone');
    const taxSourceInput = document.getElementById('field_client_store_tax_source');
    const receiptStoreNameInput = document.getElementById('field_client_store_receipt_store_name');
    const receiptPhoneInput = document.getElementById('field_client_store_receipt_phone');
    const receiptEmailInput = document.getElementById('field_client_store_receipt_email');
    const receiptWebsiteInput = document.getElementById('field_client_store_receipt_website_url');
    const receiptMessageInput = document.getElementById('field_client_store_receipt_message');
    const memoInput = document.getElementById('field_client_store_memo');
    const installedByAgentInput = document.getElementById('field_client_store_installed_by_agent_id');

    if (idInput) idInput.value = String(store.store_id || '');
  if (codeInput) codeInput.value = store.store_code || '';
    if (nameInput) nameInput.value = store.store_name || '';
    if (statusInput) statusInput.value = (store.status || 'active').toLowerCase();
    if (btInput) btInput.value = store.business_type || '';
    if (opInput) opInput.value = store.operation_type || '';
    if (contactNameInput) contactNameInput.value = store.contact_name || '';
    if (emailInput) emailInput.value = store.email || '';
    if (phoneInput) phoneInput.value = store.phone || '';
    if (zipInput) zipInput.value = store.zip || '';
    if (addr1Input) addr1Input.value = store.address_line1 || store.address || '';
    if (addr2Input) addr2Input.value = store.address_line2 || '';
    if (cityInput) cityInput.value = store.city || '';
    if (stateInput) stateInput.value = store.state || '';
    if (countryInput) countryInput.value = store.country || '';
    if (taxRateInput) taxRateInput.value = store.default_tax_rate || '';
    if (timezoneInput) timezoneInput.value = store.timezone || '';
    if (taxSourceInput) taxSourceInput.value = store.tax_source || 'auto';
    if (receiptStoreNameInput) receiptStoreNameInput.value = store.receipt_store_name || '';
    if (receiptPhoneInput) receiptPhoneInput.value = store.receipt_phone || '';
    if (receiptEmailInput) receiptEmailInput.value = store.receipt_email || '';
    if (receiptWebsiteInput) receiptWebsiteInput.value = store.receipt_website_url || '';
    if (receiptMessageInput) receiptMessageInput.value = store.receipt_message || '';
    if (memoInput) memoInput.value = store.memo || '';
    if (installedByAgentInput) installedByAgentInput.value = store.installed_by_agent_id ? String(store.installed_by_agent_id) : '';
    if (saveBtn) saveBtn.disabled = false;
    if (deleteBtn) deleteBtn.disabled = false;
    refreshClientStoreModalLogoPreview(store);
    loadClientStoreDevices();
    updateClientStoreModalChrome('edit');
    setClientStoreFormVisible(true);
  }

  function renderClientStoreList() {
    const listEl = document.getElementById('clientStoreList');
    if (!listEl) return;

    if (!clientStoreItems.length) {
      listEl.innerHTML = '<div class="text-muted px-2 py-2 small">No stores for this client.</div>';
      return;
    }

    listEl.innerHTML = clientStoreItems.map((store) => {
      const status = String(store.status || 'inactive').toLowerCase();
      const code = store.store_code || 'STORE';
      const isActive = String(selectedClientStoreId) === String(store.store_id);
      return `
        <div class="client-store-row ${isActive ? 'active' : ''}" data-store-id="${store.store_id}">
          <div>
            <div class="client-store-code">${code}</div>
            <div class="client-store-name">${store.store_name || ''}</div>
          </div>
          <span class="module-status-badge ${status === 'active' ? 'active' : 'inactive'}">${status}</span>
        </div>
      `;
    }).join('');

    listEl.querySelectorAll('.client-store-row').forEach((row) => {
      row.addEventListener('click', async () => {
        if (Date.now() < suppressClientStoreRowClickUntil) return;
        const storeId = row.dataset.storeId;
        const found = clientStoreItems.find((s) => String(s.store_id) === String(storeId));
        if (found) {
          try {
            const latestStore = await fetchClientStoreDetail(storeId);
            populateClientStoreEditor(latestStore);
          } catch (error) {
            populateClientStoreEditor(found);
          }
          renderClientStoreList();
        }
      });
    });
  }

  async function loadClientStoresForSelectedClient() {
    if (activeModule !== 'account') return;
    const { clientId, clientCode, isReadyForStoreRegistration } = getSelectedClientContext();
    const listEl = document.getElementById('clientStoreList');
    const hintEl = document.getElementById('clientStorePanelHint');
    const addBtn = document.getElementById('btnClientStoreAdd');
    const saveBtn = document.getElementById('btnClientStoreSave');
    const deleteBtn = document.getElementById('btnClientStoreDelete');

    if (!isReadyForStoreRegistration) {
      clientStoreItems = [];
      selectedClientStoreId = null;
      resetClientStoreEditor();
      setClientStoreFormVisible(false);
      if (listEl) listEl.innerHTML = '<div class="text-muted px-2 py-2 small">Save Client first to enable Store registration</div>';
      if (hintEl) hintEl.textContent = 'Save Client first to enable Store registration';
      if (addBtn) addBtn.disabled = true;
      if (saveBtn) saveBtn.disabled = true;
      if (deleteBtn) deleteBtn.disabled = true;
      return;
    }

    if (addBtn) addBtn.disabled = false;
    if (saveBtn) saveBtn.disabled = true;
    if (hintEl) hintEl.textContent = `Stores are auto-linked to Client ${clientCode} (ID ${clientId}).`;
    resetClientStoreEditor();
    setClientStoreFormVisible(false);

    let res = await fetch(`${apiBase}/client/${encodeURIComponent(clientId)}/stores?t=${Date.now()}`, {
      cache: 'no-store',
      headers: { Accept: 'application/json' },
    });
    if (!res.ok) {
      res = await fetch(`${apiBase}/stores?client_id=${encodeURIComponent(clientId)}&t=${Date.now()}`, {
        cache: 'no-store',
        headers: { Accept: 'application/json' },
      });
    }
    if (!res.ok) {
      if (listEl) listEl.innerHTML = '<div class="text-danger px-2 py-2 small">Failed to load stores.</div>';
      return;
    }

    clientStoreItems = await res.json();
    if (!clientStoreItems.some((s) => String(s.store_id) === String(selectedClientStoreId))) {
      selectedClientStoreId = null;
    }
    renderClientStoreList();
    if (saveBtn) saveBtn.disabled = true;
    if (deleteBtn) deleteBtn.disabled = true;
  }

  function normalizeSalesTaxRate(rawValue) {
    const cleaned = String(rawValue || '').replace(/[%\s,]/g, '').trim();
    if (!cleaned) return '';
    const parsed = Number.parseFloat(cleaned);
    if (!Number.isFinite(parsed)) return cleaned;
    return parsed.toFixed(4);
  }

  function resolveTaxSourceForPayload(stateValue, countryValue, taxRateValue, currentTaxSource) {
    const normalizedState = String(stateValue || '').trim().toUpperCase();
    const normalizedCountry = String(countryValue || '').trim().toUpperCase();
    const normalizedTax = normalizeSalesTaxRate(taxRateValue);
    const defaultTax = STATE_DEFAULT_TAX_RATE[normalizedState] || '';

    if (!normalizedTax) return 'auto';
    if (normalizedCountry && normalizedCountry !== 'USA' && normalizedCountry !== 'US') return 'manual';
    if (!defaultTax) return 'manual';
    if (normalizedTax !== defaultTax) return 'manual';
    return String(currentTaxSource || 'auto').toLowerCase() === 'manual' ? 'manual' : 'auto';
  }

  function collectClientStorePayload() {
    const stateValue = document.getElementById('field_client_store_state')?.value || '';
    const countryValue = document.getElementById('field_client_store_country')?.value || '';
    const taxRateRawValue = document.getElementById('field_client_store_default_tax_rate')?.value || '';
    const taxSourceValue = document.getElementById('field_client_store_tax_source')?.value || 'auto';
    const normalizedTaxRate = normalizeSalesTaxRate(taxRateRawValue);
    const resolvedTaxSource = resolveTaxSourceForPayload(stateValue, countryValue, normalizedTaxRate, taxSourceValue);

    return {
      store_name: document.getElementById('field_client_store_name')?.value || '',
      store_status: document.getElementById('field_client_store_status')?.value || 'active',
      business_type: document.getElementById('field_client_store_business_type')?.value || '',
      operation_type: document.getElementById('field_client_store_operation_type')?.value || '',
      contact_name: document.getElementById('field_client_store_contact_name')?.value || '',
      email: document.getElementById('field_client_store_email')?.value || '',
      phone: document.getElementById('field_client_store_phone')?.value || '',
      zip: document.getElementById('field_client_store_zip')?.value || '',
      address_line1: document.getElementById('field_client_store_address_line1')?.value || '',
      address_line2: document.getElementById('field_client_store_address_line2')?.value || '',
      city: document.getElementById('field_client_store_city')?.value || '',
      state: stateValue,
      country: countryValue,
      default_tax_rate: normalizedTaxRate,
      timezone: document.getElementById('field_client_store_timezone')?.value || '',
      tax_source: resolvedTaxSource,
      receipt_store_name: document.getElementById('field_client_store_receipt_store_name')?.value || '',
      receipt_phone: document.getElementById('field_client_store_receipt_phone')?.value || '',
      receipt_email: document.getElementById('field_client_store_receipt_email')?.value || '',
      receipt_website_url: document.getElementById('field_client_store_receipt_website_url')?.value || '',
      receipt_message: document.getElementById('field_client_store_receipt_message')?.value || '',
      memo: document.getElementById('field_client_store_memo')?.value || '',
      installed_by_agent_id: document.getElementById('field_client_store_installed_by_agent_id')?.value || null,
      client_id: getSelectedClientId(),
    };
  }

  async function fetchClientStoreDetail(storeId) {
    const { clientId } = getSelectedClientContext();
    if (!clientId) {
      throw new Error('Client context is missing.');
    }
    let res = await fetch(`${apiBase}/client/${encodeURIComponent(clientId)}/stores/${encodeURIComponent(storeId)}?t=${Date.now()}`, {
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { Accept: 'application/json' },
    });
    if (!res.ok) {
      res = await fetch(`${apiBase}/store/${encodeURIComponent(storeId)}?t=${Date.now()}`, {
        credentials: 'same-origin',
        cache: 'no-store',
        headers: { Accept: 'application/json' },
      });
    }
    if (!res.ok) {
      const text = await res.text();
      throw new Error(text || `Failed to load store detail (${res.status})`);
    }
    return res.json();
  }

  async function saveClientLinkedStore() {
    const { isReadyForStoreRegistration } = getSelectedClientContext();
    const isEditingStore = Boolean(selectedClientStoreId);
    if (!isReadyForStoreRegistration && !isEditingStore) {
      alert('Please save the Client first to manage stores.');
      return;
    }

    const payload = collectClientStorePayload();
    if (!payload.store_name.trim()) {
      alert('Store name is required.');
      return;
    }

    if (!(await showCenteredConfirm('Save changes to this store?', 'Save Store', { variant: 'success', okText: 'Save', cancelText: 'Back' }))) {
      return;
    }

    const storeId = selectedClientStoreId;
    const { clientId } = getSelectedClientContext();
    if (!clientId) {
      alert('Please save the Client first to manage stores.');
      return;
    }
    const method = storeId ? 'PUT' : 'POST';
    let url = storeId
      ? `${apiBase}/client/${encodeURIComponent(clientId)}/stores/${encodeURIComponent(storeId)}`
      : `${apiBase}/client/${encodeURIComponent(clientId)}/stores`;

    let res;
    try {
      res = await fetch(url, {
        method,
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!res.ok && res.status === 404) {
        url = storeId ? `${apiBase}/store/${encodeURIComponent(storeId)}` : `${apiBase}/store`;
        res = await fetch(url, {
          method,
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
      }
    } catch (error) {
      alert('Unable to save the store right now. Please refresh and try again.');
      return;
    }

    if (!res.ok) {
      if (res.status === 401) {
        alert('Session expired. Please refresh the page and login again.');
        return;
      }
      const text = await res.text();
      alert('Unable to save the store: ' + text);
      return;
    }

    let savedStoreId = storeId;
    if (!savedStoreId) {
      try {
        const created = await res.json();
        savedStoreId = created?.store_id || null;
      } catch (error) {
        savedStoreId = null;
      }
    }

    suppressClientStoreRowClickUntil = Date.now() + 700;
    resetClientStoreEditor();
    setClientStoreFormVisible(false);
    await loadClientStoresForSelectedClient();

  }

  async function deleteClientLinkedStore() {
    if (!selectedClientStoreId) {
      alert('Please select a store first.');
      return;
    }
    if (!(await showCenteredConfirm('Remove this store from the client view?', 'Delete Store', { variant: 'danger', okText: 'Delete', cancelText: 'No' }))) return;

    const { clientId } = getSelectedClientContext();
    if (!clientId) {
      alert('Please save the Client first to manage stores.');
      return;
    }

    let res = await fetch(
      `${apiBase}/client/${encodeURIComponent(clientId)}/stores/${encodeURIComponent(selectedClientStoreId)}`,
      { method: 'DELETE' }
    );
    if (!res.ok && res.status === 404) {
      res = await fetch(`${apiBase}/store/${encodeURIComponent(selectedClientStoreId)}`, { method: 'DELETE' });
    }
    if (!res.ok) {
      const text = await res.text();
      alert('Unable to delete the store: ' + text);
      return;
    }

    resetClientStoreEditor();
    setClientStoreFormVisible(false);
    await loadClientStoresForSelectedClient();
  }

  function initClientStorePanelHandlers() {
    if (activeModule !== 'account') return;
    initClientStoreDeviceSortHandlers();
    initClientStoreDeviceInfoTabs();
    bindClientStoreModalLogoHandlers();
    refreshClientStoreModalLogoPreview();
    resetClientStoreDeviceEditor();
    const addBtn = document.getElementById('btnClientStoreAdd');
    const saveBtn = document.getElementById('btnClientStoreSave');
    const deleteBtn = document.getElementById('btnClientStoreDelete');
    const deviceAddBtn = document.getElementById('btnClientStoreDeviceAdd');
    const deviceSaveBtn = document.getElementById('btnClientStoreDeviceSave');
    const deviceDeleteBtn = document.getElementById('btnClientStoreDeviceDelete');

    if (addBtn) {
      addBtn.addEventListener('click', () => {
        const { isReadyForStoreRegistration } = getSelectedClientContext();
        if (!isReadyForStoreRegistration) {
          alert('Please save the Client first to manage stores.');
          return;
        }
        resetClientStoreEditor();
        updateClientStoreModalChrome('add');
        const codeInput = document.getElementById('field_client_store_code');
        if (codeInput) codeInput.value = '';
        setClientStoreFormVisible(true);
        if (saveBtn) saveBtn.disabled = false;
        if (deleteBtn) deleteBtn.disabled = true;
        bindZipAutoFill(
          'field_client_store_zip',
          'field_client_store_city',
          'field_client_store_state',
          'field_client_store_country',
          'field_client_store_default_tax_rate',
          'field_client_store_timezone',
          'field_client_store_tax_source',
          'field_client_store_address_autofill_hint'
        );
        bindTaxSourceManualOverride('field_client_store_default_tax_rate', 'field_client_store_tax_source');
        renderClientStoreList();
      });
    }
    if (saveBtn) {
      saveBtn.addEventListener('click', saveClientLinkedStore);
    }
    if (deleteBtn) {
      deleteBtn.addEventListener('click', deleteClientLinkedStore);
      deleteBtn.disabled = true;
    }
    if (deviceAddBtn) {
      deviceAddBtn.addEventListener('click', () => {
        if (!selectedClientStoreId) {
          alert('Please save and select a Store first.');
          return;
        }
        selectedClientStoreDeviceId = null;
        const idInput = document.getElementById('field_client_store_device_id');
        const nameInput = document.getElementById('field_client_store_device_name');
        const typeInput = document.getElementById('field_client_store_device_type');
        const statusInput = document.getElementById('field_client_store_device_status');
        const installedByAgentInput = document.getElementById('field_client_store_device_installed_by_agent_id');
        const noteInput = document.getElementById('field_client_store_device_note');
        const saveDeviceBtn = document.getElementById('btnClientStoreDeviceSave');
        const deleteDeviceBtn = document.getElementById('btnClientStoreDeviceDelete');
        selectedClientStoreDeviceDetail = null;
        clientStoreDeviceLogs = [];
        if (idInput) idInput.value = '';
        if (nameInput) nameInput.value = '';
        if (typeInput) typeInput.value = 'POS';
        if (statusInput) statusInput.value = 'active';
        if (installedByAgentInput) installedByAgentInput.value = '';
        if (noteInput) noteInput.value = '';
        if (saveDeviceBtn) saveDeviceBtn.disabled = false;
        if (deleteDeviceBtn) deleteDeviceBtn.disabled = true;
        renderClientStoreDeviceList();
        renderClientStoreDeviceDetail();
        openClientStoreDeviceEditor('add');
      });
      deviceAddBtn.disabled = true;
    }
    if (deviceSaveBtn) {
      deviceSaveBtn.addEventListener('click', saveClientStoreDevice);
      deviceSaveBtn.disabled = true;
    }
    if (deviceDeleteBtn) {
      deviceDeleteBtn.addEventListener('click', deleteClientStoreDevice);
      deviceDeleteBtn.disabled = true;
    }
    const cancelBtn = document.getElementById('btnClientStoreCancel');
    if (cancelBtn) {
      cancelBtn.addEventListener('click', () => {
        resetClientStoreEditor();
        setClientStoreFormVisible(false);
      });
    }
    initClientStoreModalTabs();
    bindZipAutoFill(
      'field_client_store_zip',
      'field_client_store_city',
      'field_client_store_state',
      'field_client_store_country',
      'field_client_store_default_tax_rate',
      'field_client_store_timezone',
      'field_client_store_tax_source',
      'field_client_store_address_autofill_hint'
    );
    bindTaxSourceManualOverride('field_client_store_default_tax_rate', 'field_client_store_tax_source');
    if (clientStoreModalElement && !clientStoreModalElement.dataset.boundReset) {
      clientStoreModalElement.dataset.boundReset = '1';
      clientStoreModalElement.addEventListener('hidden.bs.modal', () => {
        const deviceModal = getClientStoreDeviceModalInstance();
        if (deviceModal) deviceModal.hide();
        resetClientStoreEditor();
        resetClientStoreDeviceEditor();
        setClientStoreModalTab('basic');
      });
    }
  }

  function createClientTabs() {
    const container = document.createElement('div');
    container.className = 'col-12';

    const tabs = Array.isArray(config.tabs) ? config.tabs : [];
    const fieldMap = Object.fromEntries(config.fields.map(field => [field.name, field]));

    const tabHeader = document.createElement('div');
    tabHeader.className = 'client-detail-tabs';

    const tabBody = document.createElement('div');

    tabs.forEach((tab, idx) => {
      const tabBtn = document.createElement('button');
      tabBtn.type = 'button';
      tabBtn.className = `client-tab-btn${idx === 0 ? ' active' : ''}`;
      tabBtn.textContent = tab.label;
      tabBtn.dataset.tabTarget = tab.id;
      tabHeader.appendChild(tabBtn);

      const panel = document.createElement('div');
      panel.className = `client-tab-panel${idx === 0 ? ' active' : ''}`;
      panel.dataset.tabPanel = tab.id;

      if (tab.summary) {
        const summary = document.createElement('div');
        summary.className = 'client-tab-summary';
        summary.textContent = tab.summary;
        panel.appendChild(summary);
      }

      const panelRow = document.createElement('div');
      panelRow.className = 'row g-3';

      if (activeModule === 'account' && tab.id === 'stores') {
        const fullCol = document.createElement('div');
        fullCol.className = 'col-12';
        fullCol.appendChild(createClientStoresPanel());
        panelRow.appendChild(fullCol);
      } else {
        (tab.fields || []).forEach((fieldName) => {
          const field = fieldMap[fieldName];
          if (field) panelRow.appendChild(createField(field));
        });
        if (activeModule === 'account' && tab.id === 'overview') {
          panelRow.appendChild(createBrandLogoUploadPanel('client'));
        }
        if (activeModule === 'store' && tab.id === 'basic') {
          panelRow.appendChild(createBrandLogoUploadPanel('store'));
        }
      }

      panel.appendChild(panelRow);
      tabBody.appendChild(panel);
    });

    tabHeader.querySelectorAll('.client-tab-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        const target = btn.dataset.tabTarget;
        tabHeader.querySelectorAll('.client-tab-btn').forEach((b) => b.classList.remove('active'));
        tabBody.querySelectorAll('.client-tab-panel').forEach((p) => p.classList.remove('active'));
        btn.classList.add('active');
        const panel = tabBody.querySelector(`.client-tab-panel[data-tab-panel="${target}"]`);
        if (panel) panel.classList.add('active');
        // Entering Stores tab always resets to LIST MODE
        if (activeModule === 'account' && target === 'stores') {
          resetClientStoreEditor();
          setClientStoreFormVisible(false);
          loadClientStoresForSelectedClient();
        }
      });
    });

    container.appendChild(tabHeader);
    container.appendChild(tabBody);
    return container;
  }

  function renderForm() {
    const targetForm = isListCentricModule ? masterFormModal : masterForm;
    if (!targetForm) return;

    targetForm.innerHTML = '';
    targetForm.appendChild(createIdField());
    if (Array.isArray(config.tabs)) {
      targetForm.appendChild(createClientTabs());
      bindBrandLogoUploadHandlers();
      refreshClientLogoPreview();
      refreshStoreLogoPreview();
      if (activeModule === 'account') {
        initClientStorePanelHandlers();
        loadClientStoresForSelectedClient();
      }
      return;
    }
    config.fields.forEach(field => {
      targetForm.appendChild(createField(field));
    });
  }

  function getFormData() {
    const data = {};
    config.fields.forEach(field => {
      if (field.type === 'section') return;
      const input = document.getElementById(`field_${field.name}`);
      if (!input) return;
      data[field.name] = input.value;
    });

    if (activeModule === 'session') {
      return {
        status: data.status,
      };
    }

    if (activeModule === 'store') {
      data.store_status = data.status;
      delete data.status;

      // Store code is always server-generated and immutable from this UI.
      delete data.store_code;
    }

    if (activeModule === 'account') {
      // Keep both keys for backend compatibility while using explicit line1 naming in UI.
      data.address = data.address_line1 ?? '';

      // Client code is server-generated and immutable from this UI.
      delete data.c_client_code;
      delete data.client_code;
    }

    if (activeModule === 'agent') {
      delete data.agent_code;
      delete data.created_at;
    }

    if (activeModule === 'agent-type') {
      data.agent_type_code = normalizeAgentTypeCode(data.agent_type_code);
    }

    return data;
  }

  function populateForm(item) {
    const resolvedId = item.id ?? item.store_id ?? item.i_store_id ?? '';
    selectedMasterId.value = resolvedId;
    selectedOriginalRecord = { ...item, id: resolvedId };
    const idInput = document.getElementById('field_record_id');
    if (idInput) idInput.value = resolvedId;
    config.fields.forEach(field => {
      if (field.type === 'section') return;
      const input = document.getElementById(`field_${field.name}`);
      if (!input) return;
      let value = item[field.name] ?? '';
      input.value = value;
    });
    renderClientDetailHero('edit', item);
    renderStoreDetailHero('edit', item);
    renderModuleDetailHero('edit', item);
    if (activeModule === 'account') {
      resetClientStoreEditor();
      loadClientStoresForSelectedClient();
    }
    refreshClientLogoPreview();
    refreshStoreLogoPreview(item);
    updateListCentricActionButtons();
    setBaselineSnapshot();
  }

  function clearForm() {
    selectedMasterId.value = '';
    selectedOriginalRecord = null;
    const idInput = document.getElementById('field_record_id');
    if (idInput) idInput.value = '';
    config.fields.forEach(field => {
      if (field.type === 'section') return;
      const input = document.getElementById(`field_${field.name}`);
      if (!input) return;
      if ((activeModule === 'store' || activeModule === 'user' || activeModule === 'account' || activeModule === 'agent' || activeModule === 'agent-type' || activeModule === 'license' || activeModule === 'payment-method' || activeModule === 'role' || activeModule === 'business-type') && field.name === 'status') {
        input.value = 'active';
      } else if (activeModule === 'session' && field.name === 'status') {
        input.value = 'active';
      } else {
        input.value = '';
      }
    });
    document.querySelectorAll('#masterListBody tr').forEach(r => r.classList.remove('table-active'));
    updateSectionTitles('create');
    renderClientDetailHero('create', null);
    renderStoreDetailHero('create', null);
    renderModuleDetailHero('create', null);
    if (activeModule === 'account') {
      resetClientStoreEditor();
      loadClientStoresForSelectedClient();
    }
    refreshClientLogoPreview();
    refreshStoreLogoPreview();
    updateListCentricActionButtons();
    setBaselineSnapshot();
  }

  function selectItem(item, row) {
    // Highlight selected row
    document.querySelectorAll('#masterListBody tr').forEach(r => r.classList.remove('table-active'));
    if (row) row.classList.add('table-active');

    populateForm({ id: item.id ?? item.store_id ?? item.i_store_id ?? '', ...item });
    updateSectionTitles('edit');
    updateListCentricActionButtons();
    setBaselineSnapshot();
  }

  function safeFilePart(value) {
    return String(value).toLowerCase().replace(/\s+/g, '-').replace(/[^a-z0-9-]/g, '');
  }

  function buildExportFilename(ext) {
    const now = new Date();
    const stamp = `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, '0')}${String(now.getDate()).padStart(2, '0')}_${String(now.getHours()).padStart(2, '0')}${String(now.getMinutes()).padStart(2, '0')}`;
    return `master-${safeFilePart(activeLabel.list)}-${stamp}.${ext}`;
  }

  function triggerDownload(blob, fileName) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = fileName;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  function exportCsv() {
    const escapeCsv = (value) => {
      const stringValue = String(value ?? '');
      if (/[",\n]/.test(stringValue)) {
        return `"${stringValue.replace(/"/g, '""')}"`;
      }
      return stringValue;
    };

    const lines = [config.headers.map(escapeCsv).join(',')];
    currentItems.forEach(item => {
      lines.push(getRowValues(item).map(escapeCsv).join(','));
    });

    const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8;' });
    triggerDownload(blob, buildExportFilename('csv'));
  }

  function exportExcel() {
    const headerRow = config.headers.map(col => `<th>${col}</th>`).join('');
    const bodyRows = currentItems.map(item => {
      const tds = getRowValues(item).map(value => `<td>${value ?? ''}</td>`).join('');
      return `<tr>${tds}</tr>`;
    }).join('');

    const tableHtml = `
      <html>
        <head><meta charset="UTF-8"></head>
        <body>
          <table border="1">
            <thead><tr>${headerRow}</tr></thead>
            <tbody>${bodyRows}</tbody>
          </table>
        </body>
      </html>
    `;

    const blob = new Blob([tableHtml], { type: 'application/vnd.ms-excel;charset=utf-8;' });
    triggerDownload(blob, buildExportFilename('xls'));
  }

  function exportPdf() {
    const popup = window.open('', '_blank', 'width=1024,height=768');
    if (!popup) {
      alert('Please allow pop-ups to export PDF.');
      return;
    }

    const printDate = new Date().toLocaleString();
    const reportTitle = `${activeLabel.list} Report`;
    const reportHeaders = ['ID', 'Name', 'Description', 'Status'];
    const headerRow = reportHeaders.map(col => `<th>${col}</th>`).join('');
    const bodyRows = currentItems.map(item => {
      const tds = getReportRowValues(item).map(value => `<td>${value ?? ''}</td>`).join('');
      return `<tr>${tds}</tr>`;
    }).join('');

    popup.document.write(`
      <html>
        <head>
          <title>${reportTitle}</title>
          <link rel="stylesheet" href="/static/bootstrap/css/bootstrap.min.css">
          <style>
            @page {
              margin: 10mm 12mm;
            }
            body {
              font-family: Arial, sans-serif;
              font-size: 10pt;
              line-height: 1.25;
              background: #fff;
              color: #0f172a;
              margin: 0;
            }
            .report-wrap {
              max-width: 100%;
              margin: 0 auto;
              font-size: 10pt;
            }
            .report-title {
              text-align: center;
              font-size: 14pt;
              font-weight: 700;
              margin-bottom: 8px;
              letter-spacing: 0.02em;
            }
            .report-meta {
              border: 1px solid #dee2e6;
              border-radius: 0;
              padding: 6px 8px;
              margin-bottom: 8px;
              background: #f8fafc;
              font-size: 10pt;
            }
            .report-meta .row {
              row-gap: 3px !important;
            }
            .table-report {
              width: 100%;
              border-collapse: collapse !important;
              border-top: 2px solid #0f172a;
              margin-bottom: 0;
              font-size: 10pt;
            }
            .table-report th,
            .table-report td {
              vertical-align: middle;
              padding: 4px 6px !important;
              border: 1px solid #d1d5db !important;
            }
            .table-report thead th {
              font-weight: 700;
            }
            .report-footer {
              display: flex;
              justify-content: space-between;
              align-items: center;
              margin-top: 6px;
              color: #475569;
              font-size: 9pt;
            }
            @media print {
              .no-print {
                display: none !important;
              }
            }
          </style>
        </head>
        <body>
          <div class="report-wrap py-1">
            <div class="report-title">${reportTitle}</div>

            <div class="report-meta">
              <div class="row g-1">
                <div class="col-6"><strong>Company Name:</strong> ${reportMeta.companyName}</div>
                <div class="col-6 text-end"><strong>Print Date:</strong> ${printDate}</div>
                <div class="col-6"><strong>Report Title:</strong> ${reportTitle}</div>
                <div class="col-6 text-end"><strong>Printed By:</strong> ${reportMeta.printedBy}</div>
              </div>
            </div>

            <table class="table table-sm table-bordered table-report">
              <thead class="table-light">
                <tr>${headerRow}</tr>
              </thead>
              <tbody>${bodyRows}</tbody>
            </table>

            <div class="report-footer">
              <div><strong>Total Records:</strong> ${currentItems.length}</div>
              <div><strong>Page:</strong> 1 / 1</div>
            </div>
          </div>

          <script>
            window.onload = function() {
              window.print();
            };
          <\/script>
        </body>
      </html>
    `);
    popup.document.close();
  }

  function handleExport(format) {
    if (!currentItems.length) {
      alert('No data to export.');
      return;
    }
    if (format === 'csv') {
      exportCsv();
      return;
    }
    if (format === 'excel') {
      exportExcel();
      return;
    }
    if (format === 'pdf') {
      exportPdf();
    }
  }

  async function saveItem() {
    const id = selectedMasterId.value;
    const data = getFormData();

    if (activeModule === 'agent-type') {
      const codeInput = document.getElementById('field_agent_type_code');
      const codeIsValid = applyAgentTypeCodeValidation();
      if (!codeIsValid) {
        if (codeInput) codeInput.reportValidity();
        return false;
      }
    }

    if (activeModule === 'account') {
      const zipValue = String(data.zip || '').trim();
      if (zipValue) {
        const usZipRegex = /^\d{5}(-\d{4})?$/;
        if (!usZipRegex.test(zipValue)) {
          alert('ZIP code must use US format: 12345 or 12345-6789.');
          return false;
        }
      }
    }

    const method = id ? 'PUT' : 'POST';
    const url = id ? `${apiBase}/${config.endpoint}/${id}` : `${apiBase}/${config.endpoint}`;

    const res = await fetch(url, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });

    if (!res.ok) {
      const text = await res.text();
      alert('Unable to save: ' + text);
      return false;
    }

    // Keep selection on the saved record
    const json = await res.json().catch(() => ({}));
    const savedId = json.id || json.store_id || id;
    if (savedId) {
      lastSavedId = savedId;
      selectedMasterId.value = savedId;
    }

    await loadList(lastSavedId);
    setBaselineSnapshot();
    updateListCentricActionButtons();
    return true;
  }

  async function deleteItem() {
    const id = selectedMasterId.value;
    if (!id) {
      alert('Please select an item first.');
      return false;
    }
    const deleteLabel = config.deleteLabel || 'Delete';
    if (!(await showCenteredConfirm(`${deleteLabel} this item?`, deleteLabel, { variant: 'danger', okText: deleteLabel, cancelText: 'No' }))) return false;

    const res = await fetch(`${apiBase}/${config.endpoint}/${id}`, { method: 'DELETE' });
    if (!res.ok) {
      const text = await res.text();
      alert('Unable to delete: ' + text);
      return false;
    }

    clearForm();
    lastSavedId = null;
    await loadList();
    updateListCentricActionButtons();
    return true;
  }

  btnNewRecord.addEventListener('click', clearForm);
  btnSaveRecord.addEventListener('click', saveItem);
  if (btnCancelRecord) {
    btnCancelRecord.addEventListener('click', cancelEdit);
  }
  btnDeleteRecord.addEventListener('click', deleteItem);
  if (btnListAdd) {
    btnListAdd.addEventListener('click', () => {
      clearForm();
      updateSectionTitles('create');
      updateListCentricActionButtons();
      openMasterEditModal('create');
    });
  }
  if (btnListEdit) {
    btnListEdit.addEventListener('click', openSelectedMasterEditFromList);
  }
  if (btnListDelete) {
    btnListDelete.addEventListener('click', async () => {
      await deleteItem();
    });
  }
  if (btnModalSave) {
    btnModalSave.addEventListener('click', async () => {
      const ok = await saveItem();
      if (ok) closeMasterEditModal();
    });
  }
  if (btnModalDelete) {
    btnModalDelete.addEventListener('click', async () => {
      const ok = await deleteItem();
      if (ok) closeMasterEditModal();
    });
  }
  exportMenuItems.forEach(item => {
    item.addEventListener('click', () => {
      handleExport(item.dataset.exportFormat);
    });
  });
  masterSearch.addEventListener('keyup', (e) => { if (e.key === 'Enter') loadList(); });
  if (masterStatusFilter) {
    masterStatusFilter.addEventListener('change', () => loadList());
  }
  btnMasterRefresh.addEventListener('click', loadList);

  // Initialize
  updateSectionTitles('create');
  masterListTable.classList.add(`module-${activeModule.replace(/[^a-z0-9_-]/gi, '-')}`);
  applyMasterLayoutMode();
  configureMasterStatusFilter();
  applyListCentricLabels();
  renderListHeader();
  renderForm();
  attachFormChangeWatchers();
  if (config.allowCreate === false) {
    btnNewRecord.disabled = true;
    btnSaveRecord.disabled = true;
    if (btnListAdd) btnListAdd.disabled = true;
    if (btnModalSave) btnModalSave.disabled = true;
  }
  if (activeModule === 'session') {
    btnDeleteRecord.textContent = config.deleteLabel || 'Terminate';
    if (btnListDelete) btnListDelete.textContent = config.deleteLabel || 'Terminate';
    if (btnModalDelete) btnModalDelete.textContent = config.deleteLabel || 'Terminate';
  }
  setBaselineSnapshot();
  updateListCentricActionButtons();
  const initialClientId = new URLSearchParams(window.location.search).get('client_id');
  if (activeModule === 'account' && initialClientId) {
    loadList(initialClientId);
  } else {
    loadList();
  }



    function formatPlatformDateTime(date) {
      const monthNames = [
        'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
        'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'
      ];
      const month = monthNames[date.getMonth()];
      const day = String(date.getDate()).padStart(2, '0');
      const year = date.getFullYear();

      const hours24 = date.getHours();
      const minutes = String(date.getMinutes()).padStart(2, '0');
      const period = hours24 >= 12 ? 'PM' : 'AM';
      const hours12 = String((hours24 % 12) || 12).padStart(2, '0');

      return `${month} ${day}, ${year} ${hours12}:${minutes} ${period}`;
    }

    function updateDateTime() {
      const now = new Date();
      const formatted = formatPlatformDateTime(now);
      document.getElementById('platformDateTime').textContent = formatted;
    }

    updateDateTime();
    setInterval(updateDateTime, 60 * 1000);
  