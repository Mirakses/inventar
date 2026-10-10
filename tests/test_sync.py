"""Тести синхронізації на імітованому сервері (без мережі й без Supabase).

Запуск:  python -m tests.test_sync
"""
import copy
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cloud import Cloud, Offline, SyncEngine  # noqa: E402
from db import ALL, FIELDS, Database  # noqa: E402


# ------------------------------------------------------------------ імітація сервера
class APIError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


class Res:
    def __init__(self, data):
        self.data = data


class Server:
    """Спрощена Supabase: таблиці в пам'яті, монотонний updated_at, правила доступу."""

    def __init__(self):
        self.tables = {"categories": {}, "items": {}, "photos": {}}
        self.files = {}
        self.clock = 0
        self.online = True
        # хто що бачить: user -> callable(table, row) -> bool
        self.visible = {}
        self.can_write = {}

    def tick(self):
        self.clock += 1
        return f"2026-10-10T20:00:{self.clock:02d}.000000+00:00"


class Query:
    def __init__(self, server, user, table):
        self.s, self.user, self.table = server, user, table
        self.op, self.payload, self.filters = "select", None, []
        self.order_by, self.rng = [], None

    # builder
    def select(self, cols="*"):
        self.cols = cols
        return self

    def eq(self, c, v):
        self.filters.append(lambda r: r.get(c) == v)
        return self

    def gte(self, c, v):
        self.filters.append(lambda r: r.get(c) >= v)
        return self

    def order(self, c, desc=False):
        self.order_by.append((c, desc))
        return self

    def range(self, a, b):
        self.rng = (a, b)
        return self

    def limit(self, n):
        self.rng = (0, n - 1)
        return self

    def insert(self, row):
        self.op, self.payload = "insert", row
        return self

    def update(self, d):
        self.op, self.payload = "update", d
        return self

    def execute(self):
        s = self.s
        if not s.online:
            raise ConnectionError("offline")
        t = s.tables[self.table]
        vis = s.visible[self.user]
        wr = s.can_write[self.user]
        if self.op == "select":
            rows = [copy.deepcopy(r) for r in t.values() if vis(self.table, r)]
            rows = [r for r in rows if all(f(r) for f in self.filters)]
            for c, d in reversed(self.order_by):
                rows.sort(key=lambda r: r.get(c) or "", reverse=d)
            if self.rng:
                rows = rows[self.rng[0]:self.rng[1] + 1]
            if self.cols == "id":
                rows = [{"id": r["id"]} for r in rows]
            return Res(rows)
        if self.op == "insert":
            row = dict(self.payload)
            row.setdefault("deleted", False)
            if row["id"] in t:
                raise APIError("23505", "duplicate")
            if not wr(self.table, None, row):
                raise APIError("42501", "new row violates row-level security policy")
            row["updated_at"] = s.tick()
            t[row["id"]] = row
            return Res([copy.deepcopy(row)])
        if self.op == "update":
            out = []
            for r in list(t.values()):
                if not vis(self.table, r) or not all(f(r) for f in self.filters):
                    continue
                new = {**r, **self.payload}
                if not wr(self.table, r, new):
                    continue  # USING не пройшов — 0 рядків
                new["updated_at"] = s.tick()
                t[r["id"]] = new
                out.append(copy.deepcopy(new))
            return Res(out)
        raise NotImplementedError(self.op)


class Bucket:
    def __init__(self, s, user):
        self.s, self.user = s, user

    def upload(self, path, data, opts):
        if not self.s.online:
            raise ConnectionError("offline")
        item_id = path.split("/")[0]
        item = self.s.tables["items"].get(item_id)
        if not item or not self.s.can_write[self.user]("items", item, item):
            raise APIError("403", "new row violates row-level security policy")
        self.s.files[path] = data

    def download(self, path):
        if not self.s.online:
            raise ConnectionError("offline")
        return self.s.files[path]

    def remove(self, paths):
        for p in paths:
            self.s.files.pop(p, None)


