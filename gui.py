# gui.py
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QColorDialog,
)

from watch_core import WatchDB, TMDBClient, WatchService, AddOrShowResult, TmdbChoice, TitleItem

def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

DB_PATH = app_dir() / "watchlist.sqlite3"


def item_to_display_text(it: TitleItem) -> str:
    seen = "seen" if it.seen else "unseen"
    year = str(it.year) if it.year else "?"
    runtime = f"{it.runtime_minutes}m" if it.runtime_minutes else "?"
    genres = ", ".join(it.genres) if it.genres else ""
    genres_part = f" | {genres}" if genres else ""
    return f'{it.title}  [{it.type}]  ({seen})  | {year} | {runtime}{genres_part}'



class PickDialog(QDialog):

    def __init__(self, choices: list[TmdbChoice], parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Pick a match")
        self.setModal(True)
        self.setMinimumSize(600, 300)
        self._choices = choices
        self.selected: Optional[TmdbChoice] = None
        layout = QVBoxLayout(self)

        info = QLabel("Select the correct match:")
        layout.addWidget(info)

        self.list = QListWidget()
        for c in choices:
            label = "show" if c.media_type == "tv" else "movie"
            year = c.year if c.year else "?"
            overview = (c.overview or "").replace("\n", " ")
            if len(overview) > 160:
                overview = overview[:157] + "..."
            text = f"[{label}] {c.title} ({year}) — {overview}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, c)
            self.list.addItem(item)

        self.list.itemDoubleClicked.connect(self._accept_selected)
        layout.addWidget(self.list)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept_selected)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if self.list.count() > 0:
            self.list.setCurrentRow(0)

    def _accept_selected(self):
        item = self.list.currentItem()
        if not item:
            QMessageBox.information(self, "No selection", "Please select an item.")
            return
        self.selected = item.data(Qt.ItemDataRole.UserRole)
        self.accept()


class LocalAddDialog(QDialog):
    def __init__(self, typed_title: str, message: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Add locally?")
        self.setModal(True)
        self.local_type: Optional[str] = None

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(message))
        layout.addWidget(QLabel(f'Title: "{typed_title}"'))

        row = QHBoxLayout()
        row.addWidget(QLabel("Type:"))
        self.type_box = QComboBox()
        self.type_box.addItems(["movie", "show", "youtube"])
        row.addWidget(self.type_box)
        row.addStretch(1)
        layout.addLayout(row)

        buttons = QDialogButtonBox(QDialogButtonBox.Yes | QDialogButtonBox.No)
        buttons.button(QDialogButtonBox.Yes).setText("Add")
        buttons.button(QDialogButtonBox.No).setText("Cancel")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self):
        self.local_type = self.type_box.currentText()
        self.accept()


