# Damaged / missing tag sale, PIN approval and item-wise stock count

This release covers one store problem end to end: a customer brings a piece whose RFID tag is
damaged or missing, and the piece still has to be sold.

Before this release:

- If every piece of an item in stock had a tag, the counter refused to add that item by hand.
- If someone sold the piece another way, the item was left with one tag more than stock, for
  example 5 tags for 4 pieces. Nothing recorded which tag was left over.
- The stock count always covered the whole warehouse. You could not check just one item.

| Part | App | What it adds |
|---|---|---|
| 1. Sale when the tag will not read | rfid_retail + POS Next | Sell the piece by hand with a reason; the leftover tag is recorded |
| 2. Approval by PIN | rfid_retail + POS Next | An approver (new role **RFID Approver**) approves with a one-time PIN or one click |
| 3. Item-wise stock count | rfid_retail | Count only some items, item templates or item groups; close the leftover tag from the result |
| 4. Faster stock count | rfid_retail | Whole warehouse count is fast; loading screen, search, working Discard |

Pull requests: RFID_Operations (rfid_retail) and POSNext (POS Next). Both must be deployed
together.

---

## 1. Selling a piece whose tag will not read

**At the counter (POS Next)**

1. The cashier picks the item by hand. It can be from the item grid or by scanning its barcode.
2. Every piece of that item in stock has a tag, so the counter would normally block it. Now a
   **Tag will not read?** dialog opens instead.
3. The cashier chooses **Tag Damaged** or **Tag Missing**. A note is optional, for example
   "tag torn".
4. The sale needs approval (see part 2). Once approved, the piece goes into the cart and the bill
   is paid as usual.

**What the system records**

- The approval is stored on the bill's RFID basket. It holds the item, quantity, reason, note,
  who approved and the request.
- When the bill is submitted, the server checks again. A bill cannot carry more hand-picked pieces
  than the untagged stock plus the approved pieces.
