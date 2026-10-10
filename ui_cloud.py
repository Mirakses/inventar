"""Інтерфейс хмари: вхід, підключення, права, користувачі, фонова синхронізація."""
import html
import json
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QStackedWidget,
    QTableWidget, QTableWidgetItem, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
    QWidget)

from cloud import ROLES, Offline, Rejected, SyncEngine, friendly_error, project_ref

ROLE_HINTS = {
    "admin": "усе, включно з користувачами й категоріями",
    "editor": "додає, змінює й видаляє речі у своїх категоріях",
    "storekeeper": "видача/повернення: лише стан, розташування, кількість",
    "viewer": "лише перегляд",
}
FIELD_NAMES = {
    "name": "назва", "category_id": "категорія", "inventory_no": "інв. №", "serial_no": "серійний №",
    "location": "розташування", "quantity": "кількість", "price": "ціна",
    "purchase_date": "дата покупки", "status": "стан", "notes": "примітки",
    "specs": "характеристики", "deleted": "видалено",
}


# ====================================================================== конфіг
def config_path(base):
    return Path(base) / "cloud.json"


def load_config(base):
    """URL і ключ: спершу cloud.json у папці даних, потім вбудовані в програму."""
    try:
        cfg = json.loads(config_path(base).read_text(encoding="utf-8"))
        if cfg.get("url") and cfg.get("key"):
            return cfg
    except (OSError, ValueError):
        pass
    try:
        import cloud_config  # створюється під час збірки / вручну
        if cloud_config.URL and cloud_config.KEY:
            return {"url": cloud_config.URL, "key": cloud_config.KEY}
    except ImportError:
        pass
    return None


def save_config(base, url, key):
    config_path(base).write_text(json.dumps({"url": url, "key": key}, indent=2), encoding="utf-8")


def cloud_dir(base, url):
    return Path(base) / "cloud" / project_ref(url)


# ====================================================================== права
class Perm:
    """Що дозволено поточному користувачу (локальний режим — усе)."""

    def __init__(self, cloud=None, db=None):
        self.cloud, self.db = cloud, db

    @property
    def local(self):
        return self.cloud is None

    @property
    def role(self):
        return "admin" if self.local else (self.cloud.role or "viewer")

    def _all(self):
        return self.local or self.role == "admin" or bool((self.cloud.profile or {}).get("all_categories"))

    def editable_categories(self):
        """None — усі; інакше множина id (дозволені + їхні підкатегорії)."""
        if self._all():
            return None
        out = set()
        for c in self.cloud.access:
            out.update(self.db.descendants(c))
        return out

    @property
    def is_admin(self):
        return self.role == "admin"

    @property
    def can_manage_categories(self):
        return self.role == "admin"

    @property
    def can_add(self):
        return self.role in ("admin", "editor")

    def can_edit(self, cat_id):
        if self.role == "admin":
            return True
        if self.role != "editor":
            return False
        allowed = self.editable_categories()
        return allowed is None or cat_id in allowed

    def item_mode(self, cat_id):
        if self.can_edit(cat_id):
            return "edit"
        if self.role == "storekeeper":
            return "store"
        return "view"


# ====================================================================== синхронізація
class _Worker(QObject):
    done = Signal(dict)
    offline = Signal()
    failed = Signal(str)
    profile = Signal(dict, list)
    kicked = Signal(str)

    def __init__(self, engine, cloud):
        super().__init__()
        self.engine, self.cloud = engine, cloud

    @Slot(bool, bool)
    def run(self, full, refresh_profile):
        try:
            if not self.cloud.ensure_session():
                self.kicked.emit("Сеанс завершився — увійдіть знову.")
                return
            if refresh_profile:
                try:
                    p = self.cloud.load_profile()
                except Rejected:
                    self.kicked.emit("Ваш обліковий запис вимкнено або видалено адміністратором.")
                    return
                self.profile.emit(p, list(self.cloud.access))
            self.done.emit(self.engine.run_once(full))
        except Offline:
            self.offline.emit()
        except Exception as e:  # noqa: BLE001
            self.failed.emit(friendly_error(e))


