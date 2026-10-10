"""Хмара (Supabase): вхід, профіль і права, адміністрування, синхронізація.

Синхронізація:
- push: незасинхронізовані (dirty) записи → сервер. Нове — INSERT, наявне — UPDATE.
  Комірник відправляє лише стан/розташування/кількість (так вимагає сервер).
  Якщо сервер відхилив зміну (немає прав) — локальний запис відновлюється з сервера.
- pull: усе, що змінилося на сервері після останньої синхронізації.
- reconcile: періодично прибирає локальні копії записів, до яких зник доступ.
- фото: вивантажуються в Storage, нові з сервера докачуються у фоні.
"""
import json
import threading
from pathlib import Path

from db import FIELDS, Database, connect

ROLES = {
    "admin": "Адміністратор",
    "editor": "Редактор",
    "storekeeper": "Комірник",
    "viewer": "Переглядач",
}
STOREKEEPER_FIELDS = ("status", "location", "quantity")
BUCKET = "photos"
PAGE = 1000


class CloudError(Exception):
    pass


class Offline(CloudError):
    """Немає зв'язку з сервером."""


class Rejected(CloudError):
    """Сервер відхилив дію (немає прав)."""


def _is_network_error(e):
    try:
        import httpx
        if isinstance(e, (httpx.TransportError, httpx.TimeoutException)):
            return True
    except ImportError:
        pass
    name = type(e).__name__
    return isinstance(e, (ConnectionError, TimeoutError, OSError)) or "Retryable" in name


def _error_code(e):
    for attr in ("code", "status", "status_code", "statusCode"):
        v = getattr(e, attr, None)
        if v:
            return str(v)
    if e.args and isinstance(e.args[0], dict):
        return str(e.args[0].get("code") or e.args[0].get("statusCode") or "")
    return ""


def _is_permission_error(e):
    code = _error_code(e)
    text = str(e).lower()
    return (code in ("42501", "403", "401", "PGRST301")
            or "row-level security" in text or "permission denied" in text
            or "unauthorized" in text or "комірник" in text)


def friendly_error(e):
    """Зрозуміле повідомлення для людини."""
    text = str(e)
    low = text.lower()
    if isinstance(e, Offline) or _is_network_error(e):
        return "Немає зв'язку з сервером. Перевірте інтернет."
    if "invalid login credentials" in low:
        return "Неправильний email або пароль."
    if "email not confirmed" in low:
        return "Email ще не підтверджено — введіть код із листа."
    if "invite_required" in low or "database error saving new user" in low:
        return "Для цього email немає запрошення. Попросіть адміністратора вас запросити."
    if "already registered" in low or "already been registered" in low:
        return "Користувач з таким email вже зареєстрований — увійдіть."
    if "password should be at least" in low or "weak" in low and "password" in low:
        return "Пароль надто простий (мінімум 6 символів)."
    if "expired" in low or "invalid" in low and ("otp" in low or "token" in low):
        return "Код неправильний або застарів. Запросіть новий."
    if "you can only request this after" in low:
        import re
        m = re.search(r"after (\d+) second", low)
        return f"Повторно надіслати можна через {m.group(1)} с." if m else "Зачекайте трохи."
    if "same_password" in low or "should be different" in low:
        return "Новий пароль має відрізнятися від старого."
    if "rate limit" in low:
        return "Забагато спроб. Зачекайте кілька хвилин."
    if _is_permission_error(e):
        return "Недостатньо прав для цієї дії."
    return text


def _wrap(fn):
    """Перетворює помилки мережі на Offline, а помилки прав — на Rejected."""
    def inner(*a, **k):
        try:
            return fn(*a, **k)
        except CloudError:
            raise
        except Exception as e:  # noqa: BLE001
            if _is_network_error(e):
                raise Offline(str(e)) from e
            if _is_permission_error(e):
                raise Rejected(str(e)) from e
            raise
    inner.__name__ = fn.__name__
    inner.__doc__ = fn.__doc__
    return inner


