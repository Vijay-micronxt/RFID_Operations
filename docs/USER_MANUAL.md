# RFID Retail: user manual

This manual explains how a store uses `rfid_retail` day to day: setting it up, tagging stock,
billing at the counter, removing tags, counting stock and fixing mistakes. It is written for
store managers, stock staff and the people who set up a site. For the developer overview see the
[README](../README.md); for device and frontend calls see [API.md](../API.md).

Billing happens in POS Next. The POS side (shifts, cart, payment, returns, closing) is covered in
the [POS Next user manual](https://github.com/Vijay-micronxt/POSNext/blob/develop/docs/USER_MANUAL.md).
Section 7 below only covers what RFID changes at the counter.

**Contents**

1. [How it works in one page](#1-how-it-works-in-one-page)
2. [Who does what (roles and stations)](#2-who-does-what-roles-and-stations)
3. [First-time setup](#3-first-time-setup)
4. [Buying stock: purchase pricing, stickers](#4-buying-stock-purchase-pricing-stickers)
5. [Tagging stock](#5-tagging-stock)
6. [The station app (/rfid)](#6-the-station-app-rfid)
7. [Selling at an RFID counter](#7-selling-at-an-rfid-counter)
8. [Returns, cancels and corrections](#8-returns-cancels-and-corrections)
9. [Daily stock count](#9-daily-stock-count)
10. [Exceptions: what went wrong and where](#10-exceptions-what-went-wrong-and-where)
11. [RFID Settings reference](#11-rfid-settings-reference)
12. [Troubleshooting](#12-troubleshooting)

---

## 1. How it works in one page

- Every garment carries a **reusable RFID tag**. The tag is a sticker or a hang tag with a chip.
  A reader sees it without a line of sight, and a tray reader sees many at once.
- ERPNext still keeps the stock. The tags only say *which* pieces are on the floor.
- An item works in one of two ways:
  - **Without serial numbers** (the usual case). A tag stands for one piece of that item in that
    warehouse. Identical pieces (same style, colour and size) are tagged together: put all their
    tags on the reader at once, and any tag can go on any piece.
  - **With serial numbers.** Each piece has its own Serial No and the tag points at that piece.
    Use this for sets, for example a suit made of a jacket and a trouser: the set is billed only
    when every piece is on the counter.
- A tag moves through a life cycle:

  | Status | Meaning |
  |---|---|
  | Available | Not on any garment. Can be used. |
  | Reserved | Read into a draft document. Becomes Assigned when the document is submitted. |
  | Assigned | On a garment that is in stock. |
  | Sold - Pending Detach | Sold, but the store waits for the tag to come off at the detach station (only when *After A Sale* is set to keep the tag). |
  | Hold | Kept aside, for example during an alteration. |
  | Damaged / Lost / Retired | Out of use. History is kept. |

- Every change to a tag is written to its history (**RFID Tag Event**): tagged, sold, freed,
  moved, returned, replaced, damaged, lost, found and every refused read. Nothing is ever
  overwritten.
- **Enable RFID** in RFID Settings is the master switch. Off means the app does nothing: no
  checks on invoices, no stations, no scheduled alerts. Item codes, barcodes and purchase pricing
  have their own switches and work even with RFID off.

The normal flow in a store:

```
Purchase Receipt / Purchase Invoice (Update Stock) submitted
        -> Print Stickers (price labels)
        -> RFID Tags button (or Tag garments station): tags go on the pieces
        -> pieces go on the floor
        -> customer brings pieces to the counter, tray reads them, POS Next bills them
        -> tag is freed at once (or at the detach station) and used again
        -> daily handheld count checks the floor against ERPNext
```

## 2. Who does what (roles and stations)

The app creates five roles on install:

| Role | Typical person |
|---|---|
| RFID Owner | Owner who reviews counts and exceptions |
| RFID Store Manager | Manager: everything in the store, including overrides |
| RFID Cashier | Counter staff |
| RFID Stock Operator | Staff who tag stock and run the daily count |
| RFID Device | Login used by a reader bridge or exit gate |

What a role may do is set per **station** in *RFID Settings > Station access*. Every screen and
every API checks this table. **System Manager can always use every station.** A station with no
rows is closed to everyone else.

| Station | What it unlocks | Default roles |
|---|---|---|
| Billing | Selling at an RFID counter in POS Next | RFID Cashier, RFID Store Manager |
| Tag Mapping | Tag garments, tag history | RFID Stock Operator, RFID Store Manager |
| Tag Repair | Replace a damaged tag, mark lost / found | RFID Store Manager |
| Tag Removal | Take a tag off an item that was **not** sold (also needs *Allow removing tags from unsold items*) | RFID Store Manager |
| Detach | Remove tags of sold items | RFID Cashier, RFID Stock Operator, RFID Store Manager |
| Exit Gate | Gate reads | RFID Device, RFID Store Manager |
| Device API | Reader heartbeats and uploads | RFID Device, RFID Store Manager |
| Stock Audit | Run the handheld count | RFID Stock Operator, RFID Store Manager |
| Audit Review | Review and accept a count | RFID Owner, RFID Store Manager |
| Manager Override | Approve a sale that broke a rule (logged) | RFID Store Manager |

A cashier also needs the normal POS Next role and must be listed on the POS Profile. See the POS
Next manual, section 2.

## 3. First-time setup

Do these once per site, in this order.

1. **Install** (developer):
   ```bash
   bench get-app https://github.com/Vijay-micronxt/RFID_Operations --branch main
   bench --site <site> install-app rfid_retail
   bench --site <site> migrate
   ```
2. **RFID Settings** (search "RFID Settings" in the desk):
   - Tick **Enable RFID**.
   - Check the **Sale rules** (section 11). The defaults suit most stores.
   - Fill **Station access** with the roles your staff hold.
3. **Give users their roles** (User > Roles): RFID Cashier for counter staff, RFID Stock
   Operator for stock staff, RFID Store Manager for the manager.
4. **Mark the items that carry tags.** On each Item (or the template, so all variants follow)
   tick **RFID Tracked Garment**. Optional: a **Style Code**.
   - For sets: tick *Has Serial No* with a Serial No Series, then add a **Garment Set
     Definition** on the template listing its pieces (Garment Piece master, e.g. JKT, TRS).
5. **Mark the counter.** On the POS Profile used at the RFID counter tick **RFID Counter**.
6. **Add the readers** as **RFID Device** records:
   - *Tray Reader* linked to the counter's POS Profile (one per counter).
   - *Handheld* for the daily count (optional; the count also works by typing or scanning).
   - *Exit Gate* linked to the warehouse, if the store has one.
   Most tray and handheld readers here work as a **keyboard wedge**: they type each tag code and
   press Enter, like a barcode scanner. Nothing else needs installing.
7. **Optional switches** (each has its own tab in RFID Settings): Item Codes, Barcodes & Labels,
   Purchase Pricing. See section 4.

**Existing stock.** Stock already in the warehouse before go-live has no tags. Tag it at the
station (section 6, *To tag*): every item with untagged stock is listed there.

## 4. Buying stock: purchase pricing, stickers

### Purchase pricing (RFID Settings > Purchase Pricing)

Turn on **Work out MRP on purchase rows** to let the purchase document set the selling price.

- On Purchase Order, Purchase Receipt and Purchase Invoice rows:
  `MRP = rate per stock unit x (1 + GST %) x (1 + Margin %)`, rounded **up** to the multiple set
  in *Round MRP up to* (10 turns 647 into 650).
- **Margin %** starts from *Default margin %*. Change it on a row to change that row's MRP.
- **MRP can be typed** on the row. The typed MRP is kept and the Margin % is worked back from it.
- **Cost Price** = rate x (1 + GST %), shown on the row and stored on the buying Item Price.
- On **submit** of a Purchase Receipt or Purchase Invoice, the item's selling price (Standard
  Selling Item Price) becomes the MRP, or the offer price when an offer is set.
- **Offers on MRP** (own switch): a selling Item Price carries MRP and Offer %, the selling rate
  is MRP less the offer, and labels print the MRP struck through next to the offer price. Use
  *Selling Price > Set Offer on Variants* on an item template to change all its variants at once.
- **Purchase entry helpers** (own switches):
  - *Fill rows from Item Name and the item's tax*: a Data Import file that has only an Item Name
    gets the item with that name, and its Item Tax Template from the item's tax.
  - *Add Variants buttons*: **Add Variants** (all variants of a template with quantities in one
    dialog) and **Update Template** on purchase item tables.
  - *Keep numeric attribute ranges in step*: saving a numeric Item Attribute updates every item
    using it.

### Stickers

On a **submitted** Purchase Receipt, Purchase Invoice with Update Stock, or Stock Entry, press
**Print Stickers**. A **Sticker Print Job** opens with the items filled in.

- Type the number of copies per row (or let the received quantity fill it: RFID Settings >
  *Label Copies From A Document*). Serial items always get one label per piece.
- Save, then Print. Two layouts:
  - **Retail Barcode Label**: name, size/colour, barcode, code, price, store name. Size is set
    in RFID Settings > Barcodes & Labels.
  - **Item Sticker 80x30**: QR code, item code, name, size / series / finish, MRP.
- A Sticker Print Job can also start from selected Items or Serial Nos (list view action), or
  from the stock on hand in a warehouse.

The printed label also works as a **fallback** at the counter when a tag will not read
(section 7).

### Item codes and barcodes (optional)

- *Item Codes* tab: item codes and names are built from patterns such as
  `{supplier_code}-{item_name}` and `{template}-{attr_abbrs}` for variants (gives
  `ZOD-FORMAL-SHIRT-RED-40`). Applies when Item Code is left blank and to every new variant. A
  typed code always wins; a duplicate is refused. The list of tokens is on the settings page.
- *Barcodes & Labels* tab: every new stock item gets a barcode (EAN-13 in the in-store `2x`
  range, a plain number, or the item code). Items that already carry a supplier barcode keep it.

## 5. Tagging stock

There are two ways to tag. Both give the same result.

### A. From the purchase document (RFID Tags button)

Best when stock has just arrived.

1. Submit the Purchase Receipt (or Purchase Invoice with **Update Stock**, or a Stock Entry).
   The **RFID Tags** button appears only after submit.
2. Press **RFID Tags**. Pick the row.
3. Put the tags of that row's pieces on the reader together. Each tag is checked as it is read:
   a tag that is unknown, already in use, damaged or lost shows in red and blocks saving.
4. Incoming stock can be tagged in part: up to the number of pieces still waiting for a tag.
   Stock going out or moving (Material Issue, Material Transfer) must match the row exactly.
5. The tags save by themselves when the count is right. The row keeps the list of its tags.

**Check stickers / move tags.** If tags went on the wrong pieces (for example the red shirt's
tags were read on the blue shirt's row), open **RFID Tags > Check stickers / move tags**, read
the pieces, and pick the row of the item the garment really is. The move is all-or-nothing.

What happens to tags on other documents:

| Document | Tags |
|---|---|
| Draft saved | Tags are **reserved** for it |
| Submitted | Tags go **on** the new pieces |
| Draft deleted | Reserved tags are freed |
| Material Issue / purchase return | Tags read on the row are **freed** |
| Material Transfer | Tags **move** to the target warehouse |
| Cancelled | Undone (see section 8) |

### B. At the station (Tag garments)

Best for existing stock, customer returns, and sets. See section 6.

### Items with and without serial numbers

- **Without serial numbers**: the station lists *untagged stock* per item and warehouse
  (stock quantity less the number of tags). Read any of that item's tags in any order.
- **With serial numbers**: every new serial waits for its tag. **Sets** are tagged piece by
  piece at the station: each piece (`<serial>-JKT`, `<serial>-TRS`) gets its own tag.

## 6. The station app (/rfid)

Open **`https://<your-site>/rfid`** on a phone, tablet or PC with a reader. Staff log in with
their normal user. The page shows only the tabs their roles allow (section 2).

### Billing

Opens POS Next (`/pos`). Billing itself happens there (section 7).

### To tag (Tag garments)

- The list shows what waits for tags, grouped by item and warehouse, with the count.
- **Identical pieces**: pick the item (or scan its barcode), put **all** its tags on the reader,
  and they save by themselves when the count is right. A tag read twice counts once. Any tag can
  go on any piece. **Clear reads** starts again; **Done** closes the item.
- **Sets and customer returns** are tagged one garment at a time: the screen asks for the tag of
  each piece in turn ("Scan the tag for ...") and moves to the next piece by itself. Press Enter
  after typing a code by hand.
- **Customer return**: before a returned garment goes back on sale, the screen lists its pieces.
  Check that every piece came back in good condition, then press **All pieces checked - tag
  again**.
- On a tagged piece you can **replace a damaged tag** or **send the piece out for alteration**
  and back.

### Tag history

Read a tag or scan a garment label to see its full story: every garment it was on, who tagged
it, which document, who sold it and at what price. From here a manager can mark a tag
**Damaged** (and put a new tag on the garment), **Lost**, **Found** or **Retired**.

### Remove tags (Detach)

- Read the tags of **paid** items to free them for reuse. Read many at once.
- Pick the reason: **Remove - use again**, **Damaged**, or **Lost**.
- A tag on an item that is **not sold** is not freed. If *Allow removing tags from unsold items*
  is on, users of the Tag Removal station (by default only RFID Store Manager) can remove it.
- *Released this session* shows what was freed since the page opened.

When a tag on an unsold piece is removed, it is also dropped from the purchase row it came from,
so the **RFID Tags** button on that document can tag the piece again.

### Exit gate

Shows the gate reads. Paid garments pass. Anything not sold raises an alarm and an **Exit Alert**
exception for the manager.

### Stock count

See section 9.

## 7. Selling at an RFID counter

This needs POS Next on the same site and a POS Profile with **RFID Counter** ticked. The cashier
must also hold the Billing station. On any other profile, POS Next works as normal.

1. **Open the shift** in POS Next as usual. The RFID tray panel appears above the cart.
2. **Put the garments on the tray** (or scan the tags with a handheld). The reader types the
   codes into the tray box and presses Enter. Many tags can be read at once.
3. **Complete pieces go straight into the cart** with their serial number (serial items) or as
   one more unit of the item (items without serials).
4. **Sets**: a set that is missing a piece stays in the tray panel with each piece marked ✓ or ✕
   ("Set incomplete - read the missing pieces"). **Payment is blocked** until the missing pieces
   are read or the set is removed with **Remove**.
5. **A tag that will not read**: tick **Tag unreadable - scanning printed labels** and scan the
   sticker instead (only if *Accept printed labels* is on). Every label use is logged as a
   *Barcode Fallback* exception.
6. **Picking by hand** from the item grid or by item barcode:
   - If *every* piece of that item in stock has a tag, it is **blocked**: "Every piece of ... in
     stock has an RFID tag. Put it on the RFID tray instead of picking it by hand."
   - If the item has both tagged and untagged pieces in stock, the cashier is asked **Sell
     without reading the RFID tag?** with two choices: **Scan the tag** or **Piece has no tag -
     add**. The cart never takes more hand-picked pieces than there are untagged pieces.
   - With *Every RFID-tracked unit must be read* on, a tagged serial item cannot be picked by
     hand at all.
7. **Pay** as usual in POS Next. On submit the server checks every piece again (read on this
   basket, every set piece verified, not on another counter). The screen cannot skip these
   checks, and neither can a desk Sales Invoice.
8. After the bill is submitted:
   - the tags are **freed at once** for reuse (default), or stay on the garment as *Sold -
     Pending Detach* until they pass the detach station (*After A Sale* setting);
   - a failed payment or a failed submit never frees a tag.

Other rules at the counter:

- **One piece, one counter.** A piece on an open basket at one counter cannot be read at
  another ("... is being billed on another counter").
- **Reload is safe.** The open basket lives on the server and comes back after a page reload.
- **Online only.** The tray needs a connection, so checkout on an RFID counter is blocked while
  offline. Non-RFID counters keep working offline as usual.
- **Messages a cashier may see when reading:**

  | Message | What to do |
  |---|---|
  | `... is not a known tag or label.` | Wrong code or a tag never used. Tag the garment first. |
  | `Tag ... is not on any garment. Tag the garment first.` | Tag the piece at the station. |
  | `Tag ... belongs to a garment that is already sold.` | The tag was not freed. Remove it at the detach station. |
  | `Tag ... is marked Damaged/Lost. Put a new tag on this garment.` | Replace the tag (Tag history). |
  | `... is in <warehouse>, not this counter's warehouse.` | Transfer the stock first. |
  | `Tag ... is on a purchase that is not submitted yet.` | Submit the purchase document. |
  | `... is a printed label. Label fallback is switched off; read the RFID tag.` | Read the tag. |

## 8. Returns, cancels and corrections

### Customer returns

Make the return in POS Next (Return Invoice) or as a Sales Invoice return.

- A returned **serial item** is marked for checking: at the station's *To tag* list it appears as
  a customer return. Check the pieces, then tag it again before it goes back on sale.
- A returned **item without serials** goes back to stock untagged and shows in the *To tag*
  list ("returned ... without a tag").

### Cancelling a purchase document

Only cancel if the document itself is wrong. Cancelling a **Purchase Receipt** or a **Purchase
Invoice with Update Stock** takes its stock back out, and frees every tag it put on garments.

When the document put tags on garments, a warning (**Cancel will free RFID tags**) lists the tags
that become free, per item, before the cancel goes through. Press **Keep document** to stop, or
**Cancel and free tags** to go ahead. The document timeline gets a comment listing the freed
tags.

> A site can widen this warning with a Client Script that replaces
> `rfid_retail.confirm_cancel_tags`, for example to also list the quantity that leaves each
> warehouse on every Purchase Receipt and stock-updating Purchase Invoice, tags or not. If the warehouse then holds more tags
than stock, an *Untagged Stock* exception asks the manager to find those tags in the next count.

If a tag was meanwhile put on another garment, the cancel leaves it there and the old piece is
flagged for a new tag.

> Who may cancel is set by the site's normal permissions (Role Permission Manager). Keep Cancel
> on Purchase Receipt and Purchase Invoice to a manager role.

### Fixing just a wrong tag

There is no need to cancel a document for a wrong tag:

- Tag on the wrong item: **RFID Tags > Check stickers / move tags** on the document.
- Tag damaged: *Tag history > Damaged*, then put a new tag on.
- Tag on an unsold piece that should come off: *Remove tags* (Tag Removal station).

## 9. Daily stock count

Open the **Stock count** tab at `/rfid`.

1. Pick the **Warehouse** (the one with the most RFID stock is first) and, if used, the
   **Handheld**. Press **Start today's count**.
2. **Scan every tag** in the warehouse (Enter after each, or let the handheld type them). Each
   scan saves straight away and ticks the piece on the checklist.
3. When everything is scanned, press **Finish count & show result**.
4. Press **Send for review**. A different user (Audit Review station) must review it.
   **Discard this count** throws away a count started by mistake.

Nothing in stock changes during a count. The result sorts every piece into:

| Result | Meaning |
|---|---|
| Matched | Expected and found |
| Unverified | Expected, not found yet |
| Confirmed Shortage | Expected, confirmed missing |
| Unexpected | Found but not expected here |
| Unmapped | Tag not on any garment |
| Sold but Observed | Sold, yet still in the store |
| Incomplete Set | Some pieces of a set found |
| Held Outside Store | Out for alteration etc. |

If *Daily handheld count required* is on and no count was compared that day, a **Missed Audit**
exception is raised at 23:00.

## 10. Exceptions: what went wrong and where

Everything that went wrong is written to **RFID Exception** (desk list), with severity, status
and links to the serial, piece, device and document. Types include Unmapped Tag, Duplicate
Assignment, Sold Tag Read, Retired Tag Read, Counter Conflict, Incomplete Set, Exit Alert,
Barcode Fallback, Tag Replaced, Device Offline, Audit Shortage, Audit Unexpected, Missed Audit,
Unconfirmed Detach, Manual Override and Untagged Stock.

Check this list daily. Close an exception once it is dealt with.

RFID information also shows on the Item, Serial No, Sales Invoice, POS Profile, Warehouse,
Purchase Receipt, Purchase Invoice and Stock Entry pages (dashboard / Connections).

## 11. RFID Settings reference

**RFID tab**

| Setting | Default | What it does |
|---|---|---|
| Enable RFID | off | Master switch. Off = no RFID rule runs anywhere. |
| Every RFID-tracked unit must be read at an RFID counter before billing | on | Off: only sets need a counter read; single tagged garments may be billed by picking the serial. |
| Accept printed labels when a tag cannot be read | on | Labels count as a read; each use is logged as Barcode Fallback. |
| Block stock items that are not RFID tracked | off | On: nothing can be sold unless it carries an RFID identity. |
| Check RFID items picked by hand against untagged stock | on | The hand-pick warning / block in section 7. |
| After A Sale | Free the tag at once | Or: keep it on the garment until it is read at the detach station. |
| Allow removing tags from unsold items | off | Lets the Tag Removal station take tags off unsold stock. |
| Mark a device offline after (minutes) | 15 | Silent readers go Offline (checked every 5 minutes). |
| Exit gate in use | on | |
| Daily handheld count required | on | Missed Audit at 23:00 when no count was compared that day. |
| Station access | (see section 2) | Who may use each station. |

**Item Codes, Barcodes & Labels, Purchase Pricing tabs**: see section 4. Each has its own switch
and works with RFID off.

## 12. Troubleshooting

| Problem | Check |
|---|---|
| `/rfid` says *No RFID station is assigned to your roles* | Add the user's role to Station access in RFID Settings. |
| `/rfid` says *RFID is switched off* | Tick Enable RFID. |
| No tray panel in POS Next | POS Profile has RFID Counter ticked? Cashier holds the Billing station? Enable RFID on? Reload the POS after changing settings. |
| Only one of many tags is read | Reader must type each code followed by Enter. Read slower or check the reader's suffix setting. |
| A tag code looks short (missing the first letters) | The reader dropped characters. Read the tag again. |
| RFID Tags button missing | The document must be submitted. A Purchase Invoice must have Update Stock ticked. The item must be RFID Tracked Garment. |
| "No row to tag here" | Tick RFID Tracked Garment on the item. Sets are tagged at the station. |
| Hand-picked item blocked at the counter | Every piece in stock has a tag: put it on the tray. |
| A sold garment raises an exit alert | The bill was not submitted, or the tag was read before payment. Check the invoice. |
| Count shows Unexpected pieces | They belong to another warehouse: transfer them or move the stock. |