class SyncController(QObject):
    """Фонова синхронізація: кожні 30 с, після змін і на вимогу."""
    state = Signal(str, str)        # текст, вид: ok / busy / offline / error
    synced = Signal(dict)
    profile_changed = Signal(dict, list)
    kicked = Signal(str)
    _go = Signal(bool, bool)

    INTERVAL_MS = 30_000

    def __init__(self, cloud, data_dir, parent=None):
        super().__init__(parent)
        self.engine = SyncEngine(cloud, data_dir)
        self.thread = QThread()
        self.worker = _Worker(self.engine, cloud)
        self.worker.moveToThread(self.thread)
        self._go.connect(self.worker.run)
        self.worker.done.connect(self._on_done)
        self.worker.offline.connect(self._on_offline)
        self.worker.failed.connect(self._on_failed)
        self.worker.profile.connect(self.profile_changed)
        self.worker.kicked.connect(self._on_kicked)
        self.thread.start()
        self.busy = False
        self.again = False
        self.runs = 0
        self.last_ok = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.request)
        self.timer.start(self.INTERVAL_MS)
        self.soon = QTimer(self)
        self.soon.setSingleShot(True)
        self.soon.timeout.connect(self.request)

    def request(self, full=False):
        if self.busy:
            self.again = True
            return
        self.busy = True
        self.state.emit("Синхронізація…", "busy")
        refresh = self.runs % 10 == 0          # профіль/права — раз на ~5 хв
        self._go.emit(full or self.runs == 0, refresh)
        self.runs += 1

    def request_soon(self):
        self.soon.start(1500)

    def _finish(self):
        self.busy = False
        if self.again:
            self.again = False
            self.request_soon()

    def _on_done(self, st):
        self.last_ok = datetime.now()
        if st["pending"]:
            self.state.emit(f"Синхронізовано {self.last_ok:%H:%M} · у черзі: {st['pending']}", "busy")
        else:
            self.state.emit(f"Синхронізовано {self.last_ok:%H:%M}", "ok")
        self.synced.emit(st)
        self._finish()

    def _on_offline(self):
        n = self.engine.db.pending_count()
        tail = f" · змін у черзі: {n}" if n else ""
        self.state.emit(f"Офлайн{tail}", "offline")
        self._finish()

    def _on_failed(self, msg):
        self.state.emit(f"Помилка синхронізації: {msg}", "error")
        self._finish()

    def _on_kicked(self, msg):
        self.timer.stop()
        self.busy = False
        self.kicked.emit(msg)

    def stop(self):
        self.timer.stop()
        self.soon.stop()
        self.thread.quit()
        self.thread.wait(10_000)
        self.engine.close()


# ====================================================================== діалоги
def _busy(fn):
    """Виконати дію з годинником замість курсора."""
    QApplication.setOverrideCursor(Qt.WaitCursor)
    try:
        return fn()
    finally:
        QApplication.restoreOverrideCursor()


class CodeEdit(QLineEdit):
    """Поле для коду з листа: лише цифри, великий моноширинний шрифт."""

    def __init__(self):
        super().__init__()
        self.setPlaceholderText("000000")
        self.setMaxLength(10)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("font-size: 26px; font-weight: 700; letter-spacing: 6px; "
                           "padding: 10px; font-family: 'Menlo', 'Consolas', monospace;")
        self.textEdited.connect(self._clean)

    def _clean(self, t):
        digits = "".join(ch for ch in t if ch.isdigit())
        if digits != t:
            self.setText(digits)