def project_ref(url):
    """https://abcd.supabase.co → abcd"""
    host = url.split("//")[-1].split("/")[0]
    return host.split(".")[0] or "cloud"


# ====================================================================== Cloud
class Cloud:
    def __init__(self, url, key, data_dir, client=None):
        self.url = url.rstrip("/")
        self.key = key
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._session_file = self.data_dir / "session.json"
        self._lock = threading.Lock()
        if client is None:
            from supabase import ClientOptions, create_client
            client = create_client(self.url, key, options=ClientOptions(
                auto_refresh_token=True, persist_session=False))
        self.client = client
        try:
            self.client.auth.on_auth_state_change(self._on_auth)
        except Exception:  # noqa: BLE001 — у тестових клієнтах може не бути
            pass
        self.user_id = None
        self.profile = None   # dict з таблиці profiles
        self.access = []      # id категорій, доступних користувачу

    # ---------- сесія ----------
    def _on_auth(self, event, session):
        if session is not None:
            self._save_session(session)
        elif str(event).upper().endswith("SIGNED_OUT"):
            self._clear_session()

    def _save_session(self, session):
        with self._lock:
            self._session_file.write_text(json.dumps(
                {"access": session.access_token, "refresh": session.refresh_token,
                 "user_id": session.user.id if session.user else None}))
        if session.user:
            self.user_id = session.user.id

    def _clear_session(self):
        with self._lock:
            self._session_file.unlink(missing_ok=True)

    def saved_user_id(self):
        try:
            return json.loads(self._session_file.read_text()).get("user_id")
        except (OSError, ValueError):
            return None

    @_wrap
    def restore(self):
        """Відновлює збережений вхід. True — успішно, False — треба увійти знову.
        Offline — сервер недоступний (можна працювати з локальною копією)."""
        try:
            s = json.loads(self._session_file.read_text())
        except (OSError, ValueError):
            return False
        try:
            res = self.client.auth.set_session(s["access"], s["refresh"])
        except Exception as e:  # noqa: BLE001
            if _is_network_error(e):
                self.user_id = s.get("user_id")
                raise Offline(str(e)) from e
            self._clear_session()
            return False
        if res and res.session:
            self._save_session(res.session)
            return True
        return False

    def ensure_session(self):
        """Перед синхронізацією: чи є дійсний вхід. Без нього сервер «нічого не покаже»,
        і звірка помилково стерла б локальні дані. Offline — якщо немає мережі."""
        try:
            if self.client.auth.get_session():
                return True
        except Exception as e:  # noqa: BLE001
            if _is_network_error(e):
                raise Offline(str(e)) from e
        return self.restore()

    @_wrap
    def login(self, email, password):
        res = self.client.auth.sign_in_with_password(
            {"email": email.strip().lower(), "password": password})
        self._save_session(res.session)
        return True

    @_wrap
    def signup(self, email, password, full_name):
        """Повертає 'ok' (одразу увійшли) або 'confirm' (треба підтвердити email)."""
        res = self.client.auth.sign_up({
            "email": email.strip().lower(), "password": password,
            "options": {"data": {"full_name": full_name.strip()}}})
        if res.session:
            self._save_session(res.session)
            return "ok"
        return "confirm"

    @_wrap
    def reset_password(self, email):
        """Надсилає лист із кодом для зміни пароля."""
        self.client.auth.reset_password_for_email(email.strip().lower())

    @_wrap
    def resend_signup_code(self, email):
        self.client.auth.resend({"type": "signup", "email": email.strip().lower()})

    @_wrap
    def verify_code(self, email, code, kind):
        """kind: 'signup' — підтвердження реєстрації, 'recovery' — зміна пароля.
        Успішний код одразу входить у систему."""
        res = self.client.auth.verify_otp({"email": email.strip().lower(),
                                           "token": "".join(code.split()), "type": kind})
        if not res.session:
            raise CloudError("Код не підійшов")
        self._save_session(res.session)
        return True

    @_wrap
    def set_password(self, password):
        self.client.auth.update_user({"password": password})

    def logout(self):
        try:
            self.client.auth.sign_out()
        except Exception:  # noqa: BLE001 — офлайн теж можна вийти
            pass
        self._clear_session()
        self.user_id = self.profile = None
        self.access = []

    # ---------- профіль і права ----------
    @_wrap
    def load_profile(self):
        uid = self.user_id or self.saved_user_id()
        rows = self.client.table("profiles").select("*").eq("id", uid).execute().data
        if not rows:
            raise Rejected("Профіль не знайдено або вимкнено")
        self.profile = rows[0]
        self.access = [r["category_id"] for r in self.client.table("category_access")
                       .select("category_id").eq("user_id", uid).execute().data]
        return self.profile

    def cache_profile(self, db):
        db.meta_set("profile", self.profile)
        db.meta_set("access", self.access)

    def load_cached_profile(self, db):
        self.profile = db.meta_get("profile")
        self.access = db.meta_get("access", [])
        if self.profile:
            self.user_id = self.profile["id"]
        return self.profile

    @property
    def role(self):
        return (self.profile or {}).get("role")

    # ---------- адміністрування ----------
    @_wrap
    def list_profiles(self):
        return self.client.table("profiles").select("*").order("full_name").execute().data

    @_wrap
    def all_access(self):
        rows = self.client.table("category_access").select("*").execute().data
        out = {}
        for r in rows:
            out.setdefault(r["user_id"], []).append(r["category_id"])
        return out

    @_wrap
    def update_profile(self, user_id, **fields):
        data = self.client.table("profiles").update(fields).eq("id", user_id).execute().data
        if not data:
            raise Rejected("Не вдалося змінити користувача")

    @_wrap
    def set_access(self, user_id, category_ids):
        self.client.table("category_access").delete().eq("user_id", user_id).execute()
        if category_ids:
            self.client.table("category_access").insert(
                [{"user_id": user_id, "category_id": c} for c in category_ids]).execute()

    @_wrap
    def list_invitations(self):
        return self.client.table("invitations").select("*").order("created_at").execute().data

    @_wrap
    def invite(self, email, full_name, role, all_categories, category_ids):
        self.client.table("invitations").upsert({
            "email": email.strip().lower(), "full_name": full_name.strip() or None,
            "role": role, "all_categories": all_categories,
            "category_ids": list(category_ids), "invited_by": self.user_id}).execute()

    @_wrap
    def delete_invitation(self, email):
        self.client.table("invitations").delete().eq("email", email).execute()

    @_wrap
    def history(self, item_id, limit=100):
        return (self.client.table("item_history").select("*").eq("item_id", item_id)
                .order("at", desc=True).limit(limit).execute().data)


