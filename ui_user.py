"""Traveller / guest pages. Each page is a plain function; app.py decides which one to draw."""
import datetime as dt
import re

import pandas as pd
import streamlit as st

from db import (EXTRA_BAGGAGE_PER_KG, INSURANCE_PRICE, MEAL_PRICE, NEARBY, RESCHEDULE_FEE, apply_promo, arrive_dt,
                cancel_booking, check_price_alerts, depart_dt, ensure_booking_seats, ensure_seats, fmt_price,
                gen_pnr, get_conn, notify, price_breakdown, referral_code_for)
from nav import go
from pdfs import build_boarding_pass_pdf, build_ticket_pdf


def dashboard():
    st.title("\U0001F3E0 Your Dashboard")

    user = st.session_state.get("user")
    guest = st.session_state.get("guest")
    currency = st.session_state.get("currency", "INR")

    if not (user or guest):
        st.warning("Please log in or continue as guest from the sidebar.")
        st.stop()

    if guest and not user:
        st.info("You're browsing as a **guest**. Create a free account to unlock loyalty points, "
                "a wallet, saved travelers and a referral bonus.")
        if st.button("Search flights \u2192", key="pl1"):
            go("Search Flights")
        st.stop()

    conn = get_conn()
    check_price_alerts(conn, user["id"])

    # ---------- loyalty tier ----------
    TIERS = [(0, "Bronze", "\U0001F949"), (500, "Silver", "\U0001F948"),
             (1500, "Gold", "\U0001F947"), (3000, "Platinum", "\U0001F48E")]
    pts = user["loyalty_points"]
    tier_name, tier_icon = "Bronze", "\U0001F949"
    next_threshold = TIERS[1][0]
    for threshold, name, icon in TIERS:
        if pts >= threshold:
            tier_name, tier_icon = name, icon
    idx = [t[1] for t in TIERS].index(tier_name)
    next_threshold = TIERS[idx + 1][0] if idx + 1 < len(TIERS) else None

    c1, c2, c3 = st.columns(3)
    c1.metric("Loyalty tier", f"{tier_icon} {tier_name}")
    c2.metric("Points balance", pts)
    c3.metric("Wallet balance", fmt_price(user["wallet_balance"], currency))

    if next_threshold:
        st.progress(min(pts / next_threshold, 1.0),
                    text=f"{pts} / {next_threshold} points to {TIERS[idx + 1][1]} tier")
    else:
        st.success("You've reached the top tier \u2014 Platinum perks unlocked (demo: priority seats, free insurance).")

    st.caption("Earn 1 point per 100 spent; redeem them at payment (1 point = 1 INR, up to 25% of a booking).")

    # ---------- wallet ----------
    with st.expander("\U0001F45B Wallet: add money & activity"):
        amt = st.number_input("Amount to add (demo, INR)", min_value=100, max_value=50000, value=1000, step=100)
        if st.button("Add money"):
            conn.execute("UPDATE users SET wallet_balance = wallet_balance + ? WHERE id=?", (amt, user["id"]))
            conn.execute("INSERT INTO wallet_transactions (user_id, amount, reason) VALUES (?,?,?)", (user["id"], amt, "Wallet top-up (demo)"))
            conn.commit()
            st.rerun()
        for t in conn.execute("SELECT * FROM wallet_transactions WHERE user_id=? ORDER BY id DESC LIMIT 10", (user["id"],)).fetchall():
            st.caption(f"{t['created_at']}  \u00b7  {t['amount']:+,.2f}  \u00b7  {t['reason']}")

    # ---------- referral ----------
    st.subheader("\U0001F381 Refer a friend")
    code = referral_code_for(user["id"])
    rc1, rc2 = st.columns([2, 3])
    with rc1:
        st.code(code)
    with rc2:
        st.write("Share this code at signup \u2014 you both get a **300** wallet credit (demo).")

    # ---------- upcoming trip ----------
    st.subheader("\u2708\ufe0f Your next trip")
    upcoming = conn.execute(
        """SELECT b.pnr, b.status, MIN(f.depart_date) AS next_date, f.origin, f.destination, f.depart_time
           FROM bookings b JOIN booking_segments bs ON bs.booking_id=b.id JOIN flights f ON f.id=bs.flight_id
           WHERE b.user_id=? AND b.status='Confirmed' AND f.depart_date >= ?
           GROUP BY b.id ORDER BY next_date LIMIT 1""",
        (user["id"], dt.date.today().isoformat()),
    ).fetchone()
    if upcoming and dt.datetime.fromisoformat(f"{upcoming['next_date']}T{upcoming['depart_time']}") <= dt.datetime.now():
        upcoming = None
    if upcoming:
        st.success(f"**{upcoming['origin']} \u2192 {upcoming['destination']}** on {upcoming['next_date']} "
                   f"at {upcoming['depart_time']}  \u2014 PNR {upcoming['pnr']}")
        bc1, bc2 = st.columns(2)
        with bc1:
            if st.button("Web check-in", key="pl2"):
                go("Web Check-in")
        with bc2:
            if st.button("Manage booking", key="pl3"):
                go("My Bookings")
    else:
        st.info("No upcoming trips yet.")
        if st.button("Search flights \u2192", key="pl4"):
            go("Search Flights")

    # ---------- recent notifications (Gmail/Messages-style preview) ----------
    st.subheader("\U0001F514 Recent updates")
    CHANNEL_ICON = {"Email": "\U0001F4E7", "SMS": "\U0001F4F1", "Push": "\U0001F514"}


    def fmt_time(ts):
        try:
            parsed = dt.datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
            return parsed.strftime("%b %d \u00b7 %I:%M %p")
        except (ValueError, TypeError):
            return ts or ""


    notes = conn.execute(
        "SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 3", (user["id"],)
    ).fetchall()

    if notes:
        for n in notes:
            preview = n["body"] if len(n["body"]) <= 70 else n["body"][:70].rstrip() + "\u2026"
            with st.container(border=True):
                c1, c2 = st.columns([5, 2])
                with c1:
                    icon = CHANNEL_ICON.get(n["channel"], "\U0001F514")
                    st.markdown(f"{icon}  **{n['subject']}**")
                    st.caption(preview)
                with c2:
                    st.caption(fmt_time(n["created_at"]))
        if st.button("View all notifications \u2192", key="pl5"):
            go("Notifications")
    else:
        st.caption("No notifications yet.")

    conn.close()