class LoginDialog(QDialog):
    """Вхід / реєстрація за запрошенням / підтвердження кодом / зміна пароля кодом.
    result(): 1 — увійшли, 2 — працювати локально."""
    LOCAL = 2

    def __init__(self, cloud, parent=None):
        super().__init__(parent)
        self.cloud = cloud
        self.setWindowTitle("Облік майна — вхід")
        self.setMinimumWidth(460)

        title = QLabel("☁️  Вхід у спільну базу")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        sub = QLabel(f"Сервер: {project_ref(cloud.url)}.supabase.co")
        sub.setObjectName("hint")

        self.pages = QStackedWidget()
        self.pages.addWidget(self._page_main())      # 0
        self.pages.addWidget(self._page_confirm())   # 1
        self.pages.addWidget(self._page_reset())     # 2

        self.msg = QLabel()
        self.msg.setWordWrap(True)
        self.msg.hide()
        self.b_local = QPushButton("Працювати без хмари (локально)")
        self.b_local.clicked.connect(lambda: self.done(self.LOCAL))

        lay = QVBoxLayout(self)
        lay.addWidget(title)
        lay.addWidget(sub)
        lay.addSpacing(6)
        lay.addWidget(self.pages)
        lay.addWidget(self.msg)
        lay.addWidget(self.b_local, 0, Qt.AlignLeft)

    # ---------- сторінки
    def _page_main(self):
        tabs = QTabWidget()
        w1 = QWidget()
        f1 = QFormLayout(w1)
        self.email = QLineEdit()
        self.email.setPlaceholderText("you@example.com")
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.Password)
        self.pw.returnPressed.connect(self.do_login)
        b_login = QPushButton("Увійти")
        b_login.setObjectName("primary")
        b_login.clicked.connect(self.do_login)
        b_forgot = QPushButton("Забули пароль?")
        b_forgot.setFlat(True)
        b_forgot.clicked.connect(self.open_reset)
        f1.addRow("Email", self.email)
        f1.addRow("Пароль", self.pw)
        row = QHBoxLayout()
        row.addWidget(b_forgot)
        row.addStretch()
        row.addWidget(b_login)
        f1.addRow(row)
        tabs.addTab(w1, "Вхід")

        w2 = QWidget()
        f2 = QFormLayout(w2)
        hint = QLabel("Зареєструватися можна лише на email, для якого адміністратор створив "
                      "запрошення. На пошту прийде код підтвердження.")
        hint.setWordWrap(True)
        hint.setObjectName("hint")
        self.r_name = QLineEdit()
        self.r_email = QLineEdit()
        self.r_pw = QLineEdit()
        self.r_pw.setEchoMode(QLineEdit.Password)
        self.r_pw2 = QLineEdit()
        self.r_pw2.setEchoMode(QLineEdit.Password)
        b_reg = QPushButton("Зареєструватися")
        b_reg.setObjectName("primary")
        b_reg.clicked.connect(self.do_signup)
        f2.addRow(hint)
        f2.addRow("Ім'я", self.r_name)
        f2.addRow("Email", self.r_email)
        f2.addRow("Пароль", self.r_pw)
        f2.addRow("Ще раз", self.r_pw2)
        f2.addRow(b_reg)
        tabs.addTab(w2, "Реєстрація за запрошенням")
        self.tabs = tabs
        return tabs

    def _page_confirm(self):
        w = QWidget()
        v = QVBoxLayout(w)
        head = QLabel("✉️  Підтвердіть email")
        head.setStyleSheet("font-size: 16px; font-weight: 700;")
        self.c_info = QLabel()
        self.c_info.setWordWrap(True)
        self.c_code = CodeEdit()
        self.c_code.returnPressed.connect(self.do_confirm)
        b_ok = QPushButton("Підтвердити")
        b_ok.setObjectName("primary")
        b_ok.clicked.connect(self.do_confirm)
        b_again = QPushButton("Надіслати код ще раз")
        b_again.clicked.connect(self.do_resend)
        b_back = QPushButton("← Назад")
        b_back.setFlat(True)
        b_back.clicked.connect(self.back)
        row = QHBoxLayout()
        row.addWidget(b_back)
        row.addStretch()
        row.addWidget(b_again)
        row.addWidget(b_ok)
        v.addWidget(head)
        v.addWidget(self.c_info)
        v.addWidget(self.c_code)
        v.addStretch()
        v.addLayout(row)
        return w

    def _page_reset(self):
        w = QWidget()
        v = QVBoxLayout(w)
        head = QLabel("🔑  Зміна пароля")
        head.setStyleSheet("font-size: 16px; font-weight: 700;")
        step1 = QLabel("1. Вкажіть email — надішлемо код.")
        self.x_email = QLineEdit()
        self.x_email.setPlaceholderText("you@example.com")
        self.x_send = QPushButton("Надіслати код")
        self.x_send.clicked.connect(self.do_send_reset)
        r1 = QHBoxLayout()
        r1.addWidget(self.x_email, 1)
        r1.addWidget(self.x_send)
        step2 = QLabel("2. Введіть код із листа і новий пароль.")
        self.x_code = CodeEdit()
        self.x_pw = QLineEdit()
        self.x_pw.setEchoMode(QLineEdit.Password)
        self.x_pw.setPlaceholderText("новий пароль (мінімум 6 символів)")
        self.x_pw2 = QLineEdit()
        self.x_pw2.setEchoMode(QLineEdit.Password)
        self.x_pw2.setPlaceholderText("ще раз")
        self.x_pw2.returnPressed.connect(self.do_reset)
        b_ok = QPushButton("Змінити пароль і увійти")
        b_ok.setObjectName("primary")
        b_ok.clicked.connect(self.do_reset)
        b_back = QPushButton("← Назад")
        b_back.setFlat(True)
        b_back.clicked.connect(self.back)
        row = QHBoxLayout()
        row.addWidget(b_back)
        row.addStretch()
        row.addWidget(b_ok)
        self.step2 = [step2, self.x_code, self.x_pw, self.x_pw2, b_ok]
        for x in (head, step1):
            v.addWidget(x)
        v.addLayout(r1)
        v.addSpacing(8)
        for x in (step2, self.x_code, self.x_pw, self.x_pw2):
            v.addWidget(x)
        v.addStretch()
        v.addLayout(row)
        self._step2(False)
        return w

    def _step2(self, on):
        for x in self.step2:
            x.setEnabled(on)

    # ---------- службове
    def show_msg(self, text, ok=False):
        color = ("#166534", "#DCFCE7") if ok else ("#991B1B", "#FEE2E2")
        self.msg.setStyleSheet(f"color:{color[0]}; background:{color[1]}; padding:8px 10px; "
                               "border-radius:8px;")
        self.msg.setText(text)
        self.msg.show()

    def back(self):
        self.msg.hide()
        self.pages.setCurrentIndex(0)
        self.b_local.show()

    def open_confirm(self, email, sent=True):
        self.pending_email = email.strip().lower()
        self.c_info.setText(
            f"Ми надіслали код на <b>{html.escape(self.pending_email)}</b>. Введіть його нижче. "
            "Лист не прийшов? Перевірте «Спам» або натисніть «Надіслати код ще раз»."
            if sent else
            f"Email <b>{html.escape(self.pending_email)}</b> ще не підтверджено. "
            "Натисніть «Надіслати код ще раз» і введіть код із листа.")
        self.c_code.clear()
        self.msg.hide()
        self.b_local.hide()
        self.pages.setCurrentIndex(1)
        self.c_code.setFocus()

    def open_reset(self):
        self.x_email.setText(self.email.text())
        self.x_code.clear()
        self.x_pw.clear()
        self.x_pw2.clear()
        self._step2(False)
        self.msg.hide()
        self.b_local.hide()
        self.pages.setCurrentIndex(2)
        self.x_email.setFocus()

    # ---------- дії
    def do_login(self):
        if not self.email.text().strip() or not self.pw.text():
            self.show_msg("Введіть email і пароль.")
            return
        try:
            _busy(lambda: self.cloud.login(self.email.text(), self.pw.text()))
        except Exception as e:  # noqa: BLE001
            if "not confirmed" in str(e).lower():
                self.open_confirm(self.email.text(), sent=False)
                return
            self.show_msg(friendly_error(e))
            return
        self.accept()

    def do_signup(self):
        if not self.r_email.text().strip() or not self.r_pw.text():
            self.show_msg("Заповніть email і пароль.")
            return
        if self.r_pw.text() != self.r_pw2.text():
            self.show_msg("Паролі не збігаються.")
            return
        if len(self.r_pw.text()) < 6:
            self.show_msg("Пароль має бути не коротшим за 6 символів.")
            return
        try:
            res = _busy(lambda: self.cloud.signup(self.r_email.text(), self.r_pw.text(),
                                                  self.r_name.text()))
        except Exception as e:  # noqa: BLE001
            self.show_msg(friendly_error(e))
            return
        if res == "ok":
            self.accept()
        else:
            self.email.setText(self.r_email.text())
            self.open_confirm(self.r_email.text())

    def do_confirm(self):
        if len(self.c_code.text()) < 6:
            self.show_msg("Введіть код із листа.")
            return
        try:
            _busy(lambda: self.cloud.verify_code(self.pending_email, self.c_code.text(), "signup"))
        except Exception as e:  # noqa: BLE001
            self.show_msg(friendly_error(e))
            return
        self.accept()

    def do_resend(self):
        try:
            _busy(lambda: self.cloud.resend_signup_code(self.pending_email))
            self.show_msg("Новий код надіслано.", ok=True)
        except Exception as e:  # noqa: BLE001
            self.show_msg(friendly_error(e))

    def do_send_reset(self):
        email = self.x_email.text().strip()
        if "@" not in email:
            self.show_msg("Вкажіть email.")
            return
        try:
            _busy(lambda: self.cloud.reset_password(email))
        except Exception as e:  # noqa: BLE001
            self.show_msg(friendly_error(e))
            return
        self.show_msg("Якщо такий користувач є, код уже в пошті (перевірте й «Спам»).", ok=True)
        self.x_send.setText("Надіслати ще раз")
        self._step2(True)
        self.x_code.setFocus()

    def do_reset(self):
        if len(self.x_code.text()) < 6:
            self.show_msg("Введіть код із листа.")
            return
        if len(self.x_pw.text()) < 6:
            self.show_msg("Пароль має бути не коротшим за 6 символів.")
            return
        if self.x_pw.text() != self.x_pw2.text():
            self.show_msg("Паролі не збігаються.")
            return
        try:
            _busy(lambda: self.cloud.verify_code(self.x_email.text(), self.x_code.text(), "recovery"))
            _busy(lambda: self.cloud.set_password(self.x_pw.text()))
        except Exception as e:  # noqa: BLE001
            self.show_msg(friendly_error(e))
            return
        QMessageBox.information(self, "Пароль", "Пароль змінено. Ви увійшли в систему.")
        self.accept()