class ManageTagsDialog(QDialog):
    tags_updated = Signal()
    
    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Manage tags")
        self.setModal(True)
        self.service = service

        layout = QVBoxLayout(self)

        self.list = QListWidget()
        layout.addWidget(self.list)

        row = QHBoxLayout()
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Tag name (e.g. Fav, Horror Night)")
        row.addWidget(self.name_input)

        self._picked_color = "#ffcc00"
        self.color_btn = QPushButton("Pick color")
        self.color_btn.clicked.connect(self.pick_color)
        row.addWidget(self.color_btn)

        self.add_btn = QPushButton("Create")
        self.add_btn.clicked.connect(self.create_tag)
        row.addWidget(self.add_btn)
        layout.addLayout(row)

        self.update_btn = QPushButton("Update selected")
        self.update_btn.clicked.connect(self.update_selected)
        row.addWidget(self.update_btn)
        
        self.list.currentItemChanged.connect(self.on_select_tag)


        row2 = QHBoxLayout()
        self.del_btn = QPushButton("Delete selected")
        self.del_btn.clicked.connect(self.delete_selected)
        row2.addWidget(self.del_btn)

        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row2.addWidget(close)
        layout.addLayout(row2)

        self.refresh()

    def refresh(self):
        self.list.clear()
        for tag_id, name, color in self.service.list_tags():
            item = QListWidgetItem(f"{name} ({color})")
            item.setData(Qt.ItemDataRole.UserRole, (tag_id, name, color))
            item.setForeground(QColor(color))
            self.list.addItem(item)

    def pick_color(self):
        c = QColorDialog.getColor(QColor(self._picked_color), self, "Pick a tag color")
        if c.isValid():
            self._picked_color = c.name()

    def create_tag(self):
        name = self.name_input.text().strip()
        color = self._picked_color
    
        if not name:
            QMessageBox.information(self, "Tag name", "Please enter a tag name.")
            return
    
        try:
            self.service.create_tag(name, color)
        except ValueError as e:
            QMessageBox.information(self, "Tag already exists", str(e))
            return
        except Exception as e:
            QMessageBox.warning(self, "Create failed", str(e))
            return
    
        self.name_input.clear()
        self._picked_color = "#ffffff"
        self.refresh()
        self.tags_updated.emit()

    def on_select_tag(self, current, _previous):
        if not current:
            return
        tag_id, name, color = current.data(Qt.ItemDataRole.UserRole)
        self.name_input.setText(name)
        self._picked_color = color 
    
    
    def update_selected(self):
        it = self.list.currentItem()
        if not it:
            QMessageBox.information(self, "Update tag", "Select a tag to update.")
            return
    
        tag_id, _old_name, _old_color = it.data(Qt.ItemDataRole.UserRole)
    
        name = self.name_input.text().strip()
        if not name:
            QMessageBox.information(self, "Tag name", "Please enter a tag name.")
            return
    
        try:
            self.service.update_tag(int(tag_id), name, self._picked_color)
        except Exception as e:
            QMessageBox.warning(self, "Update failed", str(e))
            return
    
        self.refresh()
        self.tags_updated.emit() 


    def delete_selected(self):
        it = self.list.currentItem()
        if not it:
            return
        tag_id, name, _color = it.data(Qt.ItemDataRole.UserRole)
        btn = QMessageBox.question(self, "Delete tag", f'Delete tag "{name}"?', QMessageBox.Yes | QMessageBox.No)
        if btn != QMessageBox.Yes:
            return
        self.service.delete_tag(int(tag_id))
        self.refresh()
        self.tags_updated.emit()