# ====================================================================== Синхронізація
class SyncEngine:
    """Не залежить від Qt — викликається з фонового потоку."""

    RECONCILE_EVERY = 20  # повна звірка кожні N синхронізацій

    def __init__(self, cloud, data_dir, db_filename="data.db"):
        self.cloud = cloud
        self.data_dir = Path(data_dir)
        self.db_filename = db_filename
        self._db = None
        self._runs = 0

    @property
    def db(self):
        # окреме з'єднання для фонового потоку
        if self._db is None:
            self._db = Database(self.data_dir, self.db_filename)
        return self._db

    @property
    def t(self):
        return self.cloud.client

    def run_once(self, full=False):
        stats = {"pushed": 0, "rejected": 0, "pulled": 0, "photos": 0, "pending": 0}
        try:
            self._push(stats)
            stats["pulled"] = self._pull()
            if full or self._runs % self.RECONCILE_EVERY == 0:
                stats["pulled"] += self._reconcile()
            stats["photos"] = self._download_photos()
        except Exception as e:  # noqa: BLE001
            if _is_network_error(e):
                raise Offline(str(e)) from e
            raise
        finally:
            stats["pending"] = self.db.pending_count()
        self._runs += 1
        return stats

    # ---------------------------------------------------------- push
    def _push(self, stats):
        self._push_categories(stats)
        self._push_items(stats)
        self._push_photos(stats)

    def _call(self, fn):
        """Виконує запит; повертає (ok, data). ok=False — відхилено правами."""
        try:
            res = fn()
            return True, getattr(res, "data", res)
        except Exception as e:  # noqa: BLE001
            if _is_network_error(e):
                raise
            if _is_permission_error(e):
                return False, None
            raise

    def _mark_clean(self, table, row, extra=""):
        with self.db.conn:
            self.db.conn.execute(
                f"UPDATE {table} SET dirty=0, on_server=1{extra} "
                f"WHERE id=? AND COALESCE(updated_at,'')=COALESCE(?,'')",
                (row["id"], row["updated_at"]))

    def _push_categories(self, stats):
        rows = self.db.conn.execute("SELECT * FROM categories WHERE dirty=1").fetchall()
        if not rows:
            return
        # батьківські — першими
        parents = {r["id"]: r["parent_id"] for r in
                   self.db.conn.execute("SELECT id, parent_id FROM categories")}

        def depth(cid, d=0):
            p = parents.get(cid)
            return d if not p or d > 50 else depth(p, d + 1)

        for r in sorted(rows, key=lambda r: depth(r["id"])):
            payload = {"name": r["name"], "parent_id": r["parent_id"], "deleted": bool(r["deleted"])}
            if r["on_server"]:
                ok, data = self._call(lambda: self.t.table("categories").update(payload)
                                      .eq("id", r["id"]).execute())
                ok = ok and bool(data)
            else:
                ok, _ = self._call(lambda: self.t.table("categories")
                                   .insert({"id": r["id"], **payload}).execute())
            if ok:
                self._mark_clean("categories", r)
                stats["pushed"] += 1
            else:
                self._revert("categories", r["id"])
                stats["rejected"] += 1

    def _item_payload(self, r, fields=None):
        out = {}
        for f in (fields or list(FIELDS) + ["specs", "deleted"]):
            v = r[f]
            if f == "specs":
                v = json.loads(v or "[]")
            elif f == "deleted":
                v = bool(v)
            out[f] = v
        return out

    def _push_items(self, stats):
        role = self.cloud.role
        for r in self.db.conn.execute("SELECT * FROM items WHERE dirty=1").fetchall():
            if r["category_id"] and not self._category_on_server(r["category_id"]):
                continue  # спершу має потрапити на сервер категорія
            dirty_fields = set(json.loads(r["dirty_fields"] or "[]"))
            if role == "storekeeper":
                if not r["on_server"] or dirty_fields - set(STOREKEEPER_FIELDS):
                    self._revert("items", r["id"])
                    stats["rejected"] += 1
                    continue
                payload = self._item_payload(r, [f for f in STOREKEEPER_FIELDS if f in dirty_fields])
                if not payload:
                    self._mark_clean("items", r, ", dirty_fields='[]'")
                    continue
            else:
                payload = self._item_payload(r)
            if r["on_server"]:
                ok, data = self._call(lambda: self.t.table("items").update(payload)
                                      .eq("id", r["id"]).execute())
                ok = ok and bool(data)
            else:
                ok, _ = self._call(lambda: self.t.table("items")
                                   .insert({"id": r["id"], "created_at": r["created_at"],
                                            **payload}).execute())
            if ok:
                self._mark_clean("items", r, ", dirty_fields='[]'")
                stats["pushed"] += 1
            else:
                self._revert("items", r["id"])
                stats["rejected"] += 1

    def _category_on_server(self, cid):
        r = self.db.conn.execute("SELECT on_server FROM categories WHERE id=?", (cid,)).fetchone()
        return r is None or bool(r[0])  # невідома локально — нехай вирішує сервер

    def _push_photos(self, stats):
        store = self.t.storage.from_(BUCKET)
        for r in self.db.conn.execute("SELECT * FROM photos WHERE dirty=1").fetchall():
            if r["deleted"]:
                ok = True
                if r["on_server"]:
                    ok, data = self._call(lambda: self.t.table("photos").update({"deleted": True})
                                          .eq("id", r["id"]).execute())
                    ok = ok and bool(data)
                    if ok:
                        try:
                            store.remove([r["path"]])
                        except Exception:  # noqa: BLE001 — файл приберемо пізніше
                            pass
                if ok:
                    with self.db.conn:
                        self.db.conn.execute("DELETE FROM photos WHERE id=?", (r["id"],))
                    stats["pushed"] += 1
                else:
                    self._revert("photos", r["id"])
                    stats["rejected"] += 1
                continue

            item = self.db.conn.execute(
                "SELECT on_server FROM items WHERE id=?", (r["item_id"],)).fetchone()
            if not item or not item[0]:
                continue  # спершу має синхронізуватися сама річ
            if not r["uploaded"]:
                path = self.db.photo_path(r["filename"])
                if not r["filename"] or not path.exists():
                    with self.db.conn:
                        self.db.conn.execute("DELETE FROM photos WHERE id=?", (r["id"],))
                    continue
                ctype = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
                ok, _ = self._call(lambda: store.upload(
                    r["path"], path.read_bytes(), {"content-type": ctype, "upsert": "true"}))
                if not ok:
                    self._revert("photos", r["id"])
                    stats["rejected"] += 1
                    continue
                with self.db.conn:
                    self.db.conn.execute("UPDATE photos SET uploaded=1 WHERE id=?", (r["id"],))
            payload = {"item_id": r["item_id"], "path": r["path"], "position": r["position"]}
            if r["on_server"]:
                ok, data = self._call(lambda: self.t.table("photos").update(payload)
                                      .eq("id", r["id"]).execute())
                ok = ok and bool(data)
            else:
                ok, _ = self._call(lambda: self.t.table("photos")
                                   .insert({"id": r["id"], **payload}).execute())
            if ok:
                self._mark_clean("photos", r)
                stats["pushed"] += 1
            else:
                self._revert("photos", r["id"])
                stats["rejected"] += 1

    def _revert(self, table, row_id):
        """Сервер відхилив зміну: беремо серверну версію або прибираємо локальну."""
        rows = self.t.table(table).select("*").eq("id", row_id).execute().data
        if rows:
            self._apply(table, rows[0], force=True)
        else:
            self._remove_local(table, row_id)

    # ---------------------------------------------------------- pull
    def _pull(self):
        changed = 0
        for table in ("categories", "items", "photos"):
            key = f"pull_{table}"
            since = self.db.meta_get(key)
            newest = since
            offset = 0
            while True:
                q = self.t.table(table).select("*")
                if since:
                    q = q.gte("updated_at", since)
                rows = q.order("updated_at").order("id").range(offset, offset + PAGE - 1).execute().data
                for row in rows:
                    changed += self._apply(table, row)
                    if newest is None or row["updated_at"] > newest:
                        newest = row["updated_at"]
                if len(rows) < PAGE:
                    break
                offset += PAGE
            if newest != since:
                self.db.meta_set(key, newest)
        return changed

    def _apply(self, table, row, force=False):
        """Записує серверний рядок локально. Повертає 1, якщо щось змінилося."""
        db = self.db
        local = db.conn.execute(f"SELECT * FROM {table} WHERE id=?", (row["id"],)).fetchone()
        if local is not None and local["dirty"] and not force:
            return 0  # локальна зміна ще не відправлена — вона пізніша
        if local is not None and local["updated_at"] == row["updated_at"] and local["on_server"] \
                and not force:
            return 0
        orphan = table == "photos" and not db.conn.execute(
            "SELECT 1 FROM items WHERE id=? AND deleted=0", (row["item_id"],)).fetchone()
        if row.get("deleted") or orphan:
            self._remove_local(table, row["id"])
            return 1 if local is not None else 0
        with db.conn:
            if table == "categories":
                db.conn.execute(
                    "INSERT OR REPLACE INTO categories(id, name, parent_id, deleted, updated_at, "
                    "dirty, on_server) VALUES (?,?,?,0,?,0,1)",
                    (row["id"], row["name"], row["parent_id"], row["updated_at"]))
            elif table == "items":
                cols = list(FIELDS) + ["created_at", "created_by", "updated_at", "updated_by"]
                vals = [row.get(c) for c in cols]
                db.conn.execute(
                    f"INSERT OR REPLACE INTO items(id, {','.join(cols)}, specs, deleted, dirty, "
                    f"dirty_fields, on_server) VALUES (?, {','.join('?' * len(cols))}, ?, 0, 0, '[]', 1)",
                    [row["id"]] + vals + [json.dumps(row.get("specs") or [], ensure_ascii=False)])
            else:
                db.conn.execute(
                    "INSERT INTO photos(id, item_id, path, position, updated_at, dirty, on_server, "
                    "uploaded) VALUES (?,?,?,?,?,0,1,1) ON CONFLICT(id) DO UPDATE SET "
                    "item_id=excluded.item_id, path=excluded.path, position=excluded.position, "
                    "deleted=0, updated_at=excluded.updated_at, dirty=0, on_server=1, uploaded=1",
                    (row["id"], row["item_id"], row["path"], row["position"], row["updated_at"]))
        return 1

    def _remove_local(self, table, row_id):
        db = self.db
        files = []
        with db.conn:
            if table == "items":
                files = [r[0] for r in db.conn.execute(
                    "SELECT filename FROM photos WHERE item_id=? AND filename IS NOT NULL", (row_id,))]
                db.conn.execute("DELETE FROM photos WHERE item_id=?", (row_id,))
            elif table == "photos":
                files = [r[0] for r in db.conn.execute(
                    "SELECT filename FROM photos WHERE id=? AND filename IS NOT NULL", (row_id,))]
            db.conn.execute(f"DELETE FROM {table} WHERE id=?", (row_id,))
        for fn in files:
            db.photo_path(fn).unlink(missing_ok=True)

    def _reconcile(self):
        """Прибирає локальні копії записів, яких сервер більше не показує (видалено/забрано доступ)."""
        removed = 0
        for table in ("photos", "items", "categories"):
            server_ids, offset = set(), 0
            while True:
                rows = (self.t.table(table).select("id").order("id")
                        .range(offset, offset + PAGE - 1).execute().data)
                server_ids.update(r["id"] for r in rows)
                if len(rows) < PAGE:
                    break
                offset += PAGE
            for (rid,) in self.db.conn.execute(
                    f"SELECT id FROM {table} WHERE on_server=1 AND dirty=0").fetchall():
                if rid not in server_ids:
                    self._remove_local(table, rid)
                    removed += 1
        return removed

    # ---------------------------------------------------------- фото з сервера
    def _download_photos(self, limit=25):
        store = self.t.storage.from_(BUCKET)
        n = 0
        for r in self.db.conn.execute(
                "SELECT id, path FROM photos WHERE deleted=0 AND on_server=1 AND filename IS NULL "
                "LIMIT ?", (limit,)).fetchall():
            try:
                data = store.download(r["path"])
            except Exception as e:  # noqa: BLE001
                if _is_network_error(e):
                    raise
                continue
            fn = Path(r["path"]).name
            (self.db.photos_dir / fn).write_bytes(data)
            with self.db.conn:
                self.db.conn.execute("UPDATE photos SET filename=? WHERE id=?", (fn, r["id"]))
            n += 1
        return n

    def close(self):
        if self._db is not None:
            self._db.close()
            self._db = None


__all__ = ["Cloud", "SyncEngine", "CloudError", "Offline", "Rejected", "ROLES",
           "friendly_error", "project_ref", "connect"]
