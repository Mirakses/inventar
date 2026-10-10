"""Облік майна — десктопна програма (PySide6 / Qt). Запуск: python app.py"""
import html
import os
import sys
from pathlib import Path

from PySide6.QtCore import (Qt, QItemSelectionModel, QRectF, QSize, QSortFilterProxyModel,
                            QStandardPaths, QUrl, QDate, Signal)
from PySide6.QtGui import (QAction, QColor, QDesktopServices, QFont, QIcon, QKeySequence, QPainter,
                           QPalette, QPixmap, QStandardItem, QStandardItemModel, QImageReader)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QCompleter, QDateEdit, QDialog,
    QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu,
    QMessageBox, QPlainTextEdit, QPushButton, QSizePolicy, QSpinBox, QSplitter, QStackedWidget, QStyle,
    QStyledItemDelegate, QStyleOptionViewItem, QTableView, QTableWidget, QTableWidgetItem,
    QTabWidget, QTextBrowser, QToolBar, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from db import ALL, NONE, IMAGE_EXT, Database

APP_NAME = "Облік майна"
STATUSES = ["В користуванні", "На складі", "У ремонті", "Позичено", "Списано"]

# ---------------------------------------------------------------- оформлення
ACCENT = "#4F46E5"
STATUS_COLORS = {  # фон, текст
    "В користуванні": ("#DCFCE7", "#166534"),
    "На складі": ("#DBEAFE", "#1E40AF"),
    "У ремонті": ("#FEF3C7", "#92400E"),
    "Позичено": ("#F3E8FF", "#6B21A8"),
    "Списано": ("#F3F4F6", "#6B7280"),
}
CAT_COLORS = ["#6366F1", "#EC4899", "#F59E0B", "#10B981", "#06B6D4",
              "#F43F5E", "#8B5CF6", "#84CC16", "#0EA5E9", "#F97316"]

STYLE = """
QWidget { font-size: 14px; color: #1F2937; }
QMainWindow, QDialog { background: #F4F6FB; }

QToolBar { background: #FFFFFF; border: none; border-bottom: 1px solid #E5E7EB;
           padding: 8px 10px; spacing: 4px; }
QToolBar QToolButton { background: transparent; border: none; border-radius: 9px;
                       padding: 8px 12px; }
QToolBar QToolButton:hover { background: #EEF2FF; color: #4338CA; }
QToolBar QToolButton#primary { background: #4F46E5; color: white; font-weight: 600;
                               padding: 8px 16px; }
QToolBar QToolButton#primary:hover { background: #4338CA; }
QToolBar QToolButton#danger:hover { background: #FEE2E2; color: #B91C1C; }

QTreeWidget, QTableView, QTextBrowser, QListWidget {
    background: #FFFFFF; border: 1px solid #E5E7EB; border-radius: 14px; }
QTreeWidget { padding: 6px; outline: 0; show-decoration-selected: 1; }
QTreeWidget::item { padding: 7px 4px; }
QTreeWidget::item:hover:!selected { background: #F3F4F6; }
QTreeWidget::item:selected, QTreeWidget::branch:selected { background: #E0E7FF; color: #3730A3; }

QLineEdit#search { border: 2px solid #E5E7EB; border-radius: 14px; padding: 10px 14px;
                   background: #FFFFFF; font-size: 15px; }
QLineEdit#search:focus { border-color: #818CF8; }

QTableView { gridline-color: transparent; outline: 0;
             selection-background-color: #E0E7FF; selection-color: #1F2937;
             alternate-background-color: #FAFAFF; }
QTableView::item { padding: 2px 6px; border-bottom: 1px solid #F1F2F6; }
QTableView::item:selected { background: #E0E7FF; color: #1F2937; }
QHeaderView::section { background: #FFFFFF; color: #6B7280; font-weight: 600; font-size: 13px;
                       border: none; border-bottom: 2px solid #EEF0F5; padding: 10px 6px; }
QTableCornerButton::section { background: #FFFFFF; border: none; }

QLabel#cover { background: #FFFFFF; border: 1px solid #E5E7EB; border-radius: 14px;
               color: #9CA3AF; font-size: 15px; }
QLabel#emptyIcon { font-size: 64px; }
QLabel#emptyTitle { font-size: 20px; font-weight: 700; color: #111827; }
QLabel#emptyText { font-size: 14px; color: #6B7280; }
QLabel#hint { color: #6B7280; }
QWidget#emptyCard { background: #FFFFFF; border: 2px dashed #C7D2FE; border-radius: 18px; }

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QPlainTextEdit {
    background: #FFFFFF; border: 1px solid #D1D5DB; border-radius: 8px; padding: 6px 8px;
    selection-background-color: #C7D2FE; selection-color: #111827; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QDateEdit:focus,
QPlainTextEdit:focus { border: 1px solid #6366F1; }

QComboBox, QDateEdit { padding-right: 28px; }
QComboBox::drop-down, QDateEdit::drop-down { subcontrol-origin: padding; subcontrol-position: center right;
    width: 26px; border: none; background: transparent; }
QComboBox::down-arrow, QDateEdit::down-arrow { image: url(@DOWN@); width: 12px; height: 12px; }
QSpinBox, QDoubleSpinBox { padding-right: 26px; }
QSpinBox::up-button, QDoubleSpinBox::up-button { subcontrol-origin: border; subcontrol-position: top right;
    width: 24px; border: none; border-top-right-radius: 8px; background: transparent; }
QSpinBox::down-button, QDoubleSpinBox::down-button { subcontrol-origin: border;
    subcontrol-position: bottom right; width: 24px; border: none; border-bottom-right-radius: 8px;
    background: transparent; }
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover { background: #EEF2FF; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { image: url(@UP@); width: 10px; height: 10px; }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { image: url(@DOWN@); width: 10px; height: 10px; }
QComboBox QAbstractItemView { background: #FFFFFF; border: 1px solid #E5E7EB; padding: 4px;
    selection-background-color: #EEF2FF; selection-color: #3730A3; outline: 0; }
QCalendarWidget QWidget { alternate-background-color: #F4F6FB; }
QCalendarWidget QToolButton { color: #1F2937; background: transparent; padding: 4px 8px; }
QPushButton#small { padding: 6px 8px; }

QPushButton { background: #FFFFFF; border: 1px solid #D1D5DB; border-radius: 9px;
              padding: 7px 14px; }
QPushButton:hover { background: #EEF2FF; border-color: #A5B4FC; color: #3730A3; }
QPushButton#primary { background: #4F46E5; color: white; border: none; font-weight: 600;
                      padding: 9px 20px; }
QPushButton#primary:hover { background: #4338CA; color: white; }

QTabWidget::pane { border: 1px solid #E5E7EB; border-radius: 14px; background: #FFFFFF;
                   padding: 10px; top: -1px; }
QTabBar::tab { background: transparent; color: #6B7280; padding: 8px 18px; margin: 0 4px 6px 0;
               border-radius: 9px; font-weight: 600; }
QTabBar::tab:hover { background: #EEF2FF; color: #3730A3; }
QTabBar::tab:selected { background: #4F46E5; color: white; }

QStatusBar { background: #FFFFFF; border-top: 1px solid #E5E7EB; color: #4B5563; }
QStatusBar QLabel { padding: 4px 10px; }
QSplitter::handle { background: transparent; }

QMenu { background: #FFFFFF; border: 1px solid #E5E7EB; border-radius: 10px; padding: 5px; }
QMenu::item { padding: 7px 22px; border-radius: 7px; }
QMenu::item:selected { background: #EEF2FF; color: #3730A3; }
QMenu::separator { height: 1px; background: #E5E7EB; margin: 4px 8px; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 4px 2px; }
QScrollBar::handle:vertical { background: #D1D5DB; border-radius: 3px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #A5B4FC; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px 4px; }
QScrollBar::handle:horizontal { background: #D1D5DB; border-radius: 3px; min-width: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QToolTip { background: #111827; color: white; border: none; padding: 6px 8px; border-radius: 6px; }
"""