class ConnectDialog(QDialog):
    def __init__(self, parent=None, url="", key=""):
        super().__init__(parent)
        self.setWindowTitle("Підключення до хмари")
        self.setMinimumWidth(520)
        info = QLabel(
            "Дані проєкту Supabase: <b>Project Settings → API</b>.<br>"
            "Потрібні <b>Project URL</b> і ключ <b>anon public</b>.<br>"
            "<span style='color:#B91C1C'>Ключ service_role сюди не вводьте.</span>")
        info.setWordWrap(True)
        self.url = QLineEdit(url)
        self.url.setPlaceholderText("https://xxxxxxxx.supabase.co")
        self.key = QLineEdit(key)
        self.key.setPlaceholderText("eyJhbGciOi…")
        form = QFormLayout()
        form.addRow("Project URL", self.url)
        form.addRow("anon key", self.key)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setText("Підключити")
        bb.button(QDialogButtonBox.Save).setObjectName("primary")
        bb.button(QDialogButtonBox.Cancel).setText("Скасувати")
        bb.accepted.connect(self.check)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(info)
        lay.addLayout(form)
        lay.addWidget(bb)

    def values(self):
        return self.url.text().strip().rstrip("/"), self.key.text().strip()

    def check(self):
        url, key = self.values()
        if not url.startswith("https://") or not key:
            QMessageBox.warning(self, "Хмара", "Вкажіть адресу, що починається з https://, і ключ.")
            return
        if '"role":"service_role"' in _jwt_payload(key).replace(" ", ""):
            QMessageBox.critical(self, "Хмара", "Це ключ service_role — він дає повний доступ "
                                                "в обхід усіх прав. Використайте ключ anon public.")
            return
        self.accept()


