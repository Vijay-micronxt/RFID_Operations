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

## Handheld app login (`app_auth`)

`POST login(usr, pwd)` needs no session. It checks the password as Frappe's own login does (same lockout
after repeated failures, same IP and login-hour rules) and returns
`{user, full_name, api_key, api_secret, stations[]}`. The app then sends
`Authorization: token <api_key>:<api_secret>` on every call and holds no session.

- The secret lives from one login to the next logout: **every login creates a new secret** (the key stays the same),
  so the secret from an earlier login stops working. `POST logout()` (sent with the current token) cancels the
  secret on the server as well. So a second device logging in as the same user signs the first one out, and
  anything else using this user's API key stops working at each app login: use personal accounts, never a
  service account.
- Refused (401): wrong password, `Administrator` / `Guest`, non-system users, an expired password, and users with
  two-factor login (the app has no screen for the code, and skipping it would bypass the second factor).
- Only users with the Stock Audit station (RFID Stock Operator / Store Manager) can log in; others get 401 and no key.
- Each login and logout is recorded in the Activity Log ("Logged in / out from the RFID app"); failures are logged by Frappe.
- Limited to 30 calls a minute per IP.

## Sending a count from the handheld (`physical_audit`)

`POST push(client_id, warehouse, downloaded_at?, items[], tags[], device_info?)` turns what the phone counted into a
**Physical Audit in Draft** (`PA-.YYYY.-.#####`) and returns `{name, duplicate}`. It changes no stock and no tag.
It needs the Stock Audit station and create permission on Physical Audit (RFID Stock Operator / Store Manager).

- `items[]`: `{item_code, system_qty, scanned_qty, erp_stock_qty}`. The difference is worked out on the server.
- `tags[]`: `{epc, item_code?, result}` with result `Matched` (expected, read), `Missing` (expected, not read) or
  `Unknown` (read, not expected in this warehouse).
- `client_id`: an ID the app makes up for the push. Sending the same one again (a retry after a lost reply) returns the
  record already made with `duplicate: true`; a new push has a new ID and makes a new record, even for the same
  warehouse on the same day.
- The record keeps what the phone saw, as of `downloaded_at`; the server does not recalculate it.
- Draft can be edited (notes); **Submit** locks it and needs the Store Manager or Owner role. Operators cannot submit or delete.
- **Create Stock Reconciliation** (button on a *submitted* Physical Audit; method
  `rfid_retail.rfid_retail.doctype.physical_audit.physical_audit.make_stock_reconciliation(name)`) makes a **draft**
  Stock Reconciliation for the audit's warehouse, applying each item's **Difference** (system minus scanned) to its current ERPNext
  stock **both ways**: a positive difference (pieces not found) lowers the stock, never below zero; a negative difference (more found
  than the system knew) raises it. Nothing is submitted, so stock only changes when someone submits that draft. Only the pieces the
  audit knows about (tagged ones) are touched, so untagged stock stays as it is; serial and batch items are left out. A raise needs a
  cost per piece: the item's current rate, else its valuation / last purchase / standard rate; a row with none is still put in the
  draft and listed in `no_rate` for someone to fill in before submitting. Pressing it again returns the draft already made; if that
  draft is deleted or cancelled, the audit forgets it and the button returns. The difference account is the company's default
  (Stock Adjustment). The caller needs create permission on Stock Reconciliation (Stock Manager) and read on Physical Audit, so a user
  needs both Stock Manager and RFID Store Manager.
- The phone cannot produce a negative difference (it only counts tags the ERP expects; a tag it does not know is listed as Unknown and
  does not raise stock). A raise comes from an audit whose Scanned Qty was corrected on the draft before it was submitted.

## Offline count on a handheld (`catalog`)

`GET audit_warehouses()` lists the warehouses a handheld can count, for its dropdown: `[{name, tags}]`
(real stock locations only, not groups or disabled ones; `tags` is the number of in-stock tags there).

`GET audit_snapshot(warehouse, start=0, page_length=2000)` is read-only (needs the Stock Audit station;
creates no session). It returns what the warehouse is expected to hold, so a handheld can compare its
own reads without sending anything back:

| Field | Meaning |
|---|---|
| `tags[]` | `{epc, item_code, serial_no, component}`, one per expected tag, paged (`start`, `page_length`, max 5000) |
| `items[]` | `{item_code, item_name, uom, tag_qty, stock_qty}`; `tag_qty` counts units (a set is one), `stock_qty` is ERPNext's quantity in the warehouse |
| `total_tags`, `generated_at` | page through until `start + page_length >= total_tags`; `generated_at` is the snapshot time |
