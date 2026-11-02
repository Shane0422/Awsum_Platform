# AWSUM MONEY INPUT STANDARD

THIS DOCUMENT IS THE AUTHORITATIVE AWSUM MONEY INPUT STANDARD.

Before adding or changing any monetary input or monetary presentation,
developers and coding agents (including Codex) MUST read this document.
Do not introduce a conflicting or screen-specific implementation without
explicit approval.

## 1. Permanent rule and scope

Every monetary input in AWSUM MUST use **AwsumMoneyInput** and the common money
formatting system. Do not copy its logic or implement local money masks,
regular expressions, comma removal, `toFixed(2)`, `Intl.NumberFormat`, or
component-specific currency formatters.

This applies to all current and future Master, POS, KIOSK, Sales, Payment,
Refund, Discount Amount, Tax Amount, Cost, Price, Billing, Invoice, Agency,
Merchant, Store, Reports and any other monetary field, including calculated
and read-only monetary inputs.

Classification is semantic, not a guess based on a label:

- **Money:** prices, costs, fees, monetary commissions, payment/refund amounts,
  discount amounts, tax amounts, subtotals and totals.
- **Not money:** percentages (tax/discount/commission/transaction rates),
  quantities, limits, IDs, codes, display order, dates and durations.
- The License `agency_commission` is a monetary `Numeric(12, 2)` value.
  Agency `commission_rate` is a percentage and MUST NOT use a money input.

## 2. Presentation versus stored values

Presentation is fixed **`#,##0.00`**, independent of browser locale:

| Numeric/decimal value | Presentation |
|---|---|
| `2500` | `2,500.00` |
| `2500.5` | `2,500.50` |
| `1000000` | `1,000,000.00` |
| `0` | `0.00` |
| `-2500.5` (when permitted) | `-2,500.50` |

Currency symbols/codes remain outside the editable value. Existing USD prefixes
and currency selectors are preserved; the formatter does not convert currencies.

DB and API values remain numeric/decimal **without commas or currency symbols**.
Preserve each endpoint's existing contract: decimal strings such as `"2500.50"`
remain valid where already used for exact Decimal values; numeric JSON fields
remain numeric where required. Never send `"2,500.50"` to an API or database.
Do not convert exact decimal strings to binary floats just to remove grouping.

Unset/blank and zero are different: blank stays blank/null under the endpoint's
existing rules, zero displays `0.00`, and existing "Not set" UI remains "Not set".
Do not change database schema, billing calculations, business rounding or
permissions as part of presentation changes.

## 3. Shared implementation

- Browser component/controller and formatter:
  [static/js/money.js](../static/js/money.js), global `AwsumMoneyInput`.
- Server-side monetary report presentation:
  [backend/services/money.py](../backend/services/money.py), `format_money`.
- The existing Platform, Merchant, shared Dashboard and Store base layouts load the
  same browser implementation before dependent screen scripts.

The current application uses Jinja/JavaScript inputs. `AwsumMoneyInput.bind`
is the common input component/controller; it does not require a separate widget
per screen. Future Svelte/POS/KIOSK wrappers must call this same implementation,
not reproduce its formatting logic. No standalone money input currently exists
in the Svelte starter screens.

### Input lifecycle

- Unfocused input displays grouped fixed-two-decimal presentation.
- Focus removes grouping for editing. Typing does not reformat on every
  keystroke or move the caret.
- Blur validates and restores `#,##0.00`.
- Correctly grouped pasted values are accepted.
- Malformed grouping, alphabetic text, currency symbols, non-finite amounts,
  exponent strings, or more than two typed fractional digits are rejected with
  explicit native validity/accessible error state. Invalid text is not silently
  stripped, rounded or replaced by zero.
- Blank stays blank. Range rules use `data-money-min` / `data-money-max`;
  preserve domain-specific required/min/max rules.
- Display of calculated values uses decimal half-up rounding to two places.
  This is presentation only; backend business arithmetic remains authoritative.

### Existing HTML or dynamically created inputs

```html
<input id="price" name="price" class="form-control"
       data-awsum-money data-money-min="0">
```

```javascript
const input = document.getElementById('price');
AwsumMoneyInput.bind(input);
// Or bind marked descendants after inserting a dynamic form:
AwsumMoneyInput.bindAll(container);

// API/DB value -> grouped presentation, including computed/read-only fields:
AwsumMoneyInput.setValue(input, item.price);

// Submission -> validated comma-free decimal string; invalid values throw:
const payload = { price: AwsumMoneyInput.getValue(input) };

// Table, summary or report cell:
cell.textContent = AwsumMoneyInput.format(item.price);
```

