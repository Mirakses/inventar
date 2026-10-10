"""Шар даних: SQLite-база + папка з фотографіями."""
import csv
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

ALL = -1    # усі речі
NONE = -2   # речі без категорії

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp", ".tif", ".tiff"}

FIELDS = ("name", "category_id", "inventory_no", "serial_no", "location",
          "quantity", "price", "purchase_date", "status", "notes")

SCHEMA = """
CREATE TABLE IF NOT EXISTS categories(
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL,
    parent_id INTEGER REFERENCES categories(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS items(
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    category_id   INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    inventory_no  TEXT,
    serial_no     TEXT,
    location      TEXT,
    quantity      INTEGER NOT NULL DEFAULT 1,
    price         REAL,
    purchase_date TEXT,
    status        TEXT,
    notes         TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS specs(
    id       INTEGER PRIMARY KEY,
    item_id  INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    key      TEXT NOT NULL,
    value    TEXT,
    position INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS photos(
    id       INTEGER PRIMARY KEY,
    item_id  INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_items_cat ON items(category_id);
CREATE INDEX IF NOT EXISTS idx_specs_item ON specs(item_id);
CREATE INDEX IF NOT EXISTS idx_photos_item ON photos(item_id);
"""


def _ulower(s):
    # SQLite lower()/LIKE не розуміють кирилицю — робимо це на боці Python
    return s.lower() if isinstance(s, str) else s