def search():
    st.title("\U0001F50D Search Flights")

    if not (st.session_state.get("user") or st.session_state.get("guest")):
        st.warning("Please log in, sign up, or continue as guest from the Home page first.")
        st.stop()

    conn = get_conn()
    airports = conn.execute("SELECT code, city, name FROM airports ORDER BY city").fetchall()
    conn.close()
    airport_labels = [f"{a['code']} \u2014 {a['city']} ({a['name']})" for a in airports]
    code_by_label = {lbl: a["code"] for lbl, a in zip(airport_labels, airports)}


    def airport_picker(label, key, default_idx=0):
        choice = st.selectbox(label, airport_labels, index=default_idx, key=key)
        return code_by_label[choice]


    trip_type = st.radio("Trip type", ["One-way", "Round-trip", "Multi-city"], horizontal=True)

    legs = []
    today = dt.date.today()

    if trip_type in ("One-way", "Round-trip"):
        c1, c2, c3 = st.columns(3)
        with c1:
            origin = airport_picker("From", "o1", 0)
        with c2:
            dest = airport_picker("To", "d1", 1)
        with c3:
            depart = st.date_input("Departure date", min_value=today, value=today + dt.timedelta(days=3), key="dep1")
        legs.append({"origin": origin, "destination": dest, "date": depart.isoformat()})
        if trip_type == "Round-trip":
            c4 = st.columns(1)[0]
            with c4:
                ret = st.date_input("Return date", min_value=depart, value=depart + dt.timedelta(days=5), key="ret1")
            legs.append({"origin": dest, "destination": origin, "date": ret.isoformat()})
    else:
        n_legs = st.number_input("Number of legs", min_value=2, max_value=5, value=2, step=1)
        last_date = today + dt.timedelta(days=3)
        for i in range(int(n_legs)):
            c1, c2, c3 = st.columns(3)
            with c1:
                o = airport_picker(f"Leg {i+1}: From", f"mo{i}", i % len(airport_labels))
            with c2:
                d = airport_picker(f"Leg {i+1}: To", f"md{i}", (i + 1) % len(airport_labels))
            with c3:
                dte = st.date_input(f"Leg {i+1}: Date", min_value=last_date, value=last_date, key=f"mdate{i}")
            legs.append({"origin": o, "destination": d, "date": dte.isoformat()})
            last_date = dte

    st.subheader("Passengers & Cabin")
    p1, p2, p3, p4 = st.columns(4)
    with p1:
        adults = st.number_input("Adults", min_value=1, max_value=9, value=1)
    with p2:
        children = st.number_input("Children (2-11)", min_value=0, max_value=9, value=0)
    with p3:
        infants = st.number_input("Infants (<2)", min_value=0, max_value=9, value=0)
    with p4:
        cabin = st.selectbox("Cabin class", ["Economy", "Business"])

    if st.checkbox("Show nearby airport suggestions", value=True) and legs:
        leg = legs[0]
        conn = get_conn()
        tips = []
        for alt in NEARBY.get(leg["origin"], []):
            r = conn.execute("SELECT MIN(price_economy) m FROM flights WHERE origin=? AND destination=? AND depart_date=? AND status!='Cancelled'",
                             (alt, leg["destination"], leg["date"])).fetchone()["m"]
            if r:
                tips.append(f"fly from {alt} (from {r:,.0f})")
        for alt in NEARBY.get(leg["destination"], []):
            r = conn.execute("SELECT MIN(price_economy) m FROM flights WHERE origin=? AND destination=? AND depart_date=? AND status!='Cancelled'",
                             (leg["origin"], alt, leg["date"])).fetchone()["m"]
            if r:
                tips.append(f"land at {alt} (from {r:,.0f})")
        conn.close()
        st.caption("\U0001F4A1 Nearby options on the same day: " + ", ".join(tips) if tips else
                   "\U0001F4A1 No cheaper nearby-airport options found for the first leg's date.")

    if st.button("Search flights", type="primary"):
        if any(l["origin"] == l["destination"] for l in legs):
            st.error("Origin and destination must be different.")
        elif infants > adults:
            st.error("Each infant must be accompanied by an adult.")
        elif adults + children > 9:
            st.error("Up to 9 adults + children per booking.")
        else:
            st.session_state.search_params = {
                "legs": legs, "adults": int(adults), "children": int(children),
                "infants": int(infants), "cabin": cabin, "trip_type": trip_type,
            }
            st.session_state.search_results = None
            st.session_state.selected_flights = {}
            st.session_state.booking_draft = None  # a stale draft from an earlier search must never reach payment
            go("Results")


def results():
    st.title("\U0001F6EB Search Results")

    sp = st.session_state.get("search_params")
    if not sp:
        st.warning("Please run a search first.")
        st.stop()

    currency = st.session_state.get("currency", "INR")
    conn = get_conn()


    def cabin_price_col(cabin):
        return "price_business" if cabin == "Business" else "price_economy"


    seats_needed = sp["adults"] + sp["children"]  # lap infants don't take a seat


    def fetch_flights(origin, destination, date):
        """Hide cancelled flights, flights that have already left, and flights without enough free seats."""
        now = dt.datetime.now()
        out = []
        for f in conn.execute(
            "SELECT f.*, al.name AS airline_name FROM flights f JOIN airlines al ON al.code = f.airline_code "
            "WHERE origin=? AND destination=? AND depart_date=? AND status!='Cancelled' ORDER BY depart_time",
            (origin, destination, date),
        ).fetchall():
            if depart_dt(f) <= now:
                continue
            seats = conn.execute("SELECT COUNT(*) n, COALESCE(SUM(is_booked=0),0) free FROM seats WHERE flight_id=? AND cabin=?",
                                 (f["id"], sp["cabin"])).fetchone()
            if seats["n"] and seats["free"] < seats_needed:
                continue  # sold out (seat map is created lazily, so no rows yet means nothing sold)
            out.append(f)
        return out


    for idx, leg in enumerate(sp["legs"]):
        st.subheader(f"Leg {idx + 1}: {leg['origin']} \u2192 {leg['destination']}  ({leg['date']})")

        # Fare calendar: avg price for this route across a week around the chosen date
        base_date = dt.date.fromisoformat(leg["date"])
        cal_prices = []
        for d in range(-3, 4):
            day = base_date + dt.timedelta(days=d)
            if day < dt.date.today():
                continue
            row = conn.execute(
                f"SELECT MIN({cabin_price_col(sp['cabin'])}) m FROM flights WHERE origin=? AND destination=? "
                "AND depart_date=? AND status!='Cancelled'",
                (leg["origin"], leg["destination"], day.isoformat()),
            ).fetchone()
            cal_prices.append({"Date": day.isoformat(), "Lowest fare": row["m"]})  # None = no flights that day
        with st.expander(f"\U0001F4C5 Fare calendar (lowest {sp['cabin'].lower()} fare, +/- 3 days)"):
            cal_df = pd.DataFrame(cal_prices)
            st.bar_chart(cal_df.set_index("Date"))

        flights = fetch_flights(leg["origin"], leg["destination"], leg["date"])
        if not flights:
            st.info("No flights found for this route/date in the demo schedule.")
            continue

        df = pd.DataFrame([dict(f) for f in flights])
        df["price"] = df[cabin_price_col(sp["cabin"])]

        fc1, fc2, fc3, fc4 = st.columns(4)
        with fc1:
            airlines_sel = st.multiselect("Airline", sorted(df["airline_name"].unique()), key=f"al{idx}")
        with fc2:
            max_stops = st.selectbox("Max stops", ["Any", "Non-stop only", "1 stop"], key=f"st{idx}")
        with fc3:
            p_min, p_max = int(df["price"].min()), int(df["price"].max())
            if p_min == p_max:  # single-flight routes: slider needs a non-zero range
                p_max = p_min + 1
            price_range = st.slider("Price range", p_min, p_max, (p_min, p_max), key=f"pr{idx}")
        with fc4:
            sort_by = st.selectbox("Sort by", ["Price", "Duration", "Departure time"], key=f"so{idx}")

        filtered = df.copy()
        if airlines_sel:
            filtered = filtered[filtered["airline_name"].isin(airlines_sel)]
        if max_stops == "Non-stop only":
            filtered = filtered[filtered["stops"] == 0]
        elif max_stops == "1 stop":
            filtered = filtered[filtered["stops"] <= 1]
        filtered = filtered[(filtered["price"] >= price_range[0]) & (filtered["price"] <= price_range[1])]

        sort_col = {"Price": "price", "Duration": "duration_min", "Departure time": "depart_time"}[sort_by]
        filtered = filtered.sort_values(sort_col)

        if filtered.empty:
            st.info("No flights match the selected filters.")
            continue

        options = {}
        for _, row in filtered.iterrows():
            stop_txt = "Non-stop" if row["stops"] == 0 else f"{row['stops']} stop via {row['layover_airport']}"
            late = f" | \u23F1 delayed {row['delay_min']} min" if row["status"] == "Delayed" else ""
            label = (f"{row['airline_name']} {row['flight_number']} | {row['depart_time']}\u2192{row['arrive_time']} "
                     f"({row['duration_min']}min, {stop_txt}) | {fmt_price(row['price'], currency)}{late}")
            options[label] = int(row["id"])

        choice = st.radio("Select a flight for this leg", list(options.keys()), key=f"choice{idx}")
        st.session_state.selected_flights[idx] = options[choice]

    conn.close()

    if len(st.session_state.selected_flights) == len(sp["legs"]):
        if st.button("View flight details \u2192", type="primary"):
            go("Flight Details")
    else:
        st.info("Select a flight for every leg to continue.")