class Storage:
    def __init__(self, s, user):
        self.s, self.user = s, user

    def from_(self, bucket):
        return Bucket(self.s, self.user)


class FakeClient:
    def __init__(self, server, user):
        self.s, self.user = server, user
        self.storage = Storage(server, user)

    def table(self, name):
        return Query(self.s, self.user, name)


def device(server, user, role):
    d = tempfile.mkdtemp()
    cloud = Cloud("https://test.supabase.co", "key", d, client=FakeClient(server, user))
    cloud.profile = {"id": user, "role": role}
    db = Database(d, "data.db")
    eng = SyncEngine(cloud, d)
    return db, eng


# ------------------------------------------------------------------ сценарій
def main():
    s = Server()
    TOOLS = "c-tools"

    def item_cat(row, table):
        if table == "items":
            return row.get("category_id")
        if table == "photos":
            it = s.tables["items"].get(row["item_id"])
            return it and it.get("category_id")
        return row["id"]

    # адмін: усе; глядач і комірник: лише «Інструменти»
    s.visible["admin"] = lambda t, r: True
    s.can_write["admin"] = lambda t, old, new: True
    for u in ("viewer", "store"):
        s.visible[u] = lambda t, r: item_cat(r, t) == TOOLS or t == "categories" and r["id"] == TOOLS
    s.can_write["viewer"] = lambda t, old, new: False

    def store_write(t, old, new):
        if t != "items" or old is None or item_cat(old, t) != TOOLS:
            return False
        bad = [k for k in FIELDS + ("specs", "deleted") if k not in ("status", "location", "quantity")
               and old.get(k) != new.get(k)]
        return not bad
    s.can_write["store"] = store_write

    A, a = device(s, "admin", "admin")
    V, v = device(s, "viewer", "viewer")
    K, k = device(s, "store", "storekeeper")

    # 1. адмін створює дані офлайн-першим способом і синхронізує
    tools = A.add_category("Інструменти")
    with A.conn:  # детермінований id для правил імітації
        A.conn.execute("UPDATE categories SET id=? WHERE id=?", (TOOLS, tools))
    elec = A.add_category("Електроніка")
    mcu = A.add_category("МК", elec)
    img = Path(tempfile.mkdtemp()) / "p.jpg"
    img.write_bytes(b"JPEGDATA")
    drill = A.save_item(None, {"name": "Дриль", "category_id": TOOLS, "quantity": 1, "price": 3000.0},
                        [("Потужність", "800 Вт")], [{"source": img}])
    esp = A.save_item(None, {"name": "ESP32", "category_id": mcu, "quantity": 5}, [], [])
    st = a.run_once(full=True)
    assert st["rejected"] == 0 and st["pending"] == 0, st
    assert len(s.tables["items"]) == 2 and len(s.files) == 1
    assert s.tables["items"][drill]["specs"] == [["Потужність", "800 Вт"]]
    print("OK 1 адмін відправив категорії (батьки першими), речі й фото")

    # 2. глядач бачить лише інструменти, фото докачується
    st = v.run_once(full=True)
    names = [r["name"] for r in V.list_items(ALL)]
    assert names == ["Дриль"], names
    _, _, ph = V.get_item(drill)
    assert ph and V.photo_path(ph[0]["filename"]).read_bytes() == b"JPEGDATA"
    print("OK 2 глядач отримав лише дозволене, фото завантажено")

    # 3. глядач пробує змінити — сервер відхиляє, локально відновлюється
    it, sp, ph = V.get_item(drill)
    V.save_item(drill, {**{f: it[f] for f in FIELDS}, "name": "ЗЛАМАНО"}, [], [dict(p) for p in ph])
    st = v.run_once()
    assert st["rejected"] == 1 and V.get_item(drill)[0]["name"] == "Дриль", st
    assert s.tables["items"][drill]["name"] == "Дриль"
    print("OK 3 зміна без прав відхилена й відкочена")

    # 4. комірник: видача проходить (лише дозволені поля), зміна ціни — відкат
    k.run_once(full=True)
    it, sp, ph = K.get_item(drill)
    K.save_item(drill, {**{f: it[f] for f in FIELDS}, "status": "Позичено", "location": "Петро"},
                [(x["key"], x["value"]) for x in sp], [dict(p) for p in ph])
    st = k.run_once()
    assert st["pushed"] == 1 and s.tables["items"][drill]["status"] == "Позичено", st
    it, sp, ph = K.get_item(drill)
    K.save_item(drill, {**{f: it[f] for f in FIELDS}, "price": 1.0},
                [(x["key"], x["value"]) for x in sp], [dict(p) for p in ph])
    st = k.run_once()
    assert st["rejected"] == 1 and K.get_item(drill)[0]["price"] == 3000.0, st
    print("OK 4 комірник: видача працює, ціна — ні")

    # 5. офлайн: зміни в черзі, потім відправляються
    s.online = False
    it, sp, ph = A.get_item(esp)
    A.save_item(esp, {**{f: it[f] for f in FIELDS}, "quantity": 7}, [], [])
    try:
        a.run_once()
        raise AssertionError("мав бути Offline")
    except Offline:
        pass
    assert A.pending_count() == 1
    s.online = True
    st = a.run_once()
    assert st["pending"] == 0 and s.tables["items"][esp]["quantity"] == 7
    print("OK 5 офлайн-черга відправилась після появи мережі")

    # 6. адмін бачить зміну комірника; локальна неотримана правка не перезаписується
    a.run_once()
    assert A.get_item(drill)[0]["status"] == "Позичено"
    it, sp, ph = A.get_item(drill)
    A.save_item(drill, {**{f: it[f] for f in FIELDS}, "notes": "моя"}, [(x["key"], x["value"]) for x in sp],
                [dict(p) for p in ph])
    s.tables["items"][drill]["location"] = "Склад"   # хтось змінив на сервері
    s.tables["items"][drill]["updated_at"] = s.tick()
    a._pull()
    assert A.get_item(drill)[0]["notes"] == "моя"
    a.run_once()
    assert s.tables["items"][drill]["notes"] == "моя"
    print("OK 6 локальна правка не губиться при отриманні")

    # 7. видалення доходить до інших пристроїв
    A.delete_item(drill)
    a.run_once()
    assert s.tables["items"][drill]["deleted"] is True
    v.run_once()
    assert V.list_items(ALL) == [] and not list(V.photos_dir.iterdir())
    print("OK 7 видалення синхронізується, фото прибираються")

    # 8. забрали доступ — reconcile чистить локальну копію
    drill2 = A.save_item(None, {"name": "Шуруповерт", "category_id": TOOLS, "quantity": 1}, [], [])
    a.run_once()
    v.run_once()
    assert len(V.list_items(ALL)) == 1
    s.visible["viewer"] = lambda t, r: False
    v.run_once(full=True)
    assert V.list_items(ALL) == [] and V.categories() == []
    print("OK 8 після відкликання доступу локальні дані прибрано")

    # 9. фото: видалення й порядок
    img2 = Path(tempfile.mkdtemp()) / "q.png"
    img2.write_bytes(b"PNG")
    it, sp, ph = A.get_item(drill2)
    A.save_item(drill2, {f: it[f] for f in FIELDS}, [], [{"source": img}, {"source": img2}])
    a.run_once()
    assert len(s.files) == 3  # 1 старе (річ видалено м'яко) + 2 нових
    it, sp, ph = A.get_item(drill2)
    A.save_item(drill2, {f: it[f] for f in FIELDS}, [], [dict(ph[1])])
    a.run_once()
    live = [p for p in s.tables["photos"].values() if p["item_id"] == drill2 and not p["deleted"]]
    assert len(live) == 1 and live[0]["position"] == 0 and len(s.files) == 2
    print("OK 9 видалення фото й зміна порядку")

    # 10. сервер — єдине джерело: повторний pull нічого не змінює
    assert a.run_once()["pulled"] == 0
    print("OK 10 повторна синхронізація без змін")
    print("ALL SYNC TESTS PASSED")


if __name__ == "__main__":
    main()