class Database:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.photos_dir = self.data_dir / "photos"
        self.photos_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "inventory.db"
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.create_function("ulower", 1, _ulower, deterministic=True)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    # ---------- категорії ----------
    def categories(self):
        return self.conn.execute(
            "SELECT id, name, parent_id FROM categories ORDER BY ulower(name)").fetchall()

    def add_category(self, name, parent_id=None):
        with self.conn:
            return self.conn.execute(
                "INSERT INTO categories(name, parent_id) VALUES (?, ?)",
                (name.strip(), parent_id)).lastrowid

    def rename_category(self, cat_id, name):
        with self.conn:
            self.conn.execute("UPDATE categories SET name=? WHERE id=?", (name.strip(), cat_id))

    def move_category(self, cat_id, parent_id):
        if parent_id is not None and parent_id in self.descendants(cat_id):
            raise ValueError("Не можна перемістити категорію саму в себе")
        with self.conn:
            self.conn.execute("UPDATE categories SET parent_id=? WHERE id=?", (parent_id, cat_id))

    def delete_category(self, cat_id):
        # підкатегорії видаляються каскадно, речі стають «без категорії»
        with self.conn:
            self.conn.execute("DELETE FROM categories WHERE id=?", (cat_id,))

    def descendants(self, cat_id):
        rows = self.conn.execute("""
            WITH RECURSIVE sub(id) AS (
                SELECT ? UNION ALL
                SELECT c.id FROM categories c JOIN sub ON c.parent_id = sub.id)
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

    def count_items(self, cat_id):
        if cat_id == ALL:
            return self.conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        if cat_id == NONE:
            return self.conn.execute(
                "SELECT COUNT(*) FROM items WHERE category_id IS NULL").fetchone()[0]
        ids = self.descendants(cat_id)
        return self.conn.execute(
            f"SELECT COUNT(*) FROM items WHERE category_id IN ({','.join('?' * len(ids))})",
            ids).fetchone()[0]

    # ---------- речі ----------
    def list_items(self, category=ALL, search=""):
        sql = """
            SELECT i.*,
                   (SELECT filename FROM photos p WHERE p.item_id = i.id
                     ORDER BY position LIMIT 1) AS cover,
                   (SELECT COUNT(*) FROM photos p WHERE p.item_id = i.id) AS photo_count
            FROM items i WHERE 1=1"""
        params = []
        if category == NONE:
            sql += " AND i.category_id IS NULL"
        elif category not in (ALL, None):
            ids = self.descendants(category)
            sql += f" AND i.category_id IN ({','.join('?' * len(ids))})"
            params += ids
        search = search.strip()
        if search:
            for word in search.split():
                like = f"%{word.lower()}%"
                sql += """ AND (ulower(i.name) LIKE ? OR ulower(i.inventory_no) LIKE ?
                           OR ulower(i.serial_no) LIKE ? OR ulower(i.location) LIKE ?
                           OR ulower(i.notes) LIKE ? OR ulower(i.status) LIKE ?
                           OR EXISTS (SELECT 1 FROM specs s WHERE s.item_id = i.id
                                AND (ulower(s.key) LIKE ? OR ulower(s.value) LIKE ?)))"""
                params += [like] * 8
        sql += " ORDER BY ulower(i.name)"
        return self.conn.execute(sql, params).fetchall()

    def get_item(self, item_id):
        item = self.conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if item is None:
            return None, [], []
        specs = self.conn.execute(
            "SELECT key, value FROM specs WHERE item_id=? ORDER BY position", (item_id,)).fetchall()
        photos = self.conn.execute(
            "SELECT id, filename FROM photos WHERE item_id=? ORDER BY position", (item_id,)).fetchall()
        return item, specs, photos

    def save_item(self, item_id, fields, specs, photos):
        """photos — список dict: {"id": .., "filename": ..} для наявних або {"source": шлях} для нових."""
        now = datetime.now().isoformat(timespec="seconds")
        values = [fields.get(f) for f in FIELDS]
        new_files, removed = [], []
        try:
            with self.conn:
                if item_id is None:
                    cur = self.conn.execute(
                        f"INSERT INTO items({','.join(FIELDS)}, created_at, updated_at) "
                        f"VALUES ({','.join('?' * len(FIELDS))}, ?, ?)", values + [now, now])
                    item_id = cur.lastrowid
                else:
                    sets = ", ".join(f"{f}=?" for f in FIELDS)
                    self.conn.execute(f"UPDATE items SET {sets}, updated_at=? WHERE id=?",
                                      values + [now, item_id])

                self.conn.execute("DELETE FROM specs WHERE item_id=?", (item_id,))
                self.conn.executemany(
                    "INSERT INTO specs(item_id, key, value, position) VALUES (?, ?, ?, ?)",
                    [(item_id, k, v, i) for i, (k, v) in enumerate(specs)])

                keep = {p["id"] for p in photos if p.get("id")}
                for row in self.conn.execute(
                        "SELECT id, filename FROM photos WHERE item_id=?", (item_id,)).fetchall():
                    if row["id"] not in keep:
                        removed.append(row["filename"])
                        self.conn.execute("DELETE FROM photos WHERE id=?", (row["id"],))
                for pos, p in enumerate(photos):
                    if p.get("id"):
                        self.conn.execute("UPDATE photos SET position=? WHERE id=?", (pos, p["id"]))
                    else:
                        fn = self._import_photo(p["source"])
                        new_files.append(fn)
                        self.conn.execute(
                            "INSERT INTO photos(item_id, filename, position) VALUES (?, ?, ?)",
                            (item_id, fn, pos))
        except Exception:
            for fn in new_files:
                (self.photos_dir / fn).unlink(missing_ok=True)
            raise
        for fn in removed:
            (self.photos_dir / fn).unlink(missing_ok=True)
        return item_id

    def duplicate_item(self, item_id):
        item, specs, photos = self.get_item(item_id)
        fields = {f: item[f] for f in FIELDS}
        fields["name"] = f"{item['name']} (копія)"
        fields["serial_no"] = None
        fields["inventory_no"] = None
        return self.save_item(None, fields, [(s["key"], s["value"]) for s in specs],
                              [{"source": self.photo_path(p["filename"])} for p in photos])

    def move_items(self, item_ids, category_id):
        with self.conn:
            self.conn.executemany("UPDATE items SET category_id=? WHERE id=?",
                                  [(category_id, i) for i in item_ids])

    def delete_item(self, item_id):
        files = [r[0] for r in self.conn.execute(
            "SELECT filename FROM photos WHERE item_id=?", (item_id,))]
        with self.conn:
            self.conn.execute("DELETE FROM items WHERE id=?", (item_id,))
        for fn in files:
            (self.photos_dir / fn).unlink(missing_ok=True)

    # ---------- довідкові ----------
    def distinct_values(self, column):
        assert column in ("location", "status")
        return [r[0] for r in self.conn.execute(
            f"SELECT DISTINCT {column} FROM items WHERE {column} IS NOT NULL AND {column} != '' "
            f"ORDER BY ulower({column})")]

    def spec_keys(self, category_id=None):
        """Назви характеристик, найчастіші — першими (для категорії та її підкатегорій)."""
        if category_id is None:
            rows = self.conn.execute(
                "SELECT key, COUNT(*) n FROM specs GROUP BY key ORDER BY n DESC, key")
        else:
            ids = self.descendants(category_id)
            rows = self.conn.execute(f"""
                SELECT s.key, COUNT(*) n FROM specs s JOIN items i ON i.id = s.item_id
                WHERE i.category_id IN ({','.join('?' * len(ids))})
                GROUP BY s.key ORDER BY n DESC, MIN(s.position)""", ids)
        return [r[0] for r in rows]

    # ---------- фото ----------
    def photo_path(self, filename):
        return self.photos_dir / filename

    def _import_photo(self, source):
        src = Path(source)
        ext = src.suffix.lower() if src.suffix.lower() in IMAGE_EXT else ".jpg"
        name = f"{uuid.uuid4().hex}{ext}"
        shutil.copy2(src, self.photos_dir / name)
        return name

    # ---------- експорт / резервна копія ----------
    def export_csv(self, path, item_ids):
        paths = self.category_paths()
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["ID", "Назва", "Категорія", "Інв. №", "Серійний №", "Розташування",
                        "Кількість", "Ціна", "Дата покупки", "Стан", "Примітки",
                        "Характеристики", "Фото"])
            for iid in item_ids:
                item, specs, photos = self.get_item(iid)
                if item is None:
                    continue
                w.writerow([
                    item["id"], item["name"], paths.get(item["category_id"], ""),
                    item["inventory_no"] or "", item["serial_no"] or "", item["location"] or "",
                    item["quantity"], "" if item["price"] is None else item["price"],
                    item["purchase_date"] or "", item["status"] or "", item["notes"] or "",
                    "; ".join(f"{s['key']}: {s['value'] or ''}" for s in specs),
                    len(photos)])

    def backup(self, zip_path):
        """Повна копія: база + усі фото в один zip."""
        with tempfile.TemporaryDirectory() as tmp:
            snap = Path(tmp) / "inventory.db"
            dst = sqlite3.connect(snap)
            self.conn.backup(dst)
            dst.close()
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
                z.write(snap, "inventory.db")
                for p in self.photos_dir.iterdir():
                    if p.is_file():
                        z.write(p, f"photos/{p.name}")

    def close(self):
        self.conn.close()
