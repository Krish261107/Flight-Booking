"""Database, seeding and every business rule. No Streamlit imports in this file."""
import datetime as dt
import hashlib
import hmac
import os
import random
import re
import sqlite3
import string

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.path.join(DB_DIR, "flights.db")

TAX_RATE = 0.12
MEAL_PRICE = {"None": 0, "Veg Meal": 350, "Non-Veg Meal": 400, "Jain Meal": 380}
INSURANCE_PRICE = 199
EXTRA_BAGGAGE_PER_KG = 150
RESCHEDULE_FEE = 2500
REFERRAL_BONUS = 300
CURRENCY_RATES = {"INR": 1.0, "USD": 0.012, "AED": 0.044, "GBP": 0.0095}
CURRENCY_SYMBOLS = {"INR": "\u20b9", "USD": "$", "AED": "AED ", "GBP": "\u00a3"}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL, phone TEXT, is_admin INTEGER DEFAULT 0, loyalty_points INTEGER DEFAULT 0,
    wallet_balance REAL DEFAULT 0, currency TEXT DEFAULT 'INR', language TEXT DEFAULT 'English',
    created_at TEXT DEFAULT (datetime('now')), referred_by INTEGER);
CREATE TABLE IF NOT EXISTS airports (code TEXT PRIMARY KEY, city TEXT NOT NULL, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS airlines (code TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS flights (
    id INTEGER PRIMARY KEY AUTOINCREMENT, flight_number TEXT NOT NULL, airline_code TEXT NOT NULL REFERENCES airlines(code),
    origin TEXT NOT NULL REFERENCES airports(code), destination TEXT NOT NULL REFERENCES airports(code),
    depart_date TEXT NOT NULL, depart_time TEXT NOT NULL, arrive_time TEXT NOT NULL, duration_min INTEGER NOT NULL,
    stops INTEGER DEFAULT 0, layover_airport TEXT, layover_min INTEGER DEFAULT 0, aircraft TEXT, base_price REAL NOT NULL,
    seats_economy INTEGER DEFAULT 150, seats_business INTEGER DEFAULT 20, price_economy REAL, price_business REAL,
    baggage_cabin TEXT DEFAULT '7 kg', baggage_checkin TEXT DEFAULT '15 kg', status TEXT DEFAULT 'Scheduled',
    delay_min INTEGER DEFAULT 0, gate TEXT);
CREATE TABLE IF NOT EXISTS seats (
    id INTEGER PRIMARY KEY AUTOINCREMENT, flight_id INTEGER NOT NULL REFERENCES flights(id),
    seat_no TEXT NOT NULL, cabin TEXT NOT NULL, is_booked INTEGER DEFAULT 0, UNIQUE(flight_id, seat_no));
CREATE TABLE IF NOT EXISTS promo_codes (code TEXT PRIMARY KEY, discount_pct REAL NOT NULL, max_discount REAL, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT, pnr TEXT UNIQUE NOT NULL, user_id INTEGER REFERENCES users(id),
    guest_email TEXT, guest_name TEXT, trip_type TEXT NOT NULL, cabin TEXT NOT NULL, base_fare REAL NOT NULL,
    taxes REAL NOT NULL, addon_total REAL DEFAULT 0, discount REAL DEFAULT 0, total_amount REAL NOT NULL,
    payment_method TEXT, status TEXT DEFAULT 'Confirmed', checked_in INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')), points_used INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS booking_segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT, booking_id INTEGER NOT NULL REFERENCES bookings(id),
    flight_id INTEGER NOT NULL REFERENCES flights(id), seq INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS passengers (
    id INTEGER PRIMARY KEY AUTOINCREMENT, booking_id INTEGER NOT NULL REFERENCES bookings(id), full_name TEXT NOT NULL,
    age INTEGER, gender TEXT, passenger_type TEXT DEFAULT 'Adult', seat_no TEXT, meal TEXT,
    baggage_addon_kg INTEGER DEFAULT 0, insurance INTEGER DEFAULT 0, boarding_pass_issued INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS booking_seats (
    id INTEGER PRIMARY KEY AUTOINCREMENT, booking_id INTEGER NOT NULL REFERENCES bookings(id),
    passenger_id INTEGER NOT NULL REFERENCES passengers(id), flight_id INTEGER NOT NULL REFERENCES flights(id),
    seat_no TEXT, checked_in INTEGER DEFAULT 0, UNIQUE(passenger_id, flight_id));
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, channel TEXT NOT NULL, subject TEXT, body TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS price_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id), origin TEXT NOT NULL,
    destination TEXT NOT NULL, target_date TEXT, target_price REAL NOT NULL, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT, booking_id INTEGER NOT NULL REFERENCES bookings(id),
    flight_id INTEGER NOT NULL REFERENCES flights(id), rating INTEGER NOT NULL, comment TEXT,
    created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS wallet_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id), amount REAL NOT NULL,
    reason TEXT, created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS saved_travelers (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id), full_name TEXT NOT NULL,
    age INTEGER, gender TEXT, passenger_type TEXT DEFAULT 'Adult', meal_pref TEXT DEFAULT 'None',
    created_at TEXT DEFAULT (datetime('now')));
"""

AIRPORTS = [
    ("DEL", "Delhi", "Indira Gandhi Intl"), ("BOM", "Mumbai", "Chhatrapati Shivaji Maharaj Intl"),
    ("BLR", "Bengaluru", "Kempegowda Intl"), ("MAA", "Chennai", "Chennai Intl"),
    ("CCU", "Kolkata", "Netaji Subhas Chandra Bose Intl"), ("HYD", "Hyderabad", "Rajiv Gandhi Intl"),
    ("GOI", "Goa", "Goa Intl (Dabolim)"), ("PNQ", "Pune", "Pune Airport"),
    ("JAI", "Jaipur", "Jaipur Intl"), ("COK", "Kochi", "Cochin Intl"),
    ("AMD", "Ahmedabad", "Sardar Vallabhbhai Patel Intl"), ("LKO", "Lucknow", "Chaudhary Charan Singh Intl"),
]
AIRLINES = [("6E", "IndiGo"), ("AI", "Air India"), ("UK", "Vistara"), ("SG", "SpiceJet"), ("I5", "AirAsia India")]
AIRCRAFT = ["A320neo", "A321neo", "B737-800", "ATR-72"]
NEARBY = {"BOM": ["PNQ", "GOI"], "PNQ": ["BOM", "GOI"], "GOI": ["BOM", "PNQ"], "DEL": ["JAI", "LKO"],
          "JAI": ["DEL"], "LKO": ["DEL"], "BLR": ["MAA", "HYD"], "MAA": ["BLR"], "HYD": ["BLR"]}


# ----------------------------------------------------------------- connection
def get_conn():
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ------------------------------------------------------------------ passwords
def hash_pw(pw):
    salt = os.urandom(16)
    return salt.hex() + "$" + hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 120000).hex()


def verify_pw(pw, stored):
    if "$" not in stored:  # unsalted hash written by older versions of this app
        return hashlib.sha256(pw.encode()).hexdigest() == stored
    salt, digest = stored.split("$", 1)
    test = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 120000).hex()
    return hmac.compare_digest(test, digest)


# ------------------------------------------------------------------- seeding
def _add_flights_for_day(conn, fdate):
    """Deterministic per-day schedule, so topping up later days never duplicates or reshuffles earlier ones."""
    rng = random.Random(fdate.isoformat())
    codes = [a[0] for a in AIRPORTS]
    rows = []
    for origin in codes:
        for dest in rng.sample([c for c in codes if c != origin], 4):
            for _ in range(rng.randint(1, 2)):
                airline = rng.choice(AIRLINES)[0]
                dep_h, dep_m = rng.randint(5, 22), rng.choice([0, 15, 30, 45])
                duration = rng.randint(70, 210)
                stops = 0 if duration < 150 or rng.random() > 0.3 else 1
                arr = dep_h * 60 + dep_m + duration
                base = round(rng.uniform(2500, 9500), 2)
                rows.append((
                    "%s%d" % (airline, rng.randint(100, 999)), airline, origin, dest, fdate.isoformat(),
                    "%02d:%02d" % (dep_h, dep_m), "%02d:%02d" % ((arr // 60) % 24, arr % 60), duration, stops,
                    rng.choice(codes) if stops else None, rng.randint(30, 90) if stops else 0,
                    rng.choice(AIRCRAFT), base, 150, 20, base, round(base * 2.6, 2),
                    "7 kg", "15 kg", "Scheduled", 0, "%s%d" % (rng.choice("ABCD"), rng.randint(1, 30))))
    conn.executemany(
        """INSERT INTO flights (flight_number, airline_code, origin, destination, depart_date, depart_time,
           arrive_time, duration_min, stops, layover_airport, layover_min, aircraft, base_price, seats_economy,
           seats_business, price_economy, price_business, baggage_cabin, baggage_checkin, status, delay_min, gate)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)


