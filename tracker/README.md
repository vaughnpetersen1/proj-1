# Tracker — every dollar gets a row

Add a row to `ledger.csv` whenever money moves. That's the whole system.

```
date,engine,type,description,amount
2026-07-12,flips,expense,bought switch lite (ebay),-85.00
2026-07-19,flips,income,sold switch lite,142.50
2026-07-19,flips,expense,ebay fees + shipping,-28.90
2026-07-21,service,income,Joe's Detailing site deposit,150.00
```

- **engine:** `service`, `flips`, or `setup`
- **type:** `income` or `expense`
- **amount:** positive for money in, negative for money out

Check the score anytime:

```bash
python3 tracker/summary.py
```

This is what month 2's critique runs on — if it's not in the ledger, it didn't happen.
