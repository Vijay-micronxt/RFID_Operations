# Ghost tags, hand sales of untagged pieces, and tags kept on cancel

Three changes requested after the first weeks at Chandrlok.

## 1. Ghost tags: tagging returned pieces while a "tag will not read" sale is open

A piece sold with **Tag will not read? → Tag Damaged / Tag Missing** leaves its tag counted in stock
until a stock count of the item settles it. This is a *ghost tag*: it is counted, but no piece on
the shelf carries it.

Example: 5 in stock, 5 tags. 3 sold with their tags read, so their tags are freed. 1 is sold
unread. Stock is now 1 and tags are 2: 1 real tag and 1 ghost tag. Then 1 of the 3 comes back as a
return, without a tag. Stock is 2 and tags are 2, so the system said every piece had a tag, and the
returned piece could not be tagged.

Now:

- **Tag garments station:** the item appears with *1 to tag*. Its line says how many pieces were
  sold without a tag scan.
- **Saving the tag:** the station first shows a confirmation with:
  - stock, tags and ghost tags of the item,
  - the bill, the user and the time of each open unread sale.

  The tag is saved only after **Agree and tag**. Until a count, the item then has more tags than
  stock (3 tags for 2 pieces). The open Unread Sale gets a comment, and an *Untagged Stock*
  exception is raised.
- **Item form:** a line per warehouse shows Stock, Tags, Ghost tags, Real tags and Pieces without
  tag. Each open unread sale is listed with its bill, reason, seller, time and URS link.
- **Item Connections:** the RFID group has a new **RFID Unread Sale** link. The Sales Invoice RFID
  group has one too.
- **POS:**
  - The hand-pick check leaves ghost tags out, so the returned piece counts as a piece without a
    tag.
  - The server check on submit does the same.

The next item-wise stock count still settles the ghost tag, as before.

## 2. Pieces sold by hand while untagged stock exists

Before, when an item had both tagged and untagged pieces in stock, the counter asked only once per
bill. The sale was not recorded anywhere except an exception.

Now:

- The POS asks for **every** piece added by hand. It has three answers:
  - **Scan the tag**
  - **Piece has no tag - add**
  - **It has a tag, but the tag will not read**: opens the usual Tag Damaged / Missing approval,
    and the sale leaves a ghost tag.
- Every piece sold by hand from untagged stock creates an **RFID Unread Sale** with status
  **No Tag**. It records the item, bill, counter, seller and time. Nothing is left to settle.
- A piece approved as Tag Damaged / Missing always opens an Unread Sale, even while untagged stock
  exists.

## 3. Cancel keeps the tags for the amendment

New setting **RFID Settings > Keep tags when a purchase / stock entry is cancelled**. It is on by
default, and a site where the setting is still empty counts as on.

- **Cancelling** a Purchase Receipt, a Purchase Invoice with Update Stock, or a Stock Entry takes
  the tags off stock but keeps them **held** on the cancelled document:
  - status Reserved, on the same rows,
  - the cancel warning says so,
  - the nightly cleanup leaves these tags alone.
- **Amend:** the desk copies the row tags into the amendment. Submitting the amendment puts the
  same tags back on the same rows.
  - If a row now has fewer pieces, the extra tags are left out with a message and stay held on the
    original.
  - An amendment made without the tags takes them back on submit, by row position and item.
- **Amendment draft:** a headline shows the tags it carries. It warns about rows with more tags than
  pieces, kept tags on no row, and rows with fewer tags than pieces. The **RFID Tags** button lists
  every tag with a *Goes on* choice:
  - any row of the amendment (to move it),
  - or *Take off*: the tag stays kept on the original.

  A live count per row (tags/pieces) helps. **Apply** saves the draft. With the same quantities,
  nothing needs to be done.
- **Remove:** the cancelled document shows how many tags it holds. Its **RFID Tags** button frees
  ticked tags for any garment.
- **Move:** a held tag read on another row (RFID Tags dialog or the station) moves there. The
  dialog says *Kept on cancelled X - will move here*. After submit, the existing *Check stickers /
  move tags* works between rows as before.
- **Setting off:** cancelling frees the tags at once, as before.

**Chandrlok:** the live site has its own Client Scripts *Chandrlok Purchase Receipt/Invoice Cancel
Stock Warning*. They replace the cancel warning and say the tags are freed. Update their text, or
disable them, after this is deployed.

## New in the system

- RFID Settings: `keep_tags_on_cancel` (Check, default on)
- RFID Unread Sale: status **No Tag**

## Deploy

1. Pull RFID_Operations and POSNext.
2. Run `bench --site <site> migrate`.
3. Run `bench build --app rfid_retail` and `bench build --app pos_next`.
4. Run `bench restart`.

## Tested

The automated scenario `mxt_data/Milap/setup/e2e_ghost_cancel.py` runs on a copy of the
Chandrlok data and rolls back. It has 36 checks, all passing:

- ghost count
- POS check
- No Tag record
- station agree flow
- Item summary
- cancel hold
- amendment with and without copied tags
- move to another PR
- free held tags
- setting off

`e2e_unread_count.py` still passes 63 of 63.