def flight_details():
    st.title("\U0001F4CB Flight Details")

    sp = st.session_state.get("search_params")
    sel = st.session_state.get("selected_flights")
    if not sp or not sel or len(sel) != len(sp["legs"]):
        st.warning("Please complete a search and select flights first.")
        st.stop()

    currency = st.session_state.get("currency", "INR")
    conn = get_conn()
    cabin_col = "price_business" if sp["cabin"] == "Business" else "price_economy"

    for idx in range(len(sp["legs"])):
        fid = sel[idx]
        f = conn.execute(
            "SELECT f.*, al.name AS airline_name FROM flights f JOIN airlines al ON al.code=f.airline_code WHERE f.id=?",
            (fid,),
        ).fetchone()
        with st.container(border=True):
            st.subheader(f"Leg {idx + 1}: {f['airline_name']} {f['flight_number']}")
            c1, c2, c3 = st.columns(3)
            c1.metric("Route", f"{f['origin']} \u2192 {f['destination']}")
            c2.metric("Duration", f"{f['duration_min']} min")
            c3.metric(f"{sp['cabin']} fare", fmt_price(f[cabin_col], currency))

            st.write(f"**Departs:** {f['depart_time']} on {f['depart_date']}  |  **Arrives:** {f['arrive_time']}")
            st.write(f"**Aircraft:** {f['aircraft']}  |  **Status:** {f['status']}"
                     + (f" ({f['delay_min']} min)" if f["status"] == "Delayed" else ""))
            rv = conn.execute("SELECT AVG(rv.rating) a, COUNT(*) c FROM reviews rv JOIN flights f2 ON f2.id=rv.flight_id "
                              "WHERE f2.flight_number=?", (f["flight_number"],)).fetchone()
            if rv["c"]:
                st.caption(f"\u2B50 {rv['a']:.1f} / 5 from {rv['c']} passenger review(s)")
            if f["stops"] == 0:
                st.success("Non-stop flight")
            else:
                st.info(f"{f['stops']} stop via **{f['layover_airport']}** \u2014 layover {f['layover_min']} min")

            st.markdown("**Baggage allowance**")
            b1, b2 = st.columns(2)
            b1.write(f"Cabin bag: {f['baggage_cabin']}")
            b2.write(f"Check-in bag: {f['baggage_checkin']}")

            with st.expander("Fare rules (demo)"):
                st.markdown(
                    "- Cancellation before departure: 80% of the amount paid is refunded to your wallet "
                    "(100% if the airline cancels the flight).\n"
                    "- Date change: INR 2,500 + any fare difference, onto another flight on the same route.\n"
                    "- Seat selection and meals are chargeable unless bundled.\n"
                    "- Check-in counter closes 60 minutes before departure (domestic)."
                )
    conn.close()

    if st.button("Continue to booking \u2192", type="primary"):
        go("Booking")


def booking():
    st.title("\U0001F9CD Passenger Details, Seats & Add-ons")

    sp = st.session_state.get("search_params")
    sel = st.session_state.get("selected_flights")
    if not sp or not sel or len(sel) != len(sp["legs"]):
        st.warning("Please complete search, selection and flight details first.")
        st.stop()

    currency = st.session_state.get("currency", "INR")
    user = st.session_state.get("user")
    total_pax = sp["adults"] + sp["children"] + sp["infants"]

    saved_travelers = {}
    if user:
        _conn = get_conn()
        for t in _conn.execute("SELECT * FROM saved_travelers WHERE user_id=? ORDER BY full_name", (user["id"],)).fetchall():
            saved_travelers[t["full_name"]] = dict(t)
        _conn.close()

    st.subheader("1. Passenger details")
    passengers = []
    for i in range(total_pax):
        ptype = "Adult" if i < sp["adults"] else ("Child" if i < sp["adults"] + sp["children"] else "Infant")
        with st.container(border=True):
            st.markdown(f"**Passenger {i + 1} ({ptype})**")
            if saved_travelers:
                lc1, lc2 = st.columns([3, 1])
                with lc1:
                    pick = st.selectbox("Load from saved travelers (optional)",
                                         ["\u2014 none \u2014"] + list(saved_travelers.keys()), key=f"pick{i}")
                with lc2:
                    st.write("")
                    if pick != "\u2014 none \u2014" and st.button("Load", key=f"loadbtn{i}"):
                        t = saved_travelers[pick]
                        st.session_state[f"pname{i}"] = t["full_name"]
                        st.session_state[f"page{i}"] = t["age"]
                        st.session_state[f"pgen{i}"] = t["gender"]
                        st.session_state[f"meal{i}"] = t["meal_pref"]
                        st.rerun()
            c1, c2, c3 = st.columns(3)
            with c1:
                name = st.text_input("Full name", key=f"pname{i}")
            with c2:
                age_key = f"page{i}"
                # seed the default through session_state and never pass value=: it behaves the same on every Streamlit version
                if age_key not in st.session_state:
                    st.session_state[age_key] = 28 if ptype == "Adult" else (6 if ptype == "Child" else 1)
                age = st.number_input("Age", min_value=0, max_value=120, key=age_key)
            with c3:
                gender = st.selectbox("Gender", ["Male", "Female", "Other"], key=f"pgen{i}")
            passengers.append({"full_name": name, "age": int(age), "gender": gender, "passenger_type": ptype})

    st.divider()
    st.subheader("2. Seat selection")

    conn = get_conn()
    seat_choices = {}  # (leg_idx, pax_idx) -> seat_no
    addon_total = 0.0

    for leg_idx in range(len(sp["legs"])):
        fid = sel[leg_idx]
        ensure_seats(conn, fid)
        st.markdown(f"**Leg {leg_idx + 1} seat map** ({'Business' if sp['cabin']=='Business' else 'Economy'} cabin)")
        seats = conn.execute(
            "SELECT seat_no, cabin, is_booked FROM seats WHERE flight_id=? AND cabin=? ORDER BY seat_no",
            (fid, sp["cabin"]),
        ).fetchall()
        taken = {s["seat_no"] for s in seats if s["is_booked"]}
        all_seats = [s["seat_no"] for s in seats]
        already_chosen_this_leg = set()

        cols_per_row = 3 if sp["cabin"] == "Business" else 6
        for p_idx in range(total_pax):
            if passengers[p_idx]["passenger_type"] == "Infant":
                continue  # lap infants travel without their own seat
            key = f"seat_{fid}_{p_idx}"  # keyed by flight so a seat picked for another flight never carries over
            available = [s for s in all_seats if s not in taken and s not in already_chosen_this_leg]
            label = f"Leg {leg_idx+1} \u2013 seat for {passengers[p_idx]['full_name'] or f'Passenger {p_idx+1}'}"
            choice = st.selectbox(label, ["No preference"] + available, key=key)
            if choice != "No preference":
                already_chosen_this_leg.add(choice)
                seat_choices[(leg_idx, p_idx)] = choice
        with st.expander(f"View full seat map \u2014 leg {leg_idx + 1}"):
            grid_cols = st.columns(cols_per_row)
            for i, s in enumerate(all_seats):
                mark = "\U0001F534" if s in taken else ("\U0001F535" if s in already_chosen_this_leg else "\U0001F7E2")
                grid_cols[i % cols_per_row].write(f"{mark} {s}")
        st.caption("\U0001F7E2 Available \u00b7 \U0001F535 Selected \u00b7 \U0001F534 Taken")

    conn.close()

    st.divider()
    st.subheader("3. Add-ons per passenger")
    for i, p in enumerate(passengers):
        with st.container(border=True):
            st.markdown(f"**{p['full_name'] or f'Passenger {i+1}'}** add-ons")
            a1, a2, a3 = st.columns(3)
            with a1:
                meal = st.selectbox("Meal", list(MEAL_PRICE.keys()), key=f"meal{i}")
            with a2:
                extra_bag = st.number_input("Extra baggage (kg)", min_value=0, max_value=30, value=0, key=f"bag{i}")
            with a3:
                insurance = st.checkbox("Travel insurance (+" + fmt_price(INSURANCE_PRICE, currency) + ")", key=f"ins{i}")
            passengers[i].update({
                "meal": meal, "baggage_addon_kg": int(extra_bag), "insurance": int(insurance),
                "seat_no": None,
            })
            addon_total += MEAL_PRICE[meal] + extra_bag * EXTRA_BAGGAGE_PER_KG + (INSURANCE_PRICE if insurance else 0)

    # attach chosen seats (first leg's seat shown as primary passenger seat for simplicity in confirmation)
    for (leg_idx, p_idx), seat in seat_choices.items():
        if leg_idx == 0:
            passengers[p_idx]["seat_no"] = seat

    st.info(f"Add-ons subtotal: {fmt_price(addon_total, currency)}")

    AGE_RULES = {"Adult": (12, 120), "Child": (2, 11), "Infant": (0, 1)}
    problems = []
    for i, p in enumerate(passengers):
        lo, hi = AGE_RULES[p["passenger_type"]]
        if not p["full_name"].strip():
            problems.append(f"Enter the full name for passenger {i + 1}.")
        if not lo <= p["age"] <= hi:
            problems.append(f"Passenger {i + 1} is booked as {p['passenger_type']}, so age must be {lo}\u2013{hi}.")
    for pb in problems:
        st.warning(pb)
    if not problems and st.button("Continue to payment \u2192", type="primary"):
        st.session_state.booking_draft = {
            "passengers": passengers,
            "seat_choices": {f"{k[0]}_{k[1]}": v for k, v in seat_choices.items()},
            "addon_total": round(addon_total, 2),
        }
        go("Payment")