def _jwt_payload(token):
    import base64
    try:
        part = token.split(".")[1]
        return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)).decode()
    except Exception:  # noqa: BLE001
        return ""


class CategoryPicker(QDialog):
    """Вибір категорій галочками (доступ поширюється на підкатегорії)."""

    def __init__(self, db, selected, parent=None, title="Доступні категорії"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(380, 460)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        children = {}
        for c in db.categories():
            children.setdefault(c["parent_id"], []).append(c)
        self.items = {}

        def add(parent, pid):
            for c in children.get(pid, []):
                n = QTreeWidgetItem([c["name"]])
                n.setFlags(n.flags() | Qt.ItemIsUserCheckable)
                n.setCheckState(0, Qt.Checked if c["id"] in selected else Qt.Unchecked)
                n.setData(0, Qt.UserRole, c["id"])
                (parent.addChild(n) if parent else self.tree.addTopLevelItem(n))
                self.items[c["id"]] = n
                add(n, c["id"])

        add(None, None)
        self.tree.expandAll()
        hint = QLabel("Доступ до категорії відкриває і всі її підкатегорії.")
        hint.setObjectName("hint")
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(hint)
        lay.addWidget(self.tree)
        lay.addWidget(bb)

    def selected(self):
        return [cid for cid, n in self.items.items() if n.checkState(0) == Qt.Checked]


class UsersDialog(QDialog):
    """Адміністрування: користувачі, ролі, доступ до категорій, запрошення."""

    def __init__(self, cloud, db, parent=None):
        super().__init__(parent)
        self.cloud, self.db = cloud, db
        self.setWindowTitle("Користувачі й доступ")
        self.resize(900, 560)
        self.cat_paths = db.category_paths()

        tabs = QTabWidget()
        # --- користувачі
        self.users = QTableWidget(0, 6)
        self.users.setHorizontalHeaderLabels(["Ім'я", "Email", "Роль", "Усі категорії",
                                              "Доступ до категорій", "Активний"])
        self.users.verticalHeader().setVisible(False)
        self.users.verticalHeader().setDefaultSectionSize(44)
        self.users.setSelectionMode(QAbstractItemView.NoSelection)
        self.users.setEditTriggers(QAbstractItemView.NoEditTriggers)
        h = self.users.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.Fixed)
        self.users.setColumnWidth(2, 190)
        h.setSectionResizeMode(4, QHeaderView.Stretch)
        tabs.addTab(self.users, "👥  Користувачі")

        # --- запрошення
        w = QWidget()
        v = QVBoxLayout(w)
        form = QFormLayout()
        self.i_email = QLineEdit()
        self.i_email.setPlaceholderText("email людини")
        self.i_name = QLineEdit()
        self.i_role = QComboBox()
        for k, name in ROLES.items():
            self.i_role.addItem(f"{name} — {ROLE_HINTS[k]}", k)
        self.i_role.setCurrentIndex(list(ROLES).index("viewer"))
        self.i_all = QCheckBox("Доступ до всіх категорій")
        self.i_cats = []
        self.i_cats_btn = QPushButton("Вибрати категорії…")
        self.i_cats_btn.clicked.connect(self.pick_invite_cats)
        self.i_all.toggled.connect(lambda on: self.i_cats_btn.setDisabled(on))
        b_inv = QPushButton("✉️  Запросити")
        b_inv.setObjectName("primary")
        b_inv.clicked.connect(self.do_invite)
        form.addRow("Email", self.i_email)
        form.addRow("Ім'я", self.i_name)
        form.addRow("Роль", self.i_role)
        form.addRow("", self.i_all)
        form.addRow("Категорії", self.i_cats_btn)
        form.addRow("", b_inv)
        v.addLayout(form)
        hint = QLabel("Після запрошення повідомте людині: встановити програму → «Реєстрація за "
                      "запрошенням» → вказати цей email. Лист автоматично не надсилається.")
        hint.setWordWrap(True)
        hint.setObjectName("hint")
        v.addWidget(hint)
        self.invites = QTableWidget(0, 4)
        self.invites.setHorizontalHeaderLabels(["Email", "Роль", "Категорії", ""])
        self.invites.verticalHeader().setVisible(False)
        self.invites.verticalHeader().setDefaultSectionSize(40)
        self.invites.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.invites.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.invites.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        v.addWidget(QLabel("<b>Очікують реєстрації</b>"))
        v.addWidget(self.invites, 1)
        tabs.addTab(w, "✉️  Запрошення")

        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.button(QDialogButtonBox.Close).setText("Закрити")
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(tabs)
        lay.addWidget(bb)
        self.reload()

    def _err(self, e):
        QMessageBox.warning(self, "Користувачі", friendly_error(e))

    def _cats_text(self, ids):
        names = [self.cat_paths.get(c, "?") for c in ids]
        return ", ".join(names) if names else "— нічого —"

    def reload(self):
        try:
            profiles, access, invites = _busy(lambda: (
                self.cloud.list_profiles(), self.cloud.all_access(), self.cloud.list_invitations()))
        except Exception as e:  # noqa: BLE001
            self._err(e)
            return
        me = self.cloud.user_id
        self.users.setRowCount(0)
        for p in profiles:
            r = self.users.rowCount()
            self.users.insertRow(r)
            self.users.setItem(r, 0, QTableWidgetItem(p.get("full_name") or "—"))
            self.users.setItem(r, 1, QTableWidgetItem(p["email"]))
            role = QComboBox()
            for k, name in ROLES.items():
                role.addItem(name, k)
            role.setCurrentIndex(list(ROLES).index(p["role"]))
            role.setDisabled(p["id"] == me)
            role.setToolTip("Свою роль змінити не можна" if p["id"] == me else "")
            role.currentIndexChanged.connect(
                lambda _i, uid=p["id"], cb=role: self.set_field(uid, role=cb.currentData()))
            self.users.setCellWidget(r, 2, role)
            all_cb = QCheckBox()
            all_cb.setChecked(bool(p["all_categories"]))
            all_cb.setDisabled(p["role"] == "admin")
            all_cb.toggled.connect(lambda on, uid=p["id"]: self.set_field(uid, all_categories=on))
            self.users.setCellWidget(r, 3, _center(all_cb))
            ids = access.get(p["id"], [])
            btn = QPushButton("усі" if p["all_categories"] or p["role"] == "admin"
                              else self._cats_text(ids))
            btn.setDisabled(bool(p["all_categories"]) or p["role"] == "admin")
            btn.clicked.connect(lambda _c=False, uid=p["id"], cur=ids: self.pick_user_cats(uid, cur))
            self.users.setCellWidget(r, 4, btn)
            act = QCheckBox()
            act.setChecked(bool(p["active"]))
            act.setDisabled(p["id"] == me)
            act.toggled.connect(lambda on, uid=p["id"]: self.set_field(uid, active=on))
            self.users.setCellWidget(r, 5, _center(act))
            if not p["active"]:
                for c in (0, 1):
                    self.users.item(r, c).setForeground(QColor("#9CA3AF"))

        self.invites.setRowCount(0)
        for inv in invites:
            r = self.invites.rowCount()
            self.invites.insertRow(r)
            self.invites.setItem(r, 0, QTableWidgetItem(inv["email"]))
            self.invites.setItem(r, 1, QTableWidgetItem(ROLES.get(inv["role"], inv["role"])))
            self.invites.setItem(r, 2, QTableWidgetItem(
                "усі" if inv["all_categories"] else self._cats_text(inv["category_ids"])))
            b = QPushButton("Скасувати")
            b.setObjectName("small")
            b.setMinimumWidth(100)
            b.clicked.connect(lambda _c=False, e=inv["email"]: self.cancel_invite(e))
            self.invites.setCellWidget(r, 3, b)

    def set_field(self, uid, **fields):
        try:
            _busy(lambda: self.cloud.update_profile(uid, **fields))
        except Exception as e:  # noqa: BLE001
            self._err(e)
        QTimer.singleShot(0, self.reload)

    def pick_user_cats(self, uid, current):
        dlg = CategoryPicker(self.db, set(current), self)
        if dlg.exec():
            try:
                _busy(lambda: self.cloud.set_access(uid, dlg.selected()))
            except Exception as e:  # noqa: BLE001
                self._err(e)
            self.reload()

    def pick_invite_cats(self):
        dlg = CategoryPicker(self.db, set(self.i_cats), self)
        if dlg.exec():
            self.i_cats = dlg.selected()
            self.i_cats_btn.setText(self._cats_text(self.i_cats) if self.i_cats
                                    else "Вибрати категорії…")

    def do_invite(self):
        email = self.i_email.text().strip().lower()
        if "@" not in email:
            QMessageBox.warning(self, "Запрошення", "Вкажіть коректний email.")
            return
        role = self.i_role.currentData()
        all_cats = self.i_all.isChecked() or role == "admin"
        if not all_cats and not self.i_cats:
            if QMessageBox.question(self, "Запрошення",
                                    "Не вибрано жодної категорії — людина нічого не побачить. "
                                    "Все одно запросити?") != QMessageBox.Yes:
                return
        try:
            _busy(lambda: self.cloud.invite(email, self.i_name.text(), role, all_cats, self.i_cats))
        except Exception as e:  # noqa: BLE001
            self._err(e)
            return
        QMessageBox.information(
            self, "Запрошення",
            f"Запрошення для {email} створено.\n\nПовідомте людині: встановити програму → "
            f"«Реєстрація за запрошенням» → email {email}.")
        self.i_email.clear()
        self.i_name.clear()
        self.i_cats = []
        self.i_cats_btn.setText("Вибрати категорії…")
        self.reload()

    def cancel_invite(self, email):
        try:
            _busy(lambda: self.cloud.delete_invitation(email))
        except Exception as e:  # noqa: BLE001
            self._err(e)
        self.reload()