Do NOT submit a marked input's `.value`, `FormData(form)` or native form contents
directly: its presentation may contain commas. Serialize monetary fields through
`getValue` before building the endpoint's JSON/multipart payload. Existing MD
multipart JSON uses this same normalized value. Surface validation failures
through the existing form error mechanism and do not send a request.

For programmatic changes use `setValue`, not a screen-specific formatter.
After resetting a form or assigning a set of raw values, use `refreshAll`.
`destroy(input)` removes listeners when a bound input is explicitly reused by
another controller. Binding is idempotent.

The Master Management metadata uses explicit **`type: 'money'`**. Its common
`createField` binds the control, its collector normalizes it, and its populate
and reset flows use the shared service. Do not infer money from arbitrary
field names or change every numeric field.

`toMinorUnits` / `fromMinorUnits` provide exact BigInt conversion for existing
cent-based calculations. Percentage parsing and domain calculations are separate
concerns and must not be reclassified as money formatting.

### Server-side reports

```python
from backend.services.money import format_money

display = format_money(amount)  # Decimal/int/float/decimal string -> #,##0.00
```

Use the common helper for human-readable monetary output. Keep API serializers,
numeric CSV/data contracts and business Decimal calculations unformatted.
Do not feed formatted display strings back into arithmetic.

## 4. Current project migration inventory

The audit covered current application templates, static JavaScript, frontend
source and backend monetary/report uses, excluding dependencies, generated
bundles and obsolete scratch tools.

| Surface | Migrated money inputs |
|---|---|
| MD Master | Retail Price, Cost (permission-controlled), P1, P2, P3 |
| License | Monthly Fee, Agency Commission amount, Platform Fee |
| Subscription | Read-only Subscription Fee Snapshot |
| Contract | Setup, monthly base/device/user/total fees, Tax Amount, Total Monthly Fee |
| Pricing Plan | Store Base, POS, Kiosk, Mobile Order, Extra User, Setup, Extra Device fees |
| Device Type | Default Monthly Fee |
| Invoice | Subtotal, Tax amount, Total |

There are **27 monetary controls** (5 MD and 22 metadata-defined controls,
including read-only controls). No current monetary input was found in the
Svelte starter, Merchant/Store dashboards or the quantity-only Device/License
issue dialogs. No not-yet-built Sales/Payment/Refund/Employee/POS/KIOSK UI was
invented; the permanent rule applies when those screens gain monetary fields.

MD price list cells, pricing-plan rows, subscription/contract/invoice/device-type
list cells and fee previews use the shared browser formatter. MD human-readable
exports and Contract/Invoice reports use the shared server formatter.

MD retail-discount calculation remains exact half-up cents; discount inputs
remain percentages. Non-image edits, image processing, codes, IDs, tax-rate
precision, limits and display-order behavior are unchanged. Monetary grouping
is removed before payload construction, numeric sorting and dirty-state
comparison; focusing a money field alone does not make the record dirty.

## 5. Verification and future work checklist

Run from the program root:

```powershell
node --test tools\test_money.cjs
py -W ignore -m unittest tools.test_money tools.test_money_browser tools.test_master_group tools.test_reporting
```

The browser tests reuse the existing isolated Chromium runner. Set
`AWSUM_REPORT_CHROME` if Chrome/Edge is not at the default Windows location.
They run the actual MD form code and all 22 metadata-defined money controls
with mock HTTP persistence, not the live business database. They verify
grouping, comma-free multipart/JSON values, invalid-input blocking, exact
discount calculation, read-only state, numeric subscription payloads,
numeric sorting and unchanged-edit detection.

When adding a monetary surface:

- [ ] Read this document first.
- [ ] Reuse `AwsumMoneyInput`, including read-only monetary controls.
- [ ] Use `format` / server `format_money` for human-readable money.
- [ ] Preserve currency, blank/null/zero, domain bounds and permissions.
- [ ] Normalize API payloads through `getValue`; no comma-formatted DB/API values.
- [ ] Keep percentages, quantities, IDs, codes and order fields separate.
- [ ] Test typing, paste, focus/blur, reset, invalid input and API payloads.
- [ ] Verify `2500`, `2500.5`, `1000000`, zero and unset behavior.
- [ ] Verify derived totals/discounts, numeric sorting and unchanged-edit detection.
- [ ] Extend the current inventory/tests when new monetary fields are introduced.

**Permanent Codex/developer rule:** Before adding or changing monetary fields,
read `docs/AWSUM_MONEY_INPUT_STANDARD.md` and reuse the common implementation.
Do not introduce screen-specific money formatting or a conflicting architecture
without explicit approval.