def payment():
    st.title("\U0001F4B3 Payment")

    sp = st.session_state.get("search_params")
    sel = st.session_state.get("selected_flights")
    draft = st.session_state.get("booking_draft")
    if not sp or not sel or not draft or len(sel) != len(sp["legs"]):
        st.warning("Please complete the booking steps first.")
        st.stop()

    currency = st.session_state.get("currency", "INR")
    user = st.session_state.get("user")
    conn = get_conn()
    cabin_col = "price_business" if sp["cabin"] == "Business" else "price_economy"

    base_fare, leg_flights = 0.0, []
    for idx in range(len(sp["legs"])):
        f = conn.execute("SELECT * FROM flights WHERE id=?", (sel[idx],)).fetchone()
        leg_flights.append(f)
        base_fare += f[cabin_col] * (sp["adults"] + sp["children"]) + f[cabin_col] * 0.1 * sp["infants"]
    base_fare = round(base_fare, 2)
    addon_total = draft["addon_total"]

    guest_email = guest_name = None
    if not user:
        st.subheader("Guest contact details")
        guest_name = st.text_input("Your name")
        guest_email = st.text_input("Email for confirmation")

    st.subheader("Promo code")
    promo_code = st.text_input("Have a promo code?", key="promo_input")
    discount, promo_msg = apply_promo(conn, base_fare, promo_code) if promo_code else (0, None)
    if promo_msg:
        (st.success if discount else st.error)(promo_msg)

    taxes, total = price_breakdown(base_fare, addon_total, discount)
    points_used = 0
    if user and user["loyalty_points"] > 0:
        cap = min(user["loyalty_points"], int(total * 0.25))
        if cap > 0 and st.checkbox(f"Redeem {cap} loyalty points (1 point = 1 INR, up to 25% of the total)"):
            points_used = cap
            total = round(total - points_used, 2)

    st.subheader("Price breakdown")
    rows = [("Base fare", fmt_price(base_fare, currency)), ("Taxes & fees", fmt_price(taxes, currency)),
            ("Add-ons", fmt_price(addon_total, currency)), ("Discount", f"-{fmt_price(discount, currency)}")]
    if points_used:
        rows.append(("Loyalty points", f"-{fmt_price(points_used, currency)}"))
    rows.append(("Total payable", fmt_price(total, currency)))
    st.table({"Item": [r[0] for r in rows], "Amount": [r[1] for r in rows]})

    st.subheader("Payment method")
    method = st.radio("Pay using", ["Credit/Debit Card", "UPI", "Wallet"], horizontal=True)
    if method == "Wallet":
        bal = user["wallet_balance"] if user else 0
        st.caption(f"Wallet balance: {fmt_price(bal, currency)}")
        if not user:
            st.error("Log in to pay from your wallet.")
        elif bal < total:
            st.error("Insufficient wallet balance. You can add money from the dashboard.")
    elif method == "Credit/Debit Card":
        c1, c2, c3 = st.columns(3)
        card = c1.text_input("Card number", placeholder="4111 1111 1111 1111", key="cc_no")
        exp = c2.text_input("Expiry", placeholder="MM/YY", key="cc_exp")
        cvv = c3.text_input("CVV", type="password", key="cc_cvv")
        st.caption("Demo payment \u2014 no real card is charged.")
    else:
        upi = st.text_input("UPI ID", placeholder="name@upi", key="upi_id")
        st.caption("Demo payment \u2014 no real UPI transaction occurs.")


    def payment_errors():
        errs = []
        if method == "Credit/Debit Card":
            if not re.fullmatch(r"\d{13,19}", card.replace(" ", "")):
                errs.append("Enter a valid card number (13\u201319 digits).")
            m = re.fullmatch(r"(\d{2})/(\d{2})", exp.strip())
            if not m or not 1 <= int(m[1]) <= 12 or (2000 + int(m[2]), int(m[1])) < (dt.date.today().year, dt.date.today().month):
                errs.append("Enter a valid, unexpired expiry date (MM/YY).")
            if not re.fullmatch(r"\d{3,4}", cvv):
                errs.append("Enter a valid CVV.")
        elif method == "UPI" and not re.fullmatch(r"[\w.\-]{2,}@[A-Za-z]{2,}", upi.strip()):
            errs.append("Enter a valid UPI ID like name@bank.")
        if method == "Wallet" and (not user or user["wallet_balance"] < total):
            errs.append("Wallet balance is not enough for this booking.")
        if not user and not (guest_name and guest_name.strip() and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", (guest_email or "").strip())):
            errs.append("Enter your name and a valid email for the confirmation.")
        return errs


    if st.button("Pay now & confirm booking", type="primary"):
        errs = payment_errors()
        now = dt.datetime.now()
        # re-check live state: another traveller may have taken a seat, or the flight may have changed, since selection
        for li, f in enumerate(leg_flights):
            fresh = conn.execute("SELECT status FROM flights WHERE id=?", (f["id"],)).fetchone()
            if fresh["status"] == "Cancelled" or depart_dt(f) <= now:
                errs.append(f"Flight {f['flight_number']} (leg {li + 1}) is no longer available. Please search again.")
            ensure_seats(conn, f["id"])
            for pi in range(len(draft["passengers"])):
                seat = draft["seat_choices"].get(f"{li}_{pi}")
                if seat and conn.execute("SELECT is_booked FROM seats WHERE flight_id=? AND seat_no=?", (f["id"], seat)).fetchone()["is_booked"]:
                    errs.append(f"Seat {seat} on leg {li + 1} was just taken. Go back and choose another.")
        if errs:
            for e in dict.fromkeys(errs):
                st.error(e)
            st.stop()

        pnr = gen_pnr(conn)
        cur = conn.execute(
            """INSERT INTO bookings (pnr, user_id, guest_email, guest_name, trip_type, cabin, base_fare, taxes,
               addon_total, discount, total_amount, payment_method, status, points_used)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'Confirmed', ?)""",
            (pnr, user["id"] if user else None, (guest_email or "").strip().lower() or None, guest_name, sp["trip_type"],
             sp["cabin"], base_fare, taxes, addon_total, discount, total, method, points_used))
        booking_id = cur.lastrowid
        for seq, f in enumerate(leg_flights, start=1):
            conn.execute("INSERT INTO booking_segments (booking_id, flight_id, seq) VALUES (?,?,?)", (booking_id, f["id"], seq))
        for pi, p in enumerate(draft["passengers"]):
            pid = conn.execute(
                """INSERT INTO passengers (booking_id, full_name, age, gender, passenger_type, seat_no, meal,
                   baggage_addon_kg, insurance) VALUES (?,?,?,?,?,?,?,?,?)""",
                (booking_id, p["full_name"].strip(), p["age"], p["gender"], p["passenger_type"],
                 draft["seat_choices"].get(f"0_{pi}"), p["meal"], p["baggage_addon_kg"], p["insurance"])).lastrowid
            for li, f in enumerate(leg_flights):
                seat = draft["seat_choices"].get(f"{li}_{pi}")
                conn.execute("INSERT INTO booking_seats (booking_id, passenger_id, flight_id, seat_no) VALUES (?,?,?,?)",
                             (booking_id, pid, f["id"], seat))
                if seat:
                    conn.execute("UPDATE seats SET is_booked=1 WHERE flight_id=? AND seat_no=?", (f["id"], seat))
        if user:
            if method == "Wallet":
                conn.execute("UPDATE users SET wallet_balance = wallet_balance - ? WHERE id=?", (total, user["id"]))
                conn.execute("INSERT INTO wallet_transactions (user_id, amount, reason) VALUES (?,?,?)",
                             (user["id"], -total, f"Payment for PNR {pnr}"))
            conn.execute("UPDATE users SET loyalty_points = loyalty_points - ? + ? WHERE id=?",
                         (points_used, int(total // 100), user["id"]))
        who = user["id"] if user else None
        notify(conn, who, "Email", f"Booking confirmed - PNR {pnr}",
               f"Your booking (PNR {pnr}) is confirmed. Total paid: {fmt_price(total, currency)}. "
               f"Sent to {(guest_email or user['email']).strip()}.")
        notify(conn, who, "SMS", f"Booking confirmed - PNR {pnr}", f"PNR {pnr} confirmed. Total {fmt_price(total, currency)}.")
        conn.commit()
        st.session_state.last_pnr = pnr
        st.session_state.booking_draft = None
        conn.close()
        go("Confirmation")

    conn.close()


def confirmation():
    st.title("\u2705 Booking Confirmed")

    pnr = st.session_state.get("last_pnr")
    if not pnr:
        st.warning("No recent booking found. Visit My Bookings to view past bookings.")
        st.stop()

    conn = get_conn()
    row = conn.execute("SELECT * FROM bookings WHERE pnr=?", (pnr,)).fetchone()
    user = st.session_state.get("user")
    if not row or (row["user_id"] and (not user or user["id"] != row["user_id"])):
        conn.close()
        st.warning("No recent booking found. Visit My Bookings to view past bookings.")
        st.stop()
    booking = dict(row)
    segments = conn.execute(
        """SELECT f.*, al.name AS airline_name FROM booking_segments bs
           JOIN flights f ON f.id = bs.flight_id JOIN airlines al ON al.code = f.airline_code
           WHERE bs.booking_id=? ORDER BY bs.seq""",
        (booking["id"],),
    ).fetchall()
    passengers = conn.execute("SELECT * FROM passengers WHERE booking_id=?", (booking["id"],)).fetchall()
    conn.close()

    st.success(f"Your PNR is **{pnr}**. A confirmation has been sent via email and SMS (simulated \u2014 see Notifications below).")

    c1, c2, c3 = st.columns(3)
    c1.metric("Status", booking["status"])
    c2.metric("Total paid", fmt_price(booking["total_amount"], st.session_state.get("currency", "INR")))
    c3.metric("Payment method", booking["payment_method"])

    for seg in segments:
        st.write(f"**{seg['airline_name']} {seg['flight_number']}**: {seg['origin']} \u2192 {seg['destination']} "
                 f"on {seg['depart_date']}, {seg['depart_time']} \u2192 {seg['arrive_time']}")

    st.subheader("Passengers")
    conn = get_conn()
    for p in passengers:
        seats = [r["seat_no"] or "TBD" for r in conn.execute(
            "SELECT seat_no FROM booking_seats WHERE passenger_id=? ORDER BY id", (p["id"],)).fetchall()]
        seat_txt = "no seat (infant)" if p["passenger_type"] == "Infant" else "Seat " + " / ".join(seats)
        st.write(f"- {p['full_name']} ({p['passenger_type']}) \u2014 {seat_txt}, Meal: {p['meal']}")
    conn.close()

    pdf_bytes = build_ticket_pdf(booking, [dict(s) for s in segments], [dict(p) for p in passengers])
    st.download_button("\U0001F4C4 Download e-ticket (PDF)", data=pdf_bytes, file_name=f"eticket_{pnr}.pdf", mime="application/pdf")

    with st.expander("\U0001F4E7 Simulated email / SMS sent"):
        conn = get_conn()
        logs = conn.execute(
            "SELECT * FROM notifications WHERE body LIKE ? ORDER BY id DESC LIMIT 5", (f"%{pnr}%",)
        ).fetchall()
        conn.close()
        for n in logs:
            st.write(f"**[{n['channel']}]** {n['subject']}: {n['body']}")

    if st.button("Go to My Bookings \u2192", key="pl6"):
        go("My Bookings")


def my_bookings():

    st.title("\U0001F4D1 My Bookings")

    user = st.session_state.get("user")
    currency = st.session_state.get("currency", "INR")
    now = dt.datetime.now()
    conn = get_conn()

    if user:
        bookings = conn.execute("SELECT * FROM bookings WHERE user_id=? ORDER BY id DESC", (user["id"],)).fetchall()
    else:
        st.info("Guests: enter your PNR and email to find a booking.")
        pnr_in = st.text_input("PNR")
        email_in = st.text_input("Email used at booking")
        bookings = []
        if pnr_in and email_in:
            bookings = conn.execute("SELECT * FROM bookings WHERE pnr=? AND guest_email=?",
                                    (pnr_in.strip().upper(), email_in.strip().lower())).fetchall()
            if not bookings:
                st.error("No matching booking found.")

    for b in bookings:
        b = dict(b)
        ensure_booking_seats(conn, b["id"])
        conn.commit()
        bid = b["id"]
        segs = [dict(s) for s in conn.execute(
            """SELECT f.*, al.name AS airline_name, bs.id AS seg_id, bs.seq FROM booking_segments bs
               JOIN flights f ON f.id=bs.flight_id JOIN airlines al ON al.code=f.airline_code
               WHERE bs.booking_id=? ORDER BY bs.seq""", (bid,)).fetchall()]
        passengers = conn.execute("SELECT * FROM passengers WHERE booking_id=?", (bid,)).fetchall()

        # The stored status never changes by itself, so derive the real state from flight times and flight status.
        confirmed = b["status"] == "Confirmed"
        airline_cancelled = confirmed and any(s["status"] == "Cancelled" for s in segs)
        completed = confirmed and not airline_cancelled and max(arrive_dt(s) for s in segs) < now
        first_departed = depart_dt(segs[0]) <= now
        can_cancel = confirmed and (not first_departed or airline_cancelled)
        upcoming = [s for s in segs if depart_dt(s) > now]
        label = (b["status"] if not confirmed else "Cancelled by airline" if airline_cancelled else
                 "Completed" if completed else "In progress" if first_departed else "Confirmed")

        with st.container(border=True):
            st.subheader(f"PNR {b['pnr']} \u2014 {label}")
            for s in segs:
                note = (" \u26A0\uFE0F cancelled by airline" if s["status"] == "Cancelled" else
                        f" \u23F1 delayed {s['delay_min']} min" if s["status"] == "Delayed" else "")
                st.write(f"{s['airline_name']} {s['flight_number']}: {s['origin']} \u2192 {s['destination']} "
                         f"on {s['depart_date']} at {s['depart_time']}{note}")
            seat_rows = conn.execute("SELECT checked_in FROM booking_seats WHERE booking_id=?", (bid,)).fetchall()
            st.caption(f"Total paid: {fmt_price(b['total_amount'], currency)}  |  Cabin: {b['cabin']}  |  "
                       f"Checked in: {sum(r['checked_in'] for r in seat_rows)}/{len(seat_rows)}")
            if airline_cancelled:
                st.warning("The airline cancelled a flight on this booking. Cancel it for a full refund, "
                           "or reschedule that flight free of charge.")

            colA, colB, colC, colD = st.columns(4)
            with colA:
                st.download_button("Download ticket", build_ticket_pdf(b, segs, [dict(p) for p in passengers]),
                                   file_name=f"eticket_{b['pnr']}.pdf", mime="application/pdf", key=f"dl{bid}")
            with colB:
                if can_cancel and st.button("Cancel booking", key=f"cx{bid}"):
                    st.session_state[f"cx_ask_{bid}"] = True
            with colC:
                if confirmed and upcoming and st.button("Reschedule", key=f"rs{bid}"):
                    st.session_state[f"rs_open_{bid}"] = True
            with colD:
                if confirmed and upcoming and not airline_cancelled and any(not r["checked_in"] for r in seat_rows):
                    if st.button("Web check-in", key=f"ci{bid}"):
                        go("Web Check-in")

            if st.session_state.get(f"cx_ask_{bid}"):
                refund = b["total_amount"] * (1.0 if airline_cancelled else 0.8)
                st.error(f"Cancel PNR {b['pnr']}? You will get {fmt_price(refund, currency)} back"
                         f"{' to your wallet' if b['user_id'] else ''}. This cannot be undone.")
                y, n = st.columns(2)
                if y.button("Yes, cancel it", key=f"cxy{bid}"):
                    cancel_booking(conn, b, airline_cancelled)
                    st.session_state[f"cx_ask_{bid}"] = False
                    st.rerun()
                if n.button("Keep booking", key=f"cxn{bid}"):
                    st.session_state[f"cx_ask_{bid}"] = False
                    st.rerun()

            if st.session_state.get(f"rs_open_{bid}") and upcoming:
                st.markdown("**Reschedule onto another flight on the same route**")
                names = {f"{s['flight_number']} {s['origin']}\u2192{s['destination']} ({s['depart_date']})": s for s in upcoming}
                seg = names[st.selectbox("Flight to change", list(names), key=f"rsseg{bid}")]
                prev = [s for s in segs if s["seq"] == seg["seq"] - 1]
                nxt = [s for s in segs if s["seq"] == seg["seq"] + 1]
                lo = max(dt.date.today(), dt.date.fromisoformat(prev[0]["depart_date"]) if prev else dt.date.today())
                hi = dt.date.fromisoformat(nxt[0]["depart_date"]) if nxt else None
                kw = {"max_value": hi} if hi else {}
                new_date = st.date_input("New departure date", value=max(dt.date.fromisoformat(seg["depart_date"]), lo),
                                         min_value=lo, key=f"nd{bid}_{seg['seg_id']}", **kw)
                col = "price_business" if b["cabin"] == "Business" else "price_economy"
                fare_pax = sum(1 for p in passengers if p["passenger_type"] != "Infant")
                fee = 0 if seg["status"] == "Cancelled" else RESCHEDULE_FEE
                cands = [c for c in conn.execute(
                    """SELECT f.*, al.name AS airline_name FROM flights f JOIN airlines al ON al.code=f.airline_code
                       WHERE origin=? AND destination=? AND depart_date=? AND status!='Cancelled' AND f.id!=?
                       ORDER BY depart_time""",
                    (seg["origin"], seg["destination"], new_date.isoformat(), seg["id"])).fetchall() if depart_dt(c) > now]
                if not cands:
                    st.info("No other flights on this route for that date.")
                else:
                    def charge_for(c):
                        return fee + max(0, c[col] - seg[col]) * fare_pax
                    opts = {f"{c['airline_name']} {c['flight_number']} | {c['depart_time']}\u2192{c['arrive_time']} | "
                            f"extra {fmt_price(charge_for(c), currency)}": c for c in cands}
                    new = opts[st.selectbox("New flight", list(opts), key=f"rsfl{bid}")]
                    charge = round(charge_for(new), 2)
                    if st.button(f"Confirm reschedule (pay {fmt_price(charge, currency)})", key=f"rsok{bid}"):
                        wallet = conn.execute("SELECT wallet_balance FROM users WHERE id=?", (b["user_id"],)).fetchone() if b["user_id"] else None
                        if wallet is not None and wallet["wallet_balance"] < charge:
                            st.error("Not enough wallet balance for the change fee. Add money from the dashboard first.")
                        else:
                            for r in conn.execute("SELECT seat_no FROM booking_seats WHERE booking_id=? AND flight_id=? AND seat_no IS NOT NULL",
                                                  (bid, seg["id"])).fetchall():
                                conn.execute("UPDATE seats SET is_booked=0 WHERE flight_id=? AND seat_no=?", (seg["id"], r["seat_no"]))
                            conn.execute("UPDATE booking_seats SET flight_id=?, seat_no=NULL, checked_in=0 WHERE booking_id=? AND flight_id=?",
                                         (new["id"], bid, seg["id"]))
                            conn.execute("UPDATE booking_segments SET flight_id=? WHERE id=?", (new["id"], seg["seg_id"]))
                            if seg["seq"] == 1:
                                conn.execute("UPDATE passengers SET seat_no=NULL WHERE booking_id=?", (bid,))
                            conn.execute("UPDATE bookings SET total_amount = total_amount + ? WHERE id=?", (charge, bid))
                            if wallet is not None and charge:
                                conn.execute("UPDATE users SET wallet_balance = wallet_balance - ? WHERE id=?", (charge, b["user_id"]))
                                conn.execute("INSERT INTO wallet_transactions (user_id, amount, reason) VALUES (?,?,?)",
                                             (b["user_id"], -charge, f"Reschedule fee for PNR {b['pnr']}"))
                            notify(conn, b["user_id"], "Email", f"Booking rescheduled - PNR {b['pnr']}",
                                   f"PNR {b['pnr']}: {seg['flight_number']} on {seg['depart_date']} is now {new['flight_number']} "
                                   f"on {new['depart_date']} at {new['depart_time']}. Charged {charge:,.2f} INR. Please pick your seats again at check-in.")
                            conn.commit()
                            st.session_state[f"rs_open_{bid}"] = False
                            st.rerun()

            if completed:
                done = conn.execute("SELECT rating, comment FROM reviews WHERE booking_id=?", (bid,)).fetchone()
                with st.expander("Your review" if done else "Leave a review"):
                    if done:
                        st.write("\u2B50" * done["rating"])
                        st.write(done["comment"] or "")
                    else:
                        rating = st.slider("Rating", 1, 5, 5, key=f"rt{bid}")
                        comment = st.text_area("Comment", key=f"cm{bid}")
                        if st.button("Submit review", key=f"sr{bid}"):
                            conn.execute("INSERT INTO reviews (booking_id, flight_id, rating, comment) VALUES (?,?,?,?)",
                                         (bid, segs[0]["id"], rating, comment.strip()))
                            conn.commit()
                            st.rerun()

    conn.close()


def notifications():
    st.title("\U0001F4E5 Notifications")

    user = st.session_state.get("user")
    if not user:
        st.warning("Log in to view your notifications. Guests aren't able to receive account notifications.")
        st.stop()

    CHANNEL_ICON = {"Email": "\U0001F4E7", "SMS": "\U0001F4F1", "Push": "\U0001F514"}
    CHANNEL_LABEL = {"Email": "Email", "SMS": "Text message", "Push": "Push notification"}


    def fmt_time(ts):
        """SQLite timestamps are stored as 'YYYY-MM-DD HH:MM:SS' (UTC); show them the
        way an inbox would: 'Oct 04, 2026 - 08:12 AM'."""
        try:
            parsed = dt.datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
            return parsed.strftime("%b %d, %Y \u00b7 %I:%M %p")
        except (ValueError, TypeError):
            return ts or ""


    conn = get_conn()
    all_channels = ["Email", "SMS", "Push"]
    chosen = st.multiselect("Filter by channel", all_channels, default=all_channels)
    search = st.text_input("Search subject or message", placeholder="e.g. PNR, cancelled, gate")

    notes = conn.execute(
        "SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 300", (user["id"],)
    ).fetchall()
    conn.close()

    notes = [n for n in notes if n["channel"] in chosen]
    if search:
        s = search.lower()
        notes = [n for n in notes if s in n["subject"].lower() or s in n["body"].lower()]

    st.caption(f"{len(notes)} message(s)")

    if not notes:
        st.info("Nothing here. Notifications about your bookings, check-ins and any airline alerts will show up here.")

    for n in notes:
        preview = n["body"] if len(n["body"]) <= 90 else n["body"][:90].rstrip() + "\u2026"
        icon = CHANNEL_ICON.get(n["channel"], "\U0001F514")
        header = f"{icon}  **{n['subject']}**  \u2014  {preview}"
        with st.expander(header):
            st.caption(f"From **SkyDummy Airlines**  \u00b7  {CHANNEL_LABEL.get(n['channel'], n['channel'])}  "
                       f"\u00b7  {fmt_time(n['created_at'])}")
            st.write(n["body"])


def saved_travelers():
    st.title("\U0001F465 Saved Travelers")
    st.caption("Save frequent passengers so you can autofill them at checkout next time.")

    user = st.session_state.get("user")
    if not user:
        st.warning("Log in to save traveler profiles. Guests can still book without this.")
        st.stop()

    conn = get_conn()

    with st.form("add_traveler", clear_on_submit=True):
        st.markdown("**Add a traveler**")
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            name = st.text_input("Full name")
        with c2:
            age = st.number_input("Age", min_value=0, max_value=120, value=28)
        with c3:
            gender = st.selectbox("Gender", ["Male", "Female", "Other"])
        with c4:
            ptype = st.selectbox("Type", ["Adult", "Child", "Infant"])
        meal = st.selectbox("Preferred meal", list(MEAL_PRICE.keys()))
        if st.form_submit_button("Save traveler") and name.strip():
            conn.execute(
                "INSERT INTO saved_travelers (user_id, full_name, age, gender, passenger_type, meal_pref) "
                "VALUES (?,?,?,?,?,?)",
                (user["id"], name.strip(), int(age), gender, ptype, meal),
            )
            conn.commit()
            st.success(f"Saved {name}.")
            st.rerun()

    st.divider()
    st.subheader("Your saved travelers")
    travelers = conn.execute("SELECT * FROM saved_travelers WHERE user_id=? ORDER BY full_name", (user["id"],)).fetchall()
    if not travelers:
        st.info("No saved travelers yet \u2014 add one above. During booking you can load them with one click.")
    for t in travelers:
        with st.container(border=True):
            c1, c2 = st.columns([4, 1])
            with c1:
                st.write(f"**{t['full_name']}** \u2014 {t['passenger_type']}, age {t['age']}, {t['gender']}, "
                         f"meal: {t['meal_pref']}")
            with c2:
                if st.button("Delete", key=f"del_trav{t['id']}"):
                    conn.execute("DELETE FROM saved_travelers WHERE id=?", (t["id"],))
                    conn.commit()
                    st.rerun()
    conn.close()


def checkin():
    st.title("\u2708\ufe0f Web Check-in")
    st.caption("Check-in is open until 60 minutes before departure.")

    user = st.session_state.get("user")
    pnr = st.text_input("PNR").strip().upper()
    guest_email = "" if user else st.text_input("Email used at booking").strip().lower()
    if not pnr:
        st.stop()

    conn = get_conn()
    b = conn.execute("SELECT * FROM bookings WHERE pnr=?", (pnr,)).fetchone()
    owns = b and ((user and b["user_id"] == user["id"]) or (not user and b["user_id"] is None and guest_email and b["guest_email"] == guest_email))
    if not owns:
        conn.close()
        st.error("Booking not found." if user else "Booking not found. Guests must enter the email used at booking.")
        st.stop()
    if b["status"] != "Confirmed":
        conn.close()
        st.error("This booking has been cancelled.")
        st.stop()
    b = dict(b)
    ensure_booking_seats(conn, b["id"])
    conn.commit()

    now = dt.datetime.now()
    segs = [dict(s) for s in conn.execute(
        "SELECT f.*, bs.seq FROM booking_segments bs JOIN flights f ON f.id=bs.flight_id WHERE bs.booking_id=? ORDER BY bs.seq",
        (b["id"],)).fetchall()]
    if any(s["status"] == "Cancelled" for s in segs):
        conn.close()
        st.error("A flight on this booking was cancelled by the airline. Please cancel or reschedule it in My Bookings.")
        st.stop()
    open_segs = [s for s in segs if depart_dt(s) - now > dt.timedelta(minutes=60)]
    if not open_segs:
        conn.close()
        st.error("Check-in is closed: every flight on this booking has departed or leaves within 60 minutes.")
        st.stop()

    labels = {f"{s['flight_number']}  {s['origin']}\u2192{s['destination']}  {s['depart_date']} {s['depart_time']}": s for s in open_segs}
    flight = labels[st.selectbox("Flight", list(labels))] if len(labels) > 1 else open_segs[0]
    if len(labels) == 1:
        st.success(f"{flight['flight_number']}: {flight['origin']} \u2192 {flight['destination']} on {flight['depart_date']} at {flight['depart_time']}")

    ensure_seats(conn, flight["id"])
    rows = conn.execute(
        """SELECT bk.id AS row_id, bk.seat_no AS leg_seat, bk.checked_in, p.* FROM booking_seats bk
           JOIN passengers p ON p.id = bk.passenger_id WHERE bk.booking_id=? AND bk.flight_id=? ORDER BY p.id""",
        (b["id"], flight["id"])).fetchall()


    def free_seats():
        return [r["seat_no"] for r in conn.execute(
            "SELECT seat_no FROM seats WHERE flight_id=? AND cabin=? AND is_booked=0 ORDER BY id", (flight["id"], b["cabin"])).fetchall()]


    for p in rows:
        infant = p["passenger_type"] == "Infant"
        st.write(f"**{p['full_name']}** \u2014 " + ("travelling as lap infant" if infant else f"Seat {p['leg_seat'] or 'not assigned yet'}"))
        if not p["checked_in"]:
            if not infant:
                free = free_seats()
                if p["leg_seat"]:
                    free = [p["leg_seat"]] + free
                if free:
                    pick = st.selectbox("Seat", free, key=f"ci_seat{p['row_id']}", index=0)
                else:
                    pick = None
                    st.warning("No free seats left in this cabin.")
            else:
                pick = None
            if st.button(f"Check in {p['full_name']}", key=f"ci_do{p['row_id']}", disabled=not infant and pick is None):
                seat = pick if not infant else None
                if seat and seat != p["leg_seat"]:
                    if conn.execute("SELECT is_booked FROM seats WHERE flight_id=? AND seat_no=?", (flight["id"], seat)).fetchone()["is_booked"]:
                        st.error("That seat was just taken. Pick another.")
                        st.stop()
                    if p["leg_seat"]:
                        conn.execute("UPDATE seats SET is_booked=0 WHERE flight_id=? AND seat_no=?", (flight["id"], p["leg_seat"]))
                    conn.execute("UPDATE seats SET is_booked=1 WHERE flight_id=? AND seat_no=?", (flight["id"], seat))
                conn.execute("UPDATE booking_seats SET seat_no=?, checked_in=1 WHERE id=?", (seat, p["row_id"]))
                if flight["seq"] == 1:
                    conn.execute("UPDATE passengers SET seat_no=?, boarding_pass_issued=1 WHERE id=?", (seat, p["id"]))
                conn.execute("UPDATE bookings SET checked_in=1 WHERE id=?", (b["id"],))
                conn.commit()
                st.rerun()
        else:
            pdf = build_boarding_pass_pdf(b, flight, {**dict(p), "seat_no": p["leg_seat"] or "Infant"})
            st.download_button(f"Download boarding pass \u2014 {p['full_name']}", pdf, key=f"bp{p['row_id']}",
                               file_name=f"boardingpass_{b['pnr']}_{flight['flight_number']}_{p['id']}.pdf", mime="application/pdf")
        st.divider()
    conn.close()


def status_tracker():
    st.title("\U0001F6F0\ufe0f Flight Status Tracker")

    conn = get_conn()
    SELECT = ("SELECT f.*, al.name AS airline_name FROM flights f JOIN airlines al ON al.code=f.airline_code "
              "WHERE {} AND depart_date BETWEEN ? AND ? ORDER BY depart_date, depart_time LIMIT 20")
    today = dt.date.today()
    window = ((today - dt.timedelta(days=1)).isoformat(), (today + dt.timedelta(days=7)).isoformat())

    mode = st.radio("Search by", ["Flight number", "Route"], horizontal=True)
    rows = []
    if mode == "Flight number":
        fn = st.text_input("Flight number (e.g. 6E204)").strip().upper()
        if fn:
            rows = conn.execute(SELECT.format("flight_number LIKE ?"), (f"%{fn}%", *window)).fetchall()
    else:
        airports = [a["code"] for a in conn.execute("SELECT code FROM airports").fetchall()]
        c1, c2 = st.columns(2)
        o = c1.selectbox("From", airports)
        d = c2.selectbox("To", [a for a in airports if a != o])
        rows = conn.execute(SELECT.format("origin=? AND destination=?"), (o, d, *window)).fetchall()
    st.caption("Showing flights from yesterday to the next 7 days.")


    def live_status(f):
        """Admin-set Delayed/Cancelled always wins; otherwise derive the stage from the clock."""
        if f["status"] == "Cancelled":
            return "Cancelled", "\U0001F534"
        now, dep, arr = dt.datetime.now(), depart_dt(f), arrive_dt(f)
        if now >= arr:
            return "Landed", "\U0001F6EC"
        if now >= dep:
            return "Departed", "\u2708\ufe0f"
        if f["status"] == "Delayed":
            return f"Delayed by {f['delay_min']} min", "\U0001F534"
        if dep - now <= dt.timedelta(minutes=60):
            return "Boarding", "\U0001F7E1"
        return "On Time", "\U0001F7E2"


    if (mode == "Flight number" and fn or mode == "Route") and not rows:
        st.info("No flights found in that window.")
    for f in rows:
        text, icon = live_status(f)
        with st.container(border=True):
            st.write(f"**{f['airline_name']} {f['flight_number']}** \u2014 {f['origin']} \u2192 {f['destination']} on {f['depart_date']}")
            st.write(f"{icon} **{text}**")
            st.caption(f"Departs {depart_dt(f):%H:%M} (scheduled {f['depart_time']})  |  Arrives {arrive_dt(f):%H:%M}  |  Gate {f['gate']}")
    conn.close()


def price_alerts():
    st.title("\U0001F514 Price Alerts & Fare Trends")

    user = st.session_state.get("user")
    if not user:
        st.warning("Log in to set price alerts.")
        st.stop()

    conn = get_conn()
    today = dt.date.today().isoformat()
    airports = [a["code"] for a in conn.execute("SELECT code FROM airports").fetchall()]

    st.subheader("Fare trend")
    t1, t2 = st.columns(2)
    to = t1.selectbox("From", airports, key="tr_o")
    td = t2.selectbox("To", [a for a in airports if a != to], key="tr_d")
    trend = pd.read_sql_query(
        "SELECT depart_date AS Date, MIN(price_economy) AS \"Lowest economy fare\" FROM flights "
        "WHERE origin=? AND destination=? AND depart_date>=? AND status!='Cancelled' GROUP BY depart_date ORDER BY depart_date",
        conn, params=(to, td, today))
    if trend.empty:
        st.info("No upcoming flights on this route.")
    else:
        st.line_chart(trend.set_index("Date"))
        best = trend.loc[trend["Lowest economy fare"].idxmin()]
        st.caption(f"Cheapest day: {best['Date']} at {best['Lowest economy fare']:,.0f}")

    st.subheader("Create an alert")
    with st.form("alert_form"):
        c1, c2, c3 = st.columns(3)
        o = c1.selectbox("From", airports)
        d = c2.selectbox("To", [a for a in airports if a != o])
        target = c3.number_input("Alert me if the fare drops to", min_value=500, value=4000, step=100)
        if st.form_submit_button("Create alert"):
            conn.execute("INSERT INTO price_alerts (user_id, origin, destination, target_price) VALUES (?,?,?,?)",
                         (user["id"], o, d, target))
            conn.commit()
            st.success("Alert created. You'll get a push notification when the fare reaches your target.")

    check_price_alerts(conn, user["id"])  # fires push notifications for newly hit targets
    st.subheader("Your alerts")
    alerts = conn.execute("SELECT * FROM price_alerts WHERE user_id=? AND active IN (1, 2) ORDER BY id DESC", (user["id"],)).fetchall()
    if not alerts:
        st.caption("No alerts yet.")
    for a in alerts:
        low = conn.execute("SELECT MIN(price_economy) m FROM flights WHERE origin=? AND destination=? AND depart_date>=? AND status!='Cancelled'",
                           (a["origin"], a["destination"], today)).fetchone()["m"]
        with st.container(border=True):
            st.write(f"{a['origin']} \u2192 {a['destination']}: target {a['target_price']:.0f}  |  current lowest "
                     f"{f'{low:.0f}' if low else 'n/a'}")
            if a["active"] == 2:
                st.success("\U0001F389 Target reached \u2014 check the Notifications page.")
            if st.button("Delete alert", key=f"del{a['id']}"):
                conn.execute("UPDATE price_alerts SET active=0 WHERE id=?", (a["id"],))
                conn.commit()
                st.rerun()
    conn.close()
