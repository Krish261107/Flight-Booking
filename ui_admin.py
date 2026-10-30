"""Admin pages. Each page is a plain function; app.py decides which one to draw."""
import datetime as dt
import re

import pandas as pd
import streamlit as st

from db import AIRCRAFT, delete_account, get_conn, notify


def dashboard():
    st.title("\U0001F4CA Admin Dashboard")

    user = st.session_state.get("user")
    if not user or not user.get("is_admin"):
        st.error("Admin access only.")
        st.stop()

    conn = get_conn()

    bookings = pd.read_sql_query(
        "SELECT * FROM bookings ORDER BY id DESC", conn,
    )
    total_bookings = len(bookings)
    confirmed = bookings[bookings["status"] == "Confirmed"]
    cancelled = bookings[bookings["status"] == "Cancelled"]
    revenue = confirmed["total_amount"].sum() if len(confirmed) else 0
    avg_value = confirmed["total_amount"].mean() if len(confirmed) else 0
    total_users = conn.execute("SELECT COUNT(*) c FROM users WHERE is_admin=0").fetchone()["c"]

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total bookings", total_bookings)
    c2.metric("Revenue (confirmed)", f"{revenue:,.0f}")
    c3.metric("Avg. booking value", f"{avg_value:,.0f}" if total_bookings else "0")
    c4.metric("Cancellations", len(cancelled))
    c5.metric("Registered users", total_users)

    st.divider()
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Revenue trend (last 14 days booked)")
        if len(confirmed):
            trend = confirmed.assign(day=pd.to_datetime(confirmed["created_at"]).dt.date).groupby("day")["total_amount"].sum().tail(14)
            st.line_chart(trend)
        else:
            st.caption("No bookings yet.")

    with col2:
        st.subheader("Top routes by bookings")
        routes = pd.read_sql_query(
            """SELECT f.origin || ' -> ' || f.destination AS route, COUNT(*) AS bookings
               FROM booking_segments bs JOIN flights f ON f.id = bs.flight_id
               GROUP BY route ORDER BY bookings DESC LIMIT 8""",
            conn,
        )
        if len(routes):
            st.bar_chart(routes.set_index("route"))
        else:
            st.caption("No segment data yet.")

    st.divider()
    st.subheader("\u26A0\ufe0f Operational alerts")
    delayed = conn.execute(
        "SELECT flight_number, origin, destination, depart_date, delay_min, status FROM flights "
        "WHERE status IN ('Delayed','Cancelled') AND depart_date >= ? LIMIT 20", (dt.date.today().isoformat(),)
    ).fetchall()
    if delayed:
        st.dataframe(pd.DataFrame([dict(r) for r in delayed]))
    else:
        st.success("No delayed or cancelled flights on the books right now.")

    conn.close()


MANUAL = "\u270F\uFE0F Type it myself..."
SLOTS = ["%02d:%02d" % (h, m) for h in range(24) for m in (0, 15, 30, 45)]
DURATIONS = [str(m) for m in range(60, 301, 15)]
GATES = ["%s%d" % (letter, n) for letter in "ABCD" for n in range(1, 31)]
STATUSES = ["Scheduled", "Delayed", "Cancelled"]


def _choose(label, options, key, current=None, names=None):
    """A dropdown with a 'type it myself' option, so any field can be picked or typed.
    Returns (value, typed_manually)."""
    opts = [str(o) for o in options]
    if current is not None and str(current) not in opts:
        opts.insert(0, str(current))  # an unusual existing value must stay selectable
    index = opts.index(str(current)) if current is not None else 0
    names = names or {}
    pick = st.selectbox(label, opts + [MANUAL], index=index, key=key, format_func=lambda o: names.get(o, o))
    if pick == MANUAL:
        return st.text_input(label + " - type it", key=key + "_txt").strip(), True
    return pick, False


def _airport(label, key, airports, current=None):
    """Returns (code, city_for_new_airport_or_None)."""
    codes = [a["code"] for a in airports]
    names = dict((a["code"], "%s \u2014 %s" % (a["code"], a["city"])) for a in airports)
    code, typed = _choose(label, codes, key, current, names)
    city = None
    if typed:
        code = code.upper()
        if code and code not in codes:
            city = st.text_input("City of the new airport %s" % code, key=key + "_city").strip()
    return code, city


