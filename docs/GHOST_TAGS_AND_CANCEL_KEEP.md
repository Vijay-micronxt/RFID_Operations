# Ghost tags, hand-picked pieces at the counter, and tags kept on cancel

Changes asked for after the first weeks at Chandrlok. The rfid_retail side is in this repo. The
counter (POS) side is in POSNext, branch `feat/rfid-no-tag-prompt`. Deploy both together.

## Words used here

- **Ghost tag:** a piece sold with *Tag will not read? → Tag Damaged / Tag Missing* leaves its tag
  counted in stock until a stock count of the item finds it missing. The tag is counted, but no
  piece on the shelf carries it. Each such sale is an **RFID Unread Sale** (URS) with status
  **Open**.
- **Real tags** = tags in stock − ghost tags.
- **Pieces that may get a tag** = stock − real tags.

## 1. Tagging pieces while a ghost tag is open

**The problem.** Take 5 in stock and 5 tags.

1. 3 pieces are sold with their tags read, so those tags are freed.
2. 1 piece is sold unread. Stock is now 1 and tags are 2 (1 real + 1 ghost).
3. 1 of the 3 sold pieces comes back as a return, without a tag. Stock 2, tags 2.

The system then said every piece had a tag, so the returned piece could not be tagged.

**Now:**

- **Tag garments station.** The item is listed with *1 to tag*. Its header says how many pieces
  were sold without a tag scan.
- **Saving.** Before anything is saved, the station shows a confirmation with:
  - stock, tags and ghost tags of the item,
  - each open unread sale: bill, seller, time, reason and URS number.

  The tag is saved only after **Agree and tag**. Until a stock count, the item has more tags than
  stock (3 tags for 2 pieces). The open URS gets a comment, and an *Untagged Stock* exception is
  raised so the manager sees it.
- **Limits.** Only as many pieces as *stock − real tags* can be tagged. In the example, after the
  returned piece is tagged (2 real + 1 ghost, stock 2):
  - the item leaves the to-tag list,
  - another tag is refused,
  - scanning the item opens a box: *every piece already has a tag; stock X, tags Y, of which Z
    ghost tag(s)*. The box lists the open sales and asks for a stock count of the item.
- **Purchase / stock rows.** The RFID Tags dialog of a Purchase Receipt, Purchase Invoice (Update
  Stock) or Stock Entry also leaves ghost tags out. Before, a new row of such an item showed
  *done* although no tag had been read for it.
- **Item form.** A line per warehouse shows Stock, Tags, Ghost tags, Real tags and Pieces without
  tag. Under it, each open unread sale is listed with its bill, reason, seller, time and URS
  link, plus a link to the item's *No Tag* sales.
- **Connections.** *RFID Unread Sale* is added to the RFID group of Item and of Sales Invoice.
- **POS.** The hand-pick check and the server check on submit both leave ghost tags out, so a
  returned piece counts as a piece without a tag.

The next item-wise stock count settles the ghost tag, as before (*Sold* on the tag that is not
read).

## 2. Pieces picked by hand at the counter

**Adding a piece by hand** when both tagged and untagged pieces of the item are in stock. The
counter now asks for **every** piece, not once per bill. It offers:

- **Scan the tag**: nothing is added. Put the piece on the tray.
- **Piece has no tag - add**
- **It has a tag, but the tag will not read**: opens the usual Tag Damaged / Missing approval
  (PIN or approver login, as set in RFID Settings).

When every piece in stock has a tag, the *Tag will not read?* approval opens directly, as before.

**Approvals cover only their own pieces.** An approval lets in exactly the pieces it was given
for. Before, any approval of the item let every later hand-picked piece in without a question.
It also outlived a cleared cart or a page reload. Now:

- When a basket opens (new bill, reload, resume), approvals for pieces that are not in the cart
  are dropped once.
- Removing the line drops its approvals and its tray tags.

**Taking pieces off the bill.** A cart line of an RFID item without serial numbers can hold
three kinds of pieces: read on the tray, approved as *tag will not read*, and without a tag.
When its quantity goes down, the counter asks **Which piece is taken off the bill?**, one piece
at a time:

- **Piece without a tag**
- **Piece whose tag would not read (approved)**: the approval is dropped too.
- **Piece read on the tray**: read its tag, or pick it from the list. That tag leaves the basket.
- **Close**: keeps the pieces on the bill, and the quantity goes back.

If only one kind is on the line and there is nothing to choose, no question is asked. With
quantity 1, "−" removes the line as before, with its tags and approvals.

**Every hand sale is on record.** Each piece sold by hand out of untagged stock creates an RFID
Unread Sale with status **No Tag**. It records the item, bill, counter, seller and time.
Nothing is left to settle. A piece approved as *Tag Damaged / Missing* always opens a URS
(status Open), even while untagged stock exists.

**Cart stock check (POS, general fix).** The item grid already shows stock less the cart. Adding
an item then added the cart quantity a second time. With 5 in stock and 3 in the cart, the 4th
piece was refused as "only 2 available". The check now uses the stock from the server.

## 3. Cancel keeps the tags for the amendment

New setting **RFID Settings > Keep tags when a purchase / stock entry is cancelled**. It is on by
default, and an empty value counts as on.

**Cancel.** This applies to a Purchase Receipt, a Purchase Invoice with Update Stock, or a Stock
Entry. The tags leave stock but are **kept** on the cancelled document:

- status Reserved, on the same rows,
- the cancel warning says so,
- the nightly cleanup leaves these tags alone.

