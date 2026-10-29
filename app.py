"""Skylink Airlines - run with:  streamlit run app.py

One file decides what to show: a sidebar menu picks a page *function* from ui_user.py / ui_admin.py.
There is no pages/ folder and no st.navigation, so nothing can go missing or break between Streamlit versions."""
import datetime as dt
import random
import time

import streamlit as st

import db
import ui_admin
import ui_user

st.set_page_config(page_title="Skylink Airlines", page_icon="\u2708\ufe0f", layout="wide")


@st.cache_resource
def _init_db(today):
    db.seed()  # `today` is part of the cache key, so the rolling 30-day schedule is topped up once a day
    return True


_init_db(dt.date.today().isoformat())

# ---------------------------------------------------------------- state
FLOW_KEYS = {"search_params": None, "search_results": None, "selected_flights": {}, "booking_draft": None,
             "last_pnr": None, "reset_otp": None}
for _k, _v in dict(FLOW_KEYS, user=None, guest=False, currency="INR", language="English", page=None).items():
    st.session_state.setdefault(_k, _v)

HI = {"Dashboard": "\u0921\u0948\u0936\u092c\u094b\u0930\u094d\u0921", "Search Flights": "\u0909\u0921\u093c\u093e\u0928\u0947\u0902 \u0916\u094b\u091c\u0947\u0902",
      "My Bookings": "\u092e\u0947\u0930\u0940 \u092c\u0941\u0915\u093f\u0902\u0917", "Notifications": "\u0938\u0942\u091a\u0928\u093e\u090f\u0901",
      "Saved Travelers": "\u0938\u0939\u0947\u091c\u0947 \u092f\u093e\u0924\u094d\u0930\u0940", "Web Check-in": "\u0935\u0947\u092c \u091a\u0947\u0915-\u0907\u0928",
      "Flight Status": "\u0909\u0921\u093c\u093e\u0928 \u0938\u094d\u0925\u093f\u0924\u093f", "Price Alerts": "\u092e\u0942\u0932\u094d\u092f \u0905\u0932\u0930\u094d\u091f"}
ICON = {"Dashboard": "\U0001F3E0", "Search Flights": "\U0001F50D", "My Bookings": "\U0001F4D1", "Notifications": "\U0001F4E5",
        "Saved Travelers": "\U0001F465", "Web Check-in": "\u2708\ufe0f", "Flight Status": "\U0001F6F0\ufe0f", "Price Alerts": "\U0001F514",
        "Flights & Fares": "\U0001F6E0\ufe0f", "Bookings": "\U0001F4D1", "Users": "\U0001F464", "Promo Codes": "\U0001F3F7\ufe0f"}

USER_PAGES = {
    "Dashboard": ui_user.dashboard, "Search Flights": ui_user.search, "My Bookings": ui_user.my_bookings,
    "Notifications": ui_user.notifications, "Saved Travelers": ui_user.saved_travelers, "Web Check-in": ui_user.checkin,
    "Flight Status": ui_user.status_tracker, "Price Alerts": ui_user.price_alerts,
    # steps of the booking flow: reached from the search page, not listed in the menu
    "Results": ui_user.results, "Flight Details": ui_user.flight_details, "Booking": ui_user.booking,
    "Payment": ui_user.payment, "Confirmation": ui_user.confirmation,
}
USER_MENU = ["Dashboard", "Search Flights", "My Bookings", "Notifications", "Saved Travelers", "Web Check-in",
             "Flight Status", "Price Alerts"]
ADMIN_PAGES = {"Dashboard": ui_admin.dashboard, "Flights & Fares": ui_admin.flights, "Bookings": ui_admin.bookings,
               "Users": ui_admin.users, "Promo Codes": ui_admin.promocodes, "Notifications": ui_admin.notifications}
ADMIN_MENU = list(ADMIN_PAGES)


def reset_session(keep_user=False):
    """Forget everything tied to the previous visitor so the next one never sees their last booking."""
    for k, v in FLOW_KEYS.items():
        st.session_state[k] = v
    st.session_state.page = None
    for k in ("menu_user", "menu_admin"):
        st.session_state.pop(k, None)
    if not keep_user:
        st.session_state.user = None
        st.session_state.guest = False


def logout():
    reset_session()
    st.rerun()


def refresh_user():
    """Reload the row so wallet/points changed on other pages show at once; log out if the account vanished."""
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM users WHERE id=?", (st.session_state.user["id"],)).fetchone()
    conn.close()
    if row:
        st.session_state.user = dict(row)
    else:
        logout()