def _airline(label, key, airlines, current=None):
    """Returns (code, name_for_new_airline_or_None)."""
    codes = [a["code"] for a in airlines]
    names = dict((a["code"], "%s \u2014 %s" % (a["code"], a["name"])) for a in airlines)
    code, typed = _choose(label, codes, key, current, names)
    name = None
    if typed:
        code = code.upper()
        if code and code not in codes:
            name = st.text_input("Name of the new airline %s" % code, key=key + "_name").strip()
    return code, name


def _flight_form(prefix, conn, f=None):
    """Draws every flight field (dropdown or typed) and returns (values, errors). `f` is the flight being edited."""
    d = dict(f) if f else {}
    airports = conn.execute("SELECT code, city FROM airports ORDER BY city").fetchall()
    airlines = conn.execute("SELECT code, name FROM airlines ORDER BY name").fetchall()

    c1, c2, c3 = st.columns(3)
    with c1:
        airline, new_airline = _airline("Airline", prefix + "al", airlines, d.get("airline_code"))
    with c2:
        number = st.text_input("Flight number (e.g. 6E204)", value=d.get("flight_number", ""), key=prefix + "num").strip().upper()
    with c3:
        date = st.date_input("Departure date", key=prefix + "date",
                             value=dt.date.fromisoformat(d["depart_date"]) if d else dt.date.today() + dt.timedelta(days=1))
    c4, c5 = st.columns(2)
    with c4:
        origin, new_o = _airport("From", prefix + "o", airports, d.get("origin"))
    with c5:
        dest, new_d = _airport("To", prefix + "d", airports, d.get("destination") or airports[1]["code"])  # new flights: To != From
    c6, c7 = st.columns(2)
    with c6:
        dep, _ = _choose("Departure time", SLOTS, prefix + "dep", d.get("depart_time") or "09:00")
    with c7:
        dur_txt, _ = _choose("Duration (minutes)", DURATIONS, prefix + "dur", str(d["duration_min"]) if d else "120")

    errors = []
    arrive, duration = None, 0
    if re.match(r"^([01]\d|2[0-3]):[0-5]\d$", dep) and dur_txt.isdigit() and 20 <= int(dur_txt) <= 1000:
        duration = int(dur_txt)
        total = int(dep[:2]) * 60 + int(dep[3:]) + duration
        arrive = "%02d:%02d" % ((total // 60) % 24, total % 60)
        st.caption("Arrives at %s%s" % (arrive, " (next day)" if total >= 1440 else ""))
    else:
        errors.append("Departure time must look like 09:30 and duration must be 20-1000 minutes.")

    c8, c9, c10 = st.columns(3)
    with c8:
        stops = st.selectbox("Stops", [0, 1, 2], index=d.get("stops") if d.get("stops") in (0, 1, 2) else 0, key=prefix + "stops")
    layover, new_l, layover_min = None, None, 0
    if stops:
        with c9:
            layover, new_l = _airport("Layover airport", prefix + "lay", airports, d.get("layover_airport"))
        with c10:
            layover_min = int(st.number_input("Layover (minutes)", min_value=20, max_value=600,
                                              value=int(d.get("layover_min") or 60), key=prefix + "laym"))

    c11, c12, c13, c14 = st.columns(4)
    with c11:
        aircraft, _ = _choose("Aircraft", AIRCRAFT, prefix + "ac", d.get("aircraft"))
    with c12:
        gate, _ = _choose("Gate", GATES, prefix + "gate", d.get("gate"))
    with c13:
        bag_cabin, _ = _choose("Cabin bag", ["5 kg", "7 kg", "10 kg"], prefix + "bagc", d.get("baggage_cabin") or "7 kg")
    with c14:
        bag_check, _ = _choose("Check-in bag", ["15 kg", "20 kg", "25 kg", "30 kg"], prefix + "bagk", d.get("baggage_checkin") or "15 kg")

    c15, c16 = st.columns(2)
    with c15:
        eco = float(st.number_input("Economy price", min_value=500.0, step=100.0, key=prefix + "eco",
                                    value=float(d.get("price_economy") or 4000.0)))
    with c16:
        auto_biz = st.checkbox("Business fare = 2.6 x economy", value=not d, key=prefix + "autobiz")
        if auto_biz:
            biz = round(eco * 2.6, 2)
            st.caption("Business fare: %s" % format(biz, ",.2f"))
        else:
            biz = float(st.number_input("Business price", min_value=500.0, step=100.0, key=prefix + "biz",
                                        value=float(d.get("price_business") or eco * 2.6)))
    status, delay = "Scheduled", 0
    if f:
        c17, c18 = st.columns(2)
        with c17:
            status = st.selectbox("Status", STATUSES, index=STATUSES.index(d["status"]) if d["status"] in STATUSES else 0, key=prefix + "status")
        if status == "Delayed":
            with c18:
                delay = int(st.number_input("Delay (minutes)", min_value=5, max_value=720, value=int(d.get("delay_min") or 30), key=prefix + "delay"))

    if not re.match(r"^[A-Z0-9]{3,8}$", number):
        errors.append("Flight number must be 3-8 letters/digits, like 6E204.")
    if not re.match(r"^[A-Z0-9]{2,3}$", airline):
        errors.append("Airline code must be 2-3 letters/digits.")
    elif new_airline is not None and not new_airline:
        errors.append("Enter a name for the new airline %s." % airline)
    for label, code, city in (("From", origin, new_o), ("To", dest, new_d)):
        if not re.match(r"^[A-Z]{3}$", code):
            errors.append("%s airport code must be 3 letters." % label)
        elif city is not None and not city:
            errors.append("Enter the city of the new airport %s." % code)
    if origin == dest:
        errors.append("From and To must be different airports.")
    if stops:
        if not re.match(r"^[A-Z]{3}$", layover or ""):
            errors.append("Layover airport code must be 3 letters.")
        elif layover in (origin, dest):
            errors.append("Layover airport must differ from From and To.")
        elif new_l is not None and not new_l:
            errors.append("Enter the city of the new airport %s." % layover)
    if not aircraft or not gate or not bag_cabin or not bag_check:
        errors.append("Aircraft, gate and baggage can't be empty.")
    values = dict(airline=airline, new_airline=new_airline, number=number, date=date.isoformat(), origin=origin, dest=dest,
                  new_o=new_o, new_d=new_d, dep=dep, arrive=arrive, duration=duration, stops=stops, layover=layover if stops else None,
                  new_l=new_l, layover_min=layover_min, aircraft=aircraft, gate=gate, bag_cabin=bag_cabin, bag_check=bag_check,
                  eco=eco, biz=biz, status=status, delay=delay)
    return values, errors


def _save_new_refs(conn, v):
    """Create the airline / airports the admin typed in, if they didn't exist yet."""
    if v["new_airline"]:
        conn.execute("INSERT OR IGNORE INTO airlines VALUES (?,?)", (v["airline"], v["new_airline"]))
    for code, city in ((v["origin"], v["new_o"]), (v["dest"], v["new_d"]), (v["layover"], v["new_l"])):
        if city:
            conn.execute("INSERT OR IGNORE INTO airports VALUES (?,?,?)", (code, city, city + " Airport"))


def _duplicate(conn, v, skip_id=0):
    return conn.execute("SELECT 1 FROM flights WHERE flight_number=? AND depart_date=? AND id!=?",
                        (v["number"], v["date"], skip_id)).fetchone()


def _show(errors):
    for e in dict.fromkeys(errors):
        st.error(e)


def flights():
    st.title("\U0001F6E0\ufe0f Flights & Fares")

    user = st.session_state.get("user")
    if not user or not user.get("is_admin"):
        st.error("Admin access only.")
        st.stop()
    flash = st.session_state.pop("flights_flash", None)
    if flash:
        st.success(flash)

    conn = get_conn()
    tab_edit, tab_add, tab_bulk = st.tabs(["Edit or delete a flight", "Add a flight", "Bulk delay / cancel a route"])

    # ------------------------------------------------------------------ add
    with tab_add:
        v, errs = _flight_form("add_", conn)
        if st.button("Add flight", type="primary", key="add_btn"):
            errs = list(errs)
            if not errs:
                if _duplicate(conn, v):
                    errs.append("Flight %s already exists on %s." % (v["number"], v["date"]))
                if dt.datetime.fromisoformat(v["date"] + "T" + v["dep"]) <= dt.datetime.now():
                    errs.append("That departure time is already in the past.")
            if errs:
                _show(errs)
            else:
                _save_new_refs(conn, v)
                new_id = conn.execute(
                    """INSERT INTO flights (flight_number, airline_code, origin, destination, depart_date, depart_time, arrive_time,
                       duration_min, stops, layover_airport, layover_min, aircraft, base_price, seats_economy, seats_business,
                       price_economy, price_business, baggage_cabin, baggage_checkin, status, delay_min, gate)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,150,20,?,?,?,?,'Scheduled',0,?)""",
                    (v["number"], v["airline"], v["origin"], v["dest"], v["date"], v["dep"], v["arrive"], v["duration"], v["stops"],
                     v["layover"], v["layover_min"], v["aircraft"], v["eco"], v["eco"], v["biz"], v["bag_cabin"], v["bag_check"],
                     v["gate"])).lastrowid
                conn.commit()
                st.success("Added flight %s (ID %d): %s to %s on %s at %s." %
                           (v["number"], new_id, v["origin"], v["dest"], v["date"], v["dep"]))

    # ----------------------------------------------------------------- edit
    with tab_edit:
        with st.expander("All flights (first 300)"):
            st.dataframe(pd.read_sql_query(
                "SELECT id, flight_number, airline_code, origin, destination, depart_date, depart_time, "
                "price_economy, price_business, status, delay_min, gate FROM flights ORDER BY depart_date, depart_time LIMIT 300", conn),
                height=250)
        mode = st.radio("Find the flight by", ["Route & date (dropdown)", "Flight ID or number (type it)"], horizontal=True, key="fl_find")
        rows = []
        if mode.startswith("Route"):
            airports = conn.execute("SELECT code, city FROM airports ORDER BY city").fetchall()
            names = dict((a["code"], "%s \u2014 %s" % (a["code"], a["city"])) for a in airports)
            names["Any"] = "Any"
            codes = ["Any"] + [a["code"] for a in airports]
            c1, c2, c3 = st.columns(3)
            with c1:
                fo = st.selectbox("From", codes, key="fl_o", format_func=lambda o: names[o])
            with c2:
                fd = st.selectbox("To", codes, key="fl_d", format_func=lambda o: names[o])
            with c3:
                fdate = st.date_input("Date", key="fl_date", value=dt.date.today())
                any_date = st.checkbox("Any date", key="fl_anyd")
            sql, args = "SELECT * FROM flights WHERE 1=1", []
            if fo != "Any":
                sql, args = sql + " AND origin=?", args + [fo]
            if fd != "Any":
                sql, args = sql + " AND destination=?", args + [fd]
            if not any_date:
                sql, args = sql + " AND depart_date=?", args + [fdate.isoformat()]
            rows = conn.execute(sql + " ORDER BY depart_date, depart_time LIMIT 100", args).fetchall()
        else:
            q = st.text_input("Flight ID (e.g. 120) or flight number (e.g. 6E204)", key="fl_q").strip().upper()
            if q:
                rows = (conn.execute("SELECT * FROM flights WHERE id=?", (int(q),)) if q.isdigit() else
                        conn.execute("SELECT * FROM flights WHERE flight_number=? ORDER BY depart_date LIMIT 100", (q,))).fetchall()
        if not rows:
            st.info("No flights found. Change the filters above, or add one in the next tab.")
        else:
            labels = dict(("#%d | %s | %s \u2192 %s | %s %s | %s" % (r["id"], r["flight_number"], r["origin"], r["destination"],
                                                                    r["depart_date"], r["depart_time"], r["status"]), r) for r in rows)
            f = labels[st.selectbox("Choose the flight (%d found)" % len(rows), list(labels), key="fl_pick")]
            fid, prefix = f["id"], "ed%d_" % f["id"]
            booked = conn.execute("SELECT COUNT(DISTINCT b.id) n FROM booking_segments bs JOIN bookings b ON b.id=bs.booking_id "
                                  "WHERE bs.flight_id=? AND b.status='Confirmed'", (fid,)).fetchone()["n"]
            ever = conn.execute("SELECT COUNT(*) n FROM booking_segments WHERE flight_id=?", (fid,)).fetchone()["n"]
            if booked:
                st.info("%d confirmed booking(s) on this flight, so route and date are locked. Changes to timing, gate, "
                        "airline or status notify those passengers." % booked)
            st.divider()
            v, errs = _flight_form(prefix, conn, f)
            if st.button("Save changes", type="primary", key=prefix + "save"):
                errs = list(errs)
                if not errs:
                    if booked and (v["origin"], v["dest"], v["date"]) != (f["origin"], f["destination"], f["depart_date"]):
                        errs.append("Route and date can't change while passengers are booked. Cancel the flight instead.")
                    if _duplicate(conn, v, fid):
                        errs.append("Flight %s already exists on %s." % (v["number"], v["date"]))
                if errs:
                    _show(errs)
                else:
                    _save_new_refs(conn, v)
                    conn.execute(
                        """UPDATE flights SET flight_number=?, airline_code=?, origin=?, destination=?, depart_date=?, depart_time=?,
                           arrive_time=?, duration_min=?, stops=?, layover_airport=?, layover_min=?, aircraft=?, base_price=?,
                           price_economy=?, price_business=?, baggage_cabin=?, baggage_checkin=?, status=?, delay_min=?, gate=?
                           WHERE id=?""",
                        (v["number"], v["airline"], v["origin"], v["dest"], v["date"], v["dep"], v["arrive"], v["duration"], v["stops"],
                         v["layover"], v["layover_min"], v["aircraft"], v["eco"], v["eco"], v["biz"], v["bag_cabin"], v["bag_check"],
                         v["status"], v["delay"], v["gate"], fid))
                    schedule_changed = (f["flight_number"], f["airline_code"], f["depart_time"], f["duration_min"], f["gate"]) != \
                        (v["number"], v["airline"], v["dep"], v["duration"], v["gate"])
                    status_changed = (f["status"], f["delay_min"] or 0) != (v["status"], v["delay"])
                    told = 0
                    if booked and (schedule_changed or status_changed):
                        for p in conn.execute("SELECT DISTINCT b.user_id, b.pnr FROM booking_segments bs JOIN bookings b ON b.id=bs.booking_id "
                                              "WHERE bs.flight_id=? AND b.status='Confirmed'", (fid,)).fetchall():
                            text = "Flight %s (%s to %s, %s) now departs at %s, gate %s, status %s." % (
                                v["number"], v["origin"], v["dest"], v["date"], v["dep"], v["gate"], v["status"])
                            notify(conn, p["user_id"], "Push", "Flight update - PNR " + p["pnr"], text)
                            told += 1
                    conn.commit()
                    st.success("Flight %s saved.%s" % (v["number"], " %d passenger(s) notified." % told if told else ""))

            with st.expander("Delete this flight"):
                if ever:
                    st.warning("This flight appears in %d booking record(s), so deleting it would break them. Set its status to "
                               "Cancelled instead: passengers are then flagged and can claim a full refund in My Bookings." % ever)
                elif st.checkbox("I understand this permanently deletes flight %s" % f["flight_number"], key=prefix + "delok") \
                        and st.button("Delete flight", key=prefix + "del"):
                    conn.execute("DELETE FROM seats WHERE flight_id=?", (fid,))
                    conn.execute("DELETE FROM flights WHERE id=?", (fid,))
                    conn.commit()
                    st.session_state["flights_flash"] = "Flight %s (ID %d) deleted." % (f["flight_number"], fid)
                    st.rerun()

    # ----------------------------------------------------------------- bulk
    with tab_bulk:
        st.caption("Apply a delay or cancellation to every flight on a route/date in one go, and notify "
                   "every booked passenger automatically.")
        airports = [a["code"] for a in conn.execute("SELECT code FROM airports").fetchall()]
        c1, c2, c3 = st.columns(3)
        with c1:
            o = st.selectbox("Origin", airports, key="bulk_o")
        with c2:
            d = st.selectbox("Destination", [a for a in airports if a != o], key="bulk_d")
        with c3:
            date = st.date_input("Date", key="bulk_date")
        action = st.radio("Action", ["Delay", "Cancel"], horizontal=True)
        delay_min = st.number_input("Delay (minutes)", min_value=10, value=45, step=5) if action == "Delay" else 0

        affected = conn.execute(
            "SELECT * FROM flights WHERE origin=? AND destination=? AND depart_date=?",
            (o, d, date.isoformat()),
        ).fetchall()
        st.write(f"Matches **{len(affected)}** flight(s) on this route/date.")

        if affected and st.button("Apply & notify affected passengers", type="primary"):
            notified = 0
            for f in affected:
                if action == "Delay":
                    conn.execute("UPDATE flights SET status='Delayed', delay_min=? WHERE id=?", (delay_min, f["id"]))
                else:
                    conn.execute("UPDATE flights SET status='Cancelled' WHERE id=?", (f["id"],))
                passengers = conn.execute(
                    """SELECT DISTINCT b.user_id, b.pnr FROM booking_segments bs
                       JOIN bookings b ON b.id = bs.booking_id WHERE bs.flight_id=? AND b.status='Confirmed'""",
                    (f["id"],),
                ).fetchall()
                for p in passengers:
                    msg = (f"Your flight {f['flight_number']} ({o}->{d}, {date.isoformat()}) is delayed by "
                           f"{delay_min} min." if action == "Delay" else
                           f"Your flight {f['flight_number']} ({o}->{d}, {date.isoformat()}) has been cancelled.")
                    notify(conn, p["user_id"], "Push", f"Flight update - PNR {p['pnr']}", msg)
                    notified += 1
            conn.commit()
            st.success(f"Updated {len(affected)} flight(s) and sent {notified} passenger notification(s).")

    conn.close()


def bookings():
    st.title("\U0001F4D1 All Bookings")

    user = st.session_state.get("user")
    if not user or not user.get("is_admin"):
        st.error("Admin access only.")
        st.stop()

    conn = get_conn()
    bookings = pd.read_sql_query(
        "SELECT pnr, user_id, guest_email, trip_type, cabin, base_fare, taxes, addon_total, discount, "
        "total_amount, payment_method, status, checked_in, created_at FROM bookings ORDER BY id DESC", conn,
    )

    c1, c2 = st.columns(2)
    with c1:
        status_filter = st.multiselect("Status", sorted(bookings["status"].unique()) if len(bookings) else [])
    with c2:
        method_filter = st.multiselect("Payment method", sorted(bookings["payment_method"].dropna().unique()) if len(bookings) else [])

    filtered = bookings.copy()
    if status_filter:
        filtered = filtered[filtered["status"].isin(status_filter)]
    if method_filter:
        filtered = filtered[filtered["payment_method"].isin(method_filter)]

    st.dataframe(filtered, height=400)

    m1, m2, m3 = st.columns(3)
    m1.metric("Shown bookings", len(filtered))
    m2.metric("Shown revenue", f"{filtered['total_amount'].sum():,.0f}" if len(filtered) else "0")
    m3.metric("Avg. discount", f"{filtered['discount'].mean():,.0f}" if len(filtered) else "0")

    if len(filtered):
        st.download_button("\U0001F4E5 Export shown bookings as CSV", filtered.to_csv(index=False),
                            file_name="bookings_export.csv", mime="text/csv")

    st.subheader("Revenue by payment method")
    if len(bookings):
        by_method = bookings[bookings["status"] == "Confirmed"].groupby("payment_method")["total_amount"].sum()
        st.bar_chart(by_method)

    conn.close()


def users():
    st.title("\U0001F464 User Management")

    user = st.session_state.get("user")
    if not user or not user.get("is_admin"):
        st.error("Admin access only.")
        st.stop()

    conn = get_conn()
    users_df = pd.read_sql_query(
        "SELECT id, name, email, phone, loyalty_points, wallet_balance, is_admin, created_at FROM users "
        "WHERE is_admin=0 ORDER BY id DESC", conn,
    )
    search = st.text_input("Search by name or email")
    if search:
        mask = users_df["name"].str.contains(search, case=False, na=False, regex=False) | \
               users_df["email"].str.contains(search, case=False, na=False, regex=False)
        users_df = users_df[mask]

    st.dataframe(users_df, height=300)
    st.metric("Total travellers", len(users_df))

    st.divider()
    st.subheader("Adjust a user's account")
    uid = st.number_input("User ID", min_value=1, step=1)
    u_row = conn.execute("SELECT * FROM users WHERE id=? AND is_admin=0", (int(uid),)).fetchone() if uid else None
    if u_row:
        st.write(f"**{u_row['name']}** \u2014 {u_row['email']}")
        bookings_count = conn.execute("SELECT COUNT(*) c FROM bookings WHERE user_id=?", (int(uid),)).fetchone()["c"]
        st.caption(f"Total bookings made: {bookings_count}")

        c1, c2, c3 = st.columns(3)
        with c1:
            new_points = st.number_input("Loyalty points", value=int(u_row["loyalty_points"]))
        with c2:
            new_wallet = st.number_input("Wallet balance", value=float(u_row["wallet_balance"]))
        with c3:
            reason = st.text_input("Adjustment reason", placeholder="e.g. goodwill credit")
        if st.button("Save adjustment"):
            delta = new_wallet - u_row["wallet_balance"]
            conn.execute("UPDATE users SET loyalty_points=?, wallet_balance=? WHERE id=?",
                         (int(new_points), new_wallet, int(uid)))
            if delta != 0:
                conn.execute("INSERT INTO wallet_transactions (user_id, amount, reason) VALUES (?,?,?)",
                             (int(uid), delta, reason or "Admin adjustment"))
            conn.commit()
            st.success("Updated.")
            st.rerun()

        if st.checkbox("I understand this permanently deletes the account (bookings are kept)") and \
                st.button("\U0001F5D1\ufe0f Delete this user account"):
            delete_account(int(uid))
            st.rerun()
    else:
        st.caption("Enter a valid user ID above to manage that account.")

    conn.close()


def promocodes():
    st.title("\U0001F3F7\ufe0f Promo Codes")

    user = st.session_state.get("user")
    if not user or not user.get("is_admin"):
        st.error("Admin access only.")
        st.stop()

    conn = get_conn()

    with st.form("new_promo", clear_on_submit=True):
        st.markdown("**Create a promo code**")
        c1, c2, c3 = st.columns(3)
        with c1:
            code = st.text_input("Code", placeholder="SUMMER25").upper()
        with c2:
            pct = st.number_input("Discount %", min_value=1, max_value=100, value=10)
        with c3:
            max_disc = st.number_input("Max discount amount", min_value=0, value=1000)
        if st.form_submit_button("Create") and code:
            existing = conn.execute("SELECT 1 FROM promo_codes WHERE code=?", (code,)).fetchone()
            if existing:
                st.error("That code already exists.")
            else:
                conn.execute("INSERT INTO promo_codes (code, discount_pct, max_discount, active) VALUES (?,?,?,1)",
                             (code, pct, max_disc))
                conn.commit()
                st.success(f"Created {code}.")
                st.rerun()

    st.divider()
    st.subheader("Existing codes")
    codes = conn.execute("SELECT * FROM promo_codes ORDER BY code").fetchall()
    for c in codes:
        with st.container(border=True):
            cc1, cc2, cc3 = st.columns([3, 1, 1])
            with cc1:
                st.write(f"**{c['code']}** \u2014 {c['discount_pct']}% off, capped at {c['max_discount']}")
                st.caption("Active" if c["active"] else "Inactive")
            with cc2:
                label = "Deactivate" if c["active"] else "Activate"
                if st.button(label, key=f"tog{c['code']}"):
                    conn.execute("UPDATE promo_codes SET active=? WHERE code=?", (0 if c["active"] else 1, c["code"]))
                    conn.commit()
                    st.rerun()
            with cc3:
                if st.button("Delete", key=f"delp{c['code']}"):
                    conn.execute("DELETE FROM promo_codes WHERE code=?", (c["code"],))
                    conn.commit()
                    st.rerun()

    conn.close()


def notifications():
    st.title("\U0001F4E2 Notifications")

    user = st.session_state.get("user")
    if not user or not user.get("is_admin"):
        st.error("Admin access only.")
        st.stop()

    conn = get_conn()
    tab1, tab2 = st.tabs(["Feed", "Broadcast to a route"])

    with tab1:
        notes = pd.read_sql_query("SELECT * FROM notifications ORDER BY id DESC LIMIT 200", conn)
        st.dataframe(notes, height=400)

    with tab2:
        st.caption("Send a custom alert (e.g. weather, strike, gate change) to every passenger booked "
                   "on a route for a given date.")
        airports = [a["code"] for a in conn.execute("SELECT code FROM airports").fetchall()]
        c1, c2, c3 = st.columns(3)
        with c1:
            o = st.selectbox("Origin", airports, key="bc_o")
        with c2:
            d = st.selectbox("Destination", [a for a in airports if a != o], key="bc_d")
        with c3:
            date = st.date_input("Date", value=dt.date.today(), key="bc_date")
        subject = st.text_input("Subject", value="Important travel update")
        body = st.text_area("Message", value="Please check the airport's official channels for the latest updates.")
        channel = st.selectbox("Channel", ["Email", "SMS", "Push"])

        recipients = conn.execute(
            """SELECT DISTINCT b.user_id, b.pnr, b.guest_email FROM booking_segments bs
               JOIN bookings b ON b.id = bs.booking_id JOIN flights f ON f.id = bs.flight_id
               WHERE f.origin=? AND f.destination=? AND f.depart_date=? AND b.status='Confirmed'""",
            (o, d, date.isoformat()),
        ).fetchall()
        st.write(f"This will reach **{len(recipients)}** booking(s) on {o} \u2192 {d}, {date.isoformat()}.")

        if not recipients:
            reverse_count = conn.execute(
                """SELECT COUNT(*) c FROM booking_segments bs JOIN bookings b ON b.id=bs.booking_id
                   JOIN flights f ON f.id=bs.flight_id
                   WHERE f.origin=? AND f.destination=? AND f.depart_date=? AND b.status='Confirmed'""",
                (d, o, date.isoformat()),
            ).fetchone()["c"]
            if reverse_count:
                st.warning(f"Found {reverse_count} confirmed booking(s) the other way round \u2014 "
                           f"**{d} \u2192 {o}** on {date.isoformat()} \u2014 but none for {o} \u2192 {d}. "
                           "Matching is directional; swap Origin/Destination above if that's the one you meant.")
            st.info("No confirmed bookings match that exact route and date, so there's no one to notify \u2014 "
                     "the button below is disabled. Try one of these routes/dates that actually have bookings:")
            live_routes = conn.execute(
                """SELECT DISTINCT f.origin, f.destination, f.depart_date FROM booking_segments bs
                   JOIN bookings b ON b.id = bs.booking_id JOIN flights f ON f.id = bs.flight_id
                   WHERE b.status='Confirmed' ORDER BY f.depart_date LIMIT 10"""
            ).fetchall()
            if live_routes:
                st.dataframe(pd.DataFrame([dict(r) for r in live_routes]))
            else:
                st.caption("There are no confirmed bookings in the system at all yet \u2014 "
                           "make a test booking as a traveller first, then come back here.")

        if st.button("Send broadcast", type="primary", disabled=not recipients):
            for r in recipients:
                notify(conn, r["user_id"], channel, subject, f"{body}  (PNR {r['pnr']})")
            conn.commit()
            st.success(f"Sent to {len(recipients)} booking(s).")

    conn.close()