def build_style():
    """Малює стрілки у тимчасову папку і підставляє шляхи в STYLE (потрібен QApplication)."""
    import tempfile
    d = Path(tempfile.gettempdir()) / "inventar_ui"
    d.mkdir(exist_ok=True)
    paths = {}
    for name, pts in (("down", [(3, 6), (8, 11), (13, 6)]), ("up", [(3, 10), (8, 5), (13, 10)])):
        pm = QPixmap(32, 32)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        pen = p.pen()
        pen.setColor(QColor("#6B7280"))
        pen.setWidthF(4)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            p.drawLine(x1 * 2, y1 * 2, x2 * 2, y2 * 2)
        p.end()
        f = d / f"{name}.png"
        pm.save(str(f))
        paths[name] = f.as_posix()
    return STYLE.replace("@DOWN@", paths["down"]).replace("@UP@", paths["up"])


def light_palette():
    """Фіксована світла палітра, щоб темна тема macOS/Windows не ламала кольори."""
    p = QPalette()
    for role, color in ((QPalette.Window, "#F4F6FB"), (QPalette.WindowText, "#1F2937"),
                        (QPalette.Base, "#FFFFFF"), (QPalette.AlternateBase, "#FAFAFF"),
                        (QPalette.Text, "#1F2937"), (QPalette.Button, "#FFFFFF"),
                        (QPalette.ButtonText, "#1F2937"), (QPalette.Highlight, "#6366F1"),
                        (QPalette.HighlightedText, "#FFFFFF"), (QPalette.ToolTipBase, "#111827"),
                        (QPalette.ToolTipText, "#FFFFFF"), (QPalette.PlaceholderText, "#9CA3AF"),
                        (QPalette.BrightText, "#FFFFFF"), (QPalette.Link, ACCENT)):
        p.setColor(role, QColor(color))
    return p


def cat_color(cat_id):
    return CAT_COLORS[(cat_id or 0) % len(CAT_COLORS)]


def dot_icon(color, size=12):
    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawRoundedRect(QRectF(4, 4, size * 2 - 8, size * 2 - 8), 6, 6)
    p.end()
    return QIcon(pm)


