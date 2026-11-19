import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
KYIV = ZoneInfo("Europe/Kyiv")
UTC = timezone.utc


class ApiError(Exception):
    def __init__(self, status, code, field=None):
        self.status, self.code, self.field = status, code, field


def stamp(value=None):
    return (value or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def uid():
    return str(uuid.uuid4())


def connect(path):
    db = sqlite3.connect(path, timeout=8)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    return db


def initialize(path):
    with closing(connect(path)) as db, db:
        db.executescript((ROOT / "schema.sql").read_text())
        db.execute("BEGIN IMMEDIATE")
        now = stamp()
        if not db.execute("SELECT 1 FROM services").fetchone():
            db.executemany("INSERT INTO services VALUES(?,?,?,?,1,?,?)",
                           [(uid(), title, duration, price, now, now) for title, duration, price in
                            [('Стрижка', 45, 550), ('Укладання', 30, 400), ('Фарбування', 90, 1200)]])
        # Keep the next seven days available on each application launch.
        for day in range(1, 8):
            for hour, minutes in [(9, 60), (11, 90), (14, 60), (16, 90)]:
                start = (datetime.now(KYIV) + timedelta(days=day)).replace(hour=hour, minute=0, second=0, microsecond=0)
                end = start + timedelta(minutes=minutes)
                if not db.execute("SELECT 1 FROM timeslots WHERE start_at<? AND end_at>?", (stamp(end), stamp(start))).fetchone():
                    db.execute("INSERT INTO timeslots VALUES(?,?,?,3,0,?,?)", (uid(),stamp(start),stamp(end),now,now))


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 310000)
    return salt + "." + digest.hex()


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def account(db, data, register):
    email = data.get("email")
    password = data.get("password")
    if not isinstance(email,str) or len(email)>254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+",email):
        raise ApiError(422,"invalid_data","email")
    if not isinstance(password,str) or not 8 <= len(password) <= 128:
        raise ApiError(422,"invalid_data","password")
    if register:
        name = data.get("full_name")
        if not isinstance(name,str) or not 2<=len(name.strip())<=80:
            raise ApiError(422,"invalid_data","full_name")
        try:
            db.execute("INSERT INTO user_account VALUES(?,?,?,NULL,?,'customer',1,?,?)",
                       (uid(),name.strip(),email.lower(),password_hash(password),stamp(),stamp()))
        except sqlite3.IntegrityError:
            raise ApiError(409,"account_exists")
    user=db.execute("SELECT * FROM user_account WHERE email=? AND is_active=1",(email,)).fetchone()
    if not user or not hmac.compare_digest(user["password_hash"],password_hash(password,user["password_hash"].split(".")[0])):
        raise ApiError(401,"invalid_credentials")
    token=secrets.token_urlsafe(32)
    db.execute("DELETE FROM sessions WHERE expires_at<=?",(int(time.time()),))
    db.execute("INSERT INTO sessions VALUES(?,?,?)",(token_hash(token),user["user_id"],int(time.time())+86400))
    return user,token


OCCUPANCY = """SELECT t.*, (SELECT count(*) FROM bookings b WHERE b.timeslot_id=t.timeslot_id
 AND b.status='confirmed') AS active_count FROM timeslots t"""


def slots(db, service_id=None):
    duration = 0
    if service_id is not None:
        s = db.execute("SELECT duration FROM services WHERE service_id=? AND is_active=1",(service_id,)).fetchone()
        if not s:
            raise ApiError(404,"not_found")
        duration = s[0]
    rows = db.execute("SELECT * FROM (" + OCCUPANCY + """) WHERE start_at>? AND
        (?=0 OR (is_blocked=0 AND active_count<capacity AND unixepoch(end_at)-unixepoch(start_at)>=?))
        ORDER BY start_at""",(stamp(),int(service_id is not None),duration*60))
    return [dict(timeslot_id=r["timeslot_id"],start_at=r["start_at"],end_at=r["end_at"],capacity=3,
                 is_blocked=bool(r["is_blocked"]),active_count=r["active_count"],remaining_capacity=3-r["active_count"]) for r in rows]


BOOKING = """SELECT b.booking_id,b.request_id,b.status,b.service_id,b.timeslot_id,b.created_at,b.cancelled_at,
 s.title AS service_title,t.start_at,t.end_at FROM bookings b JOIN services s USING(service_id)
 JOIN timeslots t USING(timeslot_id) WHERE b.customer_id=?"""


def get_booking(db, customer, bid):
    row = db.execute(BOOKING+" AND booking_id=?",(customer,bid)).fetchone()
    if not row:
        raise ApiError(404,"not_found")
    return dict(row)


def create_booking(db, user, data):
    for key in ("request_id","service_id","timeslot_id"):
        try:
            uuid.UUID(data.get(key,""))
        except (ValueError,AttributeError,TypeError):
            raise ApiError(422,"invalid_data",key)
    rid, sid, tid = (data[k] for k in ("request_id","service_id","timeslot_id"))
    # Caller holds BEGIN IMMEDIATE until both writes commit.
    previous = db.execute("SELECT * FROM bookings WHERE request_id=?",(rid,)).fetchone()
    if previous:
        if (previous["customer_id"],previous["service_id"],previous["timeslot_id"]) != (user,sid,tid):
            raise ApiError(409,"request_conflict")
        return 200,get_booking(db,user,previous["booking_id"])
    service = db.execute("SELECT * FROM services WHERE service_id=? AND is_active=1",(sid,)).fetchone()
    slot = db.execute(OCCUPANCY+" WHERE t.timeslot_id=?",(tid,)).fetchone()
    if not service or not slot:
        raise ApiError(404,"not_found")
    length = datetime.fromisoformat(slot["end_at"].replace("Z","+00:00"))-datetime.fromisoformat(slot["start_at"].replace("Z","+00:00"))
    now = stamp()
    if slot["start_at"]<=now or slot["is_blocked"] or slot["active_count"]>=3 or length.total_seconds()<service["duration"]*60:
        raise ApiError(409,"slot_unavailable")
    bid = uid()
    db.execute("INSERT INTO bookings VALUES(?,?,?,?,?,'confirmed',?,?,NULL)",(bid,rid,user,sid,tid,now,now))
    payload=dict(service_title=service["title"],start_at=slot["start_at"],end_at=slot["end_at"],status="confirmed")
    db.execute("INSERT INTO notifications_outbox VALUES(?,'booking_confirmed',?,?,?,?,?,'pending',0)",
               (uid(),rid,bid,user,json.dumps(payload,ensure_ascii=False),now))
    return 201,get_booking(db,user,bid)
