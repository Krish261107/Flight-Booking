# SkyDummy Airlines — demo flight booking app (Streamlit + SQLite)

No real flights, payments, emails or SMS: everything is simulated and stored in a local SQLite file.

## Run (Windows / Mac / Linux)
```
pip install -r requirements.txt
python -m streamlit run app.py
```
Use a **new, empty folder** for this project. Do not unzip it on top of an older copy: an old `pages` folder
would make Streamlit show a second, broken menu. To keep your old bookings and accounts, copy your old
`data\flights.db` into the new `data\` folder; it is upgraded automatically on first start.

Demo logins: `admin@demo.com / admin123` · `user@demo.com / user123` · or "Continue as Guest".
Works with Python 3.8 – 3.13 and Streamlit 1.36 or newer.

## Files (7 code files, no sub-folders)
| File | What it holds |
|---|---|
| `app.py` | sign-in sidebar, menu, and which page to show |
| `ui_user.py` | traveller / guest pages (search → results → details → booking → payment → confirmation, My Bookings, check-in, notifications, saved travelers, flight status, price alerts, dashboard) |
| `ui_admin.py` | admin pages (dashboard, flights & fares incl. bulk delay/cancel, bookings, users, promo codes, notifications & broadcast) |
| `db.py` | database, demo data, and every rule (pricing, promo, refunds, accounts); no Streamlit in it |
| `pdfs.py` | e-ticket and boarding-pass PDFs |
| `nav.py` | the one-line `go("Page")` helper pages use to move around |

## Rules worth knowing
- Cancel: 80% of the amount paid goes back to the wallet (100% if the airline cancelled). Points earned are reversed.
- Reschedule: move onto another flight on the same route; fee 2,500 + any fare difference (free if the airline cancelled it).
- Check-in is open until 60 minutes before departure. Infants travel without a seat.
- Loyalty points: 1 per 100 spent; redeem at payment (1 point = 1 INR, up to 25% of the total).
- The schedule is generated for the next 30 days and topped up automatically each day.
- Price alerts are evaluated when you open the dashboard or the alerts page (there is no background worker).
- "Menu language: Hindi" translates the menu only; page text stays English.
