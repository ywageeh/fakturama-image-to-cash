# Fakturama Image-to-Cash: Design

## Goal and constraints

Turn one order image into a saved Order and a linked Invoice in Fakturama, with the Debtor, Products, VAT rate and payment method resolved or created on the way, and the payment status applied. Three constraints shape the design:

- **No hardcoded coordinates or fixed layout.** Every control has to be found at run time.
- **Verify each step before the next.** A wrong record in an accounting system is worse than no record.
- **Stop when unsure.** The spec names many "stop for manual review" cases; the system should treat those as a normal outcome, not an error path bolted on later.

## Approach in one paragraph

A vision LLM reads the image once and returns typed, structured data. Deterministic code checks that data, then drives Fakturama through Microsoft UI Automation (UIA), following the spec's steps in order. OCR is used only where UIA cannot see: the data grids. The LLM never drives the UI. Each step reads back what it wrote, and any mismatch or ambiguity raises a single "manual review" stop that saves a screenshot and the reason.

## Control discovery and grounding

Before choosing, I would spend thirty minutes inspecting Fakturama with a UIA tree viewer, because the answer depends on what the application exposes. Fakturama is an Eclipse/SWT application, so I expect standard widgets to be visible to UIA and custom-drawn ones not to be. That inspection found three tiers, and the design uses a different technique for each.

**Tier 1: named controls (most of the application).** Toolbar buttons carry their tooltip as a name ("Create: New Order"), navigation entries and most form fields carry their label ("Cust.Ref.", "Street", "Alias name"), and dialogs carry their title. These are found by name and role, scoped to the editor they belong to, since several editors contain a field called "VAT" or "Name".

**Tier 2: unnamed controls, found by relationship.** Some fields have no name of their own (document number, date, first and last name, ZIP and city). They are found from the label beside them: the nearest control of the right type on the same row. Icon buttons with no name (select existing contact versus the green plus) are found by their position in the tree under a named label, top to bottom. This survives window resizing and layout shifts because it never depends on absolute position.

**Tier 3: grids, read by OCR.** The item table, the list views and the selector dialogs are drawn by the application and appear to UIA as one empty pane. For these, UIA still supplies the pane's rectangle; the system screenshots only that rectangle, finds the column lines in the pixels, and runs OCR so that each word lands in a known column. The header row names the columns. A click on a row or cell uses a point computed from that live rectangle, so it is still not a stored coordinate.

Two rules apply across all tiers. Input goes through real keystrokes and clicks where the application ignores UIA-only changes (dropdowns, checkboxes, date fields). And nothing is typed until the target is confirmed to hold keyboard focus, so keystrokes cannot land in the wrong place.

## Image extraction

One call to a vision model, with the image and a fixed schema: order date, external reference, debtor (company, contact, alias, both addresses, email, phone), payment method, paid status and date, and each item (SKU, description, quantity, unit net price, VAT, discount, line total), plus the document totals. The client speaks the OpenAI-compatible chat API, so the model is a configuration choice, not a code dependency.

The model's answer is not trusted as given. It is parsed into typed fields (dates as dates, money as decimals) and then checked arithmetically: each line's quantity times unit price less discount must equal the printed line total, the lines must sum to the net total, and net plus VAT must equal the gross total. An invoice-like document carries this redundancy for free, and it catches most misread digits. On failure the model gets one retry with the specific errors; a second failure stops for manual review before the UI is touched.

## Flow, verification and manual review

The flow is a plain sequence of functions, one per spec step, in the spec's order: open the Order, resolve the Debtor, resolve each Product, complete and save, create the Invoice from the Order's follow-up action, apply payment, verify. Each "resolve" step is the same shape: search from the open Order, accept exactly one exact match, create the record if there is none, stop if there are several or the existing definition conflicts, then select it from the Order again to prove it was saved.

Verification happens at three levels: every field is read back after it is set; every document is checked as a whole before saving (addresses, each line, totals against the source); and after saving, the document's row in the Documents list is checked, and the Invoice is reopened to confirm what was stored.

Business rules from the spec (gross price formula, VAT naming, payment code mapping, exact-match definition) live in one module of pure functions with unit tests, separate from anything that touches the screen.

## Tradeoffs

- **Scripted flow instead of an LLM agent driving the screen.** The spec is a fixed procedure, so a scripted flow is faster, repeatable and auditable step by step. The cost is that it knows only this flow; a changed Fakturama screen means a code change, not a prompt change.
- **UIA first instead of vision everywhere.** UIA gives exact values and stable identification where it works. The cost is a dependency on control names, which would change with the interface language, and on Fakturama-specific behaviour that has to be learned by inspection.
- **OCR for grids.** It is the only way to read them through the interface, but it is the least reliable part: small text, truncated cells, and a few seconds per read. The design limits the damage by filtering lists with the search box first, comparing cells exactly, and treating any misread as a manual-review stop, never as a match.
- **Arithmetic validation instead of a second model.** Cheap and strong for numbers, but it does not catch a misread name or address. A second independent read would.
- **Stop instead of recover.** Simple and safe, at the cost of more human interventions. A stopped run leaves records already created; the next run finds and reuses them, which is the spec's own existence-check behaviour.

## Alternatives considered

- **Coordinate-based automation (for example pyautogui).** Rejected: it is what the brief rules out, and it cannot read values back.
- **Writing to Fakturama's database directly.** Fast and exact, but it bypasses the application's own rules and is not what the task asks for. Reading the database only to cross-check results would be a reasonable later addition.
- **A computer-use agent for the whole flow.** More adaptable to interface change, but slower, harder to verify, and unnecessary where UIA already identifies the control. Worth keeping as a fallback for locating a control when the first three tiers fail.

## Main risks

1. Fakturama behaviour that only appears in use (dropdowns that do not refresh, dialogs that close themselves, popups). Mitigation: inspect early, verify after every action, stop on surprise.
2. OCR errors in grids. Mitigation as above; measure on more than one sample.
3. Extraction errors the arithmetic cannot see (names, addresses, SKUs). Mitigation: exact-match rules mean a wrong name creates a new record instead of corrupting an existing one; a human still needs to review new master data.
