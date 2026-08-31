import csv
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QCheckBox, QPushButton, QFileDialog, QTableWidget, QTableWidgetItem,
    QMessageBox, QGroupBox, QGridLayout, QDialog, QDialogButtonBox,
    QHeaderView, QComboBox, QStackedWidget,
)

import db_utils

def _bundle_dir() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(getattr(sys, '_MEIPASS', Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def _app_dir() -> Path:
    # Writable dir next to the exe (not PyInstaller's temp _MEIPASS).
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


BUNDLE_DIR = _bundle_dir()
APP_DIR = _app_dir()
ICON_PATH = BUNDLE_DIR / 'icon.ico'

LIGHT = {
    'bg': '#f9fafb', 'fg': '#111827', 'muted': '#6b7280',
    'btn_bg': '#0f1724', 'btn_fg': '#ffffff', 'btn_disabled': '#94a3b8',
    'btn_hover': '#0b1220', 'btn_pressed': '#07101a', 'accent': '#2563eb',
    'input_bg': '#ffffff', 'border': '#e5e7eb',
    'group_border': '#e6e7eb', 'header_bg': '#f3f4f6', 'table_bg': '#ffffff',
    'alt_row': '#f8fafc', 'selection': '#e6f0ff'
}

DARK = {
    'bg': '#0b1020', 'fg': '#e6eef8', 'muted': '#9aa6bf',
    'btn_bg': '#1f2937', 'btn_fg': '#e6eef8', 'btn_disabled': '#374151',
    'btn_hover': '#273244', 'btn_pressed': '#1b2430', 'accent': '#60a5fa',
    'input_bg': '#071027', 'border': '#24303f',
    'group_border': '#172035', 'header_bg': '#0f1724', 'table_bg': '#071027',
    'alt_row': '#051226', 'selection': '#1f3a5a'
}


def build_stylesheet(pal):
    return f"""
    QWidget {{ background: {pal['bg']}; color: {pal['fg']}; font-family: 'Segoe UI', 'Ubuntu', 'Noto Sans', Arial, sans-serif; }}
    QLabel#title {{ font-size:18px; font-weight:600; color: {pal['fg']}; }}
    QLabel#warning {{ color: #b45309; }}
    QPushButton {{ background: {pal['btn_bg']}; color: {pal['btn_fg']}; padding:6px 12px; border-radius:8px }}
    QPushButton:disabled {{ background: {pal['btn_disabled']}; color: {pal['muted']}; }}
    QLineEdit, QComboBox, QTableWidget, QPlainTextEdit {{ background: {pal['input_bg']}; color: {pal['fg']}; padding:6px; border:1px solid {pal['border']}; border-radius:6px }}
    QGroupBox {{ border: 1px solid {pal['group_border']}; border-radius:8px; margin-top:6px; padding:8px }}
    QHeaderView::section {{ background: {pal['header_bg']}; padding:6px; color: {pal['fg']}; }}
    QTableWidget {{ background: {pal['table_bg']}; gridline-color: {pal['border']}; }}
    QTableWidget::item {{ padding:6px; }}
    QTableView::item:selected, QTableWidget::item:selected {{ background: {pal['selection']}; color: {pal['fg']}; }}
    QTableWidget::item:hover {{ background: {pal['alt_row']}; }}
    QCheckBox {{ color: {pal['fg']}; }}
    QDialog {{ background: {pal['bg']}; color: {pal['fg']}; }}
    QComboBox QAbstractItemView {{ background: {pal['input_bg']}; color: {pal['fg']}; selection-background-color: {pal['header_bg']}; }}
    QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 4px; border: 1px solid {pal['border']}; background: {pal['input_bg']}; }}
    QCheckBox::indicator:checked {{ background: {pal['accent']}; border-color: {pal['accent']}; }}
    QCheckBox::indicator:hover {{ border-color: {pal['accent']}; }}
    QCheckBox::indicator:disabled {{ background: {pal['btn_disabled']}; border-color: {pal['btn_disabled']}; }}
    QCheckBox:focus {{ outline: 2px solid {pal['accent']}; outline-offset: 2px; }}
    QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top left; padding: 0 6px; color: {pal['fg']}; font-weight:600 }}
    QPushButton:hover {{ background: {pal['btn_hover']}; }}
    QPushButton:pressed {{ background: {pal['btn_pressed']}; }}
    """


def _char_label(c: dict) -> str:
    name = c.get('char_name') or str(c.get('id'))
    guild = c.get('guild_name')
    if guild:
        return f"{c.get('id')} - {name}  [clan: {guild}]"
    return f"{c.get('id')} - {name}  [no clan]"


def _account_label(a: dict) -> str:
    user = a.get('user') or '(empty)'
    return f"{a.get('id')} — {user}"


class TransferApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('ConanExilesDBTransfer')
        self.setMinimumSize(960, 700)
        try:
            ico = QIcon(str(ICON_PATH))
            if not ico.isNull():
                self.setWindowIcon(ico)
        except Exception:
            pass
        self.selected_item_keys = None
        self.selected_building_object_ids = None
        self.selected_thrall_ids = None
        self.setup_ui()

    def setup_ui(self):
        self.setFont(QFont())

        layout = QVBoxLayout()
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        header_row = QHBoxLayout()
        header = QLabel('ConanExilesDBTransfer')
        header.setObjectName('title')
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        header.setFont(title_font)
        header.setContentsMargins(6, 8, 6, 8)
        header_row.addWidget(header, 1)
        self.chk_dark = QCheckBox('Dark Mode')
        self.chk_dark.setToolTip('Toggle dark / light theme')
        self.chk_dark.toggled.connect(self.on_theme_toggled)
        header_row.addWidget(self.chk_dark, 0, Qt.AlignRight)
        layout.addLayout(header_row)

        db_row = QHBoxLayout()
        db_row.addWidget(QLabel('Game DB:'))
        self.db_path = QLineEdit(str(Path.cwd() / 'game.db'))
        self.db_path.editingFinished.connect(self.on_db_changed)
        db_row.addWidget(self.db_path)
        btn_browse = QPushButton('Browse')
        btn_browse.clicked.connect(self.browse_db)
        db_row.addWidget(btn_browse)
        layout.addLayout(db_row)

        self.lbl_db_warning = QLabel('')
        self.lbl_db_warning.setObjectName('warning')
        self.lbl_db_warning.setWordWrap(True)
        layout.addWidget(self.lbl_db_warning)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel('Mode:'))
        self.mode_combo = QComboBox()
        self.mode_combo.addItem('Character Transfer (same save)', 'transfer')
        self.mode_combo.addItem('Save Handoff (account rebind)', 'handoff')
        self.mode_combo.currentIndexChanged.connect(self.on_mode_changed)
        mode_row.addWidget(self.mode_combo, 1)
        layout.addLayout(mode_row)

        self.mode_stack = QStackedWidget()

        # --- Character transfer mode ---
        transfer_page = QWidget()
        transfer_layout = QVBoxLayout(transfer_page)
        transfer_layout.setContentsMargins(0, 0, 0, 0)

        st_group = QGridLayout()
        st_group.addWidget(QLabel('Source:'), 0, 0)
        self.src_combo = QComboBox()
        self.src_combo.currentIndexChanged.connect(self.on_source_changed)
        st_group.addWidget(self.src_combo, 0, 1)
        btn_refresh_src = QPushButton('Refresh')
        btn_refresh_src.clicked.connect(self.populate_source_combo)
        st_group.addWidget(btn_refresh_src, 0, 2)

        st_group.addWidget(QLabel('Target:'), 1, 0)
        self.tgt_combo = QComboBox()
        st_group.addWidget(self.tgt_combo, 1, 1)
        btn_refresh_tgt = QPushButton('Refresh')
        btn_refresh_tgt.clicked.connect(self.populate_target_combo)
        st_group.addWidget(btn_refresh_tgt, 1, 2)

        self.cb_include_clan = QCheckBox("Include clan-owned buildings and followers (source's guild)")
        self.cb_include_clan.setToolTip(
            'Placed thralls and pets are stored under the clan id on Funcom servers, not the character id. '
            'Enabled automatically when the source is in a clan. Other clan members lose those assets if you transfer them.'
        )
        self.cb_include_clan.toggled.connect(self.on_clan_toggled)
        st_group.addWidget(self.cb_include_clan, 2, 0, 1, 3)

        self.cb_clan_to_target_guild = QCheckBox("Give clan assets to the target's clan (otherwise personal)")
        self.cb_clan_to_target_guild.setEnabled(False)
        self.cb_clan_to_target_guild.setToolTip(
            'If checked and the target is in a clan, clan buildings/followers are written with that guildId. '
            'If unchecked, they become personal property of the target character.'
        )
        st_group.addWidget(self.cb_clan_to_target_guild, 3, 0, 1, 3)

        group_box = QGroupBox('Transfer Settings')
        group_box.setLayout(st_group)
        transfer_layout.addWidget(group_box)

        cats = QHBoxLayout()
        cats.setSpacing(18)
        self.cb_items = QCheckBox('Items (carried)')
        self.cb_buildings = QCheckBox('Buildings')
        self.cb_thralls = QCheckBox('Thralls/Pets')
        self.cb_all = QCheckBox('All')

        self.btn_items_details = QPushButton('Details')
        self.btn_items_details.clicked.connect(self.show_items_details)
        self.btn_buildings_details = QPushButton('Details')
        self.btn_buildings_details.clicked.connect(self.show_buildings_details)
        self.btn_thralls_details = QPushButton('Details')
        self.btn_thralls_details.clicked.connect(self.show_thralls_details)

        items_box = QHBoxLayout()
        items_box.addWidget(self.cb_items)
        self.lbl_items_count = QLabel('(0)')
        items_box.addWidget(self.lbl_items_count)
        items_box.addWidget(self.btn_items_details)
        cats.addLayout(items_box)

        buildings_box = QHBoxLayout()
        buildings_box.addWidget(self.cb_buildings)
        self.lbl_buildings_count = QLabel('(0)')
        buildings_box.addWidget(self.lbl_buildings_count)
        buildings_box.addWidget(self.btn_buildings_details)
        cats.addLayout(buildings_box)

        thralls_box = QHBoxLayout()
        thralls_box.addWidget(self.cb_thralls)
        self.lbl_thralls_count = QLabel('(0)')
        thralls_box.addWidget(self.lbl_thralls_count)
        thralls_box.addWidget(self.btn_thralls_details)
        cats.addLayout(thralls_box)

        cats.addWidget(self.cb_all)
        transfer_layout.addLayout(cats)

        hint = QLabel(
            'Carried items only (hotbar / inventory / equipment). Chest and bench contents stay '
            'with the structure and move when Buildings are transferred.'
        )
        hint.setWordWrap(True)
        hint.setStyleSheet('color: #6b7280;')
        transfer_layout.addWidget(hint)

        self.cb_all.toggled.connect(self.on_all_toggled)
        for _cb in (self.cb_items, self.cb_buildings, self.cb_thralls):
            _cb.toggled.connect(self.on_category_toggled)

        transfer_actions = QHBoxLayout()
        transfer_actions.setSpacing(10)
        self.btn_analyze = QPushButton('Analyze (Dry-run)')
        self.btn_analyze.clicked.connect(self.on_analyze)
        self.btn_transfer = QPushButton('Transfer')
        self.btn_transfer.clicked.connect(self.on_transfer)
        transfer_actions.addWidget(self.btn_analyze)
        transfer_actions.addWidget(self.btn_transfer)
        transfer_layout.addLayout(transfer_actions)

        self.mode_stack.addWidget(transfer_page)

        # --- Save handoff mode ---
        handoff_page = QWidget()
        handoff_layout = QVBoxLayout(handoff_page)
        handoff_layout.setContentsMargins(0, 0, 0, 0)

        ho_group = QGridLayout()
        ho_group.addWidget(QLabel('Character to keep:'), 0, 0)
        self.handoff_char_combo = QComboBox()
        ho_group.addWidget(self.handoff_char_combo, 0, 1)
        btn_refresh_ho = QPushButton('Refresh')
        btn_refresh_ho.clicked.connect(self.populate_handoff_combo)
        ho_group.addWidget(btn_refresh_ho, 0, 2)

        ho_group.addWidget(QLabel('Siptah DB:'), 1, 0)
        self.siptah_db_path = QLineEdit('')
        self.siptah_db_path.setPlaceholderText('Optional: dlc_siptah.db')
        ho_group.addWidget(self.siptah_db_path, 1, 1)
        btn_browse_siptah = QPushButton('Browse')
        btn_browse_siptah.clicked.connect(self.browse_siptah_db)
        ho_group.addWidget(btn_browse_siptah, 1, 2)

        self.cb_apply_siptah = QCheckBox('Apply same rebind to Siptah DB when path is set')
        self.cb_apply_siptah.setChecked(True)
        ho_group.addWidget(self.cb_apply_siptah, 2, 0, 1, 3)

        ho_group.addWidget(QLabel('Person B Game.ini:'), 3, 0)
        self.game_ini_path = QLineEdit('')
        self.game_ini_path.setPlaceholderText('ConanSandbox/Saved/Config/.../Game.ini')
        ho_group.addWidget(self.game_ini_path, 3, 1)
        btn_browse_ini = QPushButton('Browse')
        btn_browse_ini.clicked.connect(self.browse_game_ini)
        ho_group.addWidget(btn_browse_ini, 3, 2)

        ho_group.addWidget(QLabel('Master Account ID:'), 4, 0)
        self.target_account_id = QLineEdit('')
        self.target_account_id.setPlaceholderText('From Game.ini CachedUsers MasterAccountId')
        ho_group.addWidget(self.target_account_id, 4, 1, 1, 2)

        self.cb_remove_throwaway = QCheckBox(
            'Remove bootstrap / throwaway characters linked to Person B\'s account'
        )
        self.cb_remove_throwaway.setChecked(True)
        self.cb_remove_throwaway.setToolTip(
            'If Person B already opened this save and created a new character, remove those rows '
            'after rebinding Person A\'s character to Person B\'s account.'
        )
        ho_group.addWidget(self.cb_remove_throwaway, 5, 0, 1, 3)

        handoff_box = QGroupBox('Save Handoff Settings')
        handoff_box.setLayout(ho_group)
        handoff_layout.addWidget(handoff_box)

        handoff_hint = QLabel(
            'Rebinds the save to Person B\'s Funcom account so they can load Person A\'s character '
            'with full ownership. In-game assets stay on the same character id — no item/building rewrite needed.'
        )
        handoff_hint.setWordWrap(True)
        handoff_hint.setStyleSheet('color: #6b7280;')
        handoff_layout.addWidget(handoff_hint)

        self.lbl_handoff_accounts = QLabel('')
        self.lbl_handoff_accounts.setWordWrap(True)
        handoff_layout.addWidget(self.lbl_handoff_accounts)

        handoff_actions = QHBoxLayout()
        handoff_actions.setSpacing(10)
        self.btn_handoff_analyze = QPushButton('Analyze Handoff')
        self.btn_handoff_analyze.clicked.connect(self.on_handoff_analyze)
        self.btn_handoff = QPushButton('Rebind Save')
        self.btn_handoff.clicked.connect(self.on_handoff)
        handoff_actions.addWidget(self.btn_handoff_analyze)
        handoff_actions.addWidget(self.btn_handoff)
        handoff_layout.addLayout(handoff_actions)

        self.mode_stack.addWidget(handoff_page)
        layout.addWidget(self.mode_stack)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.btn_export_audit = QPushButton('Export Audit CSV')
        self.btn_export_audit.clicked.connect(self.on_export_audit)
        self.btn_view_audit = QPushButton('View Audit')
        self.btn_view_audit.clicked.connect(self.on_view_audit)
        self.btn_revert = QPushButton('Revert Transfer')
        self.btn_revert.clicked.connect(self.on_revert_transfer)
        row.addWidget(self.btn_export_audit)
        row.addWidget(self.btn_view_audit)
        row.addWidget(self.btn_revert)
        layout.addLayout(row)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(['Category', 'Before', 'After / Changed'])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setDefaultSectionSize(28)
        layout.addWidget(self.table)

        self.setLayout(layout)
        self.current_theme = 'light'
        self.apply_theme('light')
        if Path(self.db_path.text()).exists():
            self.on_db_changed()

    def browse_db(self):
        path, _ = QFileDialog.getOpenFileName(
            self, 'Select game.db', str(Path.cwd()),
            'SQLite DB (*.db *.sqlite *.sqlite3);;All Files (*)'
        )
        if path:
            self.db_path.setText(path)
            self.on_db_changed()

    def on_theme_toggled(self, checked: bool):
        self.apply_theme('dark' if checked else 'light')

    def apply_theme(self, which: str):
        pal = DARK if which == 'dark' else LIGHT
        app = QApplication.instance()
        if app:
            app.setStyleSheet(build_stylesheet(pal))
        self.current_theme = which

    def on_all_toggled(self, checked: bool):
        for _cb in (self.cb_items, self.cb_buildings, self.cb_thralls):
            _cb.blockSignals(True)
            _cb.setChecked(checked)
            _cb.blockSignals(False)

    def on_category_toggled(self, checked: bool):
        all_checked = all(cb.isChecked() for cb in (self.cb_items, self.cb_buildings, self.cb_thralls))
        self.cb_all.blockSignals(True)
        self.cb_all.setChecked(all_checked)
        self.cb_all.blockSignals(False)

    def _clear_selections(self):
        self.selected_item_keys = None
        self.selected_building_object_ids = None
        self.selected_thrall_ids = None

    def on_source_changed(self):
        self._clear_selections()
        self._sync_clan_controls(auto_enable_clan=True)
        self.update_category_counts()

    def on_clan_toggled(self):
        self.selected_building_object_ids = None
        self.selected_thrall_ids = None
        self.cb_clan_to_target_guild.setEnabled(self.cb_include_clan.isChecked())
        if not self.cb_include_clan.isChecked():
            self.cb_clan_to_target_guild.setChecked(False)
        self.update_category_counts()

    def _sync_clan_controls(self, auto_enable_clan: bool = False):
        src = self._selected_character(self.src_combo)
        has_guild = bool(src and src.get('guild'))
        self.cb_include_clan.setEnabled(has_guild)
        self.cb_include_clan.blockSignals(True)
        if not has_guild:
            self.cb_include_clan.setChecked(False)
            self.cb_clan_to_target_guild.setChecked(False)
        elif auto_enable_clan:
            # Funcom stores placed followers under the guildId, not the character id.
            self.cb_include_clan.setChecked(True)
        self.cb_include_clan.blockSignals(False)
        self.cb_clan_to_target_guild.setEnabled(self.cb_include_clan.isChecked())

    def on_db_changed(self):
        db = self.db_path.text().strip()
        self._clear_selections()
        self.lbl_db_warning.setText('')
        if not db or not os.path.exists(db):
            return
        warnings = []
        if db_utils.db_appears_in_use(db):
            warnings.append(
                'This database looks live (WAL/SHM file or lock). Stop the dedicated server '
                'and the game client before transferring — Funcom will overwrite unsaved edits.'
            )
        try:
            report = db_utils.schema_report(db)
            missing = report.get('missing_expected') or []
            if missing:
                warnings.append('Missing expected tables: ' + ', '.join(missing))
            if not report.get('has_properties'):
                warnings.append('No properties table — thrall/pet transfer will do nothing.')
        except Exception as e:
            warnings.append(f'Could not inspect database: {e}')
        self.lbl_db_warning.setText(' '.join(warnings))
        self.populate_source_combo()
        self.populate_target_combo()
        self.populate_handoff_combo()
        self.update_handoff_accounts_label()

    def on_mode_changed(self):
        mode = self.mode_combo.currentData()
        self.mode_stack.setCurrentIndex(0 if mode == 'transfer' else 1)
        if mode == 'handoff':
            self.update_handoff_accounts_label()

    def browse_siptah_db(self):
        path, _ = QFileDialog.getOpenFileName(
            self, 'Select dlc_siptah.db', str(Path(self.db_path.text()).parent),
            'SQLite DB (*.db *.sqlite *.sqlite3);;All Files (*)'
        )
        if path:
            self.siptah_db_path.setText(path)

    def browse_game_ini(self):
        path, _ = QFileDialog.getOpenFileName(
            self, 'Select Game.ini', str(Path.home()),
            'INI Files (*.ini);;All Files (*)'
        )
        if path:
            self.game_ini_path.setText(path)
            acct = db_utils.parse_game_ini_master_account_id(path)
            if acct:
                self.target_account_id.setText(acct)
            else:
                QMessageBox.warning(
                    self, 'No Account ID',
                    'Could not find MasterAccountId in that Game.ini. Paste it manually or launch Conan once.'
                )

    def populate_handoff_combo(self):
        self._populate_combo(self.handoff_char_combo)

    def update_handoff_accounts_label(self):
        db = self.db_path.text().strip()
        if not db or not os.path.exists(db):
            self.lbl_handoff_accounts.setText('')
            return
        try:
            accounts = db_utils.list_accounts(db)
            if not accounts:
                self.lbl_handoff_accounts.setText('No account table found in this database.')
                return
            lines = ['Accounts in save: ' + ', '.join(_account_label(a) for a in accounts)]
            self.lbl_handoff_accounts.setText('\n'.join(lines))
        except Exception as e:
            self.lbl_handoff_accounts.setText(f'Could not read accounts: {e}')

    def _selected_handoff_char_id(self) -> Optional[int]:
        c = self._selected_character(self.handoff_char_combo)
        return int(c['id']) if c else None

    def _handoff_db_paths(self) -> List[str]:
        paths = [self.db_path.text().strip()]
        siptah = self.siptah_db_path.text().strip()
        if self.cb_apply_siptah.isChecked() and siptah and os.path.isfile(siptah):
            paths.append(siptah)
        return paths

    def _require_handoff_inputs(self):
        db = self.db_path.text().strip()
        if not db or not os.path.exists(db):
            QMessageBox.critical(self, 'Error', 'Please select a valid game.db file.')
            return None
        char_id = self._selected_handoff_char_id()
        if char_id is None:
            QMessageBox.critical(self, 'Error', 'Select the character to keep.')
            return None
        target = self.target_account_id.text().strip()
        if not target:
            QMessageBox.critical(
                self, 'Error',
                'Enter Person B\'s Master Account ID or browse their Game.ini.'
            )
            return None
        return db, char_id, target

    def _confirm_db_not_live(self, db: str) -> bool:
        if not db_utils.db_appears_in_use(db):
            return True
        warn = QMessageBox(self)
        warn.setIcon(QMessageBox.Warning)
        warn.setWindowTitle('Database may be in use')
        warn.setTextFormat(Qt.RichText)
        warn.setText(
            "<b>Stop the Conan Exiles dedicated server and the game client first.</b><br><br>"
            "A live save (or leftover WAL file) will overwrite these edits on the next Funcom write."
        )
        warn.setInformativeText('I have stopped the server and the game.')
        warn.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
        return warn.exec() == QMessageBox.Ok

    def _fill_handoff_results_table(self, sim: dict):
        self.table.setRowCount(0)
        rows = [
            ('Account user (before)', sim.get('source_account_user_before', '')),
            ('Account user (after)', sim.get('target_account_user_after', '')),
            ('Rebind account.user', 'yes' if sim.get('will_rebind_account_user') else 'no'),
            ('Repoint playerId', 'yes' if sim.get('will_repoint_player_id') else 'no'),
            ('Characters to remove', ', '.join(str(x) for x in sim.get('characters_to_remove', [])) or 'none'),
        ]
        assets = sim.get('asset_counts') or {}
        for key in ('items', 'buildings', 'thralls'):
            rows.append((f'Assets ({key}) unchanged', str(assets.get(key, 0))))
        for label, val in rows:
            i = self.table.rowCount()
            self.table.insertRow(i)
            self.table.setItem(i, 0, QTableWidgetItem(label))
            self.table.setItem(i, 1, QTableWidgetItem(str(val)))
            self.table.setItem(i, 2, QTableWidgetItem(''))

    def show_handoff_summary(self, sim: dict) -> bool:
        dlg = QDialog(self)
        dlg.setWindowTitle('Pre-Handoff Summary')
        dlg.setMinimumSize(560, 420)
        v = QVBoxLayout(dlg)
        lbl = QLabel('Account rebind plan (in-game assets stay on the same character id)')
        lbl.setStyleSheet('font-weight:600; padding-bottom:6px;')
        v.addWidget(lbl)
        tbl = QTableWidget(0, 2)
        tbl.setHorizontalHeaderLabels(['Field', 'Value'])
        tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        fields = [
            ('Source character', sim.get('source_char_id')),
            ('Account before', sim.get('source_account_user_before')),
            ('Account after', sim.get('target_account_user_after')),
            ('Rebind account.user', sim.get('will_rebind_account_user')),
            ('Repoint playerId', sim.get('will_repoint_player_id')),
            ('Remove characters', sim.get('characters_to_remove')),
        ]
        assets = sim.get('asset_counts') or {}
        fields.extend([
            ('Items (unchanged)', assets.get('items', 0)),
            ('Buildings (unchanged)', assets.get('buildings', 0)),
            ('Thralls (unchanged)', assets.get('thralls', 0)),
        ])
        for k, val in fields:
            i = tbl.rowCount()
            tbl.insertRow(i)
            tbl.setItem(i, 0, QTableWidgetItem(str(k)))
            tbl.setItem(i, 1, QTableWidgetItem(str(val)))
        v.addWidget(tbl)
        dbs = self._handoff_db_paths()
        if len(dbs) > 1:
            note = QLabel('Will apply to: ' + ', '.join(Path(p).name for p in dbs))
            note.setWordWrap(True)
            v.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        v.addWidget(buttons)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        return dlg.exec() == QDialog.Accepted

    def on_handoff_analyze(self):
        req = self._require_handoff_inputs()
        if not req:
            return
        db, char_id, target = req
        sim = db_utils.simulate_save_handoff(
            db, char_id, target,
            remove_character_ids=None if self.cb_remove_throwaway.isChecked() else [],
        )
        if sim.get('errors'):
            QMessageBox.critical(self, 'Handoff Analysis Failed', '\n'.join(sim['errors']))
            return
        self._fill_handoff_results_table(sim)
        QMessageBox.information(
            self, 'Handoff Analysis',
            'Review the plan. Assets remain on the selected character; only account linkage changes.'
        )

    def on_handoff(self):
        req = self._require_handoff_inputs()
        if not req:
            return
        db, char_id, target = req
        paths = self._handoff_db_paths()
        if not self._confirm_db_not_live(db):
            return

        sim = db_utils.simulate_save_handoff(
            db, char_id, target,
            remove_character_ids=None if self.cb_remove_throwaway.isChecked() else [],
        )
        if sim.get('errors'):
            QMessageBox.critical(self, 'Handoff Failed', '\n'.join(sim['errors']))
            return
        if not self.show_handoff_summary(sim):
            return

        if len(paths) == 1:
            try:
                pre_path = db_utils.create_pre_backup(db)
            except Exception as e:
                QMessageBox.critical(self, 'Backup failed', f'Could not write {db}.pre: {e}')
                return
            stamped = db + f'.bak_{int(time.time())}'
            try:
                shutil.copy2(pre_path, stamped)
            except Exception:
                stamped = pre_path
            ok, changed, msg = db_utils.perform_save_handoff(
                db, char_id, target,
                remove_character_ids=None if self.cb_remove_throwaway.isChecked() else [],
                pre_backup_path=pre_path,
            )
        else:
            ok, changed, msg = db_utils.perform_save_handoff_multi(
                paths, char_id, target,
                remove_character_ids=None if self.cb_remove_throwaway.isChecked() else [],
            )
            pre_path = (changed.get('databases') or {}).get(db, {}).get('backup', db + '.pre')
            stamped = pre_path

        if not ok:
            QMessageBox.critical(self, 'Handoff Failed', msg)
            return

        self._fill_handoff_results_table(sim)
        self.update_handoff_accounts_label()
        self.populate_source_combo()
        self.populate_target_combo()
        self.populate_handoff_combo()
        QMessageBox.information(
            self, 'Handoff Complete',
            f'Save rebind finished.\n\nDatabase: {db}\nRevert copy: {pre_path}\nBackup: {stamped}'
        )
        record = {
            'timestamp': int(time.time()),
            'operation': 'save_handoff',
            'db_paths': paths,
            'pre_transfer_backup': pre_path,
            'source_char_id': char_id,
            'target_account_user': target,
            'remove_character_ids': sim.get('characters_to_remove', []),
            'changed': changed,
            'simulation': sim,
            'message': msg,
        }
        try:
            db_utils.write_handoff_audit_csv(str(APP_DIR / 'transfers_audit.csv'), record)
        except Exception as e:
            import traceback
            db_utils._log(f'Handoff audit write failed: {e}\n{traceback.format_exc()}')

    def include_clan(self) -> bool:
        return self.cb_include_clan.isChecked()

    def update_category_counts(self):
        db = self.db_path.text().strip()
        if not db or not os.path.exists(db):
            return
        source_id = self.get_selected_source_id()
        if source_id is None:
            return
        try:
            sim = db_utils.simulate_update_counts(db, source_id, ['all'], include_clan_assets=self.include_clan())
            items = sim.get('items', 0)
            blds = sim.get('buildings', 0)
            thralls = sim.get('thralls', 0)
            self.lbl_items_count.setText(f"({items})")
            self.lbl_buildings_count.setText(f"({blds})")
            bits = []
            if sim.get('thralls_following'):
                bits.append(f"{sim['thralls_following']} following")
            if sim.get('thralls_clan'):
                bits.append(f"{sim['thralls_clan']} clan")
            if sim.get('thralls_personal'):
                bits.append(f"{sim['thralls_personal']} placed")
            extra = f": {', '.join(bits)}" if bits else ''
            self.lbl_thralls_count.setText(f"({thralls}{extra})")

            def apply_state(count, checkbox, details_btn, label):
                enabled = bool(count)
                checkbox.setEnabled(enabled)
                if not enabled:
                    checkbox.setChecked(False)
                if details_btn:
                    details_btn.setEnabled(enabled)
                label.setStyleSheet('' if enabled else 'color: #94a3b8')

            apply_state(items, self.cb_items, self.btn_items_details, self.lbl_items_count)
            apply_state(blds, self.cb_buildings, self.btn_buildings_details, self.lbl_buildings_count)
            apply_state(thralls, self.cb_thralls, self.btn_thralls_details, self.lbl_thralls_count)
        except Exception:
            pass

    def _populate_combo(self, combo: QComboBox):
        db = self.db_path.text().strip()
        if not db or not os.path.exists(db):
            return
        previous = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        try:
            chars = db_utils.list_characters(db)
            for c in chars:
                combo.addItem(_char_label(c), c)
            if previous:
                pid = previous.get('id') if isinstance(previous, dict) else previous
                for i in range(combo.count()):
                    data = combo.itemData(i)
                    if isinstance(data, dict) and data.get('id') == pid:
                        combo.setCurrentIndex(i)
                        break
        except Exception as e:
            QMessageBox.warning(self, 'Warning', f'Failed to load characters: {e}')
        combo.blockSignals(False)

    def populate_source_combo(self):
        self._populate_combo(self.src_combo)
        self._sync_clan_controls(auto_enable_clan=True)
        self.update_category_counts()

    def populate_target_combo(self):
        self._populate_combo(self.tgt_combo)

    def _selected_character(self, combo: QComboBox) -> Optional[dict]:
        data = combo.currentData()
        return data if isinstance(data, dict) else None

    def get_selected_source_id(self):
        c = self._selected_character(self.src_combo)
        return int(c['id']) if c else None

    def get_selected_target_id(self):
        c = self._selected_character(self.tgt_combo)
        return int(c['id']) if c else None

    def _selected_categories(self) -> List[str]:
        if self.cb_all.isChecked():
            return ['all']
        cats = []
        if self.cb_items.isChecked():
            cats.append('items')
        if self.cb_buildings.isChecked():
            cats.append('buildings')
        if self.cb_thralls.isChecked():
            cats.append('thralls')
        return cats

    def _load_xref(self):
        ws = Path(__file__).resolve().parents[1]
        candidate = ws / 'item_xref'
        if candidate.exists():
            return db_utils.load_item_xref_file(str(candidate))
        return {}

    def _show_selection_dialog(self, rows, columns, title):
        """columns: list of (header, key). First data column after Select is the id key."""
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.setMinimumSize(800, 420)
        v = QVBoxLayout(dlg)
        hint = QLabel(
            'Checked rows are transferred. OK with nothing checked transfers none of this category. '
            'Cancel keeps the previous selection (or all, if you never chose).'
        )
        hint.setWordWrap(True)
        v.addWidget(hint)
        tbl = QTableWidget(0, 1 + len(columns))
        tbl.setHorizontalHeaderLabels(['Select'] + [c[0] for c in columns])
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        for r in rows:
            i = tbl.rowCount()
            tbl.insertRow(i)
            chk = QTableWidgetItem('')
            chk.setFlags(chk.flags() | Qt.ItemIsUserCheckable)
            chk.setCheckState(Qt.Unchecked)
            tbl.setItem(i, 0, chk)
            for col_i, (_header, key) in enumerate(columns, start=1):
                val = r.get(key)
                item = QTableWidgetItem('' if val is None else str(val))
                if col_i == 1:
                    item.setData(Qt.UserRole, r)
                tbl.setItem(i, col_i, item)
        v.addWidget(tbl)
        btn_row = QHBoxLayout()
        btn_all = QPushButton('Select All')
        btn_none = QPushButton('Select None')
        btn_all.clicked.connect(lambda: self._set_all_checks(tbl, True))
        btn_none.clicked.connect(lambda: self._set_all_checks(tbl, False))
        btn_row.addWidget(btn_all)
        btn_row.addWidget(btn_none)
        btn_row.addStretch(1)
        v.addLayout(btn_row)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        v.addWidget(buttons)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        if dlg.exec() != QDialog.Accepted:
            return None
        selected = []
        for r in range(tbl.rowCount()):
            it = tbl.item(r, 0)
            id_item = tbl.item(r, 1)
            if it and id_item and it.checkState() == Qt.Checked:
                payload = id_item.data(Qt.UserRole)
                selected.append(payload if isinstance(payload, dict) else None)
        return [p for p in selected if p is not None]

    @staticmethod
    def _set_all_checks(tbl: QTableWidget, checked: bool):
        state = Qt.Checked if checked else Qt.Unchecked
        for r in range(tbl.rowCount()):
            it = tbl.item(r, 0)
            if it:
                it.setCheckState(state)

    def _require_db_and_source(self):
        db = self.db_path.text().strip()
        if not db or not os.path.exists(db):
            QMessageBox.critical(self, 'Error', 'Please select a valid game.db file.')
            return None, None
        source_id = self.get_selected_source_id()
        if source_id is None:
            QMessageBox.critical(self, 'Error', 'Select a valid source.')
            return None, None
        return db, source_id

    def show_items_details(self):
        db, source_id = self._require_db_and_source()
        if source_id is None:
            return
        rows = db_utils.list_items_for_owner(db, source_id, self._load_xref())
        if not rows:
            QMessageBox.information(self, 'No Items', 'No carried items found for this character.')
            return
        picked = self._show_selection_dialog(
            rows,
            [
                ('Slot', 'item_id'),
                ('Inv', 'inv_label'),
                ('Template', 'template_name'),
                ('Template ID', 'template_id'),
            ],
            'Select carried items to transfer',
        )
        if picked is None:
            return
        self.selected_item_keys = [
            (int(r['item_id']), r.get('inv_type')) for r in picked if r.get('item_id') is not None
        ]

    def show_buildings_details(self):
        db, source_id = self._require_db_and_source()
        if source_id is None:
            return
        rows = db_utils.list_buildings_for_owner(
            db, source_id, self._load_xref(), include_clan_assets=self.include_clan()
        )
        if not rows:
            QMessageBox.information(self, 'No Buildings', 'No buildings found for this owner.')
            return
        for r in rows:
            r['kind'] = 'clan' if r.get('owned_by_guild') else 'personal'
            r['info'] = r.get('class') or ''
        picked = self._show_selection_dialog(
            rows,
            [
                ('Object ID', 'object_id'),
                ('Kind', 'kind'),
                ('Template', 'template_name'),
                ('Class', 'info'),
            ],
            'Select buildings to transfer',
        )
        if picked is None:
            return
        self.selected_building_object_ids = [int(r['object_id']) for r in picked]

    def show_thralls_details(self):
        db, source_id = self._require_db_and_source()
        if source_id is None:
            return
        rows = db_utils.list_thralls_for_owner(db, source_id, include_clan_assets=self.include_clan())
        if not rows:
            QMessageBox.information(
                self, 'No Thralls',
                'No followers found for this character (follower wheel or clan-placed OwnerUniqueID).'
            )
            return
        for r in rows:
            r['kind'] = r.get('kind') or ('clan' if r.get('owned_by_guild') else 'placed')
        picked = self._show_selection_dialog(
            rows,
            [
                ('Actor ID', 'follower_id'),
                ('Kind', 'kind'),
                ('Class', 'class'),
                ('Coords', 'coords'),
            ],
            'Select placed followers to transfer',
        )
        if picked is None:
            return
        self.selected_thrall_ids = [int(r['follower_id']) for r in picked]

    def on_export_audit(self):
        audit = APP_DIR / 'transfers_audit.csv'
        if not audit.exists():
            QMessageBox.information(self, 'No Audit', 'No audit file found to export.')
            return
        path, _ = QFileDialog.getSaveFileName(
            self, 'Export Audit CSV', str(Path.home() / 'transfers_audit.csv'), 'CSV Files (*.csv)'
        )
        if not path:
            return
        try:
            shutil.copy2(str(audit), path)
            QMessageBox.information(self, 'Exported', f'Audit CSV exported to: {path}')
        except Exception as e:
            QMessageBox.critical(self, 'Error', f'Failed to export audit: {e}')

    def _load_audit_records(self):
        audit = APP_DIR / 'transfers_audit.csv'
        if not audit.exists():
            return []
        recs = []
        try:
            with open(audit, 'r', encoding='utf-8') as f:
                for r in csv.DictReader(f):
                    if r.get('changed_json'):
                        try:
                            r['changed'] = json.loads(r['changed_json'])
                        except Exception:
                            r['changed'] = {}
                    for k in (
                        'categories', 'item_ids', 'building_object_ids', 'thrall_ids',
                        'before_source', 'after_source', 'before_target', 'after_target',
                    ):
                        if k in r and r[k]:
                            try:
                                r[k] = json.loads(r[k])
                            except Exception:
                                pass
                    recs.append(r)
        except Exception:
            return []
        return recs

    def on_view_audit(self):
        recs = self._load_audit_records()
        if not recs:
            QMessageBox.information(self, 'No Audit', 'No audit records found.')
            return
        dlg = QDialog(self)
        dlg.setWindowTitle('Transfers Audit')
        dlg.setMinimumSize(1200, 520)
        v = QVBoxLayout(dlg)
        columns = [
            'Timestamp', 'DB Path', 'Source', 'Target', 'Categories',
            'Items Δ', 'Buildings Δ', 'Thralls Δ',
            'Before Src', 'After Src', 'Before Tgt', 'After Tgt', 'Changed',
        ]
        tbl = QTableWidget(0, len(columns))
        tbl.setHorizontalHeaderLabels(columns)
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for r in recs:
            i = tbl.rowCount()
            tbl.insertRow(i)
            tbl.setItem(i, 0, QTableWidgetItem(str(r.get('timestamp', ''))))
            tbl.setItem(i, 1, QTableWidgetItem(r.get('db_path', '')))
            tbl.setItem(i, 2, QTableWidgetItem(str(r.get('source_id', ''))))
            tbl.setItem(i, 3, QTableWidgetItem(str(r.get('target_id', ''))))
            cats = r.get('categories')
            tbl.setItem(i, 4, QTableWidgetItem(','.join(cats) if isinstance(cats, list) else str(cats)))

            def safe_get(d, k):
                return d.get(k, 0) if isinstance(d, dict) else 0

            before_tgt = r.get('before_target', {})
            after_tgt = r.get('after_target', {})
            tbl.setItem(i, 5, QTableWidgetItem(str(safe_get(after_tgt, 'items') - safe_get(before_tgt, 'items'))))
            tbl.setItem(i, 6, QTableWidgetItem(str(safe_get(after_tgt, 'buildings') - safe_get(before_tgt, 'buildings'))))
            tbl.setItem(i, 7, QTableWidgetItem(str(safe_get(after_tgt, 'thralls') - safe_get(before_tgt, 'thralls'))))
            tbl.setItem(i, 8, QTableWidgetItem(json.dumps(r.get('before_source', {}), ensure_ascii=False)))
            tbl.setItem(i, 9, QTableWidgetItem(json.dumps(r.get('after_source', {}), ensure_ascii=False)))
            tbl.setItem(i, 10, QTableWidgetItem(json.dumps(before_tgt, ensure_ascii=False)))
            tbl.setItem(i, 11, QTableWidgetItem(json.dumps(after_tgt, ensure_ascii=False)))
            changed = r.get('changed')
            if isinstance(changed, dict):
                changed_s = ', '.join(f'{k}:{v}' for k, v in changed.items())
            else:
                changed_s = str(changed)
            tbl.setItem(i, 12, QTableWidgetItem(changed_s))
        for c in range(tbl.columnCount() - 1):
            tbl.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeToContents)
        tbl.horizontalHeader().setSectionResizeMode(tbl.columnCount() - 1, QHeaderView.Stretch)
        v.addWidget(tbl)
        h = QHBoxLayout()
        refresh = QPushButton('Refresh')
        refresh.clicked.connect(lambda: dlg.done(2))
        close = QPushButton('Close')
        close.clicked.connect(dlg.accept)
        h.addWidget(refresh)
        h.addWidget(close)
        v.addLayout(h)
        if dlg.exec() == 2:
            self.on_view_audit()

    def on_revert_transfer(self):
        path, _ = QFileDialog.getOpenFileName(
            self, 'Select transferred DB to revert', str(APP_DIR),
            'DB Files (*.db);;All Files (*)'
        )
        if not path:
            return
        backup = db_utils.find_pre_backup(path)
        if not backup:
            backup, _ = QFileDialog.getOpenFileName(
                self, 'Select backup (.pre or .bak_*)', str(Path(path).parent),
                'Backup Files (*.pre *.db);;All Files (*)'
            )
            if not backup:
                return
        ok = QMessageBox.question(
            self, 'Confirm Revert',
            f'Restore {Path(path).name} from\n{backup}?'
        )
        if ok != QMessageBox.StandardButton.Yes:
            return
        success, msg = db_utils.revert_transfer(path, backup)
        if success:
            QMessageBox.information(self, 'Reverted', msg)
            if path == self.db_path.text().strip():
                self.on_db_changed()
        else:
            QMessageBox.critical(self, 'Revert Failed', msg)

    def _fill_results_table(self, before: dict, transferred: dict):
        rows = [
            ('Items (carried inventory)', 'item_inventory'),
            ('Item properties', 'item_properties'),
            ('Buildings (personal)', 'buildings_personal'),
            ('Buildings (clan)', 'buildings_clan'),
            ('Buildings (total)', 'buildings'),
            ('Thralls/Pets (personal)', 'thralls_personal'),
            ('Thralls/Pets (clan)', 'thralls_clan'),
            ('Thralls/Pets (total)', 'thralls'),
        ]
        self.table.setRowCount(0)
        for label, key in rows:
            b = before.get(key, 0)
            t = transferred.get(key, 0)
            if b == 0 and t == 0 and key not in ('item_inventory', 'buildings', 'thralls'):
                continue
            i = self.table.rowCount()
            self.table.insertRow(i)
            self.table.setItem(i, 0, QTableWidgetItem(label))
            self.table.setItem(i, 1, QTableWidgetItem(str(b)))
            self.table.setItem(i, 2, QTableWidgetItem(str(t)))

    def on_analyze(self):
        db, source_id = self._require_db_and_source()
        if source_id is None:
            return
        cats = self._selected_categories()
        if not cats:
            QMessageBox.critical(self, 'Error', 'Select one or more categories to analyze.')
            return
        sim = db_utils.simulate_update_counts(db, source_id, cats, include_clan_assets=self.include_clan())
        zeros = {k: 0 for k in sim}
        self._fill_results_table(sim, zeros)
        QMessageBox.information(self, 'Dry-run Complete', 'Analysis complete. Review counts before transferring.')

    def show_pretransfer_summary(self, counts: dict, selected_items, selected_buildings, selected_thralls) -> bool:
        dlg = QDialog(self)
        dlg.setWindowTitle('Pre-Transfer Summary')
        dlg.setMinimumSize(560, 380)
        v = QVBoxLayout(dlg)
        lbl = QLabel('Rows that will be rewritten on disk')
        lbl.setStyleSheet('font-weight:600; padding-bottom:6px;')
        v.addWidget(lbl)
        tbl = QTableWidget(0, 2)
        tbl.setHorizontalHeaderLabels(['Category', 'Count'])
        tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        for k in (
            'item_inventory', 'item_properties', 'buildings_personal', 'buildings_clan',
            'buildings', 'thralls_personal', 'thralls_clan', 'thralls',
        ):
            i = tbl.rowCount()
            tbl.insertRow(i)
            tbl.setItem(i, 0, QTableWidgetItem(k.replace('_', ' ').title()))
            tbl.setItem(i, 1, QTableWidgetItem(str(counts.get(k, 0))))
        v.addWidget(tbl)

        def subset_text(sel, noun):
            if sel is None:
                return f'all matching {noun}'
            return f'{len(sel)} selected {noun}'

        details = QLabel(
            f'Subset: {subset_text(selected_items, "items")}, '
            f'{subset_text(selected_buildings, "buildings")}, '
            f'{subset_text(selected_thralls, "followers")}.'
        )
        details.setWordWrap(True)
        v.addWidget(details)
        if self.include_clan():
            note = QLabel(
                'Clan include is ON. Every building and follower owned by the source clan '
                '(not just this character) will be reassigned unless you narrowed Details.'
            )
            note.setWordWrap(True)
            note.setStyleSheet('color: #b45309;')
            v.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        v.addWidget(buttons)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        return dlg.exec() == QDialog.Accepted

    def on_transfer(self):
        db, source_id = self._require_db_and_source()
        if source_id is None:
            return
        target_id = self.get_selected_target_id()
        if target_id is None:
            QMessageBox.critical(self, 'Error', 'Select a valid target.')
            return
        if source_id == target_id:
            QMessageBox.critical(self, 'Error', 'Source and target must be different characters.')
            return
        cats = self._selected_categories()
        if not cats:
            QMessageBox.critical(self, 'Error', 'Select one or more categories to transfer.')
            return

        if db_utils.db_appears_in_use(db):
            if not self._confirm_db_not_live(db):
                return

        try:
            pre_path = db_utils.create_pre_backup(db)
        except Exception as e:
            QMessageBox.critical(self, 'Backup failed', f'Could not write {db}.pre: {e}')
            return
        stamped = db + f'.bak_{int(time.time())}'
        try:
            shutil.copy2(pre_path, stamped)
        except Exception:
            stamped = pre_path

        sim = db_utils.simulate_update_counts(db, source_id, cats, include_clan_assets=self.include_clan())
        sel_items = self.selected_item_keys
        sel_buildings = self.selected_building_object_ids
        sel_thralls = self.selected_thrall_ids
        if not self.show_pretransfer_summary(sim, sel_items, sel_buildings, sel_thralls):
            return

        include_clan = self.include_clan()
        to_guild = include_clan and self.cb_clan_to_target_guild.isChecked()
        before_source = db_utils.counts_for_owner(db, source_id, include_clan)
        before_target = db_utils.counts_for_owner(db, target_id, include_clan)
        success, changed, msg = db_utils.perform_transfer(
            db, source_id, target_id, cats, dry_run=False,
            item_keys=sel_items,
            building_object_ids=sel_buildings,
            thrall_ids=sel_thralls,
            include_clan_assets=include_clan,
            clan_assets_to_target_guild=to_guild,
            pre_backup_path=pre_path,
        )
        after_source = db_utils.counts_for_owner(db, source_id, include_clan)
        after_target = db_utils.counts_for_owner(db, target_id, include_clan)
        if not success:
            QMessageBox.critical(self, 'Transfer Failed', msg)
            return

        self._fill_results_table(sim, changed)
        QMessageBox.information(
            self, 'Transfer Complete',
            f'Transfer finished.\n\nDatabase: {db}\nRevert copy: {pre_path}\nTimestamped backup: {stamped}'
        )
        self._clear_selections()
        self.update_category_counts()

        record = {
            'timestamp': int(time.time()),
            'db_path': str(db),
            'pre_transfer_backup': pre_path,
            'source_id': source_id,
            'target_id': target_id,
            'categories': cats,
            'item_ids': sel_items or [],
            'building_object_ids': sel_buildings or [],
            'thrall_ids': sel_thralls or [],
            'changed_json': changed,
            'message': msg,
            'before_source': before_source,
            'after_source': after_source,
            'before_target': before_target,
            'after_target': after_target,
            'include_clan_assets': include_clan,
            'clan_assets_to_target_guild': to_guild,
        }
        try:
            db_utils.write_audit_csv(str(APP_DIR / 'transfers_audit.csv'), record)
        except Exception as e:
            import traceback
            db_utils._log(f'Audit log write failed: {e}\n{traceback.format_exc()}')
            QMessageBox.warning(self, 'Audit Warning', f'Failed to write audit CSV: {e}')


if __name__ == '__main__':
    if sys.platform == 'win32':
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('conanexiles.databasetransfer.tool')
        except Exception:
            pass

    app = QApplication(sys.argv)
    try:
        ico = QIcon(str(ICON_PATH))
        if not ico.isNull():
            app.setWindowIcon(ico)
    except Exception:
        pass
    w = TransferApp()
    try:
        ico = QIcon(str(ICON_PATH))
        if not ico.isNull():
            w.setWindowIcon(ico)
    except Exception:
        pass
    w.show()
    sys.exit(app.exec())
