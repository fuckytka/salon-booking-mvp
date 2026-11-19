"""Local salon booking server. Run python3 server.py --help."""
import argparse
import json
import re
import sqlite3
import time
from contextlib import closing
from datetime import datetime
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from backend.core import (ApiError, BOOKING, OCCUPANCY, account, connect, create_booking,
                          get_booking, initialize, slots, stamp, token_hash, uid)

ROOT = Path(__file__).resolve().parent
STATIC = {"/":("index.html","text/html"),"/app.js":("app.js","text/javascript"),
          "/style.css":("style.css","text/css")}


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):
        # Access logs would contain resource IDs or arbitrary query parameters.
        pass

    def send(self,status,data,kind="application/json",cookie=None):
        if kind=="application/json":
            data=json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type",kind+"; charset=utf-8")
        self.send_header("Content-Length",str(len(data)))
        self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("Content-Security-Policy","default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if cookie is not None:
            self.send_header("Set-Cookie","salon_session="+cookie+"; Path=/; HttpOnly; SameSite=Strict; Max-Age="+("86400" if cookie else "0"))
        self.end_headers()
        self.wfile.write(data)

    def route(self,db,method,path,query,body):
        cookies=SimpleCookie()
        cookies.load(self.headers.get("Cookie",""))
        token=cookies["salon_session"].value if "salon_session" in cookies else ""
        user=db.execute("""SELECT u.* FROM sessions s JOIN user_account u USING(user_id)
                        WHERE token_hash=? AND expires_at>? AND is_active=1""",(token_hash(token),int(time.time()))).fetchone()
        view=lambda u: {"full_name":u["full_name"],"role":u["role"]} if u else None
        if method=="GET" and path=="/session":
            return 200,{"user":view(user)},None
        if method=="POST" and path in ("/register","/login"):
            u,key=account(db,body,path=="/register")
            return (201 if path=="/register" else 200),{"user":view(u)},key
        if method=="POST" and path=="/logout":
            db.execute("DELETE FROM sessions WHERE token_hash=?",(token_hash(token),))
            return 200,{"user":None},""
        if method=="GET" and path=="/services":
            return 200,[dict(r) for r in db.execute("SELECT service_id,title,duration,price FROM services WHERE is_active=1 ORDER BY price")],None
        if method=="GET" and path=="/slots":
            if not query.get("service_id"):
                raise ApiError(422,"invalid_data","service_id")
            return 200,slots(db,query["service_id"][0]),None
        if not user:
            raise ApiError(401,"login_required")
        who=user["user_id"]
        if path=="/schedule" or path.startswith("/schedule/"):
            if user["role"] not in ("employee","admin"):
                raise ApiError(403,"forbidden")
            if method=="GET" and path=="/schedule":
                return 200,slots(db),None
            if method=="PATCH" and re.fullmatch(r"/schedule/[^/]+",path):
                tid=path.rsplit("/",1)[1]
                row=db.execute(OCCUPANCY+" WHERE timeslot_id=?",(tid,)).fetchone()
                if not row:
                    raise ApiError(404,"not_found")
                blocked=body.get("is_blocked")
                if type(blocked) is not bool:
                    raise ApiError(422,"invalid_data","is_blocked")
                if blocked and row["active_count"]:
                    raise ApiError(409,"slot_occupied")
                db.execute("UPDATE timeslots SET is_blocked=?,updated_at=? WHERE timeslot_id=?",(blocked,stamp(),tid))
                return 200,{"is_blocked":blocked},None
        if user["role"]!="customer":
            raise ApiError(403,"forbidden")
        if path=="/bookings":
            if method=="GET":
                return 200,[dict(r) for r in db.execute(BOOKING+" ORDER BY b.created_at DESC,b.rowid DESC",(who,))],None
            if method=="POST":
                status,result=create_booking(db,who,body)
                return status,result,None
        if re.fullmatch(r"/bookings/[^/]+",path):
            bid=path.rsplit("/",1)[1]
            booking=get_booking(db,who,bid)
            if method=="GET":
                return 200,booking,None
            if method=="PATCH":
                if body.get("status")!="cancelled":
                    raise ApiError(422,"invalid_data","status")
                if booking["status"]!="cancelled":
                    now=stamp()
                    db.execute("UPDATE bookings SET status='cancelled',cancelled_at=?,updated_at=? WHERE booking_id=?",(now,now,bid))
                return 200,get_booking(db,who,bid),None
        raise ApiError(404,"not_found")

    def handle_request(self):
        path=urlsplit(self.path)
        method=self.command
        if method=="GET" and path.path in STATIC:
            file,kind=STATIC[path.path]
            return self.send(200,(ROOT/"frontend"/file).read_bytes(),kind)
        try:
            if not path.path.startswith("/api/v1/"):
                raise ApiError(404,"not_found")
            body={}
            if method!="GET":
                if self.headers.get_content_type()!="application/json":
                    raise ApiError(415,"json_required")
                try:
                    length=int(self.headers.get("Content-Length","0"))
                    if not 0<length<=16384:
                        raise ValueError
                    body=json.loads(self.rfile.read(length))
                    if not isinstance(body,dict):
                        raise ValueError
                except (ValueError,UnicodeError,RecursionError):
                    raise ApiError(422,"invalid_data")
            with closing(connect(self.server.db_path)) as db, db:
                if method!="GET":
                    db.execute("BEGIN IMMEDIATE")
                status,result,cookie=self.route(db,method,path.path[7:],parse_qs(path.query),body)
            self.send(status,result,cookie=cookie)
        except ApiError as e:
            error={"code":e.code}
            if e.field:
                error["field"]=e.field
            self.send(e.status,{"error":error})
        except sqlite3.Error:
            self.send(503,{"error":{"code":"storage_error"}})

    do_GET=do_POST=do_PATCH=handle_request


def serve(path,port):
    initialize(path)
    server=ThreadingHTTPServer(("127.0.0.1",port),Handler)
    server.db_path=path
    return server


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--db",type=Path,default=ROOT/"salon.sqlite3")
    parser.add_argument("--port",type=int,default=8765)
    parser.add_argument("--employee",metavar="EMAIL",help="Grant the employee role to a registered demo account and exit")
    args=parser.parse_args()
    if args.employee:
        initialize(args.db)
        with closing(connect(args.db)) as db,db:
            u=db.execute("SELECT user_id FROM user_account WHERE email=?",(args.employee,)).fetchone()
            if not u or db.execute("SELECT 1 FROM bookings WHERE customer_id=?",(u[0],)).fetchone():
                parser.exit(1,"Потрібен зареєстрований акаунт без бронювань.\n")
            db.execute("UPDATE user_account SET role='employee',updated_at=? WHERE user_id=?",(stamp(),u[0]))
    else:
        with serve(args.db,args.port) as server:
            print(f"Салон Лінія · http://127.0.0.1:{server.server_port}",flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