class SetTagsDialog(QDialog):
    def __init__(self, service, title_id: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Set tags")
        self.setModal(True)
        self.service = service
        self.title_id = title_id

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Select tags for this media:"))

        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.NoSelection)
        layout.addWidget(self.list)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.populate()

    def populate(self):
        self.list.clear()
        current_names = {name for name, _color in self.service.get_title_tags(self.title_id)}
        for tag_id, name, color in self.service.list_tags():
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, int(tag_id))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if name in current_names else Qt.Unchecked)
            item.setForeground(QColor(color))
            self.list.addItem(item)

    def selected_tag_ids(self) -> list[int]:
        ids: list[int] = []
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.checkState() == Qt.Checked:
                ids.append(int(it.data(Qt.ItemDataRole.UserRole)))
        return ids


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Watchlist")
        self.resize(900, 600)

        self.db = WatchDB(DB_PATH)
        self.db.init_db()

        self.tmdb = TMDBClient()
        self.service = WatchService(db=self.db, tmdb=self.tmdb)

        # --- UI ---
        root = QVBoxLayout(self)

        # Top: search row
        top = QHBoxLayout()

        self.input = QLineEdit()

        self.input.setPlaceholderText("Type a title and press Enter to search…")
        top.addWidget(self.input)  

        self.input.textChanged.connect(self.on_live_filter)

        self.tmdb_btn = QPushButton("Search TMDB")
        self.tmdb_btn.clicked.connect(self.on_search_tmdb)
        top.addWidget(self.tmdb_btn)

        self.add_local_btn = QPushButton("Add local")
        self.add_local_btn.clicked.connect(self.on_add_local)
        top.addWidget(self.add_local_btn)

        self.local_btn = QPushButton("Search local")
        self.local_btn.clicked.connect(self.on_search_local)
        top.addWidget(self.local_btn)

        self.input.returnPressed.connect(self.on_search_tmdb)

        root.addLayout(top)

        # Filters row
        filters = QHBoxLayout()

        self.unseen_only = QCheckBox("Unseen only")
        self.unseen_only.stateChanged.connect(self.refresh_list)
        filters.addWidget(self.unseen_only)

        filters.addWidget(QLabel("Type:"))
        self.type_filter = QComboBox()
        self.type_filter.addItems(["all", "movie", "show", "youtube"])
        self.type_filter.currentIndexChanged.connect(self.refresh_list)
        filters.addWidget(self.type_filter)

        filters.addWidget(QLabel("Genre:"))
        self.genre_filter = QComboBox()
        self.genre_filter.addItems(["all"])
        self.genre_filter.currentIndexChanged.connect(self.refresh_list)
        filters.addWidget(self.genre_filter)

        filters.addWidget(QLabel("Tag:"))
        self.tag_filter = QComboBox()
        self.tag_filter.addItems(["all"])
        self.tag_filter.currentIndexChanged.connect(self.on_filters_changed)
        filters.addWidget(self.tag_filter)


        filters.addWidget(QLabel("Limit:"))
        self.limit_box = QSpinBox()
        self.limit_box.setRange(10, 2000)
        self.limit_box.setValue(200)
        self.limit_box.valueChanged.connect(self.refresh_list)
        filters.addWidget(self.limit_box)

        filters.addWidget(QLabel("Sort:"))
        self.sort_by = QComboBox()
        self.sort_by.addItems(["Title (A→Z)", "Runtime (short→long)", "Runtime (long→short)"])
        self.sort_by.currentIndexChanged.connect(self.on_filters_changed)
        filters.addWidget(self.sort_by)


        filters.addStretch(1)

        self.random_btn = QPushButton("Random")
        self.random_btn.clicked.connect(self.on_random)
        filters.addWidget(self.random_btn)

        root.addLayout(filters)

        self.live_local = QCheckBox("Live local search")
        self.live_local.setChecked(True)
        self.live_local.stateChanged.connect(self.on_live_toggle)
        filters.addWidget(self.live_local)

        # List
        self.list = QListWidget()
        self.list.itemSelectionChanged.connect(self.on_selection_changed)
        root.addWidget(self.list, 1)

        # Bottom actions
        bottom = QHBoxLayout()
        self.seen_toggle = QPushButton("Mark Seen")
        self.seen_toggle.setEnabled(False)
        self.seen_toggle.clicked.connect(self.on_toggle_seen)
        bottom.addWidget(self.seen_toggle)

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh_list)
        bottom.addWidget(self.refresh_btn)

        self.delete_btn = QPushButton("Delete")
        self.delete_btn.setEnabled(False)
        self.delete_btn.clicked.connect(self.on_delete)
        bottom.addWidget(self.delete_btn)

        self.manage_tags_btn = QPushButton("Manage tags")
        self.manage_tags_btn.clicked.connect(self.on_manage_tags)
        bottom.addWidget(self.manage_tags_btn)

        self.set_tags_btn = QPushButton("Set tags")
        self.set_tags_btn.setEnabled(False)
        self.set_tags_btn.clicked.connect(self.on_set_tags)
        bottom.addWidget(self.set_tags_btn)


        bottom.addStretch(1)
        root.addLayout(bottom)

        # Initial
        self.refresh_genres()
        self.refresh_list()
        self.refresh_tags()

    # ------------- Data helpers -------------
    def current_filters(self):
        unseen = self.unseen_only.isChecked()
        t = self.type_filter.currentText()
        type_ = None if t == "all" else t
        g = self.genre_filter.currentText()
        genre = None if g == "all" else g
        limit = int(self.limit_box.value())
        tag = self.tag_filter.currentText()
        tag = None if tag == "all" else tag
        return unseen, type_, genre, tag, limit

    def refresh_genres(self):
        self.genre_filter.blockSignals(True)
        current = self.genre_filter.currentText()
        self.genre_filter.clear()
        self.genre_filter.addItem("all")

        for name, count in self.service.list_genres():
            self.genre_filter.addItem(name)

        idx = self.genre_filter.findText(current)
        if idx >= 0:
            self.genre_filter.setCurrentIndex(idx)
        self.genre_filter.blockSignals(False)

    def refresh_tags(self):
        self.tag_filter.blockSignals(True)
        current = self.tag_filter.currentText()
        self.tag_filter.clear()
        self.tag_filter.addItem("all")
    
        for _id, name, color in self.service.list_tags():
            self.tag_filter.addItem(name)
            idx = self.tag_filter.count() - 1
            self.tag_filter.setItemData(idx, QColor(color), Qt.ItemDataRole.ForegroundRole)
    
        idx = self.tag_filter.findText(current)
        if idx >= 0:
            self.tag_filter.setCurrentIndex(idx)
    
        self.tag_filter.blockSignals(False)


    def on_filters_changed(self):
        if getattr(self, "live_local", None) and self.live_local.isChecked():
            self.on_live_filter(self.input.text())
        else:
            self.refresh_list()


    def refresh_list(self):
        unseen, type_, genre, tag, limit = self.current_filters()
        items = self.service.list_titles(unseen_only=unseen, type_=type_, genre=genre, tag=tag, limit=limit)
        tag_map = self.service.db.get_tags_for_title_ids([it.id for it in items])
        mode = self.sort_by.currentText()

        if mode == "Title (A→Z)":
            items.sort(key=lambda it: (it.title or "").lower())
        elif mode == "Runtime (short→long)":
            items.sort(key=lambda it: (it.runtime_minutes is None, it.runtime_minutes or 0, (it.title or "").lower()))
        else: 
            items.sort(key=lambda it: (it.runtime_minutes is None, -(it.runtime_minutes or 0), (it.title or "").lower()))


        self.list.clear()
        for it in items:
            w = QListWidgetItem(item_to_display_text(it))
            w.setData(Qt.ItemDataRole.UserRole, it.id)
        
            tags = tag_map.get(it.id, [])
            base_color = QColor(tags[0][1]) if tags else None
        
            self.apply_item_style(w, it.seen, base_color)
            self.list.addItem(w)


        self.refresh_genres()
        self.on_selection_changed()

    def selected_title_id(self) -> Optional[int]:
        item = self.list.currentItem()
        if not item:
            return None
        return int(item.data(Qt.ItemDataRole.UserRole))

    def blend_with_grey(self, color: QColor, factor: float = 0.55) -> QColor:
        grey = QColor(160, 160, 160)
        r = int(color.red()   * (1 - factor) + grey.red()   * factor)
        g = int(color.green() * (1 - factor) + grey.green() * factor)
        b = int(color.blue()  * (1 - factor) + grey.blue()  * factor)
        return QColor(r, g, b)

    def apply_item_style(self, qitem, is_seen: bool, base_color: QColor | None):
        if base_color is None:
            if is_seen:
                qitem.setForeground(QColor(170, 170, 170))
            return

        if is_seen:
            qitem.setForeground(self.blend_with_grey(base_color))
        else:
            qitem.setForeground(base_color)


    # ------------- UI actions -------------        
    def on_delete(self):
        tid = self.selected_title_id()
        if not tid:
            return

        row = self.db.get_by_id(tid)
        if not row:
            return

        title = row["title"]
        btn = QMessageBox.question(
            self,
            "Delete entry",
            f'Delete "{title}" from your watchlist?',
            QMessageBox.Yes | QMessageBox.No,
        )
        if btn != QMessageBox.Yes:
            return

        self.service.delete_title(tid)
        self.refresh_list()


    def on_search_local(self):
        typed = self.input.text().strip()
        if not typed:
            return

        matches = self.service.suggestions(typed, limit=25)
        if not matches:
            QMessageBox.information(self, "Local search", "No matches in your list.")
            return

        text = "\n".join(f"- {item_to_display_text(m)}" for m in matches[:10])
        QMessageBox.information(self, "Local matches (top 10)", text)

    def on_search_tmdb(self):
        typed = self.input.text().strip()
        if not typed:
            return
        self.input.clear()

        try:
            choices = self.service.tmdb_search_any(typed, limit=8)
        except Exception as e:
            QMessageBox.warning(self, "TMDB error", str(e))
            return

        if not choices:
            QMessageBox.information(self, "TMDB search", "No TMDB results found.")
            return

        pick = PickDialog(choices, parent=self)
        ok = (pick.exec() == QDialog.Accepted and pick.selected)
        if not ok:
            return

        r2 = self.service.add_or_show_confirm_tmdb_choice(pick.selected)

        if r2.status == "added" and r2.item:
            self.refresh_list()
        elif r2.status == "exists" and r2.item:
            QMessageBox.information(self, "Already in your list", item_to_display_text(r2.item))
        else:
            QMessageBox.warning(self, "Not added", r2.message or "Error")


    def on_add_local(self):
        typed = self.input.text().strip()
        if not typed:
            return

        r = self.service.add_or_show_start(typed)
        if r.status == "exists" and r.item:
            QMessageBox.information(self, "Already in your list", item_to_display_text(r.item))
            self.input.selectAll()
            return

        dlg = LocalAddDialog(
            typed,
            "Add locally without TMDB metadata?",
            parent=self,
        )
        if dlg.exec() == QDialog.Accepted and dlg.local_type:
            it = self.service.add_local(typed, type_=dlg.local_type)  # type: ignore[arg-type]
            QMessageBox.information(self, "Added locally", item_to_display_text(it))
            self.refresh_list()

        self.input.selectAll()

    def on_live_toggle(self):
        if not self.live_local.isChecked():
            self.refresh_list()
        else:
            self.on_live_filter(self.input.text())


    def on_live_filter(self, text: str):
        if not self.live_local.isChecked():
            return 
    
        text = text.strip()
        unseen, type_, genre, tag, limit = self.current_filters()
        
        if not text:
            items = self.service.list_titles(
                unseen_only=unseen, type_=type_, genre=genre, tag=tag, limit=limit
            )
        else:
            items = self.service.suggestions(text, limit=limit)

            tag_map = self.service.db.get_tags_for_title_ids([it.id for it in items])
            if unseen:
                items = [it for it in items if not it.seen]
            if type_:
                items = [it for it in items if it.type == type_]
            if genre:
                items = [it for it in items if genre in it.genres]
            if tag:
                items = [
                    it for it in items
                    if any(tname == tag for tname, _c in tag_map.get(it.id, []))
                ]
    
        tag_map = self.service.db.get_tags_for_title_ids([it.id for it in items])
    
        mode = self.sort_by.currentText()
        if mode == "Title (A→Z)":
            items.sort(key=lambda it: (it.title or "").lower())
        elif mode == "Runtime (short→long)":
            items.sort(key=lambda it: (it.runtime_minutes is None, it.runtime_minutes or 0, (it.title or "").lower()))
        else: 
            items.sort(key=lambda it: (it.runtime_minutes is None, -(it.runtime_minutes or 0), (it.title or "").lower()))
    
        self.list.clear()
        for it in items:
            w = QListWidgetItem(item_to_display_text(it))
            w.setData(Qt.ItemDataRole.UserRole, it.id)
            tags = tag_map.get(it.id, [])
            base_color = QColor(tags[0][1]) if tags else None
            self.apply_item_style(w, it.seen, base_color)
            self.list.addItem(w)
    
        self.on_selection_changed()






    def on_selection_changed(self):
        tid = self.selected_title_id()
        if not tid:
            self.seen_toggle.setEnabled(False)
            self.seen_toggle.setText("Mark Seen")
            return

        it = self.db.get_by_id(tid)
        if not it:
            self.seen_toggle.setEnabled(False)
            return

        is_seen = bool(it["seen"])
        self.seen_toggle.setEnabled(True)
        self.delete_btn.setEnabled(tid is not None)
        self.seen_toggle.setText("Mark Unseen" if is_seen else "Mark Seen")
        self.set_tags_btn.setEnabled(tid is not None)


    def on_toggle_seen(self):
        tid = self.selected_title_id()
        if not tid:
            return
        row = self.db.get_by_id(tid)
        if not row:
            return
        new_seen = not bool(row["seen"])
        self.service.set_seen(tid, new_seen)
        self.refresh_list()

        for i in range(self.list.count()):
            if int(self.list.item(i).data(Qt.ItemDataRole.UserRole)) == tid:
                self.list.setCurrentRow(i)
                break

    def on_random(self):
        unseen, type_, genre, _limit = self.current_filters()
        pick = self.service.random_pick(type_=type_, genre=genre) if unseen else self.service.random_pick(type_=type_, genre=genre)
        if not pick:
            QMessageBox.information(self, "Random", "No unseen titles found for these filters.")
            return

        QMessageBox.information(self, "Random pick", item_to_display_text(pick))
        for i in range(self.list.count()):
            if int(self.list.item(i).data(Qt.ItemDataRole.UserRole)) == pick.id:
                self.list.setCurrentRow(i)
                self.list.scrollToItem(self.list.item(i))
                break

    def on_manage_tags(self):
        dlg = ManageTagsDialog(self.service, parent=self)
        dlg.tags_updated.connect(self.refresh_tags) 
        dlg.tags_updated.connect(self.refresh_list) 
        dlg.exec()

    def on_set_tags(self):
        tid = self.selected_title_id()
        if not tid:
            return
        dlg = SetTagsDialog(self.service, tid, parent=self)
        if dlg.exec() == QDialog.Accepted:
            self.service.set_title_tags(tid, dlg.selected_tag_ids())
            self.refresh_list()



def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