def ensure_booking_seats(conn, booking_id):
    """One row per passenger per flight (also back-fills bookings made by older versions)."""
    if conn.execute("SELECT 1 FROM booking_seats WHERE booking_id=? LIMIT 1", (booking_id,)).fetchone():
        return
    segs = conn.execute("SELECT flight_id, seq FROM booking_segments WHERE booking_id=? ORDER BY seq", (booking_id,)).fetchall()
    pax = conn.execute("SELECT id, seat_no, boarding_pass_issued FROM passengers WHERE booking_id=?", (booking_id,)).fetchall()
    for sg in segs:
        for p in pax:
            first = sg["seq"] == 1
            conn.execute("INSERT OR IGNORE INTO booking_seats (booking_id, passenger_id, flight_id, seat_no, checked_in) VALUES (?,?,?,?,?)",
                         (booking_id, p["id"], sg["flight_id"], p["seat_no"] if first else None, p["boarding_pass_issued"] if first else 0))


def seed():
    """Safe to call any number of times: creates tables, migrates old databases, keeps a rolling 30-day schedule."""
    conn = get_conn()
    conn.executescript(SCHEMA)
    for change in ("users ADD COLUMN referred_by INTEGER", "bookings ADD COLUMN points_used INTEGER DEFAULT 0"):
        try:
            conn.execute("ALTER TABLE " + change)
        except sqlite3.OperationalError:
            pass  # column already there
    if conn.execute("SELECT COUNT(*) c FROM airports").fetchone()["c"] == 0:
        conn.executemany("INSERT INTO airports VALUES (?,?,?)", AIRPORTS)
        conn.executemany("INSERT INTO airlines VALUES (?,?)", AIRLINES)
        conn.executemany("INSERT INTO promo_codes VALUES (?,?,?,1)", [("FIRST10", 10, 1000), ("FLY500", 100, 500), ("SAVE15", 15, 2000)])
        conn.execute("INSERT INTO users (name,email,password_hash,phone,is_admin,loyalty_points,wallet_balance) VALUES (?,?,?,?,?,?,?)",
                     ("Admin User", "admin@demo.com", hash_pw("admin123"), "9999999999", 1, 0, 0))
        conn.execute("INSERT INTO users (name,email,password_hash,phone,is_admin,loyalty_points,wallet_balance) VALUES (?,?,?,?,?,?,?)",
                     ("Demo Traveller", "user@demo.com", hash_pw("user123"), "9876543210", 0, 450, 750.0))
    today = dt.date.today()
    last = conn.execute("SELECT MAX(depart_date) m FROM flights").fetchone()["m"]
    day = max(today, dt.date.fromisoformat(last) + dt.timedelta(days=1)) if last else today
    while day <= today + dt.timedelta(days=29):
        _add_flights_for_day(conn, day)
        day += dt.timedelta(days=1)
    for b in conn.execute("SELECT id FROM bookings").fetchall():
        ensure_booking_seats(conn, b["id"])
    conn.commit()
    conn.close()