def letter_avatar(text, color, size=80):
    """Кольоровий квадратик з першою літерою — для речей без фото."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    bg = QColor(color)
    bg.setAlpha(45)
    p.setPen(Qt.NoPen)
    p.setBrush(bg)
    p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.25, size * 0.25)
    f = QFont()
    f.setPixelSize(int(size * 0.45))
    f.setBold(True)
    p.setFont(f)
    p.setPen(QColor(color))
    p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, (text or "?").strip()[:1].upper())
    p.end()
    return pm


def rounded(pm, radius=10):
    if pm.isNull():
        return pm
    out = QPixmap(pm.size())
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(pm)
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(out.rect()), radius, radius)
    p.end()
    return out


class PillDelegate(QStyledItemDelegate):
    """Малює стан речі як кольорову «пігулку»."""

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text = opt.text
        opt.text = ""
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)
        if not text:
            return
        bg, fg = STATUS_COLORS.get(text, ("#E0F2FE", "#075985"))
        fm = opt.fontMetrics
        h = fm.height() + 8
        w = min(fm.horizontalAdvance(text) + 22, opt.rect.width() - 12)
        r = QRectF(opt.rect.x() + 6, opt.rect.center().y() - h / 2 + 1, w, h)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(bg))
        painter.drawRoundedRect(r, h / 2, h / 2)
        f = QFont(opt.font)
        f.setBold(True)
        f.setPixelSize(12)
        painter.setFont(f)
        painter.setPen(QColor(fg))
        painter.drawText(r, Qt.AlignCenter,
                         painter.fontMetrics().elidedText(text, Qt.ElideRight, int(w) - 12))
        painter.restore()
SORT_ROLE = Qt.UserRole + 1
ID_ROLE = Qt.UserRole + 2
EMPTY_DATE = QDate(1900, 1, 1)

QImageReader.setAllocationLimit(512)  # дозволяє відкривати великі фото з телефона


# ---------------------------------------------------------------- утиліти
_thumb_cache = {}


def thumbnail(path, size):
    key = (str(path), size)
    if key not in _thumb_cache:
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)  # враховує EXIF-поворот
        img = reader.read()
        pm = QPixmap.fromImage(img) if not img.isNull() else QPixmap()
        if not pm.isNull():
            pm = pm.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        _thumb_cache[key] = pm
    return _thumb_cache[key]


def fmt_price(v):
    return "" if v is None else f"{v:,.2f}".replace(",", " ")


def image_filter():
    return "Зображення (" + " ".join(f"*{e}" for e in sorted(IMAGE_EXT)) + ")"


class PhotoList(QListWidget):
    """Список мініатюр, приймає перетягування файлів з провідника."""
    filesDropped = Signal(list)

    def __init__(self, size=110, parent=None):
        super().__init__(parent)
        self.setViewMode(QListWidget.IconMode)
        self.setIconSize(QSize(size, size))
        self.setResizeMode(QListWidget.Adjust)
        self.setMovement(QListWidget.Static)
        self.setSpacing(4)
        self.setAcceptDrops(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e):
        if e.mimeData().hasUrls():
            files = [u.toLocalFile() for u in e.mimeData().urls()
                     if u.isLocalFile() and Path(u.toLocalFile()).suffix.lower() in IMAGE_EXT]
            if files:
                self.filesDropped.emit(files)
            e.acceptProposedAction()
        else:
            super().dropEvent(e)


def fill_category_combo(combo, db, with_none=True, none_text="— без категорії —"):
    combo.clear()
    if with_none:
        combo.addItem(none_text, None)
    cats = db.categories()
    children = {}
    for c in cats:
        children.setdefault(c["parent_id"], []).append(c)

    def add(parent, depth):
        for c in children.get(parent, []):
            combo.addItem("    " * depth + c["name"], c["id"])
            add(c["id"], depth + 1)

    add(None, 0)


# ---------------------------------------------------------------- діалог речі
class ItemDialog(QDialog):
    def __init__(self, db, item_id=None, default_category=None, parent=None):
        super().__init__(parent)
        self.db = db
        self.item_id = item_id
        self.setWindowTitle("Нова річ" if item_id is None else "Редагування")
        self.resize(720, 620)

        tabs = QTabWidget()

        # --- Основне
        main = QWidget()
        form = QFormLayout(main)
        form.setVerticalSpacing(10)
        form.setHorizontalSpacing(16)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Наприклад: Паяльна станція TS101")
        self.name.setStyleSheet("font-size: 16px; font-weight: 600; padding: 8px 10px;")
        self.category = QComboBox()
        fill_category_combo(self.category, db)
        self.inventory_no = QLineEdit()
        self.inventory_no.setPlaceholderText("необов'язково")
        self.serial_no = QLineEdit()
        self.serial_no.setPlaceholderText("необов'язково")
        self.location = QLineEdit()
        self.location.setPlaceholderText("Наприклад: Майстерня, шафа 2, полиця 3")
        self.location.setCompleter(QCompleter(db.distinct_values("location"), self))
        self.quantity = QSpinBox()
        self.quantity.setRange(0, 1_000_000)
        self.quantity.setValue(1)
        self.price = QDoubleSpinBox()
        self.price.setRange(0, 1e10)
        self.price.setDecimals(2)
        self.price.setSpecialValueText("—")
        self.price.setGroupSeparatorShown(True)
        self.date = QDateEdit()
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("dd.MM.yyyy")
        self.date.setMinimumDate(EMPTY_DATE)
        self.date.setSpecialValueText("—")
        self.date.setDate(EMPTY_DATE)
        today_btn = QPushButton("Сьогодні")
        today_btn.clicked.connect(lambda: self.date.setDate(QDate.currentDate()))
        clear_date = QPushButton("✕")
        clear_date.setObjectName("small")
        clear_date.setToolTip("Очистити дату")
        clear_date.setFixedWidth(36)
        clear_date.clicked.connect(lambda: self.date.setDate(EMPTY_DATE))
        date_row = QHBoxLayout()
        date_row.addWidget(self.date, 1)
        date_row.addWidget(today_btn)
        date_row.addWidget(clear_date)
        self.status = QComboBox()
        self.status.setEditable(True)
        self.status.addItems(list(dict.fromkeys(STATUSES + db.distinct_values("status"))))
        self.status.setCurrentText("")
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("Будь-які нотатки: де купив, гарантія, що варто пам'ятати…")

        form.addRow("Назва*", self.name)
        form.addRow("Категорія", self.category)
        form.addRow("Інвентарний №", self.inventory_no)
        form.addRow("Серійний №", self.serial_no)
        form.addRow("Розташування", self.location)
        form.addRow("Кількість", self.quantity)
        form.addRow("Ціна", self.price)
        form.addRow("Дата покупки", date_row)
        form.addRow("Стан", self.status)
        form.addRow("Примітки", self.notes)
        tabs.addTab(main, "📝  Основне")

        # --- Характеристики
        spec_w = QWidget()
        sv = QVBoxLayout(spec_w)
        self.specs = QTableWidget(0, 2)
        self.specs.setHorizontalHeaderLabels(["Параметр", "Значення"])
        self.specs.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.specs.horizontalHeader().setStretchLastSection(True)
        self.specs.verticalHeader().setVisible(False)
        self.specs.verticalHeader().setDefaultSectionSize(36)
        self.specs.setAlternatingRowColors(True)
        sb = QHBoxLayout()
        for text, slot in (("+ Рядок", self.add_spec_row), ("− Видалити", self.remove_spec_rows),
                           ("↑", lambda: self.move_spec(-1)), ("↓", lambda: self.move_spec(1)),
                           ("Підставити з категорії", self.fill_spec_keys)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            sb.addWidget(b)
        sb.addStretch()
        sv.addWidget(self.specs)
        sv.addLayout(sb)
        tabs.addTab(spec_w, "⚙️  Характеристики")

        # --- Фото
        ph_w = QWidget()
        pv = QVBoxLayout(ph_w)
        self.photos = PhotoList(120)
        self.photos.filesDropped.connect(self.add_photo_files)
        self.photos.itemDoubleClicked.connect(self.open_photo)
        pb = QHBoxLayout()
        for text, slot in (("Додати фото…", self.pick_photos), ("Видалити", self.remove_photos),
                           ("Зробити головним", self.make_cover)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            pb.addWidget(b)
        pb.addStretch()
        hint = QLabel("💡 Перетягніть фото прямо сюди з Finder / Провідника. Перше фото — головне.")
        hint.setObjectName("hint")
        pv.addWidget(hint)
        pv.addWidget(self.photos)
        pv.addLayout(pb)
        tabs.addTab(ph_w, "📷  Фото")

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("💾  Зберегти")
        buttons.button(QDialogButtonBox.Save).setObjectName("primary")
        buttons.button(QDialogButtonBox.Cancel).setText("Скасувати")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(tabs)
        lay.addWidget(buttons)

        if item_id is not None:
            self.load(item_id)
        else:
            self.set_category(default_category if default_category not in (ALL, NONE) else None)
            self.fill_spec_keys()
        self.name.setFocus()

    # --- завантаження
    def set_category(self, cat_id):
        idx = self.category.findData(cat_id)
        self.category.setCurrentIndex(max(idx, 0))

    def load(self, item_id):
        item, specs, photos = self.db.get_item(item_id)
        self.name.setText(item["name"])
        self.set_category(item["category_id"])
        self.inventory_no.setText(item["inventory_no"] or "")
        self.serial_no.setText(item["serial_no"] or "")
        self.location.setText(item["location"] or "")
        self.quantity.setValue(item["quantity"] or 0)
        self.price.setValue(item["price"] or 0)
        d = QDate.fromString(item["purchase_date"] or "", "yyyy-MM-dd")
        self.date.setDate(d if d.isValid() else EMPTY_DATE)
        self.status.setCurrentText(item["status"] or "")
        self.notes.setPlainText(item["notes"] or "")
        for s in specs:
            self.add_spec_row(s["key"], s["value"] or "")
        for p in photos:
            self._add_photo_item({"id": p["id"], "filename": p["filename"]},
                                 self.db.photo_path(p["filename"]))

    # --- характеристики
    def add_spec_row(self, key="", value=""):
        r = self.specs.rowCount()
        self.specs.insertRow(r)
        self.specs.setItem(r, 0, QTableWidgetItem(key if isinstance(key, str) else ""))
        self.specs.setItem(r, 1, QTableWidgetItem(value))
        if not key:
            self.specs.setCurrentCell(r, 0)
            self.specs.editItem(self.specs.item(r, 0))

    def remove_spec_rows(self):
        for r in sorted({i.row() for i in self.specs.selectedIndexes()}, reverse=True):
            self.specs.removeRow(r)

    def move_spec(self, d):
        r = self.specs.currentRow()
        t = r + d
        if r < 0 or not 0 <= t < self.specs.rowCount():
            return
        for c in range(2):
            a, b = self.specs.takeItem(r, c), self.specs.takeItem(t, c)
            self.specs.setItem(r, c, b)
            self.specs.setItem(t, c, a)
        self.specs.setCurrentCell(t, self.specs.currentColumn())

    def current_specs(self):
        out = []
        for r in range(self.specs.rowCount()):
            k = (self.specs.item(r, 0).text() if self.specs.item(r, 0) else "").strip()
            v = (self.specs.item(r, 1).text() if self.specs.item(r, 1) else "").strip()
            if k:
                out.append((k, v))
        return out

    def fill_spec_keys(self):
        cat = self.category.currentData()
        if cat is None:
            return
        existing = {k for k, _ in self.current_specs()}
        for k in self.db.spec_keys(cat):
            if k not in existing:
                r = self.specs.rowCount()
                self.specs.insertRow(r)
                self.specs.setItem(r, 0, QTableWidgetItem(k))
                self.specs.setItem(r, 1, QTableWidgetItem(""))

    # --- фото
    def _add_photo_item(self, data, path):
        it = QListWidgetItem(QIcon(thumbnail(path, 240)), "")
        it.setData(Qt.UserRole, data)
        it.setData(Qt.UserRole + 1, str(path))
        it.setToolTip(Path(path).name)
        self.photos.addItem(it)

    def add_photo_files(self, files):
        for f in files:
            self._add_photo_item({"source": f}, f)

    def pick_photos(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Виберіть фото", "", image_filter())
        self.add_photo_files(files)

    def remove_photos(self):
        for it in self.photos.selectedItems():
            self.photos.takeItem(self.photos.row(it))

    def make_cover(self):
        it = self.photos.currentItem()
        if it:
            self.photos.insertItem(0, self.photos.takeItem(self.photos.row(it)))
            self.photos.setCurrentRow(0)

    def open_photo(self, it):
        QDesktopServices.openUrl(QUrl.fromLocalFile(it.data(Qt.UserRole + 1)))

    # --- збереження
    def accept(self):
        name = self.name.text().strip()
        if not name:
            QMessageBox.warning(self, APP_NAME, "Вкажіть назву.")
            return
        d = self.date.date()
        fields = {
            "name": name,
            "category_id": self.category.currentData(),
            "inventory_no": self.inventory_no.text().strip() or None,
            "serial_no": self.serial_no.text().strip() or None,
            "location": self.location.text().strip() or None,
            "quantity": self.quantity.value(),
            "price": self.price.value() or None,
            "purchase_date": None if d == EMPTY_DATE else d.toString("yyyy-MM-dd"),
            "status": self.status.currentText().strip() or None,
            "notes": self.notes.toPlainText().strip() or None,
        }
        photos = [self.photos.item(i).data(Qt.UserRole) for i in range(self.photos.count())]
        try:
            self.item_id = self.db.save_item(self.item_id, fields, self.current_specs(), photos)
        except Exception as e:
            QMessageBox.critical(self, APP_NAME, f"Не вдалося зберегти:\n{e}")
            return
        super().accept()


# ---------------------------------------------------------------- головне вікно
COLUMNS = ["", "Назва", "Категорія", "Інв. №", "Розташування", "К-сть", "Ціна", "Стан", "Куплено"]


class MainWindow(QMainWindow):
    def __init__(self, db):
        super().__init__()
        self.db = db
        self.current_category = ALL
        self.setWindowTitle(APP_NAME)
        self.resize(1320, 800)

        # категорії
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(16)
        self.tree.setIconSize(QSize(14, 14))
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.category_menu)
        self.tree.currentItemChanged.connect(self.on_category_changed)
        self.tree.itemDoubleClicked.connect(lambda *_: self.rename_category())
        add_cat = QPushButton("＋  Нова категорія")
        add_cat.clicked.connect(lambda: self.add_category(None))
        cat_title = QLabel("Категорії")
        cat_title.setStyleSheet("font-size: 13px; font-weight: 700; color: #6B7280; padding: 4px 6px;")
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(8)
        lv.addWidget(cat_title)
        lv.addWidget(self.tree, 1)
        lv.addWidget(add_cat)

        # пошук + таблиця
        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText("🔍  Пошук: назва, номер, місце, характеристики…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.reload_items)

        self.model = QStandardItemModel(0, len(COLUMNS))
        self.model.setHorizontalHeaderLabels(COLUMNS)
        self.proxy = QSortFilterProxyModel()
        self.proxy.setSourceModel(self.model)
        self.proxy.setSortRole(SORT_ROLE)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(1, Qt.AscendingOrder)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(52)
        self.table.setIconSize(QSize(40, 40))
        self.table.setAlternatingRowColors(True)
        self.table.setItemDelegateForColumn(COLUMNS.index("Стан"), PillDelegate(self.table))
        hh = self.table.horizontalHeader()
        hh.setHighlightSections(False)
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setMinimumSectionSize(40)
        self.table.setColumnWidth(0, 56)
        for col, w in (("Категорія", 140), ("Інв. №", 80), ("Розташування", 130),
                       ("К-сть", 56), ("Ціна", 96), ("Стан", 136), ("Куплено", 96)):
            self.table.setColumnWidth(COLUMNS.index(col), w)
        # менш важливі колонки сховані; ПКМ по заголовку — показати/сховати
        for col in ("Інв. №", "Куплено"):
            self.table.setColumnHidden(COLUMNS.index(col), True)
        hh.setContextMenuPolicy(Qt.CustomContextMenu)
        hh.customContextMenuRequested.connect(self.header_menu)
        self.table.doubleClicked.connect(lambda *_: self.edit_item())
        self.table.selectionModel().selectionChanged.connect(self.show_details)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.item_menu)

        # заглушка, коли список порожній
        self.empty = QWidget()
        self.empty.setObjectName("emptyCard")
        self.empty.setAttribute(Qt.WA_StyledBackground, True)
        ev = QVBoxLayout(self.empty)
        ev.addStretch()
        self.empty_icon = QLabel("📦")
        self.empty_icon.setObjectName("emptyIcon")
        self.empty_title = QLabel()
        self.empty_title.setObjectName("emptyTitle")
        self.empty_text = QLabel()
        self.empty_text.setObjectName("emptyText")
        self.empty_text.setWordWrap(True)
        self.empty_btn = QPushButton("＋  Додати річ")
        self.empty_btn.setObjectName("primary")
        self.empty_btn.setCursor(Qt.PointingHandCursor)
        self.empty_btn.clicked.connect(self.add_item)
        for w in (self.empty_icon, self.empty_title, self.empty_text):
            w.setAlignment(Qt.AlignCenter)
            ev.addWidget(w)
        ev.addSpacing(12)
        ev.addWidget(self.empty_btn, 0, Qt.AlignCenter)
        ev.addStretch()

        self.stack = QStackedWidget()
        self.stack.addWidget(self.table)
        self.stack.addWidget(self.empty)

        center = QWidget()
        cv = QVBoxLayout(center)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(10)
        cv.addWidget(self.search)
        cv.addWidget(self.stack, 1)

        # деталі
        self.cover = QLabel()
        self.cover.setObjectName("cover")
        self.cover.setAlignment(Qt.AlignCenter)
        self.cover.setMinimumHeight(240)
        self.cover.setCursor(Qt.PointingHandCursor)
        self.cover.setToolTip("Клік — відкрити фото")
        self.cover.mousePressEvent = lambda e: self.open_cover()
        self.details = QTextBrowser()
        self.details.setOpenExternalLinks(False)
        self.details.document().setDocumentMargin(14)
        self.strip = PhotoList(56)
        self.strip.setMaximumHeight(84)
        self.strip.setFlow(QListWidget.LeftToRight)
        self.strip.setWrapping(False)
        self.strip.setAcceptDrops(True)
        self.strip.filesDropped.connect(self.drop_photos_on_item)
        self.strip.currentItemChanged.connect(self.strip_selected)
        self.strip.itemDoubleClicked.connect(
            lambda it: QDesktopServices.openUrl(QUrl.fromLocalFile(it.data(Qt.UserRole))))
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(10)
        rv.addWidget(self.cover)
        rv.addWidget(self.strip)
        rv.addWidget(self.details, 1)

        split = QSplitter()
        split.setHandleWidth(14)
        split.setChildrenCollapsible(False)
        split.addWidget(left)
        split.addWidget(center)
        split.addWidget(right)
        split.setSizes([220, 800, 320])
        split.setStretchFactor(1, 1)
        wrap = QWidget()
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(14, 14, 14, 10)
        wl.addWidget(split)
        self.setCentralWidget(wrap)

        self.count_label = QLabel()
        self.total_label = QLabel()
        self.statusBar().addWidget(self.count_label)
        self.statusBar().addPermanentWidget(self.total_label)

        self.build_toolbar()
        self.reload_categories()
        self.show_details()

    # --- тулбар
    def build_toolbar(self):
        tb = QToolBar()
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.addToolBar(tb)

        def act(text, slot, shortcut=None, tip=None, name=None):
            a = QAction(text, self)
            a.triggered.connect(slot)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            a.setToolTip(tip or text)
            tb.addAction(a)
            w = tb.widgetForAction(a)
            w.setCursor(Qt.PointingHandCursor)
            if name:
                w.setObjectName(name)
            return a

        act("＋  Додати річ", self.add_item, "Ctrl+N", "Додати нову річ (Ctrl+N)", "primary")
        tb.addSeparator()
        act("✏️  Редагувати", self.edit_item, "Ctrl+E", "Редагувати вибрану річ (Ctrl+E)")
        act("📄  Копія", self.duplicate_item, "Ctrl+D", "Створити копію (Ctrl+D)")
        act("🗑  Видалити", self.delete_items, QKeySequence.Delete, "Видалити вибрані (Delete)",
            "danger")
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        act("📊  Експорт CSV", self.export_csv, tip="Зберегти поточний список у CSV для Excel")
        act("💾  Резервна копія", self.backup, tip="Зберегти базу і всі фото в один ZIP")
        act("📁  Папка даних", lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(self.db.data_dir))), tip="Відкрити папку з базою та фото")
        find = QAction(self)
        find.setShortcut(QKeySequence.Find)
        find.triggered.connect(lambda: (self.search.setFocus(), self.search.selectAll()))
        self.addAction(find)

    # --- категорії
    def _tree_node(self, text, count, data, icon=None, gray=False):
        n = QTreeWidgetItem([text, str(count)])
        n.setData(0, Qt.UserRole, data)
        n.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
        n.setForeground(1, QColor("#9CA3AF"))
        if icon:
            n.setIcon(0, icon)
        if gray:
            n.setForeground(0, QColor("#6B7280"))
        return n

    def reload_categories(self):
        keep = self.current_category
        self.tree.blockSignals(True)
        self.tree.clear()
        root = self._tree_node("📦  Усе майно", self.db.count_items(ALL), ALL)
        f = QFont()
        f.setPixelSize(14)
        f.setBold(True)
        root.setFont(0, f)
        self.tree.addTopLevelItem(root)
        children = {}
        for c in self.db.categories():
            children.setdefault(c["parent_id"], []).append(c)
        nodes = {}

        def add(parent_node, parent_id):
            for c in children.get(parent_id, []):
                n = self._tree_node(c["name"], self.db.count_items(c["id"]), c["id"],
                                    dot_icon(cat_color(c["id"])))
                parent_node.addChild(n)
                nodes[c["id"]] = n
                add(n, c["id"])

        add(root, None)
        none = self._tree_node("🗂  Без категорії", self.db.count_items(NONE), NONE, gray=True)
        self.tree.addTopLevelItem(none)
        self.tree.expandAll()
        target = {ALL: root, NONE: none}.get(keep) or nodes.get(keep) or root
        self.tree.setCurrentItem(target)
        self.current_category = target.data(0, Qt.UserRole)
        self.tree.blockSignals(False)
        self.reload_items()

    def on_category_changed(self, cur, _prev):
        if cur is not None:
            self.current_category = cur.data(0, Qt.UserRole)
            self.reload_items()

    def category_menu(self, pos):
        node = self.tree.itemAt(pos)
        if node:
            self.tree.setCurrentItem(node)
        cid = node.data(0, Qt.UserRole) if node else ALL
        m = QMenu(self)
        if cid in (ALL, NONE):
            m.addAction("Нова категорія…", lambda: self.add_category(None))
        else:
            m.addAction("Нова підкатегорія…", lambda: self.add_category(cid))
            m.addAction("Нова категорія верхнього рівня…", lambda: self.add_category(None))
            m.addAction("Перейменувати…", self.rename_category)
            m.addAction("Перемістити в…", self.move_category)
            m.addSeparator()
            m.addAction("Видалити", self.delete_category)
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def add_category(self, parent_id):
        name, ok = QInputDialog.getText(self, APP_NAME, "Назва категорії:")
        if ok and name.strip():
            self.current_category = self.db.add_category(name, parent_id)
            self.reload_categories()

    def rename_category(self):
        cid = self.current_category
        if cid in (ALL, NONE):
            return
        old = next(c["name"] for c in self.db.categories() if c["id"] == cid)
        name, ok = QInputDialog.getText(self, APP_NAME, "Нова назва:", text=old)
        if ok and name.strip():
            self.db.rename_category(cid, name)
            self.reload_categories()

    def move_category(self):
        cid = self.current_category
        dlg = QDialog(self)
        dlg.setWindowTitle("Перемістити категорію")
        combo = QComboBox()
        fill_category_combo(combo, self.db, none_text="— верхній рівень —")
        bad = set(self.db.descendants(cid))
        for i in reversed(range(combo.count())):
            if combo.itemData(i) in bad:
                combo.removeItem(i)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay = QVBoxLayout(dlg)
        lay.addWidget(combo)
        lay.addWidget(bb)
        if dlg.exec():
            self.db.move_category(cid, combo.currentData())
            self.reload_categories()

    def delete_category(self):
        cid = self.current_category
        if cid in (ALL, NONE):
            return
        n = self.db.count_items(cid)
        if QMessageBox.question(
                self, APP_NAME,
                f"Видалити категорію разом з підкатегоріями?\n"
                f"Речі ({n}) не видаляться, а перейдуть у «Без категорії».") == QMessageBox.Yes:
            self.db.delete_category(cid)
            self.current_category = ALL
            self.reload_categories()

    # --- список речей
    def reload_items(self, *_):
        selected = set(self.selected_ids())
        paths = self.db.category_paths()
        rows = self.db.list_items(self.current_category, self.search.text())
        self.table.setSortingEnabled(False)
        self.model.setRowCount(0)
        total = 0.0
        for r in rows:
            def cell(text, sort=None):
                it = QStandardItem("" if text is None else str(text))
                s = sort if sort is not None else ("" if text is None else str(text).lower())
                it.setData(s, SORT_ROLE)
                return it

            pic = QStandardItem()
            color = cat_color(r["category_id"]) if r["category_id"] else "#9CA3AF"
            pm = thumbnail(self.db.photo_path(r["cover"]), 80) if r["cover"] else QPixmap()
            pic.setIcon(QIcon(rounded(pm, 10) if not pm.isNull()
                              else letter_avatar(r["name"], color)))
            pic.setData(r["photo_count"], SORT_ROLE)
            pic.setData(r["id"], ID_ROLE)
            date = r["purchase_date"] or ""
            date_txt = ".".join(reversed(date.split("-"))) if date else ""
            qty = cell(r["quantity"], r["quantity"] or 0)
            price = cell(fmt_price(r["price"]), r["price"] if r["price"] is not None else -1)
            qty.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            price.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            name = cell(r["name"])
            nf = QFont()
            nf.setPixelSize(14)
            nf.setBold(True)
            name.setFont(nf)
            if r["serial_no"]:
                name.setToolTip(f"S/N: {r['serial_no']}")
            full = paths.get(r["category_id"], "")
            cat = cell(full.split(" / ")[-1])
            cat.setToolTip(full)
            if r["category_id"]:
                cat.setIcon(dot_icon(color))
            self.model.appendRow([
                pic, name, cat,
                cell(r["inventory_no"]), cell(r["location"]), qty, price,
                cell(r["status"]), cell(date_txt, date)])
            total += (r["price"] or 0) * (r["quantity"] or 0)
        self.table.setSortingEnabled(True)
        self.count_label.setText(f"📋  Позицій: <b>{len(rows)}</b>")
        self.total_label.setText(f"💰  Загальна вартість: <b>{fmt_price(total) or '0.00'}</b>")
        self.update_empty_state(len(rows))
        # відновити виділення
        sm = self.table.selectionModel()
        for row in range(self.proxy.rowCount()):
            idx = self.proxy.index(row, 0)
            if idx.data(ID_ROLE) in selected:
                sm.select(idx, QItemSelectionModel.SelectionFlag.Select
                          | QItemSelectionModel.SelectionFlag.Rows)
                self.table.scrollTo(idx)
        self.show_details()

    def header_menu(self, pos):
        m = QMenu(self)
        for i, name in enumerate(COLUMNS):
            if i in (0, 1):
                continue
            a = m.addAction(name)
            a.setCheckable(True)
            a.setChecked(not self.table.isColumnHidden(i))
            a.toggled.connect(lambda on, i=i: self.table.setColumnHidden(i, not on))
        m.exec(self.table.horizontalHeader().mapToGlobal(pos))

    def update_empty_state(self, n):
        if n:
            self.stack.setCurrentWidget(self.table)
            return
        q = self.search.text().strip()
        if q:
            self.empty_icon.setText("🔎")
            self.empty_title.setText("Нічого не знайдено")
            self.empty_text.setText(f"За запитом «{q}» немає збігів.\n"
                                    "Спробуйте інше слово або виберіть «Усе майно».")
            self.empty_btn.hide()
        else:
            self.empty_icon.setText("📦")
            self.empty_title.setText("Тут поки порожньо")
            self.empty_text.setText("Додайте першу річ — назву, фото і характеристики.\n"
                                    "Категорії створюються кнопкою ліворуч.")
            self.empty_btn.show()
        self.stack.setCurrentWidget(self.empty)

    def selected_ids(self):
        return [i.data(ID_ROLE) for i in self.table.selectionModel().selectedRows(0)]

    def select_id(self, item_id):
        for row in range(self.proxy.rowCount()):
            idx = self.proxy.index(row, 0)
            if idx.data(ID_ROLE) == item_id:
                self.table.selectRow(row)
                self.table.scrollTo(idx)
                return

    # --- деталі
    def show_details(self, *_):
        ids = self.selected_ids()
        self.strip.clear()
        self.cover.clear()
        self.cover.setToolTip("Клік — відкрити фото")
        if len(ids) != 1:
            self.strip.hide()
            if ids:
                self.cover.setText(f"✅\n\nВибрано речей: {len(ids)}")
                self.details.setHtml(
                    "<p style='color:#6B7280'>Можна видалити їх разом або перенести в іншу "
                    "категорію — клацніть правою кнопкою по списку.</p>")
            else:
                self.cover.setText("👈\n\nВиберіть річ у списку")
                self.details.setHtml(
                    "<p style='color:#6B7280; line-height:150%'>"
                    "💡 <b>Подвійний клік</b> — редагувати річ<br>"
                    "💡 <b>Перетягніть фото</b> сюди, щоб додати їх до вибраної речі<br>"
                    "💡 <b>Правий клік</b> по категорії — підкатегорії, перейменування<br>"
                    "💡 <b>Ctrl+F</b> — швидкий пошук</p>")
            return
        item, specs, photos = self.db.get_item(ids[0])
        if item is None:
            return
        for p in photos:
            path = self.db.photo_path(p["filename"])
            it = QListWidgetItem(QIcon(thumbnail(path, 128)), "")
            it.setData(Qt.UserRole, str(path))
            self.strip.addItem(it)
        self.strip.setVisible(len(photos) > 1)
        color = cat_color(item["category_id"]) if item["category_id"] else "#9CA3AF"
        if photos:
            self.strip.setCurrentRow(0)
        else:
            self.cover.setPixmap(letter_avatar(item["name"], color, 150))
            self.cover.setToolTip("Фото ще немає — перетягніть його сюди або додайте в редакторі")

        e = lambda v: html.escape(str(v)) if v not in (None, "") else "<span style='color:#C0C4CC'>—</span>"
        paths = self.db.category_paths()
        date = item["purchase_date"]
        status = item["status"]
        sbg, sfg = STATUS_COLORS.get(status, ("#E0F2FE", "#075985"))
        h = f"<h2 style='margin:0'>{e(item['name'])}</h2>"
        sub = []
        if item["category_id"]:
            sub.append(f"<span style='color:{color}'>●</span> {html.escape(paths.get(item['category_id'], ''))}")
        if status:
            sub.append(f"<span style='background-color:{sbg}; color:{sfg}; font-weight:600'>"
                       f"&nbsp;{html.escape(status)}&nbsp;</span>")
        if sub:
            h += f"<p style='margin-top:6px'>{' &nbsp; '.join(sub)}</p>"
        if item["price"] is not None:
            total = (item["price"] or 0) * (item["quantity"] or 0)
            h += (f"<p style='font-size:20px; font-weight:700; color:{ACCENT}; margin:4px 0'>"
                  f"{fmt_price(item['price'])} ₴"
                  + (f" <span style='font-size:13px; color:#6B7280; font-weight:400'>"
                     f"× {item['quantity']} = {fmt_price(total)} ₴</span>"
                     if (item["quantity"] or 0) > 1 else "") + "</p>")

        def table(rows):
            out = "<table width='100%' cellspacing=0 cellpadding=6>"
            for i, (k, v) in enumerate(rows):
                bg = "#F8F9FC" if i % 2 == 0 else "#FFFFFF"
                out += (f"<tr style='background-color:{bg}'><td style='color:#6B7280' width='50%'>{k}</td>"
                        f"<td><b>{v}</b></td></tr>")
            return out + "</table>"

        def section(title):
            return f"<p style='margin:14px 0 4px 0; font-weight:700; color:{ACCENT}'>{title}</p>"

        rows = [("📍 Розташування", e(item["location"])), ("🔢 Кількість", e(item["quantity"])),
                ("🏷 Інв. №", e(item["inventory_no"])), ("🔖 Серійний №", e(item["serial_no"])),
                ("📅 Куплено", e(".".join(reversed(date.split("-"))) if date else None))]
        h += section("Основне") + table(rows)
        if specs:
            h += section("⚙️ Характеристики") + table([(html.escape(s["key"]), e(s["value"]))
                                                      for s in specs])
        if item["notes"]:
            h += section("📝 Примітки")
            h += f"<p>{html.escape(item['notes']).replace(chr(10), '<br>')}</p>"
        h += (f"<p style='color:#9CA3AF; font-size:12px; margin-top:14px'>"
              f"Створено {item['created_at'].replace('T', ' ')[:16]} · "
              f"змінено {item['updated_at'].replace('T', ' ')[:16]}</p>")
        self.details.setHtml(h)

    def strip_selected(self, cur, _prev):
        if cur is None:
            return
        self._cover_path = cur.data(Qt.UserRole)
        pm = thumbnail(self._cover_path, 600)
        w, hgt = max(self.cover.width(), 200), max(self.cover.height(), 200)
        self.cover.setPixmap(pm.scaled(w, hgt, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def open_cover(self):
        it = self.strip.currentItem()
        if it:
            QDesktopServices.openUrl(QUrl.fromLocalFile(it.data(Qt.UserRole)))

    def drop_photos_on_item(self, files):
        ids = self.selected_ids()
        if len(ids) != 1:
            return
        item, specs, photos = self.db.get_item(ids[0])
        from db import FIELDS
        self.db.save_item(ids[0], {f: item[f] for f in FIELDS},
                          [(s["key"], s["value"]) for s in specs],
                          [dict(p) for p in photos] + [{"source": f} for f in files])
        self.reload_items()

    # --- дії з речами
    def item_menu(self, pos):
        if not self.selected_ids():
            return
        m = QMenu(self)
        m.addAction("Редагувати", self.edit_item)
        m.addAction("Створити копію", self.duplicate_item)
        m.addAction("Перемістити в категорію…", self.move_items)
        m.addSeparator()
        m.addAction("Видалити", self.delete_items)
        m.exec(self.table.viewport().mapToGlobal(pos))

    def add_item(self):
        dlg = ItemDialog(self.db, None, self.current_category, self)
        if dlg.exec():
            self.reload_categories()
            self.select_id(dlg.item_id)

    def edit_item(self):
        ids = self.selected_ids()
        if len(ids) != 1:
            return
        dlg = ItemDialog(self.db, ids[0], parent=self)
        if dlg.exec():
            _thumb_cache.clear()
            self.reload_categories()
            self.select_id(dlg.item_id)

    def duplicate_item(self):
        ids = self.selected_ids()
        if len(ids) == 1:
            new_id = self.db.duplicate_item(ids[0])
            self.reload_categories()
            self.select_id(new_id)

    def move_items(self):
        ids = self.selected_ids()
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Перемістити ({len(ids)})")
        combo = QComboBox()
        fill_category_combo(combo, self.db)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay = QVBoxLayout(dlg)
        lay.addWidget(combo)
        lay.addWidget(bb)
        if dlg.exec():
            self.db.move_items(ids, combo.currentData())
            self.reload_categories()

    def delete_items(self):
        ids = self.selected_ids()
        if not ids:
            return
        if QMessageBox.question(self, APP_NAME, f"Видалити вибрані речі ({len(ids)}) разом з фото?"
                                ) == QMessageBox.Yes:
            for i in ids:
                self.db.delete_item(i)
            self.reload_categories()

    # --- експорт
    def export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "Експорт CSV", "майно.csv", "CSV (*.csv)")
        if path:
            ids = [self.proxy.index(r, 0).data(ID_ROLE) for r in range(self.proxy.rowCount())]
            self.db.export_csv(path, ids)
            self.statusBar().showMessage(f"Експортовано {len(ids)} позицій → {path}", 5000)

    def backup(self):
        from datetime import date
        path, _ = QFileDialog.getSaveFileName(
            self, "Резервна копія", f"inventar-backup-{date.today()}.zip", "ZIP (*.zip)")
        if path:
            self.db.backup(path)
            self.statusBar().showMessage(f"Резервну копію збережено → {path}", 5000)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Inventar")
    app.setApplicationDisplayName(APP_NAME)
    app.setStyle("Fusion")
    app.setPalette(light_palette())
    app.setStyleSheet(build_style())
    data_dir = os.environ.get("INVENTAR_DATA") or \
        QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
    db = Database(data_dir)
    w = MainWindow(db)
    w.show()
    code = app.exec()
    db.close()
    sys.exit(code)


if __name__ == "__main__":
    main()
