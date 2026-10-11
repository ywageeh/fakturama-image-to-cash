# Design - Fakturama Image to Cash

## Overall idea

Two parts that stay separate:

1. A vision LLM reads the order image once and gives back structured data (JSON).
2. Normal code drives Fakturama step by step following the spec, and checks after every action.

The LLM never clicks anything. The task is a fixed procedure so a script is faster and I can tell exactly what happened at each step, an agent clicking around would be harder to verify.

## Finding the controls

No coordinates are stored anywhere. I use Microsoft UI Automation (UIA) through pywinauto, which gives a tree of the app's controls with their type and name.

Before deciding I inspected Fakturama with a UIA viewer to see what it actually exposes. There are 3 cases:

- Controls with a name - most of the app. The toolbar button is called "Create: New Order", the field is called "Cust.Ref." and so on. Found by name + type
- Controls without a name (date, first/last name, zip/city). Found from the label beside them, e.g the text box right of "Date". The icons next to "Addresses" have no name either so I take them by their order under that label.
- Tables. Fakturama draws them itself so UIA only sees an empty box. For these I screenshot only that box and read it with OCR. UIA still tells me where the box is, so clicking a row is calculated at runtime

Some controls (dropdowns, checkboxes, date fields) ignore changes made through UIA so they get real key presses. And nothing gets typed until the field is confirmed to have focus.

## Reading the image

One call to a vision model with a fixed schema: order date, reference, customer and addresses, payment, items and totals. Any OpenAI compatible endpoint works, the model is just config.

I dont trust the answer directly. The document checks itself: qty x price - discount has to equal the line total, lines have to sum to the net total and net + VAT has to equal the total. If a digit is misread the math fails. The model gets one retry with the errors, after that the run stops before touching Fakturama.

## Verification

Every "find or create" step works the same way: search from the open Order, accept only one exact match, create it if there is none, stop if there are several. Then select it again from the Order which proves it was saved.

- each field is read back after setting it
- the whole document is checked against the image before saving
- after saving, the row in Documents is checked and the Invoice is reopened

Anything unexpected stops the run with a screenshot and the reason. The spec says "stop for manual review" in many places so this is a normal outcome not an error.

## Tradeoffs

- Script instead of an agent: predictable, but it only knows this flow. If the screen changes the code has to change
- UIA first: exact values, but depends on the English control names
- OCR for tables is the weakest part - small text, cut off cells, a few seconds per read. A misread stops the run, its never treated as a match.
- Math checks catch wrong numbers but not a misspelled name
- Stopping instead of recovering is simple and safe but means more manual work

## What I decided against

- pyautogui: was my first idea, but it clicks fixed coordinates which the task rules out, and it cant read values back
- reading the order with OCR because this is what LLMs really excel at so any small model with vision capabilities can do that work almost perfectly and much better than OCR.
- an LLM agent for the whole flow: not needed where UIA already finds the control but might be more reliable for unseen scenarios, but is not predictable so I don't think it's worth making this probabilistic.
