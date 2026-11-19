"""Run from project root: python3 -m unittest discover -s tests -v"""
import http.client
import json
import secrets
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from uuid import uuid4
from server import serve
from backend.core import connect
from backend.dispatch import dispatch


class SalonAPI(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db=Path(self.temp.name)/"test.sqlite3"
        self.server=serve(self.db,0)
        self.addCleanup(self.server.server_close)
        threading.Thread(target=self.server.serve_forever,kwargs={"poll_interval":0.01},daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.cookie=self.register()
        self.services=self.http("GET","/services")[1]
        self.sid=self.services[0]["service_id"]
        self.tid=self.available()[0]["timeslot_id"]

    def http(self,method,path,payload=None,cookie=None):
        with closing(http.client.HTTPConnection("127.0.0.1",self.server.server_port,timeout=12)) as conn:
            conn.request(method,"/api/v1"+path,json.dumps(payload) if payload is not None else None,
                         {"Content-Type":"application/json","Cookie":cookie or ""})
            res=conn.getresponse()
            return res.status,json.load(res),res.getheader("Set-Cookie","").split(";")[0]

    def register(self):
        email=str(uuid4())+"@example.test"
        status,_,cookie=self.http("POST","/register",{"full_name":"Демо клієнт","email":email,"password":secrets.token_urlsafe(16)})
        self.assertEqual(status,201)
        return cookie

    def available(self):
        return self.http("GET","/slots?service_id="+self.sid)[1]

    def payload(self):
        return dict(request_id=str(uuid4()),service_id=self.sid,timeslot_id=self.tid)

    def create(self,p=None,cookie=None):
        return self.http("POST","/bookings",p or self.payload(),cookie or self.cookie)[:2]

    def counts(self):
        with closing(connect(self.db)) as db:
            return tuple(db.execute("SELECT count(*) FROM "+t).fetchone()[0] for t in ["bookings","notifications_outbox"])

    def test_01_available(self):
        self.assertEqual(self.available()[0]["remaining_capacity"],3)
        long=next(s for s in self.services if s["duration"]==90)
        rows=self.http("GET","/slots?service_id="+long["service_id"])[1]
        self.assertTrue(rows)
        self.assertNotIn(self.tid,[r["timeslot_id"] for r in rows])

    def test_02_create(self):
        status,b=self.create()
        self.assertEqual((status,b["status"],self.counts()),(201,"confirmed",(1,1)))

    def test_03_read(self):
        _,b=self.create()
        self.assertEqual(self.http("GET","/bookings/"+b["booking_id"],cookie=self.cookie)[:2],(200,b))
        self.assertEqual(self.http("GET","/bookings",cookie=self.cookie)[1],[b])

    def test_04_cancel(self):
        _,b=self.create()
        path="/bookings/"+b["booking_id"]
        first=self.http("PATCH",path,{"status":"cancelled"},self.cookie)
        again=self.http("PATCH",path,{"status":"cancelled"},self.cookie)
        self.assertEqual(first[:2],again[:2])
        self.assertEqual((first[0],first[1]["status"],self.available()[0]["remaining_capacity"]),(200,"cancelled",3))

    def test_05_validation(self):
        status,res,_=self.http("POST","/register",{"email":"invalid-private-value"})
        self.assertEqual((status,res),(422,{"error":{"code":"invalid_data","field":"email"}}))
        self.assertEqual(self.create({"service_id":True})[0],422)

    def test_06_concurrent_capacity(self):
        clients=[self.cookie]+[self.register() for _ in range(3)]
        gate=threading.Barrier(4)
        def attempt(item):
            i,cookie=item
            p=self.payload()
            p["service_id"]=self.services[i%2]["service_id"]
            gate.wait()
            return self.create(p,cookie)
        with ThreadPoolExecutor(max_workers=4) as pool:
            result=list(pool.map(attempt,enumerate(clients)))
        self.assertEqual(sorted(s for s,_ in result),[201,201,201,409])
        self.assertEqual([v["error"]["code"] for s,v in result if s==409],["slot_unavailable"])
        self.assertEqual(self.counts(),(3,3))
        self.assertNotIn(self.tid,[r["timeslot_id"] for r in self.available()])

    def test_07_missing(self):
        status,res,_=self.http("GET","/bookings/"+str(uuid4()),cookie=self.cookie)
        self.assertEqual((status,res),(404,{"error":{"code":"not_found"}}))

    def test_08_storage_failure(self):
        with closing(connect(self.db)) as db,db:
            db.execute("CREATE TRIGGER fail_event BEFORE INSERT ON notifications_outbox BEGIN SELECT RAISE(ABORT,'test'); END")
        self.assertEqual(self.create(),(503,{"error":{"code":"storage_error"}}))
        self.assertEqual(self.counts(),(0,0))
        with closing(connect(self.db)) as db,db:
            db.execute("DROP TRIGGER fail_event")
        self.assertEqual(self.create()[0],201)

    def test_09_replay(self):
        p=self.payload()
        status,b=self.create(p)
        self.assertEqual(self.create(p),(200,b))
        p["service_id"]=self.services[1]["service_id"]
        self.assertEqual(self.create(p),(409,{"error":{"code":"request_conflict"}}))
        self.assertEqual(self.counts(),(1,1))

    def test_10_ownership(self):
        _,b=self.create()
        other=self.register()
        path="/bookings/"+b["booking_id"]
        self.assertEqual(self.http("GET",path,cookie=other)[0],404)
        self.assertEqual(self.http("PATCH",path,{"status":"cancelled"},other)[0],404)
        self.assertEqual(self.http("GET",path,cookie=self.cookie)[1]["status"],"confirmed")

    def test_11_staff(self):
        worker=self.register()
        self.assertEqual(self.http("GET","/schedule",cookie=worker)[0],403)
        with closing(connect(self.db)) as db,db:
            db.execute("UPDATE user_account SET role='employee' WHERE rowid=2")
        self.create()
        rows=self.http("GET","/schedule",cookie=worker)[1]
        self.assertEqual(rows[0]["active_count"],1)
        patch=lambda tid:self.http("PATCH","/schedule/"+tid,{"is_blocked":True},worker)
        self.assertEqual(patch(self.tid)[0],409)
        self.assertEqual(patch(rows[1]["timeslot_id"])[0],200)
        self.assertNotIn(rows[1]["timeslot_id"],[r["timeslot_id"] for r in self.available()])

    def test_12_outbox_retry(self):
        self.create()
        self.assertEqual(dispatch(self.db,Path(self.temp.name)/"absent"/"inbox"),(0,1))
        inbox=Path(self.temp.name)/"inbox"
        self.assertEqual(dispatch(self.db,inbox),(1,0))
        self.assertEqual(dispatch(self.db,inbox),(0,0))
        with closing(connect(self.db)) as db:
            self.assertEqual(tuple(db.execute("SELECT status,attempt_count FROM notifications_outbox").fetchone()),("sent",2))
        with closing(connect(inbox)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM receipts").fetchone()[0],1)
