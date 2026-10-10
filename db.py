"""Локальна база (SQLite) + папка з фото.

Працює і самостійно (локальний режим), і як офлайн-копія хмари:
- ідентифікатори — UUID, тож записи можна створювати без інтернету;
- dirty=1 означає «змінено тут, ще не відправлено на сервер»;
- deleted=1 — м'яке видалення, щоб інші пристрої теж дізналися про нього;
- on_server=1 — запис уже існує на сервері (для вибору INSERT чи UPDATE).
"""
import csv
import json
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

ALL = "__all__"    # усі речі
NONE = "__none__"  # речі без категорії

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp", ".tif", ".tiff"}

FIELDS = ("name", "category_id", "inventory_no", "serial_no", "location",
          "quantity", "price", "purchase_date", "status", "notes")

SCHEMA = """
CREATE TABLE IF NOT EXISTS categories(
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    parent_id  TEXT,
    deleted    INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT,
    dirty      INTEGER NOT NULL DEFAULT 0,
    on_server  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS items(
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    category_id   TEXT,
    inventory_no  TEXT,
    serial_no     TEXT,
    location      TEXT,
    quantity      INTEGER NOT NULL DEFAULT 1,
    price         REAL,
    purchase_date TEXT,
    status        TEXT,
    notes         TEXT,
    specs         TEXT NOT NULL DEFAULT '[]',
    deleted       INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT,
    created_by    TEXT,
    updated_at    TEXT,
    updated_by    TEXT,
    dirty         INTEGER NOT NULL DEFAULT 0,
    dirty_fields  TEXT NOT NULL DEFAULT '[]',
    on_server     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS photos(
    id         TEXT PRIMARY KEY,
    item_id    TEXT NOT NULL,
    filename   TEXT,
    path       TEXT NOT NULL,
    position   INTEGER NOT NULL DEFAULT 0,
    deleted    INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT,
    dirty      INTEGER NOT NULL DEFAULT 0,
    on_server  INTEGER NOT NULL DEFAULT 0,
    uploaded   INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS meta(
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_cat ON items(category_id);
CREATE INDEX IF NOT EXISTS idx_photos_item ON photos(item_id);
"""


def new_id():
    return str(uuid.uuid4())


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _ulower(s):
    # SQLite lower()/LIKE не розуміють кирилицю — робимо це на боці Python
    return s.lower() if isinstance(s, str) else s


