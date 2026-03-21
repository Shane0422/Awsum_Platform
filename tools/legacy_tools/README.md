# legacy_tools

This folder contains legacy or temporary diagnostics that are not part of the active Client-based toolset.

## Files moved here

- `db_customer_columns.py`
  - Legacy schema probe for `tb_customer`, `tb_customer_auth`, `tb_account`.
  - Kept only for historical migration checks.
- `db_customer_counts.py`
  - Legacy row-count probe tied to `customer/account` naming.
  - Replaced by `tools/db_client_counts.py`.
- `db_customer_schema_check.py`
  - Legacy keyword scan focused on `customer` columns/tables.
  - Replaced by `tools/db_client_schema_check.py`.
- `crud_regression_out.txt`, `ui_regression_out.txt`, `regression_out.txt`
  - Temporary command outputs, not source scripts.
- `_terminal_probe.txt`
  - Temporary terminal probe artifact.

If a file here must be reused, migrate it back to `tools/` with Client terminology and clear scope notes.
