PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS user_account (
 user_id TEXT PRIMARY KEY NOT NULL, full_name TEXT NOT NULL, email TEXT NOT NULL UNIQUE COLLATE NOCASE,
 phone TEXT, password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('customer','employee','admin')),
 is_active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS services (
 service_id TEXT PRIMARY KEY NOT NULL, title TEXT NOT NULL, duration INTEGER NOT NULL CHECK(duration>0),
 price INTEGER NOT NULL CHECK(price>=0), is_active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS timeslots (
 timeslot_id TEXT PRIMARY KEY NOT NULL, start_at TEXT NOT NULL UNIQUE, end_at TEXT NOT NULL CHECK(end_at>start_at),
 capacity INTEGER NOT NULL DEFAULT 3 CHECK(capacity=3), is_blocked INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS disjoint_insert BEFORE INSERT ON timeslots
WHEN EXISTS(SELECT 1 FROM timeslots WHERE start_at<NEW.end_at AND end_at>NEW.start_at)
BEGIN SELECT RAISE(ABORT,'overlap'); END;
CREATE TABLE IF NOT EXISTS bookings (
 booking_id TEXT PRIMARY KEY NOT NULL, request_id TEXT NOT NULL UNIQUE,
 customer_id TEXT NOT NULL REFERENCES user_account, service_id TEXT NOT NULL REFERENCES services,
 timeslot_id TEXT NOT NULL REFERENCES timeslots, status TEXT NOT NULL CHECK(status IN ('confirmed','cancelled')),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, cancelled_at TEXT,
 CHECK((status='cancelled')=(cancelled_at IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS notifications_outbox (
 event_id TEXT PRIMARY KEY NOT NULL, event_type TEXT NOT NULL CHECK(event_type='booking_confirmed'),
 request_id TEXT NOT NULL, booking_id TEXT NOT NULL UNIQUE REFERENCES bookings,
 recipient_id TEXT NOT NULL REFERENCES user_account, payload TEXT NOT NULL, created_at TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','sent','failed')), attempt_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions (
 token_hash TEXT PRIMARY KEY NOT NULL, user_id TEXT NOT NULL REFERENCES user_account, expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS confirmed_slot ON bookings(timeslot_id) WHERE status='confirmed';
CREATE INDEX IF NOT EXISTS customer_history ON bookings(customer_id,created_at DESC);
CREATE INDEX IF NOT EXISTS open_slots ON timeslots(start_at) WHERE is_blocked=0;