def reset_password_ui():
    otp = st.session_state.reset_otp
    if not otp:
        email = st.text_input("Account email", key="rs_email")
        if st.button("Send reset code", key="rs_send") and email:
            if db.email_exists(email):
                st.session_state.reset_otp = {"email": email, "code": "%06d" % random.randint(0, 999999), "expires": time.time() + 600}
                st.rerun()
            else:
                st.error("No account with this email.")
        return
    st.info("Demo mode: no email is sent, so your code is **%s** (valid 10 minutes)." % otp["code"])
    code = st.text_input("Enter the code", key="rs_code")
    new_pw = st.text_input("New password", type="password", key="rs_pw")
    if st.button("Reset password", key="rs_btn"):
        if time.time() > otp["expires"]:
            st.session_state.reset_otp = None
            st.error("Code expired. Request a new one.")
        elif code.strip() != otp["code"]:
            st.error("Incorrect code.")
        else:
            ok, msg = db.reset_password(otp["email"], new_pw)
            if ok:
                st.session_state.reset_otp = None
            (st.success if ok else st.error)(msg)
    if st.button("Cancel", key="rs_cancel"):
        st.session_state.reset_otp = None
        st.rerun()


def sidebar_account():
    """Returns True when someone is signed in (or browsing as a guest)."""
    st.sidebar.title("\u2708\ufe0f Skylink Airlines")
    user = st.session_state.user
    if user:
        refresh_user()
        user = st.session_state.user
        st.sidebar.success("%s: %s" % ("Admin" if user["is_admin"] else "Traveller", user["name"]))
        if not user["is_admin"]:
            st.sidebar.caption("Loyalty points: %d  |  Wallet: %.2f" % (user["loyalty_points"], user["wallet_balance"]))
    if user or st.session_state.guest:
        if not (user and user["is_admin"]):
            currencies = list(db.CURRENCY_RATES)
            st.session_state.currency = st.sidebar.selectbox("Currency", currencies, index=currencies.index(st.session_state.currency))
            languages = ["English", "Hindi"]
            st.session_state.language = st.sidebar.selectbox("Menu language", languages, index=languages.index(st.session_state.language))
        if st.sidebar.button("Log out"):
            logout()
        if user and not user["is_admin"]:
            with st.sidebar.expander("Delete account"):
                st.warning("This permanently deletes your account. Past bookings are kept for airline records.")
                if st.button("Confirm delete account"):
                    db.delete_account(user["id"])
                    logout()
        return True

    tab_login, tab_signup, tab_reset, tab_guest = st.sidebar.tabs(["Login", "Sign up", "Reset", "Guest"])
    with tab_login:
        email = st.text_input("Email", key="li_email")
        password = st.text_input("Password", type="password", key="li_pw")
        if st.button("Login", key="li_btn"):
            found, err = db.login(email, password)
            if err:
                st.error(err)
            else:
                reset_session(keep_user=True)
                st.session_state.user = found
                st.rerun()
        st.text("admin@demo.com / admin123  |  user@demo.com / user123")
    with tab_signup:
        name = st.text_input("Full name", key="su_name")
        email2 = st.text_input("Email", key="su_email")
        phone = st.text_input("Phone", key="su_phone")
        pw2 = st.text_input("Password", type="password", key="su_pw")
        ref = st.text_input("Referral code (optional)", key="su_ref", help="A code like REF0002 from a friend gives you both a wallet bonus.")
        if st.button("Create account", key="su_btn"):
            ok, msg = db.signup(name, email2, pw2, phone, ref or None)
            (st.success if ok else st.error)(msg)
    with tab_reset:
        reset_password_ui()
    with tab_guest:
        st.caption("Search and book without an account (confirmation goes to the email you give at checkout).")
        if st.button("Continue as Guest"):
            reset_session(keep_user=True)
            st.session_state.guest = True
            st.rerun()
    return False


def main():
    menu_box = st.sidebar.container()  # reserved first, so the menu sits at the very top of the sidebar
    signed_in = sidebar_account()
    if not signed_in:
        st.title("Fly smarter with Skylink Airlines \u2708\ufe0f")
        st.info("Log in, sign up, or continue as **Guest** from the sidebar to start searching flights.")
        return

    admin = bool(st.session_state.user and st.session_state.user["is_admin"])
    pages, menu, key = (ADMIN_PAGES, ADMIN_MENU, "menu_admin") if admin else (USER_PAGES, USER_MENU, "menu_user")

    # a page asked to move somewhere (see nav.go); this must happen before the menu widget is drawn
    target = st.session_state.pop("_goto", None)
    if target in pages:
        st.session_state.page = target
        st.session_state[key] = target if target in menu else "Search Flights"  # booking-flow steps sit under Search
    if st.session_state.page not in pages:
        st.session_state.page = menu[0]
        st.session_state[key] = menu[0]

    hindi = st.session_state.language == "Hindi" and not admin
    menu_box.radio(
        "Menu", menu, key=key,
        format_func=lambda p: "%s  %s" % (ICON.get(p, ""), HI.get(p, p) if hindi else p),
        on_change=lambda: st.session_state.update(page=st.session_state[key]))
    menu_box.divider()
    pages[st.session_state.page]()


main()
