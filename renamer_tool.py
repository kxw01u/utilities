 (cd "$(git rev-parse --show-toplevel)" && git apply --3way <<'EOF' 
diff --git a/rename_tool.py b/rename_tool.py
new file mode 100644
index 0000000000000000000000000000000000000000..b751eee0b4f9751506fed57280b7e0f6612d68b5
--- /dev/null
+++ b/rename_tool.py
@@ -0,0 +1,448 @@
+"""Desktop filename renaming utility with replacement rules and EXIF-aware templates."""
+from __future__ import annotations
+
+import importlib
+import os
+import sys
+from dataclasses import dataclass
+from typing import Dict, List, Optional, Tuple
+
+if importlib.util.find_spec("PIL") is not None:  # pragma: no cover - environment dependent
+    from PIL import Image, ExifTags  # type: ignore
+else:  # pragma: no cover - Pillow is optional at runtime
+    Image = None  # type: ignore
+    ExifTags = None  # type: ignore
+
+from PyQt6.QtCore import Qt
+from PyQt6.QtWidgets import (
+    QApplication,
+    QDialog,
+    QDialogButtonBox,
+    QFileDialog,
+    QHBoxLayout,
+    QLabel,
+    QLineEdit,
+    QMessageBox,
+    QPushButton,
+    QTableWidget,
+    QTableWidgetItem,
+    QTextEdit,
+    QVBoxLayout,
+    QWidget,
+)
+
+
+@dataclass
+class FileInfo:
+    """Container tracking original and current filenames along with custom template."""
+
+    original_name: str
+    current_name: str
+    custom_template: Optional[str] = None
+    preview_name: str = ""
+
+    def reset(self) -> None:
+        self.current_name = self.original_name
+        self.custom_template = None
+        self.preview_name = self.original_name
+
+
+class TemplateDialog(QDialog):
+    """Dialog allowing the user to craft an EXIF-aware rename template."""
+
+    def __init__(self, file_path: str, existing_template: Optional[str], parent: QWidget | None = None):
+        super().__init__(parent)
+        self.setWindowTitle("Custom Rename Template")
+        self._file_path = file_path
+
+        layout = QVBoxLayout(self)
+
+        intro = QLabel(
+            "Use placeholders to build the filename. Available placeholders are listed below.\n"
+            "• <EXIF_date> → shooting date in YYYYMMDD (requires EXIF)\n"
+            "• <EXIF_datetime> → shooting date & time in YYYYMMDD_HHMMSS (requires EXIF)\n"
+            "• <ORIGINAL> → original filename without extension\n"
+            "• <EXT> → original extension including dot\n"
+            "• ### → sequential number padded to three digits"
+        )
+        intro.setWordWrap(True)
+        layout.addWidget(intro)
+
+        exif_summary = QLabel(self._build_exif_summary())
+        exif_summary.setWordWrap(True)
+        exif_summary.setStyleSheet("color:#444;margin:6px 0;")
+        layout.addWidget(exif_summary)
+
+        self.text_edit = QLineEdit(self)
+        if existing_template:
+            self.text_edit.setText(existing_template)
+        layout.addWidget(self.text_edit)
+
+        button_row = QHBoxLayout()
+        for placeholder in ("<EXIF_date>", "<EXIF_datetime>", "<ORIGINAL>", "<EXT>", "###"):
+            btn = QPushButton(placeholder, self)
+            btn.clicked.connect(lambda checked=False, ph=placeholder: self.insert_placeholder(ph))
+            button_row.addWidget(btn)
+        layout.addLayout(button_row)
+
+        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
+        buttons.accepted.connect(self.accept)
+        buttons.rejected.connect(self.reject)
+        layout.addWidget(buttons)
+
+    def insert_placeholder(self, placeholder: str) -> None:
+        current = self.text_edit.text()
+        cursor_pos = self.text_edit.cursorPosition()
+        updated = current[:cursor_pos] + placeholder + current[cursor_pos:]
+        self.text_edit.setText(updated)
+        self.text_edit.setCursorPosition(cursor_pos + len(placeholder))
+
+    def _build_exif_summary(self) -> str:
+        if Image is None:
+            return "Pillow is not available, EXIF values cannot be read."
+        if not os.path.exists(self._file_path):
+            return "File does not exist."
+        try:
+            with Image.open(self._file_path) as img:
+                exif_data = img.getexif() or {}
+        except Exception as exc:  # pragma: no cover - best effort only
+            return f"Unable to read EXIF data: {exc}"
+
+        if not exif_data:
+            return "No EXIF metadata found."
+
+        tag_names: Dict[str, int] = {}
+        if ExifTags is not None:
+            tag_names = {v: k for k, v in ExifTags.TAGS.items()}
+
+        interesting = []
+        for desired in ("DateTimeOriginal", "DateTime", "DateTimeDigitized"):
+            key = tag_names.get(desired)
+            if key and key in exif_data:
+                interesting.append(f"{desired}: {exif_data.get(key)}")
+        if not interesting:
+            return "No timestamp EXIF values found."
+        return "\n".join(interesting)
+
+    def template(self) -> str:
+        return self.text_edit.text().strip()
+
+
+class RenameAssistant(QWidget):
+    """Main widget implementing the desktop tool described by the user."""
+
+    COL_BEFORE = 0
+    COL_CURRENT = 1
+    COL_CUSTOM = 2
+    COL_PREVIEW = 3
+    COL_UNDO = 4
+    COL_COMMIT = 5
+
+    def __init__(self):
+        super().__init__()
+        self.setWindowTitle("Filename Replace Assistant")
+        self.resize(900, 600)
+
+        self.current_directory: Optional[str] = None
+        self.file_infos: List[FileInfo] = []
+
+        outer = QVBoxLayout(self)
+        path_row = QHBoxLayout()
+        outer.addLayout(path_row)
+
+        self.path_edit = QLineEdit(self)
+        self.path_edit.editingFinished.connect(self.handle_manual_path)
+        self.browse_button = QPushButton("Browse…", self)
+        self.browse_button.clicked.connect(self.choose_directory)
+        path_row.addWidget(QLabel("Path:", self))
+        path_row.addWidget(self.path_edit)
+        path_row.addWidget(self.browse_button)
+
+        replace_row = QHBoxLayout()
+        outer.addLayout(replace_row)
+
+        self.replace_edit = QTextEdit(self)
+        self.replace_edit.setPlaceholderText("Enter replacement rules e.g. abc/ab;sss/;")
+        self.replace_edit.textChanged.connect(self.update_all_previews)
+        replace_row.addWidget(QLabel("Replace:", self))
+        replace_row.addWidget(self.replace_edit, 1)
+
+        self.table = QTableWidget(0, 6, self)
+        self.table.setHorizontalHeaderLabels(["Before", "Current", "Custom", "Preview", "Undo", "Commit"])
+        self.table.verticalHeader().setVisible(False)
+        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
+        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
+        self.table.cellDoubleClicked.connect(self.handle_cell_double_click)
+        outer.addWidget(self.table, 1)
+
+        hint = QLabel(
+            "Double-click the Custom column to define an EXIF-aware template for that file.\n"
+            "The Preview column shows the filename after applying replacement rules or the custom template."
+        )
+        hint.setWordWrap(True)
+        hint.setStyleSheet("color:#555;font-size:11px;")
+        outer.addWidget(hint)
+
+    # ----- directory handling -------------------------------------------------
+    def handle_manual_path(self) -> None:
+        text = self.path_edit.text().strip()
+        if not text or text == self.current_directory:
+            return
+        self.load_directory(text)
+
+    def choose_directory(self) -> None:
+        directory = QFileDialog.getExistingDirectory(self, "Select folder", self.current_directory or os.getcwd())
+        if directory:
+            self.path_edit.setText(directory)
+            self.load_directory(directory)
+
+    def load_directory(self, directory: str) -> None:
+        if not os.path.isdir(directory):
+            QMessageBox.warning(self, "Invalid directory", "Selected path is not a directory.")
+            return
+        self.current_directory = directory
+        self.file_infos.clear()
+        self.table.setRowCount(0)
+
+        for entry in sorted(os.listdir(directory)):
+            full_path = os.path.join(directory, entry)
+            if not os.path.isfile(full_path):
+                continue
+            info = FileInfo(original_name=entry, current_name=entry, preview_name=entry)
+            self.file_infos.append(info)
+            self._append_row(info)
+
+        self.update_all_previews()
+
+    def _append_row(self, info: FileInfo) -> None:
+        row = self.table.rowCount()
+        self.table.insertRow(row)
+
+        before_item = QTableWidgetItem(info.original_name)
+        current_item = QTableWidgetItem(info.current_name)
+        custom_item = QTableWidgetItem(info.custom_template or "")
+        custom_item.setToolTip("Double-click to edit the custom template for this file.")
+        preview_item = QTableWidgetItem(info.preview_name)
+
+        for item in (before_item, current_item, custom_item, preview_item):
+            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
+
+        self.table.setItem(row, self.COL_BEFORE, before_item)
+        self.table.setItem(row, self.COL_CURRENT, current_item)
+        self.table.setItem(row, self.COL_CUSTOM, custom_item)
+        self.table.setItem(row, self.COL_PREVIEW, preview_item)
+
+        undo_button = QPushButton("Undo", self)
+        undo_button.clicked.connect(lambda checked=False, r=row: self.undo_row(r))
+        commit_button = QPushButton("Commit", self)
+        commit_button.clicked.connect(lambda checked=False, r=row: self.commit_row(r))
+
+        self.table.setCellWidget(row, self.COL_UNDO, undo_button)
+        self.table.setCellWidget(row, self.COL_COMMIT, commit_button)
+
+    # ----- replacements -------------------------------------------------------
+    def parse_rules(self) -> List[Tuple[str, str]]:
+        text = self.replace_edit.toPlainText().strip()
+        if not text:
+            return []
+        rules: List[Tuple[str, str]] = []
+        for segment in text.split(';'):
+            segment = segment.strip()
+            if not segment:
+                continue
+            if '/' not in segment:
+                continue
+            find, replacement = segment.split('/', 1)
+            rules.append((find, replacement))
+        return rules
+
+    def apply_rules(self, name: str, rules: List[Tuple[str, str]]) -> str:
+        result = name
+        for find, replacement in rules:
+            result = result.replace(find, replacement)
+        return result
+
+    def update_all_previews(self) -> None:
+        if not self.file_infos:
+            return
+        rules = self.parse_rules()
+        sequence_counter = 1
+        for row, info in enumerate(self.file_infos):
+            preview, used_sequence = self.compute_preview(info, rules, sequence_counter)
+            info.preview_name = preview
+            preview_item = self.table.item(row, self.COL_PREVIEW)
+            if preview_item is not None:
+                preview_item.setText(preview)
+            if used_sequence:
+                sequence_counter += 1
+
+    def compute_preview(self, info: FileInfo, rules: List[Tuple[str, str]], sequence_counter: int) -> Tuple[str, bool]:
+        if info.custom_template:
+            preview = self.render_template(info, sequence_counter)
+            return preview, '###' in info.custom_template
+        return self.apply_rules(info.current_name, rules), False
+
+    def render_template(self, info: FileInfo, sequence_counter: int) -> str:
+        template = info.custom_template or ""
+        name, ext = os.path.splitext(info.original_name)
+        current_name, current_ext = os.path.splitext(info.current_name)
+        if not name:
+            name = current_name
+        ext = current_ext or ext
+
+        replacements = {
+            '<ORIGINAL>': name,
+            '<EXT>': current_ext or ext,
+            '<EXIF_date>': '',
+            '<EXIF_datetime>': '',
+        }
+
+        if Image is not None and self.current_directory is not None:
+            file_path = os.path.join(self.current_directory, info.current_name)
+            exif_values = self.extract_exif_values(file_path)
+            replacements.update(exif_values)
+
+        result = template
+        for placeholder, value in replacements.items():
+            result = result.replace(placeholder, value)
+
+        if '###' in result:
+            result = result.replace('###', f"{sequence_counter:03d}")
+
+        return result
+
+    def extract_exif_values(self, file_path: str) -> Dict[str, str]:
+        values: Dict[str, str] = {}
+        if Image is None or not os.path.exists(file_path):
+            return values
+        try:
+            with Image.open(file_path) as img:
+                exif = img.getexif() or {}
+        except Exception:  # pragma: no cover - best effort only
+            return values
+        if not exif:
+            return values
+
+        tag_lookup: Dict[str, int] = {}
+        if ExifTags is not None:
+            tag_lookup = {v: k for k, v in ExifTags.TAGS.items()}
+
+        date_value = self._fetch_exif_value(exif, tag_lookup, "DateTimeOriginal") or \
+            self._fetch_exif_value(exif, tag_lookup, "DateTime") or \
+            self._fetch_exif_value(exif, tag_lookup, "DateTimeDigitized")
+
+        if date_value:
+            date_str = date_value.replace(':', '-').replace(' ', '_')
+            parts = date_value.split(' ')
+            if len(parts) == 2:
+                raw_date = parts[0].replace(':', '')
+                raw_time = parts[1].replace(':', '')
+                values['<EXIF_date>'] = raw_date
+                values['<EXIF_datetime>'] = f"{raw_date}_{raw_time}"
+            else:
+                values['<EXIF_date>'] = date_str.replace('-', '')
+                values['<EXIF_datetime>'] = date_str
+        return values
+
+    @staticmethod
+    def _fetch_exif_value(exif: Dict[int, object], tag_lookup: Dict[str, int], desired: str) -> Optional[str]:
+        key = tag_lookup.get(desired)
+        if key and key in exif:
+            value = exif.get(key)
+            if isinstance(value, bytes):
+                try:
+                    value = value.decode('utf-8', errors='ignore')
+                except Exception:
+                    return None
+            return str(value)
+        return None
+
+    # ----- custom template editing -------------------------------------------
+    def handle_cell_double_click(self, row: int, column: int) -> None:
+        if column != self.COL_CUSTOM:
+            return
+        if row >= len(self.file_infos):
+            return
+        info = self.file_infos[row]
+        if self.current_directory is None:
+            QMessageBox.information(self, "No directory", "Please choose a directory first.")
+            return
+        dialog = TemplateDialog(os.path.join(self.current_directory, info.current_name), info.custom_template, self)
+        if dialog.exec() == QDialog.DialogCode.Accepted:
+            template = dialog.template()
+            info.custom_template = template or None
+            custom_item = self.table.item(row, self.COL_CUSTOM)
+            if custom_item is not None:
+                custom_item.setText(info.custom_template or "")
+            self.update_all_previews()
+
+    # ----- undo / commit ------------------------------------------------------
+    def undo_row(self, row: int) -> None:
+        if row >= len(self.file_infos):
+            return
+        info = self.file_infos[row]
+        if self.current_directory is None:
+            return
+        current_path = os.path.join(self.current_directory, info.current_name)
+        original_path = os.path.join(self.current_directory, info.original_name)
+        if not os.path.exists(current_path):
+            QMessageBox.warning(self, "Undo failed", "Current file no longer exists on disk.")
+            return
+        if info.current_name == info.original_name:
+            return
+        if os.path.exists(original_path):
+            QMessageBox.warning(self, "Undo failed", "A file with the original name already exists.")
+            return
+        try:
+            os.rename(current_path, original_path)
+        except OSError as exc:
+            QMessageBox.critical(self, "Undo failed", f"Could not rename file: {exc}")
+            return
+        info.current_name = info.original_name
+        self.table.item(row, self.COL_CURRENT).setText(info.current_name)
+        if info.custom_template is None:
+            custom_item = self.table.item(row, self.COL_CUSTOM)
+            if custom_item is not None:
+                custom_item.setText("")
+        self.update_all_previews()
+
+    def commit_row(self, row: int) -> None:
+        if row >= len(self.file_infos):
+            return
+        info = self.file_infos[row]
+        if self.current_directory is None:
+            return
+        preview = info.preview_name
+        if not preview:
+            QMessageBox.warning(self, "Invalid preview", "No preview available for this file.")
+            return
+        if preview == info.current_name:
+            QMessageBox.information(self, "No change", "The preview matches the current filename.")
+            return
+        new_path = os.path.join(self.current_directory, preview)
+        if os.path.exists(new_path):
+            QMessageBox.warning(self, "Commit failed", "A file with the preview name already exists.")
+            return
+        old_path = os.path.join(self.current_directory, info.current_name)
+        if not os.path.exists(old_path):
+            QMessageBox.warning(self, "Commit failed", "The current file is missing on disk.")
+            return
+        try:
+            os.rename(old_path, new_path)
+        except OSError as exc:
+            QMessageBox.critical(self, "Commit failed", f"Could not rename file: {exc}")
+            return
+        info.current_name = preview
+        self.table.item(row, self.COL_CURRENT).setText(info.current_name)
+        self.update_all_previews()
+
+
+def main() -> None:
+    app = QApplication(sys.argv)
+    widget = RenameAssistant()
+    widget.show()
+    sys.exit(app.exec())
+
+
+if __name__ == "__main__":
+    main()
 
EOF
)
