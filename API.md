# RFID Retail API

All endpoints are Frappe whitelisted methods: `POST /api/method/rfid_retail.api.<module>.<method>`
(GET works for read-only ones). Authenticate with a session cookie + `X-Frappe-CSRF-Token`,
or with `Authorization: token <api_key>:<api_secret>` for device bridges and custom frontends.
List arguments (`codes`, `payments`) accept a JSON array or newline/comma-separated text.

Every business rule is enforced server-side, including on the Sales Invoice itself. A frontend
cannot bypass set verification, counter locking or the sale-before-detach rule.

## Counter billing (`pos`)

| Method | Args | Returns |
|---|---|---|
| `counter_config` | `pos_profile` | `{enabled, require_scan, allow_label_fallback, device}` for a POS frontend; never throws (used by POS Next RFID mode) |
| `get_counters` | – | POS Profiles the user may bill on, with tray devices and payment modes |
| `open_basket` | `pos_profile`, `device?` | basket view (reuses the cashier's open basket) |
| `scan` | `basket`, `codes[]`, `source` = RFID/Barcode/Manual | basket view + `events[]` (rejected reads) |
| `remove_unit` | `basket`, `serial_no` | basket view |
| `basket_view` | `basket` | `lines[]` (one per sellable unit, `pieces[]` with `verified`), `blocked`, `totals` |
| `checkout` | `basket`, `payments[{mode_of_payment, amount}]`, `customer?`, `redeem_points?` | `sales_invoice`, totals, change. Retrying a billed basket returns the same invoice |
| `cancel_basket` | `basket` | releases the units |
| `find_customer` / `create_customer` / `loyalty_balance` | mobile / name+mobile / customer | |

`scan` accepts tag EPCs, printed piece labels (`SUIT-000001-JKT`) and standalone serial labels.
Labels are logged as barcode fallback. A set label on its own never verifies a set.

## Mapping station and detacher (`mapping`)

`pending_units`, `unit_detail(serial_no | piece label | tag)`, `map_tag(epc, serial_no, component?)`,
`replace_tag(old_epc, new_epc, reason)`, `detach(codes[])`, `release_return(serial_no)`,
`set_alteration(component, out=1|0, custodian?, expected_return?)`, `identify(codes[])`.

## Exit gate and devices (`gate`)

`gate_read(codes[], device)` returns `{alarm, events[]}`. The alarm is judged against the tag's
current assignment, not historical sold flags. `heartbeat(device, status)`: a device silent past
`stale_after_minutes` goes Offline and raises an exception (checked every 5 min).

## Handheld audit (`audit`)

`start(warehouse, device?)` freezes the expected position, `upload(session, batch_id, codes[], area)`
is idempotent per `batch_id`, then `compare(session)`, `submit_for_review(session)`,
`mark_reviewed(session)` (reviewer must differ from the operator), and `summary(session)`.
