
import sys
import csv
import json
import os
from datetime import datetime, timedelta
from functools import partial
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QTableWidget, QTableWidgetItem,
    QCheckBox, QHBoxLayout, QPushButton, QProgressBar, QComboBox, QDateEdit,
    QMessageBox, QLineEdit, QToolButton, QStyle
)
from PyQt5.QtCore import Qt, QDate

CSV_FILE = "tasks.csv"
WIDTH_FILE = "column_widths.json"
FIELDS = [
    "Case #", "Project", "Nature", "Task Name", "Parent Case", "Ref#", "Dependency",
    "Start Date", "End Date", "Weight"
]
STEPS = [
    "1_NI", "2_PI", "3_PR", "4_PO", "5_Shipping",
    "6_DC", "7_BRD", "8_DEV", "9_UAT", "10_Sig_off", "11_Delivered"
]
NATURE_OPTIONS = ["", "PA_Project-Assessment", "OA_Procurement", "DEV_Development"]


class TaskManager(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Task Manager with Sub-Tasks (v2)")
        self.resize(1700, 680)

        # mappings by case string (stable across row insert/delete)
        self.parent_map = {}   # child_case -> parent_case
        self.children_map = {} # parent_case -> [child_case,...]
        self.case_counters = {}

        layout = QVBoxLayout()
        self.setLayout(layout)

        self.table = QTableWidget()
        self.table.setColumnCount(len(FIELDS) + len(STEPS) + 1)
        headers = FIELDS + STEPS + ["Progress"]
        self.table.setHorizontalHeaderLabels(headers)
        layout.addWidget(self.table)

        self.load_column_widths()

        btn_layout = QHBoxLayout()
        add_btn = QPushButton("Add Task")
        save_btn = QPushButton("Save All")
        add_btn.clicked.connect(self.add_task)
        save_btn.clicked.connect(self.save_tasks)
        btn_layout.addWidget(add_btn)
        btn_layout.addWidget(save_btn)
        layout.addLayout(btn_layout)

        # single handler for weight edits
        self.table.itemChanged.connect(self.handle_weight_change)

        self.load_tasks()

    # ---------- utilities ----------
    def find_row_by_case(self, case):
        if not case:
            return None
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item and item.text() == case:
                return r
        return None

    def get_case_at_row(self, row):
        item = self.table.item(row, 0)
        return item.text() if item else ""

    def find_row_of_widget(self, widget):
        # search the progress cell widget areas to locate which row contains this widget
        for r in range(self.table.rowCount()):
            cell_widget = self.table.cellWidget(r, len(FIELDS) + len(STEPS))
            if cell_widget:
                layout = cell_widget.layout()
                for i in range(layout.count()):
                    w = layout.itemAt(i).widget()
                    if w is widget:
                        return r
        # also search task cell (for subtask buttons)
        for r in range(self.table.rowCount()):
            task_cell = self.table.cellWidget(r, 3)
            if task_cell:
                layout = task_cell.layout()
                for i in range(layout.count()):
                    w = layout.itemAt(i).widget()
                    if w is widget:
                        return r
        return None

    # ---------- row creation ----------
    def add_task(self):
        self._add_row(parent_case=None)

    def _add_row(self, parent_case=None, before_row=None):
        """Add a row. If parent_case provided, this will be a sub-task of that parent_case.
           If before_row provided, insert at that index; otherwise append."""
        if before_row is None:
            row = self.table.rowCount()
            self.table.insertRow(row)
        else:
            row = before_row
            self.table.insertRow(row)

        # Column 0: Case #
        self.table.setItem(row, 0, QTableWidgetItem(""))

        # Column 1: Project
        self.table.setItem(row, 1, QTableWidgetItem(""))

        # Column 2: Nature combo
        nature_combo = QComboBox()
        nature_combo.addItems(NATURE_OPTIONS)
        # avoid auto-generate case while loading: connect but will be harmless
        nature_combo.currentTextChanged.connect(lambda _: self.generate_case_number_for_row(row))
        self.table.setCellWidget(row, 2, nature_combo)

        # Column 3: Task Name composite widget (QLineEdit + subtask button)
        task_widget = QWidget()
        task_layout = QHBoxLayout()
        task_layout.setContentsMargins(2, 0, 2, 0)
        task_layout.setSpacing(6)

        name_edit = QLineEdit()
        name_edit.setPlaceholderText("Task name")
        name_edit.textChanged.connect(lambda _: None)  # placeholder, kept so it's editable

        # Sub-task tool button with icon (small)
        sub_btn = QToolButton()
        try:
            icon = self.style().standardIcon(QStyle.SP_FileDialogNewFolder)  # a plus-like icon
            sub_btn.setIcon(icon)
        except Exception:
            sub_btn.setText("➕")
        sub_btn.setToolTip("Add sub-task")
        sub_btn.setFixedSize(24, 24)
        sub_btn.clicked.connect(self.handle_subtask_button_clicked)

        task_layout.addWidget(name_edit)
        task_layout.addWidget(sub_btn)
        task_widget.setLayout(task_layout)
        self.table.setCellWidget(row, 3, task_widget)

        # Column 4: Parent Case
        parent_item = QTableWidgetItem(parent_case or "")
        self.table.setItem(row, 4, parent_item)

        # Column 5: Ref#
        ref_item = QTableWidgetItem("")
        ref_item.setBackground(Qt.yellow)
        self.table.setItem(row, 5, ref_item)

        # Column 6: Dependency
        dep_combo = QComboBox()
        dep_combo.addItem("")
        dep_combo.currentTextChanged.connect(lambda _: self.apply_dependency_to_row(row))
        self.table.setCellWidget(row, 6, dep_combo)

        # Column 7: Start Date
        start_date = QDateEdit()
        start_date.setCalendarPopup(True)
        start_date.setDate(QDate.currentDate())
        start_date.dateChanged.connect(lambda _: self.update_end_date_from_weight_by_row(row))
        self.table.setCellWidget(row, 7, start_date)

        # Column 8: End Date
        end_date = QDateEdit()
        end_date.setCalendarPopup(True)
        end_date.setSpecialValueText("None")
        end_date.setDate(QDate(2000, 1, 1))
        end_date.setMinimumDate(QDate(2000, 1, 1))
        end_date.dateChanged.connect(lambda _: [self.check_end_date(row), self.update_weight(row), self.update_parent_on_row_change(row)])
        self.table.setCellWidget(row, 8, end_date)

        # Column 9: Weight (editable QTableWidgetItem)
        weight_item = QTableWidgetItem("")
        self.table.setItem(row, 9, weight_item)

        # Steps (checkboxes)
        for i in range(len(STEPS)):
            cb = QCheckBox()
            cb.stateChanged.connect(lambda _, r=row: self.update_progress(r))
            self.table.setCellWidget(row, len(FIELDS) + i, cb)

        # Progress + Delete button in last cell
        progress = QProgressBar()
        progress.setRange(0, 100)
        delete_btn = QPushButton("❌")
        delete_btn.setFixedWidth(30)
        delete_btn.setStyleSheet("color:red; font-weight:bold;")
        delete_btn.clicked.connect(self.handle_delete_button_clicked)

        container = QWidget()
        hl = QHBoxLayout()
        hl.setContentsMargins(2, 0, 2, 0)
        hl.setSpacing(6)
        hl.addWidget(progress)
        hl.addWidget(delete_btn)
        container.setLayout(hl)
        self.table.setCellWidget(row, len(FIELDS) + len(STEPS), container)

        # If this row is a sub-task (parent_case provided), hide subtask button and style name smaller & indented
        if parent_case:
            # set the name edit style to smaller and indented
            name_edit.setStyleSheet("padding-left: 12px; font-size: 11px;")
            sub_btn.setVisible(False)
        else:
            name_edit.setStyleSheet("")  # default

        # refresh dependencies comboboxes
        self.refresh_dependencies()

        return row  # return the new row index

    # ---------- sub-task handling ----------
    def handle_subtask_button_clicked(self):
        sender = self.sender()
        row = self.find_row_of_widget(sender)
        if row is None:
            return
        # add subtask below this row
        parent_case = self.get_case_at_row(row)
        insert_at = row + 1
        new_row = self._add_row(parent_case=parent_case, before_row=insert_at)
        # copy project and nature from parent to child
        proj_item = self.table.item(row, 1)
        if proj_item:
            self.table.setItem(new_row, 1, QTableWidgetItem(proj_item.text()))
        parent_nature_widget = self.table.cellWidget(row, 2)
        child_nature_widget = self.table.cellWidget(new_row, 2)
        if parent_nature_widget and child_nature_widget:
            try:
                child_nature_widget.blockSignals(True)
                child_nature_widget.setCurrentText(parent_nature_widget.currentText())
            finally:
                child_nature_widget.blockSignals(False)
        # generate case number for child
        self.generate_case_number_for_row(new_row)
        # update mapping by case strings
        child_case = self.get_case_at_row(new_row)
        if parent_case and child_case:
            self.parent_map[child_case] = parent_case
            self.children_map.setdefault(parent_case, []).append(child_case)
        # refresh parent aggregates
        self.update_parent_status_by_case(parent_case)

    def add_subtask(self, parent_row):
        # legacy support (not used)
        self.handle_subtask_button_clicked()

    # ---------- weight/date logic ----------
    def business_days_inclusive(self, start_date, end_date):
        if end_date < start_date:
            return 0
        count = 0
        cur = start_date
        while cur <= end_date:
            if cur.weekday() < 5:
                count += 1
            cur += timedelta(days=1)
        return count

    def update_end_date_from_weight_by_row(self, row):
        weight_item = self.table.item(row, 9)
        if not weight_item:
            return
        text = weight_item.text().strip().lower()
        start_widget = self.table.cellWidget(row, 7)
        end_widget = self.table.cellWidget(row, 8)
        if not text:
            end_widget.setDate(QDate(2000, 1, 1))
            return
        start_date = start_widget.date().toPyDate()
        if text.endswith("d"):
            try:
                days = int(text[:-1])
                if days <= 0:
                    end_widget.setDate(QDate(start_date.year, start_date.month, start_date.day))
                else:
                    current = start_date
                    added = 1
                    while added < days:
                        current += timedelta(days=1)
                        if current.weekday() < 5:
                            added += 1
                    end_widget.setDate(QDate(current.year, current.month, current.day))
            except:
                pass
        elif text.endswith("w"):
            try:
                weeks = int(text[:-1])
                end_date = start_date + timedelta(weeks=weeks)
                end_widget.setDate(QDate(end_date.year, end_date.month, end_date.day))
            except:
                pass
        self.update_weight(row)
        # if this is a child, update parent's aggregate
        case = self.get_case_at_row(row)
        parent_case = self.parent_map.get(case)
        if parent_case:
            self.update_parent_status_by_case(parent_case)

    def handle_weight_change(self, item):
        if not item or item.column() != 9:
            return
        row = item.row()
        # update end date from weight text
        self.update_end_date_from_weight_by_row(row)

    def update_weight(self, row):
        start_widget = self.table.cellWidget(row, 7)
        end_widget = self.table.cellWidget(row, 8)
        weight_item = self.table.item(row, 9)
        if not (start_widget and end_widget and weight_item):
            return
        start_date = start_widget.date().toPyDate()
        end_date = end_widget.date().toPyDate()
        if end_date.year == 2000:
            weight_item.setText("")
            return
        bdays = self.business_days_inclusive(start_date, end_date)
        weight_item.setText(f"{bdays}d")

    # ---------- progress & roll-up ----------
    def update_progress(self, row):
        # calculate percent for this row
        checked = 0
        for i in range(len(STEPS)):
            cb = self.table.cellWidget(row, len(FIELDS) + i)
            if cb and cb.isChecked():
                checked += 1
        percent = int((checked / len(STEPS)) * 100) if len(STEPS) > 0 else 0
        prog_widget = self.table.cellWidget(row, len(FIELDS) + len(STEPS))
        if prog_widget:
            # prog_widget is container with layout: [QProgressBar, DeleteBtn]
            prog_bar = prog_widget.layout().itemAt(0).widget()
            prog_bar.setValue(percent)

        # if this row is a child, update parent roll-up
        case = self.get_case_at_row(row)
        parent_case = self.parent_map.get(case)
        if parent_case:
            self.update_parent_status_by_case(parent_case)

    def update_parent_status_by_case(self, parent_case):
        if not parent_case:
            return
        parent_row = self.find_row_by_case(parent_case)
        if parent_row is None:
            return
        self.update_parent_status(parent_row)

    def update_parent_status(self, parent_row):
        parent_case = self.get_case_at_row(parent_row)
        children_cases = self.children_map.get(parent_case, [])
        if not children_cases:
            # ensure parent checkboxes enabled (no children)
            for i in range(len(STEPS)):
                cb = self.table.cellWidget(parent_row, len(FIELDS) + i)
                if cb:
                    cb.setEnabled(True)
                    cb.setStyleSheet("")
            return

        # gather start/end from children
        start_dates = []
        end_dates = []
        child_rows = []
        for c in children_cases:
            r = self.find_row_by_case(c)
            if r is None:
                continue
            child_rows.append(r)
            s = self.table.cellWidget(r, 7).date().toPyDate()
            e = self.table.cellWidget(r, 8).date().toPyDate()
            # ignore sentinel end date
            if e.year != 2000:
                start_dates.append(s)
                end_dates.append(e)

        if start_dates and end_dates:
            min_s = min(start_dates)
            max_e = max(end_dates)
            self.table.cellWidget(parent_row, 7).setDate(QDate(min_s.year, min_s.month, min_s.day))
            self.table.cellWidget(parent_row, 8).setDate(QDate(max_e.year, max_e.month, max_e.day))
            self.update_weight(parent_row)

        # For each step, set parent's checkbox checked only if ALL children checked
        for i in range(len(STEPS)):
            all_checked = True
            for r in child_rows:
                cb = self.table.cellWidget(r, len(FIELDS) + i)
                if cb and not cb.isChecked():
                    all_checked = False
                    break
            parent_cb = self.table.cellWidget(parent_row, len(FIELDS) + i)
            if parent_cb:
                parent_cb.blockSignals(True)
                parent_cb.setChecked(all_checked)
                parent_cb.setEnabled(False)
                parent_cb.setStyleSheet("background-color: lightgray;")
                parent_cb.blockSignals(False)

        # update parent's progress bar based on aggregated children? We'll set progress based on the parent's own steps (which now reflect children)
        self.update_progress(parent_row)

    def update_parent_on_row_change(self, row):
        # when a row's end date changed manually, update weight and parent status if it is a child
        case = self.get_case_at_row(row)
        parent_case = self.parent_map.get(case)
        if parent_case:
            self.update_parent_status_by_case(parent_case)

    # ---------- dependency handling ----------
    def apply_dependency_to_row(self, row):
        dep_combo = self.table.cellWidget(row, 6)
        if not dep_combo:
            return
        dep_case = dep_combo.currentText()
        if not dep_case:
            return
        # find row with that case and set start date same as that row's end date
        r = self.find_row_by_case(dep_case)
        if r is not None:
            end_widget = self.table.cellWidget(r, 8)
            if end_widget:
                self.table.cellWidget(row, 7).setDate(end_widget.date())
                self.update_weight(row)

    def refresh_dependencies(self):
        cases = [self.table.item(r, 0).text() for r in range(self.table.rowCount()) if self.table.item(r, 0)]
        for r in range(self.table.rowCount()):
            combo = self.table.cellWidget(r, 6)
            if combo:
                current = combo.currentText()
                combo.blockSignals(True)
                combo.clear()
                combo.addItem("")
                combo.addItems(cases)
                combo.setCurrentText(current)
                combo.blockSignals(False)

    # ---------- deletion ----------
    def handle_delete_button_clicked(self):
        sender = self.sender()
        row = self.find_row_of_widget(sender)
        if row is None:
            return
        case = self.get_case_at_row(row)
        if not case:
            # no case, just remove row
            self.table.removeRow(row)
            self.refresh_dependencies()
            return

        # if this is a parent with children, ask whether to delete all
        children = self.children_map.get(case, [])
        if children:
            reply = QMessageBox.question(self, "Delete", f"'{case}' has {len(children)} sub-task(s). Delete parent and all sub-tasks?",
                                         QMessageBox.Yes | QMessageBox.No)
            if reply != QMessageBox.Yes:
                return
            # delete children first
            for child_case in list(children):
                self.delete_row_by_case(child_case)
        # delete the row itself
        self.delete_row_by_case(case)
        self.refresh_dependencies()

    def delete_row_by_case(self, case):
        # remove mapping entries
        # remove children_map entry if present
        if case in self.children_map:
            del self.children_map[case]
        # remove parent_map entries that reference this case
        to_remove = [c for c, p in self.parent_map.items() if p == case or c == case]
        for c in to_remove:
            if c in self.parent_map:
                del self.parent_map[c]
        # find and remove row
        row = self.find_row_by_case(case)
        if row is not None:
            self.table.removeRow(row)
        # also remove this case from any children lists
        for p, childs in list(self.children_map.items()):
            self.children_map[p] = [x for x in childs if x != case]

    # ---------- generate case ----------
    def generate_case_number_for_row(self, row):
        # only generate if Case # cell empty
        item = self.table.item(row, 0)
        if item and item.text().strip():
            return
        proj_item = self.table.item(row, 1)
        nature_widget = self.table.cellWidget(row, 2)
        proj = proj_item.text().strip() if proj_item else ""
        nat = nature_widget.currentText().strip() if nature_widget else ""
        if not proj or not nat:
            return
        prefix = f"{proj}_{nat[:2]}"
        count = self.case_counters.get(prefix, 1)
        case_number = f"{prefix}_{count:03}"
        self.case_counters[prefix] = count + 1
        self.table.setItem(row, 0, QTableWidgetItem(case_number))
        # if this row has Parent Case set, register mapping
        parent_case_item = self.table.item(row, 4)
        parent_case = parent_case_item.text() if parent_case_item else ""
        if parent_case:
            self.parent_map[case_number] = parent_case
            self.children_map.setdefault(parent_case, []).append(case_number)
        self.refresh_dependencies()

    # ---------- check end date ----------
    def check_end_date(self, row):
        end_widget = self.table.cellWidget(row, 8)
        if end_widget:
            dt = end_widget.date().toPyDate()
            if dt.year == 2000:
                end_widget.setStyleSheet("")
                return
            today = datetime.today().date()
            if dt <= today:
                end_widget.setStyleSheet("background-color: red;")
            else:
                end_widget.setStyleSheet("")

    # ---------- save / load ----------
    def save_tasks(self):
        self.save_column_widths()
        with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(FIELDS + ["Progress"] + STEPS)
            for r in range(self.table.rowCount()):
                rowvals = []
                for i, field in enumerate(FIELDS):
                    if field == "Nature":
                        widget = self.table.cellWidget(r, i)
                        rowvals.append(widget.currentText() if widget else "")
                    elif field == "Task Name":
                        widget = self.table.cellWidget(r, i)
                        if widget:
                            # QLineEdit is first widget
                            edit = widget.layout().itemAt(0).widget()
                            rowvals.append(edit.text())
                        else:
                            itm = self.table.item(r, i)
                            rowvals.append(itm.text() if itm else "")
                    elif field in ["Start Date", "End Date"]:
                        date_widget = self.table.cellWidget(r, i)
                        date = date_widget.date() if date_widget else QDate(2000,1,1)
                        rowvals.append(date.toString("yyyy-MM-dd") if date.year() != 2000 else "")
                    elif field == "Dependency":
                        combo = self.table.cellWidget(r, i)
                        rowvals.append(combo.currentText() if combo else "")
                    else:
                        itm = self.table.item(r, i)
                        rowvals.append(itm.text() if itm else "")
                # progress value
                prog_widget = self.table.cellWidget(r, len(FIELDS) + len(STEPS))
                prog_val = 0
                if prog_widget:
                    prog_val = prog_widget.layout().itemAt(0).widget().value()
                rowvals.append(prog_val)
                # steps yes/no
                for i in range(len(STEPS)):
                    cb = self.table.cellWidget(r, len(FIELDS) + i)
                    rowvals.append("Yes" if cb and cb.isChecked() else "No")
                writer.writerow(rowvals)
        QMessageBox.information(self, "Saved", f"Tasks saved to {CSV_FILE}")

    def load_tasks(self):
        if not os.path.exists(CSV_FILE):
            return
        # clear table
        self.table.setRowCount(0)
        with open(CSV_FILE, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            # first pass: add rows and populate fields
            for row_data in reader:
                # determine if this row is a sub-task by Parent Case column
                parent_case = row_data.get("Parent Case", "").strip()
                # append row
                new_row = self._add_row(parent_case=parent_case)
                # set fields
                for i, field in enumerate(FIELDS):
                    if field == "Nature":
                        widget = self.table.cellWidget(new_row, i)
                        if widget:
                            widget.blockSignals(True)
                            widget.setCurrentText(row_data.get(field, ""))
                            widget.blockSignals(False)
                    elif field == "Task Name":
                        widget = self.table.cellWidget(new_row, i)
                        if widget:
                            edit = widget.layout().itemAt(0).widget()
                            edit.setText(row_data.get(field, ""))
                            # hide subtask button if parent_case present
                            if parent_case:
                                btn = widget.layout().itemAt(1).widget()
                                if btn:
                                    btn.setVisible(False)
                                    edit.setStyleSheet("padding-left:12px; font-size:11px;")
                    elif field in ["Start Date", "End Date"]:
                        w = self.table.cellWidget(new_row, i)
                        date_str = row_data.get(field, "")
                        if date_str:
                            try:
                                w.setDate(QDate.fromString(date_str, "yyyy-MM-dd"))
                            except:
                                pass
                    elif field == "Dependency":
                        combo = self.table.cellWidget(new_row, i)
                        if combo:
                            combo.setCurrentText(row_data.get(field, ""))
                    else:
                        itm = self.table.item(new_row, i)
                        if itm:
                            itm.setText(row_data.get(field, ""))
                # steps
                for i, step in enumerate(STEPS):
                    cb = self.table.cellWidget(new_row, len(FIELDS) + i)
                    if cb:
                        cb.setChecked(row_data.get(step, "No") == "Yes")
                # progress and weight will be recalculated
            # second pass: build parent/child maps based on 'Case #' and 'Parent Case'
            for r in range(self.table.rowCount()):
                case = self.get_case_at_row(r)
                parent_case_item = self.table.item(r, 4)
                parent_case = parent_case_item.text().strip() if parent_case_item else ""
                if parent_case:
                    self.parent_map[case] = parent_case
                    self.children_map.setdefault(parent_case, []).append(case)
            # final pass: update progress, weights and parent aggregates
            for r in range(self.table.rowCount()):
                self.update_progress(r)
                self.update_weight(r)
            # update parents
            for parent_case in list(self.children_map.keys()):
                self.update_parent_status_by_case(parent_case)
            self.refresh_dependencies()

    def save_column_widths(self):
        widths = {i: self.table.columnWidth(i) for i in range(self.table.columnCount())}
        with open(WIDTH_FILE, "w", encoding="utf-8") as f:
            json.dump(widths, f)

    def load_column_widths(self):
        if not os.path.exists(WIDTH_FILE):
            return
        try:
            with open(WIDTH_FILE, "r", encoding="utf-8") as f:
                widths = json.load(f)
            for i, w in widths.items():
                self.table.setColumnWidth(int(i), int(w))
        except:
            pass


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = TaskManager()
    window.show()
    sys.exit(app.exec_())
