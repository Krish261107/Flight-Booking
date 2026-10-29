"""Admin pages. Each page is a plain function; app.py decides which one to draw."""
import datetime as dt

import pandas as pd
import streamlit as st

from db import delete_account, get_conn, notify


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


def flights():
    st.title("\U0001F6E0\ufe0f Flights & Fares")

    user = st.session_state.get("user")
    if not user or not user.get("is_admin"):
        st.error("Admin access only.")
        st.stop()

    conn = get_conn()
    tab1, tab2 = st.tabs(["Browse & edit a flight", "Bulk delay / cancel a route"])

    with tab1:
        flights = pd.read_sql_query(
            "SELECT id, flight_number, airline_code, origin, destination, depart_date, depart_time, "
            "price_economy, price_business, status, delay_min, gate FROM flights ORDER BY depart_date LIMIT 300",
            conn,
        )
        st.dataframe(flights, height=300)

        st.markdown("**Update a flight**")
        fid = st.number_input("Flight ID", min_value=1, step=1)
        f_row = conn.execute("SELECT * FROM flights WHERE id=?", (int(fid),)).fetchone() if fid else None
        if f_row:
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                new_eco = st.number_input("Economy price", value=float(f_row["price_economy"]))
            with c2:
                new_biz = st.number_input("Business price", value=float(f_row["price_business"]))
            with c3:
                statuses = ["Scheduled", "Delayed", "Cancelled"]
                new_status = st.selectbox("Status", statuses, index=statuses.index(f_row["status"]) if f_row["status"] in statuses else 0)
            with c4:
                new_delay = st.number_input("Delay (min)", value=int(f_row["delay_min"]), min_value=0)
            if new_status != "Delayed":
                new_delay = 0  # a delay only makes sense for a delayed flight
            if st.button("Save changes"):
                conn.execute("UPDATE flights SET price_economy=?, price_business=?, status=?, delay_min=? WHERE id=?",
                             (new_eco, new_biz, new_status, new_delay, int(fid)))
                conn.commit()
                st.success("Flight updated.")
        else:
            st.caption("Enter a valid flight ID above to edit it.")

    with tab2:
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
