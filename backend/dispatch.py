"""P5 local demonstration transport. No external mail or SMS is sent."""
import argparse
import sqlite3
from contextlib import closing
from pathlib import Path
from .core import connect


def dispatch(source, inbox):
    with closing(connect(source)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        events=db.execute("SELECT event_id,payload FROM notifications_outbox WHERE status IN ('pending','failed')").fetchall()
        sent=0
        for event in events:
            try:
                with closing(sqlite3.connect(inbox)) as receiver, receiver:
                    receiver.execute("CREATE TABLE IF NOT EXISTS receipts(event_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
                    receiver.execute("INSERT OR IGNORE INTO receipts VALUES(?,?)",tuple(event))
                status="sent"
                sent+=1
            except sqlite3.Error:
                status="failed"
            db.execute("UPDATE notifications_outbox SET status=?,attempt_count=attempt_count+1 WHERE event_id=?",
                       (status,event["event_id"]))
        return sent,len(events)-sent


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--db",type=Path,default=Path("salon.sqlite3"))
    parser.add_argument("--inbox",type=Path,default=Path("inbox.sqlite3"))
    args=parser.parse_args()
    sent,failed=dispatch(args.db,args.inbox)
    print(f"Доставлено у локальну скриньку: {sent}. Невдалих спроб: {failed}.")
