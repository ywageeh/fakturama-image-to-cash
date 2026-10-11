# Written questions

## 1. If you had 3 more hours, what would you do for this task?

1. Generate lots of sample orders and run them through the workflow to check what might break it.

2. I would add the creation of new shipping methods but the details on creating those are missing from the spec, so I'm only putting a value for now while the dropdown says free shipping.

3. Implement monitoring for a folder where the scanned orders would be dropped and the flow starts automatically whenever a new order image is added, and some small data persistence would be needed to detect orders already processed.


### What was skipped or differs from the spec

- The payment method is resolved or created **before** the Debtor editor is opened, not from inside it (spec 2.10). Fakturama fills that dropdown when the editor opens and does not refresh it.
- Only tested end to end with the one sample image, other layouts were not tried.