def connect(path):
    # check_same_thread=False: з'єднання фонового потоку закривається з головного
    conn = sqlite3.connect(path, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.create_function("ulower", 1, _ulower, deterministic=True)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


class Database:
    def __init__(self, data_dir, filename="local.db"):
        self.data_dir = Path(data_dir)
        self.photos_dir = self.data_dir / "photos"
        self.photos_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / filename
        fresh = not self.db_path.exists()
        self.conn = connect(self.db_path)
        self.conn.executescript(SCHEMA)
        if fresh and filename == "local.db":
            self._migrate_v1(self.data_dir / "inventory.db")

    # ---------- службове ----------
    def meta_get(self, key, default=None):
        r = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else default

    def meta_set(self, key, value):
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                              (key, json.dumps(value)))

    def pending_count(self):
        return sum(self.conn.execute(f"SELECT COUNT(*) FROM {t} WHERE dirty=1").fetchone()[0]
                   for t in ("categories", "items", "photos"))

    def _migrate_v1(self, old_path):
        """Перенесення даних з версії 0.1 (цілі ID) — старий файл залишається як копія."""
        if not old_path.exists():
            return
        old = sqlite3.connect(old_path)
        old.row_factory = sqlite3.Row
        cols = {r[1] for r in old.execute("PRAGMA table_info(items)")}
        if not cols or "dirty" in cols:
            old.close()
            return
        cat_map = {r["id"]: new_id() for r in old.execute("SELECT id FROM categories")}
        item_map = {r["id"]: new_id() for r in old.execute("SELECT id FROM items")}
        ts = now_iso()
        with self.conn:
            for c in old.execute("SELECT * FROM categories"):
                self.conn.execute(
                    "INSERT INTO categories(id, name, parent_id, updated_at, dirty) VALUES (?,?,?,?,1)",
                    (cat_map[c["id"]], c["name"], cat_map.get(c["parent_id"]), ts))
            for i in old.execute("SELECT * FROM items"):
                specs = [[s["key"], s["value"] or ""] for s in old.execute(
                    "SELECT key, value FROM specs WHERE item_id=? ORDER BY position", (i["id"],))]
                iid = item_map[i["id"]]
                self.conn.execute(
                    "INSERT INTO items(id, name, category_id, inventory_no, serial_no, location, "
                    "quantity, price, purchase_date, status, notes, specs, created_at, updated_at, "
                    "dirty, dirty_fields) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)",
                    (iid, i["name"], cat_map.get(i["category_id"]), i["inventory_no"], i["serial_no"],
                     i["location"], i["quantity"], i["price"], i["purchase_date"], i["status"],
                     i["notes"], json.dumps(specs, ensure_ascii=False), i["created_at"],
                     i["updated_at"], json.dumps(list(FIELDS))))
                for p in old.execute(
                        "SELECT filename, position FROM photos WHERE item_id=? ORDER BY position",
                        (i["id"],)):
                    pid = new_id()
                    self.conn.execute(
                        "INSERT INTO photos(id, item_id, filename, path, position, dirty) "
                        "VALUES (?,?,?,?,?,1)",
                        (pid, iid, p["filename"], f"{iid}/{pid}{Path(p['filename']).suffix}",
                         p["position"]))
        old.close()

    # ---------- категорії ----------
    def categories(self):
        return self.conn.execute(
            "SELECT id, name, parent_id FROM categories WHERE deleted=0 ORDER BY ulower(name)"
        ).fetchall()

    def add_category(self, name, parent_id=None):
        cid = new_id()
        with self.conn:
            self.conn.execute(
                "INSERT INTO categories(id, name, parent_id, updated_at, dirty) VALUES (?,?,?,?,1)",
                (cid, name.strip(), parent_id, now_iso()))
        return cid

    def rename_category(self, cat_id, name):
        with self.conn:
            self.conn.execute("UPDATE categories SET name=?, updated_at=?, dirty=1 WHERE id=?",
                              (name.strip(), now_iso(), cat_id))

    def move_category(self, cat_id, parent_id):
        if parent_id is not None and parent_id in self.descendants(cat_id):
            raise ValueError("Не можна перемістити категорію саму в себе")
        with self.conn:
            self.conn.execute("UPDATE categories SET parent_id=?, updated_at=?, dirty=1 WHERE id=?",
                              (parent_id, now_iso(), cat_id))

    def delete_category(self, cat_id):
        """Видаляє категорію з підкатегоріями; речі стають «без категорії»."""
        ids = self.descendants(cat_id)
        q = ",".join("?" * len(ids))
        ts = now_iso()
        with self.conn:
            self.conn.execute(
                f"UPDATE items SET category_id=NULL, updated_at=?, dirty=1, "
                f"dirty_fields=json_insert(dirty_fields, '$[#]', 'category_id') "
                f"WHERE category_id IN ({q})", [ts] + ids)
            self.conn.execute(f"DELETE FROM categories WHERE on_server=0 AND id IN ({q})", ids)
            self.conn.execute(
                f"UPDATE categories SET deleted=1, updated_at=?, dirty=1 WHERE id IN ({q})", [ts] + ids)

    def descendants(self, cat_id):
        rows = self.conn.execute("""
            WITH RECURSIVE sub(id, depth) AS (
                SELECT ?, 0 UNION ALL
                SELECT c.id, sub.depth + 1 FROM categories c JOIN sub ON c.parent_id = sub.id
                WHERE c.deleted = 0 AND sub.depth < 50)
            SELECT id FROM sub""", (cat_id,)).fetchall()
        return [r[0] for r in rows]

    def category_paths(self):
        """{id: 'Батьківська / Дочірня'}"""
        cats = {r["id"]: r for r in self.categories()}
        cache = {}

        def path(cid, depth=0):
            if cid in cache:
                return cache[cid]
            c = cats[cid]
            p = c["name"]
            if c["parent_id"] in cats and depth < 50:
                p = path(c["parent_id"], depth + 1) + " / " + p
            cache[cid] = p
            return p

        return {cid: path(cid) for cid in cats}

    def _cat_filter(self, category):
        """SQL-умова + параметри для вибору речей категорії (з підкатегоріями)."""
        if category == NONE:
            live = "SELECT id FROM categories WHERE deleted=0"
            return f" AND (i.category_id IS NULL OR i.category_id NOT IN ({live}))", []
        if category in (ALL, None):
            return "", []
        ids = self.descendants(category)
        return f" AND i.category_id IN ({','.join('?' * len(ids))})", ids

    def count_items(self, cat_id):
        cond, params = self._cat_filter(cat_id)
        return self.conn.execute(
            f"SELECT COUNT(*) FROM items i WHERE i.deleted=0{cond}", params).fetchone()[0]

    # ---------- речі ----------
    def list_items(self, category=ALL, search=""):
        sql = """
            SELECT i.*,
                   (SELECT filename FROM photos p WHERE p.item_id = i.id AND p.deleted=0
                     ORDER BY position LIMIT 1) AS cover,
                   (SELECT COUNT(*) FROM photos p WHERE p.item_id = i.id AND p.deleted=0)
                     AS photo_count
            FROM items i WHERE i.deleted=0"""
        cond, params = self._cat_filter(category)
        sql += cond
        for word in search.strip().split():
            like = f"%{word.lower()}%"
            sql += """ AND (ulower(i.name) LIKE ? OR ulower(i.inventory_no) LIKE ?
                       OR ulower(i.serial_no) LIKE ? OR ulower(i.location) LIKE ?
                       OR ulower(i.notes) LIKE ? OR ulower(i.status) LIKE ?
                       OR ulower(i.specs) LIKE ?)"""
            params += [like] * 7
        sql += " ORDER BY ulower(i.name)"
        return self.conn.execute(sql, params).fetchall()

    def get_item(self, item_id):
        item = self.conn.execute(
            "SELECT * FROM items WHERE id=? AND deleted=0", (item_id,)).fetchone()
        if item is None:
            return None, [], []
        specs = [{"key": k, "value": v} for k, v in json.loads(item["specs"] or "[]")]
        photos = self.conn.execute(
            "SELECT id, filename FROM photos WHERE item_id=? AND deleted=0 ORDER BY position",
            (item_id,)).fetchall()
        return item, specs, photos

    def save_item(self, item_id, fields, specs, photos):
        """photos — список dict: {"id": ..} для наявних або {"source": шлях} для нових."""
        ts = now_iso()
        specs_json = json.dumps([[k, v] for k, v in specs], ensure_ascii=False)
        new_files, removed_files = [], []
        try:
            with self.conn:
                if item_id is None:
                    item_id = new_id()
                    cols = list(FIELDS) + ["specs", "created_at", "updated_at"]
                    self.conn.execute(
                        f"INSERT INTO items(id, {','.join(cols)}, dirty, dirty_fields) "
                        f"VALUES (?, {','.join('?' * len(cols))}, 1, ?)",
                        [item_id] + [fields.get(f) for f in FIELDS]
                        + [specs_json, ts, ts, json.dumps(list(FIELDS) + ["specs"])])
                else:
                    old = self.conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
                    changed = [f for f in FIELDS if old[f] != fields.get(f)]
                    if (old["specs"] or "[]") != specs_json:
                        changed.append("specs")
                    if changed:
                        prev = json.loads(old["dirty_fields"] or "[]")
                        dirty_fields = sorted(set(prev) | set(changed))
                        sets = ", ".join(f"{f}=?" for f in FIELDS)
                        self.conn.execute(
                            f"UPDATE items SET {sets}, specs=?, updated_at=?, dirty=1, dirty_fields=? "
                            f"WHERE id=?",
                            [fields.get(f) for f in FIELDS] + [specs_json, ts,
                                                               json.dumps(dirty_fields), item_id])

                keep = {p["id"] for p in photos if p.get("id")}
                for row in self.conn.execute(
                        "SELECT id, filename, on_server FROM photos WHERE item_id=? AND deleted=0",
                        (item_id,)).fetchall():
                    if row["id"] not in keep:
                        if row["filename"]:
                            removed_files.append(row["filename"])
                        if row["on_server"]:
                            self.conn.execute(
                                "UPDATE photos SET deleted=1, dirty=1, filename=NULL, updated_at=? "
                                "WHERE id=?", (ts, row["id"]))
                        else:
                            self.conn.execute("DELETE FROM photos WHERE id=?", (row["id"],))
                for pos, p in enumerate(photos):
                    if p.get("id"):
                        self.conn.execute(
                            "UPDATE photos SET dirty=CASE WHEN position=? THEN dirty ELSE 1 END, "
                            "position=? WHERE id=?", (pos, pos, p["id"]))
                    else:
                        pid = new_id()
                        fn = self._import_photo(p["source"], pid)
                        new_files.append(fn)
                        self.conn.execute(
                            "INSERT INTO photos(id, item_id, filename, path, position, updated_at, dirty) "
                            "VALUES (?,?,?,?,?,?,1)",
                            (pid, item_id, fn, f"{item_id}/{fn}", pos, ts))
        except Exception:
            for fn in new_files:
                (self.photos_dir / fn).unlink(missing_ok=True)
            raise
        for fn in removed_files:
            (self.photos_dir / fn).unlink(missing_ok=True)
        return item_id

    def duplicate_item(self, item_id):
        item, specs, photos = self.get_item(item_id)
        fields = {f: item[f] for f in FIELDS}
        fields["name"] = f"{item['name']} (копія)"
        fields["serial_no"] = None
        fields["inventory_no"] = None
        return self.save_item(None, fields, [(s["key"], s["value"]) for s in specs],
                              [{"source": self.photo_path(p["filename"])} for p in photos
                               if p["filename"] and self.photo_path(p["filename"]).exists()])

    def move_items(self, item_ids, category_id):
        ts = now_iso()
        with self.conn:
            self.conn.executemany(
                "UPDATE items SET category_id=?, updated_at=?, dirty=1, "
                "dirty_fields=json_insert(dirty_fields, '$[#]', 'category_id') WHERE id=?",
                [(category_id, ts, i) for i in item_ids])

    def delete_item(self, item_id):
        files = [r[0] for r in self.conn.execute(
            "SELECT filename FROM photos WHERE item_id=? AND filename IS NOT NULL", (item_id,))]
        ts = now_iso()
        with self.conn:
            row = self.conn.execute("SELECT on_server FROM items WHERE id=?", (item_id,)).fetchone()
            if row and row[0]:
                self.conn.execute(
                    "UPDATE items SET deleted=1, updated_at=?, dirty=1, "
                    "dirty_fields=json_insert(dirty_fields, '$[#]', 'deleted') WHERE id=?",
                    (ts, item_id))
                self.conn.execute("DELETE FROM photos WHERE item_id=? AND on_server=0", (item_id,))
                self.conn.execute("UPDATE photos SET filename=NULL WHERE item_id=?", (item_id,))
            else:
                self.conn.execute("DELETE FROM photos WHERE item_id=?", (item_id,))
                self.conn.execute("DELETE FROM items WHERE id=?", (item_id,))
        for fn in files:
            (self.photos_dir / fn).unlink(missing_ok=True)

    # ---------- довідкові ----------
    def distinct_values(self, column):
        assert column in ("location", "status")
        return [r[0] for r in self.conn.execute(
            f"SELECT DISTINCT {column} FROM items WHERE deleted=0 AND {column} IS NOT NULL "
            f"AND {column} != '' ORDER BY ulower({column})")]

    def spec_keys(self, category_id=None):
        """Назви характеристик, найчастіші — першими (для категорії та її підкатегорій)."""
        cond, params = ("", []) if category_id is None else self._cat_filter(category_id)
        counts, order = {}, {}
        for (specs,) in self.conn.execute(
                f"SELECT specs FROM items i WHERE i.deleted=0{cond}", params):
            for pos, (k, _v) in enumerate(json.loads(specs or "[]")):
                counts[k] = counts.get(k, 0) + 1
                order[k] = min(order.get(k, pos), pos)
        return sorted(counts, key=lambda k: (-counts[k], order[k], k))

    # ---------- фото ----------
    def photo_path(self, filename):
        return self.photos_dir / (filename or "__missing__")

    def _import_photo(self, source, photo_id):
        src = Path(source)
        ext = src.suffix.lower() if src.suffix.lower() in IMAGE_EXT else ".jpg"
        name = f"{photo_id}{ext}"
        shutil.copy2(src, self.photos_dir / name)
        return name

    # ---------- перенесення між базами ----------
    def import_from(self, other):
        """Копіює всі живі записи іншої бази (напр. локальної) як нові незасинхронізовані."""
        n = 0
        with self.conn:
            for c in other.conn.execute("SELECT * FROM categories WHERE deleted=0"):
                self.conn.execute(
                    "INSERT OR IGNORE INTO categories(id, name, parent_id, updated_at, dirty) "
                    "VALUES (?,?,?,?,1)", (c["id"], c["name"], c["parent_id"], now_iso()))
            for i in other.conn.execute("SELECT * FROM items WHERE deleted=0"):
                cols = list(FIELDS) + ["specs", "created_at"]
                cur = self.conn.execute(
                    f"INSERT OR IGNORE INTO items(id, {','.join(cols)}, updated_at, dirty, dirty_fields) "
                    f"VALUES (?, {','.join('?' * len(cols))}, ?, 1, ?)",
                    [i["id"]] + [i[c] for c in cols]
                    + [now_iso(), json.dumps(list(FIELDS) + ["specs"])])
                n += cur.rowcount
            for p in other.conn.execute(
                    "SELECT * FROM photos WHERE deleted=0 AND filename IS NOT NULL"):
                src = other.photo_path(p["filename"])
                if not src.exists():
                    continue
                shutil.copy2(src, self.photos_dir / p["filename"])
                self.conn.execute(
                    "INSERT OR IGNORE INTO photos(id, item_id, filename, path, position, dirty) "
                    "VALUES (?,?,?,?,?,1)",
                    (p["id"], p["item_id"], p["filename"], p["path"], p["position"]))
        return n

    # ---------- експорт / резервна копія ----------
    def export_csv(self, path, item_ids):
        paths = self.category_paths()
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["Назва", "Категорія", "Інв. №", "Серійний №", "Розташування",
                        "Кількість", "Ціна", "Дата покупки", "Стан", "Примітки",
                        "Характеристики", "Фото"])
            for iid in item_ids:
                item, specs, photos = self.get_item(iid)
                if item is None:
                    continue
                w.writerow([
                    item["name"], paths.get(item["category_id"], ""),
                    item["inventory_no"] or "", item["serial_no"] or "", item["location"] or "",
                    item["quantity"], "" if item["price"] is None else item["price"],
                    item["purchase_date"] or "", item["status"] or "", item["notes"] or "",
                    "; ".join(f"{s['key']}: {s['value'] or ''}" for s in specs),
                    len(photos)])

    def backup(self, zip_path):
        """Повна копія: база + усі фото в один zip."""
        with tempfile.TemporaryDirectory() as tmp:
            snap = Path(tmp) / self.db_path.name
            dst = sqlite3.connect(snap)
            self.conn.backup(dst)
            dst.close()
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
                z.write(snap, self.db_path.name)
                for p in self.photos_dir.iterdir():
                    if p.is_file():
                        z.write(p, f"photos/{p.name}")

    def close(self):
        self.conn.close()