def ensure_seats(conn, flight_id):
    """Create the seat map the first time a flight needs one (commits, so call it before other pending writes)."""
    if conn.execute("SELECT 1 FROM seats WHERE flight_id=? LIMIT 1", (flight_id,)).fetchone():
        return
    rows = [(flight_id, "%d%s" % (r, c), "Business") for r in range(1, 6) for c in "ABC"]
    rows += [(flight_id, "%d%s" % (r, c), "Economy") for r in range(6, 31) for c in "ABCDEF"]
    conn.executemany("INSERT OR IGNORE INTO seats (flight_id, seat_no, cabin) VALUES (?,?,?)", rows)
    conn.commit()


# ------------------------------------------------------------------- helpers
def fmt_price(amount, currency="INR"):
    return "%s%s" % (CURRENCY_SYMBOLS.get(currency, ""), format(amount * CURRENCY_RATES.get(currency, 1.0), ",.2f"))


def gen_pnr(conn):
    while True:
        pnr = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        if not conn.execute("SELECT 1 FROM bookings WHERE pnr=?", (pnr,)).fetchone():
            return pnr


def depart_dt(f):
    """Scheduled departure plus any delay an admin applied."""
    return dt.datetime.fromisoformat(f["depart_date"] + "T" + f["depart_time"]) + dt.timedelta(minutes=f["delay_min"] or 0)