def _center(w):
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(w, 0, Qt.AlignCenter)
    return box


# ====================================================================== історія
def history_html(rows, names, cat_paths):
    if not rows:
        return "<p style='color:#6B7280'>Історії ще немає.</p>"
    out = []
    for r in rows:
        when = r["at"].replace("T", " ")[:16]
        who = html.escape(names.get(r["user_id"], "система"))
        if r["action"] == "created":
            what = "створив(ла) запис"
        elif r["action"] == "deleted":
            what = "видалив(ла)"
        else:
            parts = []
            for k, (old, new) in (r.get("changes") or {}).items():
                label = FIELD_NAMES.get(k, k)
                if k == "category_id":
                    old, new = cat_paths.get(old, old), cat_paths.get(new, new)
                if k == "specs":
                    parts.append(f"<i>{label}</i>")
                    continue
                o = html.escape(str(old)) if old not in (None, "") else "—"
                n = html.escape(str(new)) if new not in (None, "") else "—"
                parts.append(f"<i>{label}</i>: {o} → <b>{n}</b>")
            what = "; ".join(parts) or "оновив(ла)"
        out.append(f"<p style='margin:0 0 8px 0'><span style='color:#6B7280'>{when}</span> · "
                   f"<b>{who}</b><br>{what}</p>")
    return "".join(out)
