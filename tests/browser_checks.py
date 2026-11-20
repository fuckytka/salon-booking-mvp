"""Optional isolated browser checks. Run python3 tests/browser_checks.py [--screens DIRECTORY]."""
import argparse
import secrets
import sys
import tempfile
import threading
from contextlib import closing
from pathlib import Path
from time import perf_counter
from uuid import uuid4
from playwright.sync_api import sync_playwright, expect
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server import serve
from backend.core import connect

def run(folder):
    with tempfile.TemporaryDirectory() as temp, serve(Path(temp)/"test.sqlite3",0) as server, sync_playwright() as p:
        threading.Thread(target=server.serve_forever,daemon=True).start()
        browser=p.chromium.launch()
        ctx=browser.new_context(viewport={"width":1120,"height":780},locale="uk-UA")
        page=ctx.new_page()
        url=f"http://127.0.0.1:{server.server_port}"
        failures=[]
        page.on("pageerror", lambda error:failures.append(str(error)))
        def shot(name):
            if folder:
                folder.mkdir(exist_ok=True,parents=True)
                page.screenshot(path=folder/(name+".png"))
        def title(value):
            expect(page.locator("h1")).to_have_text(value)
            expect(page.locator("#view")).not_to_have_attribute("aria-busy","true")
        def key(k):
            page.keyboard.press(k)
        def tab(id):
            key("Tab")
            expect(page.locator("#"+id)).to_be_focused()
        def action(id):
            page.locator("#"+id).click()
        def navigate(section):
            action(section)
            expect(page.locator("#view")).not_to_have_attribute("aria-busy","true")
        page.goto(url)
        title("Запис до салону")
        shot("catalog")
        tab("service-0")
        assert page.locator(":focus").evaluate("e=>getComputedStyle(e).outlineWidth")=="3px"
        key("Enter");title("Вільний час")
        # Book the first available day using only the keyboard.
        tab("day")
        tab("slot-0")
        shot("slots")
        key("Enter")
        title("Дані клієнта")
        tab("full_name");page.keyboard.insert_text("Демо Клієнт")
        tab("email");page.keyboard.insert_text("invalid")
        tab("password");pw=secrets.token_urlsafe(14);page.keyboard.insert_text(pw)
        key("Tab");key("Enter")
        expect(page.locator("#email")).to_be_focused()
        expect(page.locator("#email")).to_have_attribute("aria-invalid","true")
        expect(page.locator("#full_name")).to_have_value("Демо Клієнт")
        expect(page.locator("#password")).to_have_value(pw)
        shot("invalid")
        page.keyboard.press("ControlOrMeta+a")
        page.keyboard.insert_text(str(uuid4())+"@example.test")
        tab("password");key("Tab");key("Enter");title("Підтвердження візиту")
        tab("confirm");key("Enter");title("Ваш візит")
        expect(page.locator(".status")).to_have_text("Підтверджено")
        shot("success")
        tab("all");key("Enter");title("Мої записи")
        tab("detail-0");key("Enter");title("Ваш візит")
        tab("all");tab("cancel");key("Enter")
        expect(page.locator("#keep")).to_be_focused()
        tab("yes");key("Enter")
        expect(page.locator(".status")).to_have_text("Скасовано")
        shot("cancelled")
        page.reload();title("Запис до салону")
        navigate("mine");expect(page.locator(".row small")).to_have_text("Скасовано")
        # The list still contains the visible record, but the backend now sees another owner.
        # Replace only this isolated test fixture's data, then request the real detail endpoint.
        with closing(connect(server.db_path)) as db,db:
            db.execute("DELETE FROM notifications_outbox")
            db.execute("DELETE FROM bookings")
        action("detail-0");expect(page.locator("#error")).to_contain_text("уже недоступний")
        navigate("mine");expect(page.locator("h2")).to_have_text("У вас ще немає записів")
        ctx.set_offline(True)
        action("new");expect(page.locator("#error")).to_contain_text("Немає зв’язку")
        ctx.set_offline(False)
        page.get_by_role("button",name="Повторити").click();title("Запис до салону")
        # Hold a read to observe the loading message on the actual rendered interface.
        held=[]
        page.route("**/api/v1/slots?*",lambda route:held.append(route))
        action("service-0");expect(page.locator("#notice")).to_have_text("Завантаження…")
        page.wait_for_timeout(80)
        held.pop().continue_()
        title("Вільний час");page.unroute("**/api/v1/slots?*")
        action("slot-0");title("Підтвердження візиту")
        api=ctx.request
        sid=api.get(url+"/api/v1/services").json()[0]["service_id"]
        tid=api.get(url+"/api/v1/slots?service_id="+sid).json()[0]["timeslot_id"]
        for _ in range(3):
            r=api.post(url+"/api/v1/bookings",data={"service_id":sid,"timeslot_id":tid,"request_id":str(uuid4())})
            assert r.status==201
        with page.expect_response(lambda r:r.url.endswith("/bookings") and r.request.method=="POST") as received:
            action("confirm")
        assert received.value.status==409 and received.value.json()["error"]["code"]=="slot_unavailable"
        expect(page.locator("#error")).to_contain_text("Оберіть інший час")
        assert "slot_unavailable" not in page.locator("body").inner_text()
        shot("conflict")
        page.get_by_role("button",name="Обрати інший час").click();title("Вільний час")
        # Exercise a date change through the native select, independently of OS popup shortcuts.
        tab("day")
        selector = page.locator("#day")
        next_day = selector.locator("option").nth(1).inner_text()
        selector.select_option(label=next_day)
        expect(selector).to_have_value(next_day)
        expect(selector).to_be_focused()
        tab("slot-0")
        for size in (390,1120):
            page.set_viewport_size({"width":size,"height":780})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        samples=[]
        for _ in range(30):
            start=perf_counter()
            assert api.get(url+"/api/v1/slots?service_id="+sid).status==200
            samples.append((perf_counter()-start)*1000)
        print("GET slots, 30 local requests, p95 = %.1f ms" % sorted(samples)[28])
        assert not failures,failures
        browser.close()
        server.shutdown()
        print("PASS: keyboard flow, 422, 404, 409, offline/retry, loading, empty, persistence, responsive width")

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--screens",type=Path)
    run(parser.parse_args().screens)