**Amend.**

- The desk copies the row tags into the amendment. Submitting it puts the same tags back on the
  same rows.
- With the same quantities, nothing needs to be done.
- If a row has fewer pieces, the extra tags are left out on save, with a message. They stay kept
  on the original.
- An amendment made without the tags (for example from code) takes them back on submit, by row
  position and item.

**Amendment draft.** A headline shows how many tags it carries. It warns about:

- rows with more tags than pieces,
- kept tags that are on no row,
- rows with fewer tags than pieces (tag the new pieces after submit).

Its **RFID Tags** button lists every tag with a *Goes on* choice:

- any row of the amendment (map or move),
- or *Take off* (the tag stays kept on the original).

A live count per row (tags/pieces) helps, and **Apply** saves the draft.

**Cancelled document.** A headline shows the tags it keeps. Its **RFID Tags** button has a scan
box: read the tags to free and they get ticked. Unknown codes are named, and Tick all / Untick
all are there. **Free ticked tags** makes them available for any garment.

**Not amending at all.** The kept tags block nothing:

- A kept tag read on any other purchase / stock row, a new draft, or the Tag garments station
  moves there by itself. The check says *Kept on cancelled X - will move here*.
- Deleting the cancelled document frees its kept tags.
- Kept tags are not counted in stock. At the POS they read as *not on any garment*, like a free
  tag.

**Setting off:** cancelling frees the tags at once, as before.

**Chandrlok.** The live site has its own Client Scripts *Chandrlok Purchase Receipt Cancel Stock
Warning* and *Chandrlok Purchase Invoice Cancel Stock Warning*. They replace the app's cancel
warning and say the tags are freed. After deploy, make them use `impact.keep` (as done on the
test copy) or disable them.

## 4. Amending a cancelled counter bill

A POS bill (Sales Invoice) cancelled on the desk puts its sold tags back on their garments
(Assigned) and cancels its RFID basket. The desk amendment copies that cancelled basket, so its
submit stopped with *RFID basket ... is cancelled*.

**Now:** saving the amendment gives it a new open basket with the reads of the original bill:

- A read is taken over only while its tag is still on the same item, or its serial is in stock,
  and no other open basket holds it.
- Tag-will-not-read approvals are taken over too.
- An alert names the new basket. A read that was not taken over is named, for example when the
  tag was sold again on another bill. Read that piece again.
- If nothing can be taken over, the amendment has no basket. The usual hand-pick checks apply.

Submitting the amendment sells the same tags again, so the tag history reads Sold, then Assigned
(cancel), then Sold (amendment).

**Cancel without amending:** the tags stay on their garments, the stock is back, and the open
unread sales of the bill are cancelled. Nothing else is needed.

## 5. Holding a bill at the counter (from PR #16)

A bill held in POS Next keeps its RFID basket, its tags and its tag-will-not-read approvals until
the bill is resumed or its draft is deleted.

- RFID Basket has **On Hold** (`held`) and **Held At** (`held_at`). A held basket stays Open, so
  other counters still see its tags as taken.
- `hold_basket`, `resume_basket` and `release_held_basket` do the work. The next customer gets a
  new basket. On resume, the empty basket opened meanwhile is cancelled.
- A held basket refuses reads, removals, approvals and dropped approvals (`drop_unread`), and a
  Sales Invoice on it is refused.
- An amended counter bill does not take over a tag that a held bill holds.

## New in the system

- RFID Settings: `keep_tags_on_cancel` (Check, default on)
- RFID Unread Sale: status **No Tag**
- RFID Basket: `held`, `held_at`
- API:
  - `rfid_retail.api.pos.drop_unread(basket, item_code, qty=None)`
  - `rfid_retail.api.mapping.item_rfid_summary(item_code)`
  - `rfid_retail.api.mapping.bulk_assign(..., accept_ghost=0)`: without agreement it answers
    `confirm_ghost` and saves nothing.
  - `rfid_retail.overrides.stock.held_tag_rows(doctype, name)`
  - `rfid_retail.overrides.stock.free_held_tags(doctype, name, codes=None)`
  - `rfid_retail.overrides.stock.cancel_impact`: now also returns `keep`.
- Station page assets: `rfid_station.js?v=22`.

## Deploy

1. Pull RFID_Operations (this PR) and POSNext (`feat/rfid-no-tag-prompt`).
2. Run `bench --site <site> migrate`. This adds the setting, the URS status and the basket hold fields.
3. Run `bench build --app rfid_retail` and `bench build --app pos_next`.
4. Run `bench restart`.
5. On Chandrlok, update or disable the two cancel-warning Client Scripts (see above).
6. The POS is a PWA. Counters may need two reloads to pick up the new build.

## Tested

Tested on a copy of the Chandrlok data. The automated scenario rolls back and has 52 checks,
all passing:

- ghost counts
- POS check
- No Tag record
- station agree flow and limits
- Item summary
- PR row with a ghost tag
- cancel keeps tags
- nightly job
- amendment with and without copied tags, and with a shorter row
- moving a kept tag to another PR
- freeing kept tags
- setting off
- counter bill cancelled and amended: new basket, same tags sold again
- held bill: new basket for the next customer, held basket locked, resume, deleted draft

The earlier unread-sale and stock-count scenario still passes 63 of 63. `drop_unread` was
checked directly.

The station confirmation, the amendment dialog and the counter's removal dialog were then tried
by hand on the test site.