- After payment, the item has one tag more than its stock. The system creates an
  **RFID Unread Sale** (URS-YYYY-#####) with the invoice, item, warehouse, quantity, reason and
  approver. It stays **Open** until a stock count settles it (part 3).
- If the invoice is cancelled, the Unread Sale becomes **Cancelled**.
- The RFID Exception list also gets a *Barcode Fallback* entry that names the Unread Sale.

Only items without serial numbers are covered. A serial item is sold by picking its serial.

## 2. Approval by PIN

**Who approves:** users with the new role **RFID Approver**. The role is mapped to the new
station **Unread Sale Approval** in RFID Settings, and the station's role can be changed there.

**How it works**

1. The cashier presses **Ask approver for PIN**.
2. Every approver gets **their own 6 digit PIN**:
   - in the desk **notifications (bell)**, always, for example: *"Billing 1 at Chandrlok POS asks
     to sell 1 x LEO SILK without reading its RFID tag (Tag Damaged). Note: tag torn. PIN: 311408
     (valid till 10:19)."*
   - by **WhatsApp** to the Mobile No on their user, when switched on.
3. The approver tells the cashier the PIN. The cashier types it and presses **Confirm PIN**.
   Alternatively, the approver opens the notification and presses **Approve** or **Reject**. The
   counter screen moves on by itself within about 3 seconds.

**Rules**

- A PIN works once, and only for that request: that bill, that item, that quantity.
- A PIN is valid for **10 minutes** by default.
- **5 wrong PINs lock the request.** The cashier then presses **Ask again**.
- PINs are stored only as a keyed hash, never as plain text.
- The cashier cannot approve their own request.
- Every request is kept as an **RFID Approval Request** (APR-YYYY-#####). It records who asked,
  who approved or rejected, how (PIN or button), the wrong tries, and the WhatsApp result per
  approver.
- A cashier who also holds the approval station sees **Sell without tag** and needs no PIN.

> **Note:** System Manager is counted in every station, including this one. A System Manager user
> at the counter therefore gets **Sell without tag** without a PIN. Give cashiers a user without
> System Manager.

**Other approval modes** (RFID Settings > *How the approver approves*)

- *PIN sent to approvers* (default)
- *Approver login and password*: the approver types their own login at the counter
- *PIN or approver login*: both are offered

## 3. Item-wise stock count

On the **Stock count** tab (`/rfid`):

1. Choose the warehouse, then **What to count**:
   - **Whole warehouse**, as before, or
   - **Some items only**: search and pick one or more items, a **template** (all its variants)
     or an **item group** (with its sub-groups).
2. Press **Start count** and scan. With some items only, only their tags are expected. Tags of
   other items read on the way are ignored.
3. Press **Finish count & show result**. **Tags not read, by item** shows each item, for example
   "4 of 5 read", with every tag that was not read. Each tag has two buttons:
   - **Sold without tag read**: shown when the item has an open Unread Sale. The tag is closed
     against that sale's invoice (marked Lost, because it left with the garment) and the Unread
     Sale becomes **Settled**. When the tags not read are no more than the pieces sold unread, one
     button settles them all.
   - **Piece is here, tag dead**: the piece is on the shelf but its tag does not read. The tag is
     marked Damaged, and the piece returns to the *To tag* list for a new tag.
4. Anything else goes through the manager's review as before.

**The full cycle, using the example**

```
5 pieces, 5 tags
→ customer's piece has a dead tag → "Ask approver for PIN" → approved → paid
→ 4 pieces, 5 tags, Unread Sale URS-… Open (1 waiting)
→ stock count of that item: 4 read, 1 not read → "Sold without tag read"
→ 4 pieces, 4 tags, URS Settled
```

## 4. Faster stock count

For a whole warehouse of 2261 tags:

| Step | Before | Now |
|---|---|---|
| Start count | 9 s | about 2 s |
| Each scan | 1.6 to 1.9 s | **under 0.1 s** |
| Finish count | 4.4 s | about 1.7 s |
| Discard | several seconds | about 0.05 s |

Changes on the count screen:

- **Start**, **Finish**, **Send**, **Settle** and **Discard** show a full-screen spinner with a
  message, so the screen never looks hung. Buttons are disabled while it runs.
- The scan box shows "Saving n scans…" and then "Saved".
- A **search box** ("Find an item, code or tag in this count") works on whole warehouse counts
  too, for example "LEO SILK: 2 of 31 read".
- Lists show 150 rows at a time, not-found first. Search to find the rest.
- **Discard** now uses the page's own confirm dialog. The browser's confirm() was being blocked
  in some browsers, so Discard did nothing.

---

## New settings (RFID Settings)

| Setting | Default | Meaning |
|---|---|---|
| Allow sale when the tag will not read | on | Off brings back the old hard block |
| Unread-tag sales need an approver | on | Off: the cashier decides alone, with a reason |
| How the approver approves | PIN sent to approvers | PIN / approver login and password / either |
| PIN valid for (minutes) | 10 | |
| Send the PIN by WhatsApp | off | Needs the API URL, Sender ID and Auth Token |
| WhatsApp API URL | BotMasterSender send URL | Any API that takes senderId, authToken, receiverId, messageText |
| WhatsApp Sender ID / Auth Token | (empty) | |
| Test mobile | (empty) | While set, every PIN goes to this number only. Clear it to go live |

All defaults are filled on migrate.

## New in the system

- **Role:** RFID Approver.
- **Station:** Unread Sale Approval. A patch adds it with the RFID Approver role on sites that
  are already set up.
- **DocTypes:**
  - RFID Unread Sale (URS-)
  - RFID Approval Request (APR-) with RFID Approval Request Approver
  - RFID Basket Unread (approvals on a basket)
  - RFID Audit Scope Item (items or groups of a count)
- **Fields:**
  - RFID Audit Session: scope *Selected Items* and the items counted
  - RFID Audit Result: resolutions *Tag Damaged* and *Sold Without Read*, and a link to the
    Unread Sale
- The RFID Retail workspace has a link to **RFID Unread Sale**.

## Deploy

1. Pull both repos: RFID_Operations (rfid_retail) and POSNext (pos_next).
2. Run `bench --site <site> migrate`. This creates the role, station, doctypes and settings
   defaults.
3. Run `bench build --app rfid_retail` and `bench build --app pos_next`. The counter needs the
   new POS build.
4. Run `bench restart`.

## After deploy (each store)

1. Give each approver (owner or manager) the **RFID Approver** role, and fill **Mobile No** on
   their user.
2. Make sure cashiers do **not** have RFID Approver or System Manager. Otherwise they approve
   themselves.
3. For WhatsApp PINs, open RFID Settings > *Approval of unread-tag sales* and:
   - fill the Sender ID and Auth Token (on Chandrlok, the same ones as *Invoice WhatsApp
     Settings*),
   - set **Test mobile** to your own number,
   - tick **Send the PIN by WhatsApp**,
   - try one sale, then clear Test mobile.
4. Approvers need a desk login to see the bell and the Approve button. WhatsApp works without
   one.
5. Run an item-wise count now and then, so open Unread Sales get settled. The list is at
   **RFID Unread Sale** (status Open).

## Tested

- Automated scenario test, 63 checks, all passing:
  - hand sale refused without approval
  - approval by login, by PIN and by button
  - wrong PIN, lock after 5, expiry, reject
  - no-approver message
  - WhatsApp request format
  - Unread Sale created, settled by a count, and cancelled with the invoice
  - item, template and group count scope; other items ignored
  - dead-tag action
  - switch off
- Browser checks on a copy of Chandrlok's data:
  - POS dialog: PIN wrong, then right; one-click approval
  - count screen: loading cover, search, discard
- Not tested: a real WhatsApp send (the request was simulated); a full checkout clicked through
  the POS screen (submit was tested on the server).
