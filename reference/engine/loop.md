# The loop

Every subsystem serviced in one controlled order, once per presentation slot.

Not written. What it owes:

- the ordering contract per slot, and why each step sits where it does
- the frame as a BEGIN/COMMIT transaction — prepare, tick, commit
- why composition bills at tick rate, never at slot rate
- where lifecycle boundaries land, and where the metrics cut falls