def arrive_dt(f):
    return depart_dt(f) + dt.timedelta(minutes=f["duration_min"])


def notify(conn, user_id, channel, subject, body):
    """Simulated email / SMS / push. Uses the caller's connection (the caller commits), so it can never
    deadlock against the caller's own uncommitted writes."""
    conn.execute("INSERT INTO notifications (user_id, channel, subject, body) VALUES (?,?,?,?)", (user_id, channel, subject, body))


def apply_promo(conn, base_fare, code):
    """Returns (discount, message)."""
    if not code:
        return 0, None
    row = conn.execute("SELECT * FROM promo_codes WHERE code=? AND active=1", (code.strip().upper(),)).fetchone()
    if not row:
        return 0, "Promo code invalid or expired."
    disc = base_fare * row["discount_pct"] / 100.0
    if row["max_discount"]:
        disc = min(disc, row["max_discount"])
    disc = round(disc, 2)
    return disc, "Promo %s applied." % row["code"]


def price_breakdown(base_fare, addon_total, discount):
    taxes = round(base_fare * TAX_RATE, 2)
    return taxes, round(base_fare + taxes + addon_total - discount, 2)


# -------------------------------------------------------------------- bookings
def cancel_booking(conn, b, airline_cancelled=False):
    """Cancel, free every seat, refund to wallet (100% if the airline cancelled, otherwise 80%), reverse the
    points earned and give back points redeemed. One commit. Returns the refund."""
    refund = round(b["total_amount"] * (1.0 if airline_cancelled else 0.8), 2)
    for s in conn.execute("SELECT flight_id, seat_no FROM booking_seats WHERE booking_id=? AND seat_no IS NOT NULL", (b["id"],)).fetchall():
        conn.execute("UPDATE seats SET is_booked=0 WHERE flight_id=? AND seat_no=?", (s["flight_id"], s["seat_no"]))
    conn.execute("UPDATE booking_seats SET seat_no=NULL, checked_in=0 WHERE booking_id=?", (b["id"],))
    conn.execute("UPDATE passengers SET seat_no=NULL WHERE booking_id=?", (b["id"],))
    conn.execute("UPDATE bookings SET status='Cancelled' WHERE id=?", (b["id"],))
    if b["user_id"]:
        conn.execute("UPDATE users SET wallet_balance = wallet_balance + ?, loyalty_points = MAX(0, loyalty_points - ?) + ? WHERE id=?",
                     (refund, int(b["total_amount"] // 100), b["points_used"] or 0, b["user_id"]))
        conn.execute("INSERT INTO wallet_transactions (user_id, amount, reason) VALUES (?,?,?)",
                     (b["user_id"], refund, "Refund for cancelled PNR " + b["pnr"]))
    where = "credited to your wallet" if b["user_id"] else "sent back to the original payment method (demo)"
    notify(conn, b["user_id"], "Email", "Booking cancelled - PNR " + b["pnr"],
           "PNR %s was cancelled. A refund of %s INR has been %s." % (b["pnr"], format(refund, ",.2f"), where))
    notify(conn, b["user_id"], "SMS", "Booking cancelled", "PNR %s cancelled. Refund %s INR." % (b["pnr"], format(refund, ",.2f")))
    conn.commit()
    return refund


def check_price_alerts(conn, user_id):
    """One-time push notification when the cheapest upcoming fare reaches the target. There is no background
    worker, so this runs when the user opens the dashboard or the alerts page."""
    fired = False
    for a in conn.execute("SELECT * FROM price_alerts WHERE user_id=? AND active=1", (user_id,)).fetchall():
        low = conn.execute("SELECT MIN(price_economy) m FROM flights WHERE origin=? AND destination=? AND depart_date>=? AND status!='Cancelled'",
                           (a["origin"], a["destination"], dt.date.today().isoformat())).fetchone()["m"]
        if low is not None and low <= a["target_price"]:
            conn.execute("UPDATE price_alerts SET active=2 WHERE id=?", (a["id"],))
            notify(conn, user_id, "Push", "Price alert: %s to %s" % (a["origin"], a["destination"]),
                   "Fares on %s to %s are now from %s INR, at or below your %s target." %
                   (a["origin"], a["destination"], format(low, ",.0f"), format(a["target_price"], ",.0f")))
            fired = True
    if fired:
        conn.commit()


# -------------------------------------------------------------------- accounts
def referral_code_for(user_id):
    return "REF%04d" % user_id


def signup(name, email, password, phone, referral_code=None):
    name, email = name.strip(), email.strip().lower()
    if not name:
        return False, "Please enter your name."
    if not EMAIL_RE.match(email):
        return False, "Invalid email format."
    if len(password) < 6:
        return False, "Password must be at least 6 characters."
    conn = get_conn()
    try:
        if conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            return False, "An account with this email already exists."
        referrer = None
        if referral_code:
            code = referral_code.strip().upper()
            if code.startswith("REF") and code[3:].isdigit():
                referrer = conn.execute("SELECT id FROM users WHERE id=? AND is_admin=0", (int(code[3:]),)).fetchone()
            if not referrer:
                return False, "Referral code not recognized."
        cur = conn.execute("INSERT INTO users (name, email, password_hash, phone, wallet_balance, referred_by) VALUES (?,?,?,?,?,?)",
                           (name, email, hash_pw(password), phone, REFERRAL_BONUS if referrer else 0, referrer["id"] if referrer else None))
        if referrer:
            conn.execute("UPDATE users SET wallet_balance = wallet_balance + ? WHERE id=?", (REFERRAL_BONUS, referrer["id"]))
            conn.execute("INSERT INTO wallet_transactions (user_id, amount, reason) VALUES (?,?,?)",
                         (referrer["id"], REFERRAL_BONUS, "Referral bonus: invited user #%d" % cur.lastrowid))
            conn.execute("INSERT INTO wallet_transactions (user_id, amount, reason) VALUES (?,?,?)",
                         (cur.lastrowid, REFERRAL_BONUS, "Referral bonus: signed up with a referral code"))
        conn.commit()
    finally:
        conn.close()
    msg = "Account created. Please log in."
    if referrer:
        msg += " You and your referrer each received a %d wallet credit." % REFERRAL_BONUS
    return True, msg


def login(email, password):
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM users WHERE email=?", (email.strip().lower(),)).fetchone()
        if not row or not verify_pw(password, row["password_hash"]):
            return None, "Incorrect email or password."
        if "$" not in row["password_hash"]:  # upgrade an old unsalted hash
            conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_pw(password), row["id"]))
            conn.commit()
        return dict(row), None
    finally:
        conn.close()


def email_exists(email):
    conn = get_conn()
    try:
        return bool(conn.execute("SELECT 1 FROM users WHERE email=?", (email.strip().lower(),)).fetchone())
    finally:
        conn.close()


def reset_password(email, new_password):
    if len(new_password) < 6:
        return False, "Password must be at least 6 characters."
    conn = get_conn()
    try:
        cur = conn.execute("UPDATE users SET password_hash=? WHERE email=?", (hash_pw(new_password), email.strip().lower()))
        conn.commit()
        return (True, "Password updated. Please log in.") if cur.rowcount else (False, "No account with this email.")
    finally:
        conn.close()


def delete_account(user_id):
    """Bookings stay for the airline's records but are detached from the account; personal data is removed."""
    conn = get_conn()
    try:
        u = conn.execute("SELECT name, email FROM users WHERE id=?", (user_id,)).fetchone()
        if u:
            conn.execute("UPDATE bookings SET user_id=NULL, guest_email=?, guest_name=? WHERE user_id=?", (u["email"], u["name"], user_id))
            for table in ("saved_travelers", "price_alerts", "wallet_transactions", "notifications"):
                conn.execute("DELETE FROM %s WHERE user_id=?" % table, (user_id,))
            conn.execute("UPDATE users SET referred_by=NULL WHERE referred_by=?", (user_id,))
            conn.execute("DELETE FROM users WHERE id=?", (user_id,))
            conn.commit()
    finally:
        conn.close()
