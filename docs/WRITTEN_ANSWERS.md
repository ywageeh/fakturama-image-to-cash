# Written questions

## 1. If you had 3 more hours, what would you do for this task?

In order, with rough time budgets. The first two are about trusting the result; the rest are about coverage.

**1. Test on more than one image (about 60 min).** Everything end to end has been proven on the single sample. I would build a small set of ten to fifteen order images: different layouts, a photographed and a skewed scan, an unpaid order, mixed VAT rates, an order-level discount and a shipping charge, and one with a deliberately inconsistent total. Each gets a hand-checked expected result, and a script reports field-level accuracy. This tells me whether extraction is as good as it looks, and it is the regression suite for every later change.

**2. Make re-runs safe (about 40 min).** Running the same image twice creates a second Order today. Before opening a New Order, I would search Documents for the external reference and stop for manual review if it exists. With that in place, a stopped run can be restarted from the top: the Debtor, VAT, payment method and Products it already created are found and reused, which already works.

**3. Prove order-level discount and shipping from a real image (about 30 min).** The flow enters an overall discount and a shipping charge when the extraction supplies them, and this was tested by replaying edited data. No image with those values has gone through the model, and shipping was only tested with a single VAT rate; with mixed rates I have not confirmed how Fakturama splits the VAT on shipping.

**4. Exercise the ambiguity stops against real data (about 20 min).** The "more than one exact match" rule is unit-tested and the "conflicting definition" checks are in place, but I have not seeded real duplicate or conflicting Debtors, Products, VAT rates or payment methods and watched the flow stop. I would create those duplicates in Fakturama by hand and confirm each stop.

**5. Harden grid reading (about 30 min).** It reads only the visible rows (about twelve item lines), assumes English dates and decimal-point amounts, and compares OCR text exactly. I would add scrolling for long item lists, take number and date formats from configuration, and log OCR confidence so that a low-confidence cell is flagged instead of only failing a comparison. I would also replace the clipboard paste used for search boxes, which overwrites the user's clipboard.

**If time remained:** a read-only cross-check against Fakturama's database after the run, as a second source independent of the screen; and a second, independent read of names and addresses from the image, since arithmetic validation catches wrong numbers but not a misspelled company.

### What was skipped or differs from the spec

- The payment method is resolved or created **before** the Debtor editor is opened, not from inside it (spec 2.10). Fakturama fills that dropdown when the editor opens and does not refresh it.
- The follow-up Invoice does not copy a shipping amount typed on the Order, so the flow enters it on the Invoice as well (the spec's 5.1 only says to confirm it was copied).
- Tested with one sample image and one preview vision model on Groq; the model is a configuration value.
