import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional, Dict, Any, List
import re
import xml.etree.ElementTree as ET

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from classes.info_card import InfoCard
from classes.count_badge import CountBadge
from classes.vtk_viewer import VtkStepViewer
from classes.nails_info import NailsInfoPage
from methods.StepViewerMethodsMixin import StepViewerMethodsMixin


class EditNailsDialog(QDialog):
    """Dialog for assigning nails to intersection groups grouped by layer count.

    Left side: embedded VTK viewer (mirrors the host's model + current nails).
    Right side: scrollable list of intersection buckets with controls.
    """

    def __init__(self, groups, host, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Nails")
        self.setMinimumWidth(1120)
        self.setMinimumHeight(600)
        self._groups = groups
        self._host = host
        self._spinboxes: Dict[int, QSpinBox] = {}
        self._buckets: Dict[int, list] = defaultdict(list)
        for g in self._groups:
            self._buckets[len(g)].append(g)
        self._inner_viewer: Optional[VtkStepViewer] = None
        self._build_ui()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)
        outer.setSpacing(12)

        title = QLabel("Edit Nails – Intersection Groups")
        title.setObjectName("panelTitle")
        outer.addWidget(title)

        # ── body: viewer (left) + controls (right) ──────────────────────
        body = QHBoxLayout()
        body.setSpacing(16)
        outer.addLayout(body, 1)

        # Left: VTK viewer
        self._inner_viewer = VtkStepViewer()
        body.addWidget(self._inner_viewer, 1)

        # Right: controls panel
        right_widget = QWidget()
        right_widget.setFixedWidth(380)
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)
        body.addWidget(right_widget, 0)

        # -- Placement section (always visible, above the scroll) --
        place_hdr = QLabel("Placement")
        place_hdr.setObjectName("miniSectionTitle")
        right_layout.addWidget(place_hdr)

        self._place_btn = QPushButton("Add Nail by Position")
        self._place_btn.setObjectName("drawButton")
        self._place_btn.setCheckable(True)
        self._place_btn.toggled.connect(self._toggle_placement)
        right_layout.addWidget(self._place_btn)

        self._place_status_lbl = QLabel("")
        self._place_status_lbl.setObjectName("miniLabel")
        right_layout.addWidget(self._place_status_lbl)

        place_sep = QFrame()
        place_sep.setFrameShape(QFrame.Shape.HLine)
        place_sep.setStyleSheet("color: #dbe3ee; margin: 4px 0;")
        right_layout.addWidget(place_sep)

        # -- Intersection scroll --
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner_widget = QWidget()
        layout = QVBoxLayout(inner_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        scroll.setWidget(inner_widget)
        right_layout.addWidget(scroll, 1)

        if not self._buckets:
            layout.addWidget(QLabel("No layer intersections found in this model."))
        else:
            for count in sorted(self._buckets.keys(), reverse=True):
                grps = self._buckets[count]

                # Collapsible header button
                toggle_btn = QPushButton(f"▶  {count} Layers Intersections ({len(grps)} group(s))")
                toggle_btn.setObjectName("miniSectionTitle")
                toggle_btn.setCheckable(True)
                toggle_btn.setChecked(False)
                toggle_btn.setStyleSheet(
                    "QPushButton { text-align: left; padding: 4px 6px; }"
                )
                layout.addWidget(toggle_btn)

                # Collapsible body — hidden by default
                body_widget = QWidget()
                body_widget.setVisible(False)
                body_layout = QVBoxLayout(body_widget)
                body_layout.setContentsMargins(12, 2, 0, 4)
                body_layout.setSpacing(4)

                for grp in grps:
                    names = " > ".join(p.get("panel_name", "?") for p in grp)
                    lbl = QLabel(names)
                    lbl.setObjectName("miniLabel")
                    lbl.setWordWrap(True)
                    body_layout.addWidget(lbl)

                layout.addWidget(body_widget)

                def _make_toggler(btn, widget):
                    def _toggle(checked):
                        widget.setVisible(checked)
                        btn.setText(
                            btn.text().replace("▶", "▼") if checked
                            else btn.text().replace("▼", "▶")
                        )
                    return _toggle

                toggle_btn.toggled.connect(_make_toggler(toggle_btn, body_widget))

                # Controls row (always visible)
                row = QHBoxLayout()
                spin = QSpinBox()
                spin.setMinimum(1)
                spin.setMaximum(20)
                spin.setValue(1)
                self._spinboxes[count] = spin
                row.addWidget(QLabel("Nails:"))
                row.addWidget(spin)
                add_btn = QPushButton("Add Nails")
                add_btn.setObjectName("primaryButton")
                add_btn.clicked.connect(lambda checked, c=count: self._add_nails_for(c))
                row.addWidget(add_btn)
                clear_btn = QPushButton("Clear Nails")
                clear_btn.setObjectName("secondaryButton")
                clear_btn.clicked.connect(lambda checked, c=count: self._clear_nails_for(c))
                row.addWidget(clear_btn)
                layout.addLayout(row)

                sep = QFrame()
                sep.setFrameShape(QFrame.Shape.HLine)
                sep.setStyleSheet("color: #dbe3ee; margin: 4px 0;")
                layout.addWidget(sep)

        layout.addStretch(1)

        # ── global action row ────────────────────────────────────────────
        global_row = QHBoxLayout()
        add_all_btn = QPushButton("Add All Nails")
        add_all_btn.setObjectName("primaryButton")
        add_all_btn.clicked.connect(self._add_all_nails)
        global_row.addWidget(add_all_btn)
        clear_all_btn = QPushButton("Clear All Nails")
        clear_all_btn.setObjectName("secondaryButton")
        clear_all_btn.clicked.connect(self._clear_all_nails)
        global_row.addWidget(clear_all_btn)
        global_row.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.setObjectName("secondaryButton")
        close_btn.clicked.connect(self.accept)
        global_row.addWidget(close_btn)
        outer.addLayout(global_row)

    # ------------------------------------------------------------------ Qt events

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self._init_inner_viewer)

    def closeEvent(self, event) -> None:
        if self._place_btn.isChecked():
            self._place_btn.setChecked(False)
        QApplication.processEvents()
        if self._inner_viewer is not None:
            self._inner_viewer.shutdown()
        super().closeEvent(event)

    # ------------------------------------------------------------------ placement

    def _toggle_placement(self, checked: bool) -> None:
        if checked:
            layer_infos = [s["info"] for s in self._host.viewer.step_solid_actors]
            if not layer_infos:
                self._place_btn.setChecked(False)
                return
            self._inner_viewer.start_manual_nail_placement(layer_infos, self._on_nail_placed_in_dialog)
            self._inner_viewer.vtk_widget.setCursor(Qt.CursorShape.CrossCursor)
            self._place_btn.setText("Stop Placing  ✕")
            self._place_status_lbl.setText("Click on the grid to place a nail")
        else:
            self._inner_viewer.stop_manual_nail_placement()
            self._inner_viewer.clear_layer_grid()
            self._inner_viewer.vtk_widget.unsetCursor()
            self._place_btn.setText("Add Nail by Position")
            self._place_status_lbl.setText("")

    def _on_nail_placed_in_dialog(self, point: tuple) -> None:
        infos = [s["info"] for s in self._host.viewer.step_solid_actors if s["info"].get("layer_name")]
        if infos:
            top = max(infos, key=lambda i: i.get("zmax", 0))
            layer_name = top.get("layer_name", "-")
        else:
            layer_name = "-"
        self._host._add_nail_item_typed(point, layer_name, nail_type="long", source="manual_dialog")
        self._sync_inner_nails()
        self._place_status_lbl.setText(
            f"Nail placed at ({point[0]:.1f}, {point[1]:.1f})"
        )

    # ------------------------------------------------------------------ viewer init

    def _init_inner_viewer(self) -> None:
        if self._inner_viewer is None or self._inner_viewer._initialized:
            return
        self._inner_viewer.initialize()
        solids_data = [
            (s["polydata"], s["info"]) for s in self._host.viewer.step_solid_actors
        ]
        if solids_data:
            self._inner_viewer.set_step_solids(solids_data)
        self._inner_viewer.show_all_layers()
        self._sync_inner_nails()

    def _sync_inner_nails(self) -> None:
        if self._inner_viewer is None or not self._inner_viewer._initialized:
            return
        nails = self._host.current_nail_positions
        self._inner_viewer.set_nail_positions(nails)
        self._inner_viewer.set_nails_visible(bool(nails))

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _source_key(layer_count: int) -> str:
        return f"intersection_{layer_count}L"

    def _push_to_host(self, status: str) -> None:
        nails = self._host.current_nail_positions
        self._host.viewer.set_nail_positions(nails)
        self._host.viewer.set_nails_visible(bool(nails))
        self._host.status_label.setText(status)
        self._sync_inner_nails()

    # ------------------------------------------------------------------ per-bucket actions

    def _add_nails_for(self, layer_count: int) -> None:
        nail_count = self._spinboxes[layer_count].value()
        source = self._source_key(layer_count)
        added = 0

        for grp in self._buckets[layer_count]:
            rect = self._host._build_common_rect_for_group(grp)
            if rect is None:
                continue
            z_top = grp[0].get("zmax", grp[0].get("cz", 0)) + 1.8
            layer_name = grp[0].get("layer_name", "-")
            for pt in self._host._build_nail_points_for_rect(rect, z_top, nail_count):
                self._host._add_nail_item_typed(
                    pt, layer_name, nail_type="long", source=source, side="top"
                )
                added += 1

            if layer_count in (3, 4):
                z_bottom = grp[-1].get("zmin", grp[-1].get("cz", 0)) - 1.8
                layer_name_bottom = grp[-1].get("layer_name", "-")
                for pt in self._host._build_nail_points_for_rect(rect, z_bottom, nail_count):
                    self._host._add_nail_item_typed(
                        pt, layer_name_bottom, nail_type="long", source=source, side="bottom"
                    )
                    added += 1

        self._push_to_host(f"Added {added} nail(s) for {layer_count}-layer intersections.")

    def _clear_nails_for(self, layer_count: int) -> None:
        source = self._source_key(layer_count)
        before = len(self._host.current_nail_positions)
        self._host.current_nail_positions = [
            n for n in self._host.current_nail_positions if n.get("source") != source
        ]
        removed = before - len(self._host.current_nail_positions)
        self._push_to_host(f"Cleared {removed} nail(s) for {layer_count}-layer intersections.")

    # ------------------------------------------------------------------ global actions

    def _add_all_nails(self) -> None:
        for count in sorted(self._buckets.keys(), reverse=True):
            self._add_nails_for(count)
        total = len(self._host.current_nail_positions)
        self._push_to_host(f"All intersection nails added — {total} nail(s) total.")

    def _clear_all_nails(self) -> None:
        before = len(self._host.current_nail_positions)
        self._host.current_nail_positions = [
            n for n in self._host.current_nail_positions
            if not n.get("source", "").startswith("intersection_")
        ]
        removed = before - len(self._host.current_nail_positions)
        self._push_to_host(f"Cleared {removed} intersection nail(s).")


class StepViewerWindow(QMainWindow, StepViewerMethodsMixin):
    Path = Path

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Wood Pallet Viewer")

        screen = QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            self.resize(int(avail.width() * 0.88), int(avail.height() * 0.88))
            self.move(
                avail.x() + (avail.width() - self.width()) // 2,
                avail.y() + (avail.height() - self.height()) // 2,
            )
        else:
            self.resize(1280, 720)
        self.setMinimumSize(900, 580)

        self.current_file: Optional[Path] = None
        self.current_model = None
        self.current_base_bb = None
        self.current_solid_infos: List[Dict[str, Any]] = []
        self.current_gap_measurements: List[Dict[str, Any]] = []
        self.current_nail_positions: List[Dict[str, Any]] = []
        self.custom_layer_requests: List[Dict[str, str]] = []

        self.current_panels_count = 0
        self.current_rectangular_count = 0
        self.current_cubique_count = 0
        self.current_other_count = 0
        self.current_4panel_sets_count = 0

        self.current_top_gaps_count = 0
        self.current_bottom_gaps_count = 0
        self.current_cubic_gaps_count = 0

        self._build_ui()
        self._apply_styles()

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)

        main = QVBoxLayout(root)
        main.setContentsMargins(22, 18, 22, 22)
        main.setSpacing(18)

        header = QFrame()
        header.setObjectName("heroCard")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(22, 20, 22, 20)
        header_layout.setSpacing(14)

        title_row = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(6)

        title = QLabel("Wood Pallet Viewer")
        title.setObjectName("heroTitle")

        subtitle = QLabel("CAD / Wood ")
        subtitle.setObjectName("heroSubtitle")

        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        title_row.addLayout(title_box, 1)

        self.open_btn = QPushButton("Open Layer File")
        self.open_btn.setObjectName("primaryButton")
        self.open_btn.clicked.connect(self.open_step_file)
        title_row.addWidget(self.open_btn, 0, Qt.AlignRight)

        self.settings_btn = QPushButton("⚙")
        self.settings_btn.setObjectName("iconButton")
        self.settings_btn.setFixedSize(52, 52)
        self.settings_btn.setToolTip("Settings")
        self.settings_btn.clicked.connect(self._show_settings_dialog)
        title_row.addWidget(self.settings_btn, 0, Qt.AlignRight)

        header_layout.addLayout(title_row)
        main.addWidget(header)

        content = QHBoxLayout()
        content.setSpacing(18)
        main.addLayout(content, 1)

        left_panel = QFrame()
        left_panel.setObjectName("sidePanel")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(18, 18, 18, 18)
        left_layout.setSpacing(14)

        left_title = QLabel("File Details")
        left_title.setObjectName("panelTitle")
        left_layout.addWidget(left_title)

        self.file_card = InfoCard("Loaded File", "No file loaded")
        self.unit_card = InfoCard("Display Unit", "mm")
        self.length_card = InfoCard("Length", "--")
        self.width_card = InfoCard("Width", "--")
        self.height_card = InfoCard("Height", "--")
        self.material_card = InfoCard("Current View", "Wood")
        self.clicked_panel_card = InfoCard("Detected Panel Name", "Click a panel")

        left_layout.addWidget(self.file_card)
        left_layout.addWidget(self.unit_card)
        left_layout.addWidget(self.length_card)
        left_layout.addWidget(self.width_card)
        left_layout.addWidget(self.height_card)
        left_layout.addWidget(self.material_card)
        left_layout.addWidget(self.clicked_panel_card)
        left_layout.addStretch(1)

        left_panel.setMaximumWidth(260)
        content.addWidget(left_panel, 0)

        center_panel = QFrame()
        center_panel.setObjectName("viewerPanel")
        center_layout = QVBoxLayout(center_panel)
        center_layout.setContentsMargins(18, 18, 18, 18)
        center_layout.setSpacing(14)

        top_row = QHBoxLayout()
        top_row.setSpacing(12)

        preview_title = QLabel("3D View")
        preview_title.setObjectName("panelTitle")
        top_row.addWidget(preview_title)

        top_row.addStretch(1)

        self.unit_combo = QComboBox()
        self.unit_combo.addItems(["cm", "mm", "m"])
        self.unit_combo.currentIndexChanged.connect(self.on_unit_changed)
        self.unit_combo.hide()

        self.panels_info_btn = QPushButton("Calculate Panels Info")
        self.panels_info_btn.setObjectName("secondaryButton")
        self.panels_info_btn.clicked.connect(self.show_panels_info_dialog)
        top_row.addWidget(self.panels_info_btn)

        self.add_layer_btn = QPushButton("Add New Layer")
        self.add_layer_btn.setObjectName("secondaryButton")
        self.add_layer_btn.clicked.connect(self._show_add_layer_dialog)
        top_row.addWidget(self.add_layer_btn)

        self.edit_layer_btn = QPushButton("Edit Nails")
        self.edit_layer_btn.setObjectName("secondaryButton")
        self.edit_layer_btn.clicked.connect(self._edit_selected_layer)
        top_row.addWidget(self.edit_layer_btn)

        self.move_rotate_btn = QPushButton("Move / Rotate")
        self.move_rotate_btn.setObjectName("secondaryButton")
        self.move_rotate_btn.setEnabled(False)
        self.move_rotate_btn.setCheckable(True)
        self.move_rotate_btn.toggled.connect(self._toggle_edit_transform)
        top_row.addWidget(self.move_rotate_btn)

        self.toggle_dist_btn = QPushButton("Show Distances")
        self.toggle_dist_btn.setObjectName("secondaryButton")
        self.toggle_dist_btn.clicked.connect(self.toggle_distances)
        top_row.addWidget(self.toggle_dist_btn)
        
        self.toggle_names_btn = QPushButton("Show Panel Names")
        self.toggle_names_btn.setObjectName("secondaryButton")
        self.toggle_names_btn.clicked.connect(self.toggle_panel_names)
        top_row.addWidget(self.toggle_names_btn)

        self.reset_view_btn = QPushButton("Reset View")
        self.reset_view_btn.setObjectName("secondaryButton")
        self.reset_view_btn.clicked.connect(self._reset_view_and_zoom_bar)
        top_row.addWidget(self.reset_view_btn)

        self.reload_btn = QPushButton("Reload")
        self.reload_btn.setObjectName("secondaryButton")
        self.reload_btn.clicked.connect(self.reload_current_file)
        top_row.addWidget(self.reload_btn)

        center_layout.addLayout(top_row)

        # --- object color toolbar ---
        obj_color_row = QHBoxLayout()
        obj_color_row.setSpacing(10)

        _oc_lbl = QLabel("Panel Color:")
        _oc_lbl.setObjectName("miniLabel")
        obj_color_row.addWidget(_oc_lbl)

        self._obj_color_presets = [
            ("#fef655", (1.00, 0.97, 0.55)),  # pastel yellow (default)
            ("#e8c882", (0.91, 0.78, 0.51)),  # light oak
            ("#c89b6e", (0.78, 0.61, 0.43)),  # warm wood
            ("#8b5e3c", (0.55, 0.37, 0.24)),  # dark walnut
            ("#f5f0e8", (0.96, 0.94, 0.91)),  # birch white
            ("#b0b0b0", (0.69, 0.69, 0.69)),  # concrete gray
            ("#5b9bd5", (0.36, 0.61, 0.84)),  # steel blue
            ("#6abf6a", (0.42, 0.75, 0.42)),  # green
            ("#e05c5c", (0.88, 0.36, 0.36)),  # red
            ("#f5a623", (0.96, 0.65, 0.14)),  # orange
        ]
        for hex_c, rgb in self._obj_color_presets:
            _ocb = QPushButton()
            _ocb.setFixedSize(24, 24)
            _ocb.setToolTip(hex_c)
            _ocb.setStyleSheet(
                f"QPushButton {{background:{hex_c};border:2px solid #ccd4e0;"
                f"border-radius:12px;padding:0px;}}"
                f"QPushButton:hover {{border:2px solid #4b5563;}}"
            )
            _ocb.clicked.connect(lambda _checked, c=rgb: self._obj_color_apply_all(c))
            obj_color_row.addWidget(_ocb)

        _oc_custom_btn = QPushButton("Custom…")
        _oc_custom_btn.setObjectName("secondaryButton")
        _oc_custom_btn.clicked.connect(self._obj_color_custom_all)
        obj_color_row.addWidget(_oc_custom_btn)

        _oc_sep = QLabel("|")
        _oc_sep.setObjectName("miniLabel")
        obj_color_row.addWidget(_oc_sep)

        _oc_sel_btn = QPushButton("Color Selected")
        _oc_sel_btn.setObjectName("secondaryButton")
        _oc_sel_btn.setToolTip("Open color picker and apply to the currently selected panel(s) only")
        _oc_sel_btn.clicked.connect(self._obj_color_selected)
        obj_color_row.addWidget(_oc_sel_btn)

        _oc_reset_btn = QPushButton("Reset Colors")
        _oc_reset_btn.setObjectName("secondaryButton")
        _oc_reset_btn.setToolTip("Reset all panels to default pastel yellow")
        _oc_reset_btn.clicked.connect(self._obj_color_reset)
        obj_color_row.addWidget(_oc_reset_btn)

        obj_color_row.addStretch(1)
        center_layout.addLayout(obj_color_row)

        # --- lines toolbar ---
        lines_row = QHBoxLayout()
        lines_row.setSpacing(10)

        _del_btn = QPushButton("Delete")
        _del_btn.setObjectName("secondaryButton")
        _del_btn.clicked.connect(self._delete_selected_annotation)
        lines_row.addWidget(_del_btn)

        _clear_btn = QPushButton("Clear All")
        _clear_btn.setObjectName("secondaryButton")
        _clear_btn.clicked.connect(self._clear_all_annotations)
        lines_row.addWidget(_clear_btn)

        self.show_only_btn = QPushButton("Show Only Selected")
        self.show_only_btn.setObjectName("drawButton")
        self.show_only_btn.setCheckable(True)
        self.show_only_btn.toggled.connect(self._toggle_show_only_selected)
        lines_row.addWidget(self.show_only_btn)

        self.select_same_type_btn = QPushButton("Select Same Type")
        self.select_same_type_btn.setObjectName("drawButton")
        self.select_same_type_btn.setCheckable(True)
        self.select_same_type_btn.toggled.connect(self._toggle_select_same_type)
        lines_row.addWidget(self.select_same_type_btn)

        lines_row.addStretch(1)

        self.save_step_btn = QPushButton("Save Layer")
        self.save_step_btn.setObjectName("primaryButton")
        self.save_step_btn.clicked.connect(self._save_layer_to_xml)
        lines_row.addWidget(self.save_step_btn)

        self.export_layer_parts_btn = QPushButton("Export Layer Parts")
        self.export_layer_parts_btn.setObjectName("primaryButton")
        self.export_layer_parts_btn.clicked.connect(self._export_layer_parts_to_xml)
        lines_row.addWidget(self.export_layer_parts_btn)

        center_layout.addLayout(lines_row)
        # --- end lines toolbar ---

        self.viewer = VtkStepViewer()
        self.viewer.on_panel_clicked = self.handle_panel_clicked
        self.viewer.on_step_solid_selected = self._handle_step_solid_selected
        self.viewer.on_layer_double_clicked = self._open_nails_page
        center_layout.addWidget(self.viewer, 1)

        # Copy / paste shortcuts — work while VTK has focus because QShortcut
        # is handled at the window level before VTK consumes key events.
        copy_sc = QShortcut(QKeySequence.StandardKey.Copy, self)
        copy_sc.activated.connect(self._copy_selected_shape)
        paste_sc = QShortcut(QKeySequence.StandardKey.Paste, self)
        paste_sc.activated.connect(self._paste_shape)

        zoom_row = QHBoxLayout()
        zoom_row.setSpacing(8)
        zoom_row.addStretch(1)

        self._zoom_slider_value = 100

        self.zoom_out_btn = QPushButton("-")
        self.zoom_out_btn.setObjectName("zoomButton")
        self.zoom_out_btn.setFixedSize(32, 32)
        self.zoom_out_btn.clicked.connect(lambda: self._nudge_zoom_slider(-10))
        zoom_row.addWidget(self.zoom_out_btn)

        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setObjectName("zoomSlider")
        self.zoom_slider.setRange(25, 400)
        self.zoom_slider.setValue(self._zoom_slider_value)
        self.zoom_slider.setFixedWidth(220)
        self.zoom_slider.valueChanged.connect(self._on_zoom_slider_changed)
        zoom_row.addWidget(self.zoom_slider)

        self.zoom_in_btn = QPushButton("+")
        self.zoom_in_btn.setObjectName("zoomButton")
        self.zoom_in_btn.setFixedSize(32, 32)
        self.zoom_in_btn.clicked.connect(lambda: self._nudge_zoom_slider(10))
        zoom_row.addWidget(self.zoom_in_btn)

        zoom_row.addStretch(1)
        center_layout.addLayout(zoom_row)

        counts_row = QHBoxLayout()
        counts_row.setSpacing(12)

        self.total_badge = CountBadge("All Panels", "--")
        self.rect_badge = CountBadge("Rectangular Panels", "--")
        self.cubique_badge = CountBadge("Cubique Panels", "--")

        self.total_badge.setMaximumWidth(140)
        self.rect_badge.setMaximumWidth(140)
        self.cubique_badge.setMaximumWidth(140)

        counts_row.addWidget(self.total_badge, 0)
        counts_row.addWidget(self.rect_badge, 0)
        counts_row.addWidget(self.cubique_badge, 0)
        counts_row.addStretch()

        gap_cards_row1 = QHBoxLayout()
        gap_cards_row1.setSpacing(12)

        self.sets_card = InfoCard("Detected 4-Panel Sets", "--")
        self.gaps_card = InfoCard("All Spaces", "--")

        gap_cards_row1.addWidget(self.sets_card)
        gap_cards_row1.addWidget(self.gaps_card)

        gap_cards_row2 = QHBoxLayout()
        gap_cards_row2.setSpacing(12)

        self.top_gaps_card = InfoCard("Top Panel Spaces", "--")
        self.bottom_gaps_card = InfoCard("Bottom Panel Spaces", "--")
        self.cubic_gaps_card = InfoCard("Cubique Spaces", "--")

        gap_cards_row2.addWidget(self.top_gaps_card)
        gap_cards_row2.addWidget(self.bottom_gaps_card)
        gap_cards_row2.addWidget(self.cubic_gaps_card)

        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        center_layout.addWidget(self.status_label)

        self.page_stack = QStackedWidget()
        self.page_stack.addWidget(center_panel)   # index 0: main 3-D viewer
        content.addWidget(self.page_stack, 1)

        all_panels_row = QHBoxLayout()
        all_panels_row.setSpacing(10)

        widgets = [
            self.total_badge,
            self.rect_badge,
            self.cubique_badge,
            self.sets_card,
            self.gaps_card,
            self.top_gaps_card,
            self.bottom_gaps_card,
            self.cubic_gaps_card,
        ]

        for w in widgets:
            w.setMaximumWidth(180)
            all_panels_row.addWidget(w, 0)

        all_panels_row.addStretch()
        center_layout.addLayout(all_panels_row)

        right_panel = QFrame()
        right_panel.setObjectName("sidePanel")
        right_panel.setMinimumWidth(350)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(18, 18, 18, 18)
        right_layout.setSpacing(14)

        panel_title = QLabel("Panels & Distances")
        panel_title.setObjectName("panelTitle")
        right_layout.addWidget(panel_title)

        self.panel_names_card = InfoCard("Detected Panel Names", "--")
        right_layout.addWidget(self.panel_names_card)

        sub0 = QLabel("Clicked Panel Details")
        sub0.setObjectName("miniSectionTitle")
        right_layout.addWidget(sub0)

        self.clicked_panel_text = QTextEdit()
        self.clicked_panel_text.setObjectName("sideTextBox")
        self.clicked_panel_text.setReadOnly(True)
        right_layout.addWidget(self.clicked_panel_text, 1)

        size_row = QHBoxLayout()
        size_row.setSpacing(6)
        for label_text, attr in [("L", "size_l_input"), ("W", "size_w_input"), ("H", "size_h_input")]:
            lbl = QLabel(label_text)
            lbl.setFixedWidth(16)
            inp = QLineEdit()
            inp.setPlaceholderText("—")
            inp.setFixedWidth(72)
            inp.returnPressed.connect(self._apply_panel_resize)
            setattr(self, attr, inp)
            size_row.addWidget(lbl)
            size_row.addWidget(inp)
        size_row.addStretch()
        right_layout.addLayout(size_row)

        sub1 = QLabel("Stacked Panels")
        sub1.setObjectName("miniSectionTitle")
        right_layout.addWidget(sub1)

        self.stacked_panels_text = QTextEdit()
        self.stacked_panels_text.setObjectName("sideTextBox")
        self.stacked_panels_text.setReadOnly(True)
        right_layout.addWidget(self.stacked_panels_text, 2)

        sub2 = QLabel("Calculated Distances")
        sub2.setObjectName("miniSectionTitle")
        right_layout.addWidget(sub2)

        self.distance_list_text = QTextEdit()
        self.distance_list_text.setObjectName("sideTextBox")
        self.distance_list_text.setReadOnly(True)
        right_layout.addWidget(self.distance_list_text, 2)

        content.addWidget(right_panel, 0)

        # Page 1: persistent Edit-Nails page (built once, populated on open)
        self.nails_page = NailsInfoPage()
        self._connect_nails_signals()
        self.page_stack.addWidget(self.nails_page)

    # ------------------------------------------------------------------ nails page

    def _connect_nails_signals(self) -> None:
        self.nails_page.back_requested.connect(self._close_nails_page)
        self.nails_page.nail_placed.connect(self._on_nails_nail_placed)
        self.nails_page.nails_clear_requested.connect(self._on_nails_clear_requested)
        self.nails_page.preset_requested.connect(self._on_nails_preset_requested)
        self.nails_page.add_layer_requested.connect(self._show_add_layer_dialog)
        self.nails_page.layer_changed.connect(self._on_nails_layer_changed)

    def _open_nails_page(self, info=None) -> None:
        if info is not None:
            active_group = info.get("layer_name", "-")
        elif self.viewer.step_solid_actors:
            active_group = self.viewer.step_solid_actors[0]["info"].get("layer_name", "-")
        else:
            self.status_label.setText("Load a model first.")
            return

        all_layer_names: list[str] = list(
            dict.fromkeys(
                s["info"].get("layer_name", "-") for s in self.viewer.step_solid_actors
            )
        )
        self.nails_page.populate(all_layer_names, active_group)
        self.page_stack.setCurrentIndex(1)
        solids_data = [(s["polydata"], s["info"]) for s in self.viewer.step_solid_actors]
        self.nails_page.load_solids(solids_data, nail_positions=self.current_nail_positions)
        self.nails_page.refresh_count(self.current_nail_positions)

    def _close_nails_page(self) -> None:
        self.nails_page.stop_placement()
        self.viewer.set_nail_positions(self.current_nail_positions)
        self.viewer.set_nails_visible(bool(self.current_nail_positions))
        self.viewer.show_all_layers()
        self.page_stack.setCurrentIndex(0)

    def _on_nails_nail_placed(self, point, layer_name: str, nail_type: str, source: str) -> None:
        self._add_nail_item_typed(point, layer_name, nail_type=nail_type, source=source)
        self.nails_page.sync_nails(self.current_nail_positions)
        self.nails_page.refresh_count(self.current_nail_positions)

    def _on_nails_clear_requested(self, layer_name: str) -> None:
        self.current_nail_positions = [
            n for n in self.current_nail_positions if n.get("group") != layer_name
        ]
        self.viewer.set_nail_positions(self.current_nail_positions)
        self.viewer.set_nails_visible(bool(self.current_nail_positions))
        self.nails_page.sync_nails(self.current_nail_positions)
        self.nails_page.refresh_count(self.current_nail_positions)

    def _on_nails_preset_requested(self, layer_name: str, count: int, layout: str) -> None:
        layer_infos = self._layer_infos_for(layer_name)
        points = self._nail_points_for_layer_preset(layer_infos, count, layout)
        nail_type = self.nails_page.nail_type
        for point in points:
            self._add_nail_item_typed(point, layer_name, nail_type=nail_type,
                                      source=f"preset:{layout}")
        self.nails_page.sync_nails(self.current_nail_positions)
        self.nails_page.refresh_count(self.current_nail_positions)

    def _on_nails_layer_changed(self, _layer_name: str) -> None:
        self.nails_page.sync_nails(self.current_nail_positions)
        self.nails_page.refresh_count(self.current_nail_positions)

    def closeEvent(self, event):
        # Drain pending Qt paint/resize events before touching OpenGL
        QApplication.processEvents()
        # Shut down both VTK viewers — removes observers, clears interactor
        # style, and calls Finalize() so wglMakeCurrent stops firing
        self.viewer.shutdown()
        self.nails_page.finalize()
        super().closeEvent(event)

    def _apply_styles(self):
        self.setStyleSheet(
            """
            * {
                font-family: "Segoe UI", "Inter", "Arial";
                color: #1f2937;
            }

            QMainWindow {
                background: #f5f7fb;
            }

            QWidget {
                background: #f5f7fb;
            }

            QLabel {
                background: transparent;
            }

            #heroCard {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:1,
                    stop:0 #ffffff,
                    stop:0.55 #f7eadf,
                    stop:1 #ecd8c4
                );
                border: 1px solid #d8c2ad;
                border-radius: 22px;
            }

            #heroTitle {
                font-size: 28px;
                font-weight: 700;
                color: #3f2b1d;
            }

            #heroSubtitle {
                font-size: 14px;
                color: #6b7280;
            }

            #sidePanel, #viewerPanel {
                background: #ffffff;
                border: 1px solid #dbe3ee;
                border-radius: 22px;
            }

            #panelTitle {
                font-size: 18px;
                font-weight: 700;
                color: #243041;
            }

            #miniLabel {
                color: #6b7280;
                font-size: 13px;
                font-weight: 600;
            }

            #miniSectionTitle {
                color: #8a6b50;
                font-size: 13px;
                font-weight: 700;
                padding-top: 6px;
            }

            #previewFrame {
                background: #eef2f7;
                border: 1px solid #d7dee9;
                border-radius: 18px;
            }

            #statusLabel {
                color: #6b7280;
                font-size: 13px;
                padding-top: 2px;
            }

            #infoCard {
                background: #ffffff;
                border: 1px solid #dbe3ee;
                border-radius: 18px;
            }

            #cardTitle {
                color: #8a6b50;
                font-size: 12px;
                font-weight: 600;
                text-transform: uppercase;
            }

            #cardValue {
                color: #1f2937;
                font-size: 12px;
                font-weight: 700;
            }

            #countBadge {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:1,
                    stop:0 #ffffff,
                    stop:1 #f6efe8
                );
                border: 1px solid #dcc8b6;
                border-radius: 13px;
                min-height: 48px;
                max-height: 48px;
            }

            #countBadgeTitle {
                color: #8a6b50;
                font-size: 10px;
                font-weight: 700;
                text-transform: uppercase;
                qproperty-alignment: AlignCenter;
            }

            #countBadgeValue {
                color: #2b2118;
                font-size: 12px;
                font-weight: 800;
            }

            #sideTextBox {
                background: #fbfcff;
                border: 1px solid #d4dce8;
                border-radius: 14px;
                padding: 10px;
                selection-background-color: #d9e7ff;
                font-family: "Consolas", "Courier New";
                font-size: 12px;
            }

            QPushButton {
                border-radius: 12px;
                padding: 10px 16px;
                font-weight: 700;
                border: 1px solid transparent;
            }

            QPushButton#primaryButton {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 #c58a54,
                    stop:1 #a56d3f
                );
                color: white;
            }

            QPushButton#primaryButton:hover {
                background: #cf9661;
            }

            QPushButton#secondaryButton {
                background: #ffffff;
                border: 1px solid #d4dce8;
                color: #2a3442;
            }

            QPushButton#secondaryButton:hover {
                background: #f5f7fb;
            }

            QPushButton#iconButton {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:1,
                    stop:0 #ffffff,
                    stop:1 #f3f6fb
                );
                border: 1px solid #c8d2df;
                border-radius: 26px;
                color: #243041;
                font-size: 24px;
                font-weight: 800;
                padding: 0px;
            }

            QPushButton#iconButton:hover {
                background: #ffffff;
                border-color: #a56d3f;
                color: #8a5a32;
            }

            QPushButton#iconButton:pressed {
                background: #eef2f7;
                padding-top: 1px;
            }

            QComboBox {
                background: #ffffff;
                border: 1px solid #d4dce8;
                border-radius: 10px;
                padding: 8px 12px;
                min-width: 90px;
                color: #1f2937;
            }

            QComboBox QAbstractItemView {
                background: #ffffff;
                border: 1px solid #d4dce8;
                selection-background-color: #d9e7ff;
                color: #1f2937;
            }

            QPushButton#drawButton {
                background: #ffffff;
                border: 1px solid #d4dce8;
                color: #2a3442;
            }

            QPushButton#drawButton:hover {
                background: #f0f4ff;
                border-color: #93b4f5;
            }

            QPushButton#drawButton:checked {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 #3b82f6,
                    stop:1 #2563eb
                );
                color: #ffffff;
                border: 1px solid #2563eb;
            }

            QPushButton#drawButton:checked:hover {
                background: #4b93ff;
            }

            QPushButton#zoomButton {
                background: #ffffff;
                border: 1px solid #d4dce8;
                color: #2a3442;
                border-radius: 16px;
                padding: 0px;
                font-size: 18px;
                font-weight: 800;
            }

            QPushButton#zoomButton:hover {
                background: #f5f7fb;
                border-color: #b7c4d6;
            }

            QSlider#zoomSlider {
                background: transparent;
            }

            QSlider#zoomSlider::groove:horizontal {
                height: 6px;
                background: #dbe3ee;
                border-radius: 3px;
            }

            QSlider#zoomSlider::sub-page:horizontal {
                background: #c58a54;
                border-radius: 3px;
            }

            QSlider#zoomSlider::handle:horizontal {
                width: 16px;
                height: 16px;
                margin: -6px 0;
                background: #ffffff;
                border: 2px solid #a56d3f;
                border-radius: 8px;
            }
            """
        )

    def _nudge_zoom_slider(self, step: int):
        self.zoom_slider.setValue(self.zoom_slider.value() + step)

    def _on_zoom_slider_changed(self, value: int):
        delta = value - self._zoom_slider_value
        if delta == 0:
            return
        self._zoom_slider_value = value
        self.viewer.zoom_by_factor(1.15 ** (delta / 10.0))

    def _reset_view_and_zoom_bar(self):
        self.reset_view()
        self.zoom_slider.blockSignals(True)
        self.zoom_slider.setValue(100)
        self.zoom_slider.blockSignals(False)
        self._zoom_slider_value = 100

    def _layer_infos_for(self, layer_name: str):
        return [
            info for info in self.current_solid_infos
            if info.get("layer_name") == layer_name
        ]

    def _layer_rect(self, layer_infos):
        if not layer_infos:
            return None
        return (
            min(info["xmin"] for info in layer_infos),
            max(info["xmax"] for info in layer_infos),
            min(info["ymin"] for info in layer_infos),
            max(info["ymax"] for info in layer_infos),
        )

    def _nail_points_for_layer_preset(self, layer_infos, count: int, layout: str):
        rect = self._layer_rect(layer_infos)
        if rect is None:
            return []

        z = max(info["zmax"] for info in layer_infos) + 1.8
        xmin, xmax, ymin, ymax = rect
        cx = (xmin + xmax) / 2.0
        cy = (ymin + ymax) / 2.0
        width = xmax - xmin
        depth = ymax - ymin

        if count <= 1:
            return [(cx, cy, z)]

        if count >= 3 and layout in ("centered", "triangle"):
            return self._build_nail_points_for_rect(rect, z, 3)

        if width >= depth:
            y = cy
            xs = [xmin + width * 0.35, xmax - width * 0.35] if count == 2 else [
                xmin + width * 0.20,
                cx,
                xmax - width * 0.20,
            ]
            return [(x, y, z) for x in xs[:count]]

        x = cx
        ys = [ymin + depth * 0.35, ymax - depth * 0.35] if count == 2 else [
            ymin + depth * 0.20,
            cy,
            ymax - depth * 0.20,
        ]
        return [(x, y, z) for y in ys[:count]]

    def _add_nail_item(self, point, layer_name: str, source: str = "manual"):
        item = {
            "point": point,
            "group": layer_name,
            "side": "top",
            "count_in_group": 1,
            "source": source,
        }
        self.current_nail_positions.append(item)
        self.viewer.set_nail_positions(self.current_nail_positions)
        self.viewer.set_nails_visible(True)
        self.status_label.setText(f"Nails shown: {len(self.current_nail_positions)}")

    def _copy_selected_shape(self):
        if self.viewer.copy_selected_step_solid():
            info = self.viewer.selected_step_solid["info"] if self.viewer.selected_step_solid else {}
            name = info.get("panel_name", "shape")
            self.status_label.setText(f"Copied '{name}' — press Ctrl+V to paste a duplicate.")

    def _paste_shape(self):
        result = self.viewer.paste_step_solid()
        if result is not None:
            name = result["info"].get("panel_name", "copy")
            self.status_label.setText(f"Pasted duplicate '{name}'.")
        else:
            self.status_label.setText("Nothing to paste — select a shape and press Ctrl+C first.")

    def _add_nail_item_typed(self, point, layer_name: str,
                             nail_type: str = "long", source: str = "manual",
                             side: str = "top"):
        item = {
            "point": point,
            "group": layer_name,
            "side": side,
            "nail_type": nail_type,
            "count_in_group": 1,
            "source": source,
        }
        self.current_nail_positions.append(item)
        self.viewer.set_nail_positions(self.current_nail_positions)
        self.viewer.set_nails_visible(True)
        self.status_label.setText(f"Nails shown: {len(self.current_nail_positions)}")

    def _show_add_layer_dialog(self, parent_dialog=None):
        from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QPen, QBrush, QPolygon
        from PySide6.QtCore import QPoint
        from PySide6.QtWidgets import QButtonGroup

        factor, unit = self._get_factor_and_unit()

        dialog = QDialog(self)
        dialog.setWindowTitle("Add New Layer")
        dialog.setMinimumWidth(460)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = QLabel("Add New Layer")
        title.setObjectName("panelTitle")
        layout.addWidget(title)

        # ── Layer type ────────────────────────────────────────────────────────
        type_label = QLabel("Layer Type")
        type_label.setObjectName("miniLabel")
        layout.addWidget(type_label)

        layer_type_combo = QComboBox()
        layer_type_combo.addItems(self._layer_types_from_xml())
        layout.addWidget(layer_type_combo)

        # ── Custom shape picker (visible only when "Custom" is selected) ──────
        _SHAPES = [
            ("Box",      "Rectangular"),
            ("Cylinder", "Cylinder"),
            ("Sphere",   "Sphere"),
            ("Cone",     "Cone"),
            ("Torus",    "Torus"),
        ]
        _selected_shape = ["Box"]

        def _make_shape_pixmap(shape: str, sz: int = 64) -> QPixmap:
            pm = QPixmap(sz, sz)
            pm.fill(QColor(0, 0, 0, 0))
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            pen = QPen(QColor("#5b3e26"))
            pen.setWidth(2)
            p.setPen(pen)
            wood = QColor("#e8c882")
            dark = QColor("#c89b6e")
            p.setBrush(QBrush(wood))

            if shape == "Box":
                p.drawRect(6, 22, 36, 28)
                top = QPolygon([QPoint(6, 22), QPoint(18, 10), QPoint(54, 10), QPoint(42, 22)])
                p.drawPolygon(top)
                p.setBrush(QBrush(dark))
                right = QPolygon([QPoint(42, 22), QPoint(54, 10), QPoint(54, 38), QPoint(42, 50)])
                p.drawPolygon(right)

            elif shape == "Cylinder":
                p.drawRect(8, 16, 48, 34)
                p.setBrush(QBrush(dark))
                p.drawEllipse(8, 32, 48, 16)
                p.setBrush(QBrush(wood))
                p.drawEllipse(8, 8, 48, 16)

            elif shape == "Sphere":
                p.drawEllipse(6, 6, 52, 52)
                p.setBrush(QBrush(QColor(255, 255, 255, 70)))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(16, 12, 18, 14)

            elif shape == "Cone":
                cone = QPolygon([QPoint(32, 4), QPoint(58, 58), QPoint(6, 58)])
                p.drawPolygon(cone)
                p.setBrush(QBrush(dark))
                p.drawEllipse(6, 48, 52, 16)

            elif shape == "Torus":
                p.setBrush(Qt.BrushStyle.NoBrush)
                outer_pen = QPen(QColor("#e8c882"))
                outer_pen.setWidth(14)
                p.setPen(outer_pen)
                p.drawEllipse(8, 8, 48, 48)
                border_pen = QPen(QColor("#5b3e26"))
                border_pen.setWidth(2)
                p.setPen(border_pen)
                p.drawEllipse(8, 8, 48, 48)
                p.drawEllipse(22, 22, 20, 20)

            p.end()
            return pm

        custom_widget = QWidget()
        custom_inner = QVBoxLayout(custom_widget)
        custom_inner.setContentsMargins(0, 0, 0, 0)
        custom_inner.setSpacing(6)

        shape_label = QLabel("Shape")
        shape_label.setObjectName("miniLabel")
        custom_inner.addWidget(shape_label)

        shape_btn_group = QButtonGroup(dialog)
        shape_btn_group.setExclusive(True)
        shapes_row_w = QWidget()
        shapes_row = QHBoxLayout(shapes_row_w)
        shapes_row.setSpacing(8)
        shapes_row.setContentsMargins(0, 0, 0, 0)

        _SHAPE_BTN_STYLE = (
            "QPushButton {"
            "  border: 2px solid #d4dce8; border-radius: 12px;"
            "  background: #ffffff; color: #2a3442;"
            "  font-size: 11px; font-weight: 600;"
            "  padding: 4px 2px 6px 2px;"
            "}"
            "QPushButton:checked {"
            "  border: 2px solid #c58a54;"
            "  background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "    stop:0 #fef3e8,stop:1 #f6deba);"
            "  color: #7a4a1e;"
            "}"
            "QPushButton:hover { background: #f5f7fb; }"
        )

        for shape_key, shape_label_text in _SHAPES:
            btn = QPushButton(shape_label_text)
            btn.setCheckable(True)
            btn.setFixedSize(80, 90)
            btn.setIcon(QIcon(_make_shape_pixmap(shape_key)))
            btn.setIconSize(_make_shape_pixmap(shape_key).size())
            btn.setStyleSheet(_SHAPE_BTN_STYLE)
            btn.setProperty("shape_key", shape_key)
            if shape_key == "Box":
                btn.setChecked(True)
            shape_btn_group.addButton(btn)
            shapes_row.addWidget(btn)

        shapes_row.addStretch()
        custom_inner.addWidget(shapes_row_w)
        layout.addWidget(custom_widget)

        # ── Position ──────────────────────────────────────────────────────────
        position_label = QLabel("Position")
        position_label.setObjectName("miniLabel")
        layout.addWidget(position_label)

        position_combo = QComboBox()
        position_combo.addItems(["Top of Pallet", "Bottom of Pallet"])
        layout.addWidget(position_combo)

        # ── Load from saved parts ─────────────────────────────────────────────
        use_parts_chk = QCheckBox("Load from saved XML parts")
        layout.addWidget(use_parts_chk)

        parts_status_lbl = QLabel()
        parts_status_lbl.setObjectName("miniLabel")
        layout.addWidget(parts_status_lbl)

        # ── Dimensions section ────────────────────────────────────────────────
        bb = self.current_base_bb
        if bb is not None:
            deck = [i for i in self.current_solid_infos if i.get("panel_class") == "p1"]
            _def_l_mm = bb.xlen
            _def_w_mm = bb.ylen
            _def_h_mm = sum(i["z"] for i in deck) / len(deck) if deck else 18.0
        else:
            _def_l_mm, _def_w_mm, _def_h_mm = 1200.0, 800.0, 18.0

        # per-shape: (labels_tuple, defaults_mm_tuple)
        _SHAPE_DIMS: Dict[str, Any] = {
            "Box":      (("L", "W", "H"), (_def_l_mm, _def_w_mm, _def_h_mm)),
            "Cylinder": (("D", "—", "H"), (min(_def_l_mm, _def_w_mm), None, _def_h_mm)),
            "Sphere":   (("R", "—", "—"), (min(_def_l_mm, _def_w_mm) / 2, None, None)),
            "Cone":     (("R", "—", "H"), (min(_def_l_mm, _def_w_mm) / 2, None, _def_h_mm)),
            "Torus":    (("R", "r", "—"), (min(_def_l_mm, _def_w_mm) / 2, _def_h_mm / 2, None)),
        }
        _SHAPE_HINTS = {
            "Box":      "Length × Width × Height",
            "Cylinder": "Diameter, Height",
            "Sphere":   "Radius",
            "Cone":     "Base Radius, Height",
            "Torus":    "Major Radius, Minor Radius",
        }

        dims_widget = QWidget()
        dims_inner = QVBoxLayout(dims_widget)
        dims_inner.setContentsMargins(0, 0, 0, 0)
        dims_inner.setSpacing(4)

        dims_label = QLabel()
        dims_label.setObjectName("miniLabel")
        dims_inner.addWidget(dims_label)

        dims_row = QHBoxLayout()
        dims_row.setSpacing(8)
        _dim_inputs: Dict[str, Any] = {}
        for key in ("L", "W", "H"):
            lbl_w = QLabel()
            lbl_w.setObjectName("miniLabel")
            lbl_w.setFixedWidth(22)
            inp_w = QLineEdit()
            inp_w.setFixedWidth(85)
            _dim_inputs[key] = (lbl_w, inp_w)
            dims_row.addWidget(lbl_w)
            dims_row.addWidget(inp_w)
        dims_row.addStretch()
        dims_inner.addLayout(dims_row)
        layout.addWidget(dims_widget)

        def _update_dims_ui(reset_values: bool = False):
            shape = _selected_shape[0]
            labels, defaults_mm = _SHAPE_DIMS.get(shape, (("L", "W", "H"), (_def_l_mm, _def_w_mm, _def_h_mm)))
            dims_label.setText(f"Dimensions ({unit})  —  {_SHAPE_HINTS.get(shape, '')}")
            for (key, (lbl_w, inp_w)), lbl_txt, val_mm in zip(_dim_inputs.items(), labels, defaults_mm):
                lbl_w.setText(f"{lbl_txt}:")
                disabled = lbl_txt == "—"
                inp_w.setEnabled(not disabled)
                if disabled:
                    inp_w.setText("—")
                    inp_w.setStyleSheet("color: #9ca3af;")
                else:
                    inp_w.setStyleSheet("")
                    cur = inp_w.text().strip()
                    if reset_values or not cur or cur == "—":
                        inp_w.setText(f"{val_mm * factor:.1f}" if val_mm is not None else "")

        def _on_shape_btn_changed():
            for btn in shape_btn_group.buttons():
                if btn.isChecked():
                    _selected_shape[0] = btn.property("shape_key")
                    break
            _update_dims_ui(reset_values=True)

        shape_btn_group.buttonToggled.connect(lambda *_: _on_shape_btn_changed())
        _update_dims_ui(reset_values=True)

        def _refresh_parts_ui():
            layer_type = layer_type_combo.currentText()
            is_custom = layer_type.casefold() == "custom"
            custom_widget.setVisible(is_custom)
            xml_parts = self._find_xml_parts_for_layer(layer_type)
            if xml_parts:
                use_parts_chk.setEnabled(True)
                parts_status_lbl.setText(f"{len(xml_parts)} saved part(s) found for '{layer_type}'")
            else:
                use_parts_chk.blockSignals(True)
                use_parts_chk.setChecked(False)
                use_parts_chk.blockSignals(False)
                use_parts_chk.setEnabled(False)
                parts_status_lbl.setText("No saved parts found for this layer type")
            dims_widget.setVisible(not (use_parts_chk.isEnabled() and use_parts_chk.isChecked()))

        layer_type_combo.currentTextChanged.connect(lambda _: _refresh_parts_ui())
        use_parts_chk.toggled.connect(lambda _: _refresh_parts_ui())
        _refresh_parts_ui()

        # ── Buttons ───────────────────────────────────────────────────────────
        buttons_row = QHBoxLayout()
        buttons_row.setSpacing(8)
        buttons_row.addStretch(1)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("secondaryButton")
        cancel_btn.clicked.connect(dialog.reject)
        buttons_row.addWidget(cancel_btn)

        add_btn = QPushButton("Add Layer")
        add_btn.setObjectName("primaryButton")
        buttons_row.addWidget(add_btn)
        layout.addLayout(buttons_row)

        # ── Add logic ─────────────────────────────────────────────────────────
        def _read_dim_mm(key: str) -> Optional[float]:
            _, inp_w = _dim_inputs[key]
            if not inp_w.isEnabled():
                return None
            try:
                return float(inp_w.text()) / factor  # display unit → mm
            except ValueError:
                return None

        def add_layer():
            import cadquery as cq

            layer_type = layer_type_combo.currentText()
            position = position_combo.currentText()

            if use_parts_chk.isChecked():
                self._add_layer_from_xml_parts(layer_type, position)
                dialog.accept()
                return

            shape = _selected_shape[0] if layer_type.casefold() == "custom" else "Box"

            L = _read_dim_mm("L")
            W = _read_dim_mm("W")
            H = _read_dim_mm("H")

            _required: Dict[str, tuple] = {
                "Box":      (L, W, H),
                "Cylinder": (L, H),
                "Sphere":   (L,),
                "Cone":     (L, H),
                "Torus":    (L, W),
            }
            if any(v is None or v <= 0 for v in _required.get(shape, (L, W, H))):
                QMessageBox.warning(
                    dialog, "Add Layer",
                    f"Invalid dimensions for {shape} — enter positive numeric values."
                )
                return

            if self.current_base_bb is not None:
                cur_bb = self.current_base_bb
                cx = (cur_bb.xmin + cur_bb.xmax) / 2.0
                cy = (cur_bb.ymin + cur_bb.ymax) / 2.0
            else:
                cx, cy = 0.0, 0.0

            if self.current_solid_infos:
                pallet_top = max(i["zmax"] for i in self.current_solid_infos)
                pallet_bottom = min(i["zmin"] for i in self.current_solid_infos)
            else:
                pallet_top = 0.0
                pallet_bottom = 0.0

            gap = 2.0
            on_top = position == "Top of Pallet"

            try:
                if shape == "Box":
                    thickness = H
                    z_c = (pallet_top + gap + thickness / 2.0) if on_top else (pallet_bottom - gap - thickness / 2.0)
                    solid = cq.Workplane("XY", origin=(cx, cy, z_c)).box(L, W, thickness).val()

                elif shape == "Cylinder":
                    radius, thickness = L / 2.0, H
                    z_base = (pallet_top + gap) if on_top else (pallet_bottom - gap - thickness)
                    solid = (
                        cq.Workplane("XY", origin=(cx, cy, z_base))
                        .circle(radius).extrude(thickness).val()
                    )

                elif shape == "Sphere":
                    radius = L
                    z_c = (pallet_top + gap + radius) if on_top else (pallet_bottom - gap - radius)
                    solid = cq.Workplane("XY", origin=(cx, cy, z_c)).sphere(radius).val()

                elif shape == "Cone":
                    radius, thickness = L, H
                    z_base = (pallet_top + gap) if on_top else (pallet_bottom - gap - thickness)
                    solid = cq.Solid.makeCone(
                        radius, 0.01, thickness,
                        pnt=cq.Vector(cx, cy, z_base),
                        dir=cq.Vector(0, 0, 1),
                    )

                elif shape == "Torus":
                    major_r, minor_r = L, W
                    z_c = (pallet_top + gap + minor_r) if on_top else (pallet_bottom - gap - minor_r)
                    solid = cq.Solid.makeTorus(
                        major_r, minor_r,
                        pnt=cq.Vector(cx, cy, z_c),
                        dir=cq.Vector(0, 0, 1),
                    )

                else:
                    raise ValueError(f"Unknown shape: {shape}")

                polydata = self._cadquery_shape_to_vtk_polydata(solid)

            except Exception as exc:
                QMessageBox.critical(dialog, "Add Layer Failed", f"Could not create geometry:\n{exc}")
                return

            sbb = solid.BoundingBox()
            n_custom = sum(1 for i in self.current_solid_infos if i.get("is_custom_layer"))
            panel_name = f"custom_{shape.lower()}_{layer_type.lower()}_{n_custom + 1}"
            new_order = max((i.get("panel_order", 0) for i in self.current_solid_infos), default=0) + 1

            info = {
                "index": len(self.current_solid_infos) + 1,
                "solid": solid,
                "solid_shape": getattr(solid, "wrapped", None),
                "x": sbb.xlen,
                "y": sbb.ylen,
                "z": sbb.zlen,
                "xmin": sbb.xmin, "xmax": sbb.xmax,
                "ymin": sbb.ymin, "ymax": sbb.ymax,
                "zmin": sbb.zmin, "zmax": sbb.zmax,
                "cx": (sbb.xmin + sbb.xmax) / 2.0,
                "cy": (sbb.ymin + sbb.ymax) / 2.0,
                "cz": (sbb.zmin + sbb.zmax) / 2.0,
                "footprint": sbb.xlen * sbb.ylen,
                "longest_xy": max(sbb.xlen, sbb.ylen),
                "shortest_xy": min(sbb.xlen, sbb.ylen),
                "panel_class": "p1",
                "panel_layer": "top" if on_top else "bottom",
                "panel_name": panel_name,
                "panel_order": new_order,
                "layer_name": layer_type,
                "shape_type": shape,
                "is_custom_layer": True,
            }

            self.viewer.add_step_solid(polydata, info)
            self.current_solid_infos.append(info)
            self.current_panels_count += 1
            self.total_badge.set_value(str(self.current_panels_count))
            self.panel_names_card.set_value(str(self.current_panels_count))
            self.status_label.setText(
                f"New '{layer_type}' ({shape}) layer added ({position})  |  {panel_name}"
            )
            dialog.accept()

        add_btn.clicked.connect(add_layer)
        dialog.exec()

    def _layer_types_from_xml(self) -> List[str]:
        default_types = [
            "Deck",
            "Perimetral",
            "Perimeter",
            "Stringer",
            "Blocker",
            "Spacer",
            "Custom",
        ]
        xml_dir = Path(__file__).resolve().parent / "layer_parts_xml"
        if not xml_dir.exists() or not xml_dir.is_dir():
            return default_types

        seen = set()
        layer_types: List[str] = []
        for xml_path in sorted(xml_dir.glob("*.xml")):
            try:
                root = ET.parse(xml_path).getroot()
            except Exception:
                continue
            layer_name = (root.findtext("layer_name") or "").strip()
            if not layer_name:
                continue
            key = layer_name.casefold()
            if key in seen:
                continue
            seen.add(key)
            layer_types.append(layer_name)

        return layer_types or default_types

    def _find_xml_parts_for_layer(self, layer_type: str) -> List[Path]:
        xml_dir = Path(__file__).resolve().parent / "layer_parts_xml"
        if not xml_dir.exists():
            return []
        parts = []
        for xml_path in sorted(xml_dir.glob("*.xml")):
            try:
                root = ET.parse(xml_path).getroot()
            except Exception:
                continue
            if root.tag == "layer":
                name = root.get("name", "").strip()
            elif root.tag == "part":
                name = (root.findtext("layer_name") or "").strip()
            else:
                continue
            if name.casefold() == layer_type.casefold():
                parts.append(xml_path)
        return parts

    def _add_layer_from_xml_parts(self, layer_type: str, position: str):
        import cadquery as cq

        xml_parts = self._find_xml_parts_for_layer(layer_type)
        if not xml_parts:
            self.status_label.setText(f"No saved XML parts found for '{layer_type}'")
            return

        if self.current_solid_infos:
            pallet_top = max(i["zmax"] for i in self.current_solid_infos)
            pallet_bottom = min(i["zmin"] for i in self.current_solid_infos)
        else:
            pallet_top = 0.0
            pallet_bottom = 0.0

        def _parse_part_el(el):
            size = el.find("size_mm")
            center = el.find("center_mm")
            bounds = el.find("bounds_mm")
            if size is None or center is None:
                return None
            x = float(size.findtext("x") or 0)
            y = float(size.findtext("y") or 0)
            z = float(size.findtext("z") or 0)
            cx = float(center.findtext("x") or 0)
            cy = float(center.findtext("y") or 0)
            cz = float(center.findtext("z") or 0)
            xmin = float(bounds.findtext("xmin") or cx - x / 2) if bounds is not None else cx - x / 2
            xmax = float(bounds.findtext("xmax") or cx + x / 2) if bounds is not None else cx + x / 2
            ymin = float(bounds.findtext("ymin") or cy - y / 2) if bounds is not None else cy - y / 2
            ymax = float(bounds.findtext("ymax") or cy + y / 2) if bounds is not None else cy + y / 2
            zmin = float(bounds.findtext("zmin") or cz - z / 2) if bounds is not None else cz - z / 2
            zmax = float(bounds.findtext("zmax") or cz + z / 2) if bounds is not None else cz + z / 2
            return {
                "x": x, "y": y, "z": z,
                "cx": cx, "cy": cy, "cz": cz,
                "xmin": xmin, "xmax": xmax,
                "ymin": ymin, "ymax": ymax,
                "zmin": zmin, "zmax": zmax,
                "panel_name": (el.findtext("panel_name") or "").strip(),
                "panel_class": (el.findtext("panel_class") or "p1").strip(),
            }

        parsed_parts = []
        saved_zmins, saved_zmaxs = [], []
        for xml_path in xml_parts:
            try:
                root = ET.parse(xml_path).getroot()
                if root.tag == "layer":
                    elements = root.findall("part")
                elif root.tag == "part":
                    elements = [root]
                else:
                    continue
                for el in elements:
                    parsed = _parse_part_el(el)
                    if parsed is None:
                        continue
                    saved_zmins.append(parsed["zmin"])
                    saved_zmaxs.append(parsed["zmax"])
                    parsed_parts.append(parsed)
            except Exception:
                continue

        if not parsed_parts:
            self.status_label.setText(f"Could not parse any XML parts for '{layer_type}'")
            return

        saved_group_zmin = min(saved_zmins)
        saved_group_zmax = max(saved_zmaxs)
        gap = 2.0

        if position == "Top of Pallet":
            z_offset = pallet_top + gap - saved_group_zmin
        else:
            z_offset = pallet_bottom - gap - saved_group_zmax

        n_added = 0
        for part in parsed_parts:
            px, py, pz = part["x"], part["y"], part["z"]
            pcx, pcy = part["cx"], part["cy"]
            pcz = part["cz"] + z_offset
            pzmin = part["zmin"] + z_offset
            pzmax = part["zmax"] + z_offset
            try:
                box_solid = (
                    cq.Workplane("XY", origin=(pcx, pcy, pcz))
                    .box(px, py, pz)
                    .val()
                )
                polydata = self._cadquery_shape_to_vtk_polydata(box_solid)
            except Exception:
                continue

            n_custom = sum(1 for i in self.current_solid_infos if i.get("is_custom_layer"))
            panel_name = part["panel_name"] or f"custom_{layer_type.lower()}_{n_custom + 1}"
            new_order = max((i.get("panel_order", 0) for i in self.current_solid_infos), default=0) + 1

            info = {
                "index": len(self.current_solid_infos) + 1,
                "solid": box_solid,
                "solid_shape": getattr(box_solid, "wrapped", None),
                "x": px,
                "y": py,
                "z": pz,
                "xmin": part["xmin"],
                "xmax": part["xmax"],
                "ymin": part["ymin"],
                "ymax": part["ymax"],
                "zmin": pzmin,
                "zmax": pzmax,
                "cx": pcx,
                "cy": pcy,
                "cz": pcz,
                "footprint": px * py,
                "longest_xy": max(px, py),
                "shortest_xy": min(px, py),
                "panel_class": part["panel_class"],
                "panel_layer": "top" if position == "Top of Pallet" else "bottom",
                "panel_name": panel_name,
                "panel_order": new_order,
                "layer_name": layer_type,
                "is_custom_layer": True,
            }

            self.viewer.add_step_solid(polydata, info)
            self.current_solid_infos.append(info)
            self.current_panels_count += 1
            n_added += 1

        self.total_badge.set_value(str(self.current_panels_count))
        self.panel_names_card.set_value(str(self.current_panels_count))
        self._recompute_gaps_from_solid_infos()
        self.status_label.setText(
            f"Added {n_added} part(s) for '{layer_type}' layer ({position})"
        )

    # ------------------------------------------------------------------ XML open

    def _load_xml_layer_file(self, file_path: str):
        try:
            root = ET.parse(file_path).getroot()
        except Exception as exc:
            QMessageBox.critical(self, "Open Failed", f"Could not parse XML file.\n\n{exc}")
            return

        tag = root.tag
        path = Path(file_path)

        loaded_parts = False
        if tag == "layers":
            for layer_el in root.findall("layer"):
                for part_el in layer_el.findall("part"):
                    self._load_xml_part_file(part_el, path)
                    loaded_parts = True
        elif tag == "layer":
            for part_el in root.findall("part"):
                self._load_xml_part_file(part_el, path)
                loaded_parts = True
        elif tag == "part":
            self._load_xml_part_file(root, path)
            loaded_parts = True
        elif tag == "drawing":
            self._load_xml_drawing_file(root, path)
        else:
            QMessageBox.warning(
                self, "Unknown XML Format",
                f"Cannot open this XML file — unrecognised root element <{tag}>.\n"
                "Expected <layers> (saved scene), <layer> (layer group), <part> (panel part), or <drawing> (saved drawing).",
            )

        if loaded_parts:
            self._recompute_gaps_from_solid_infos()
            self.viewer.reset_view()

    def _recompute_gaps_from_solid_infos(self):
        import types
        infos = self.current_solid_infos
        if not infos:
            return

        # classify_panels sets panel_class AND panel_layer (top/bottom) from geometry;
        # without this, all XML parts stay panel_layer="top" and bottom gaps are never found
        counts = self.classify_panels(infos)
        self.current_panels_count = counts["total"]
        self.current_rectangular_count = counts["p1"]
        self.current_cubique_count = counts["p2"]
        self.current_other_count = counts["other"]

        bb = types.SimpleNamespace(
            xmin=min(i["xmin"] for i in infos),
            xmax=max(i["xmax"] for i in infos),
            ymin=min(i["ymin"] for i in infos),
            ymax=max(i["ymax"] for i in infos),
            zmin=min(i["zmin"] for i in infos),
            zmax=max(i["zmax"] for i in infos),
        )
        bb.xlen = bb.xmax - bb.xmin
        bb.ylen = bb.ymax - bb.ymin
        bb.zlen = bb.zmax - bb.zmin
        self.current_base_bb = bb

        top_gaps = self.detect_top_panel_spaces(infos, bb)
        bottom_gaps = self.detect_bottom_panel_spaces(infos, bb)
        cubic_gaps = self.detect_cubic_panel_spaces(infos, bb)
        self.current_gap_measurements = top_gaps + bottom_gaps + cubic_gaps
        self.current_top_gaps_count = len(top_gaps)
        self.current_bottom_gaps_count = len(bottom_gaps)
        self.current_cubic_gaps_count = len(cubic_gaps)

        self.refresh_gap_measurements()
        self.viewer.set_measurements_visible(False)
        self.toggle_dist_btn.setText("Show Distances")
        self.update_dimension_cards()
        self.update_panel_side_lists()

        # Update count cards now that classify_panels may have updated classifications
        self.total_badge.set_value(str(self.current_panels_count))
        if hasattr(self, "rect_badge"):
            self.rect_badge.set_value(str(self.current_rectangular_count))
        if hasattr(self, "cubique_badge"):
            self.cubique_badge.set_value(str(self.current_cubique_count))
        self.gaps_card.set_value(str(len(self.current_gap_measurements)))
        self.top_gaps_card.set_value(str(self.current_top_gaps_count))
        self.bottom_gaps_card.set_value(str(self.current_bottom_gaps_count))
        self.cubic_gaps_card.set_value(str(self.current_cubic_gaps_count))

    def _load_xml_part_file(self, root, path: Path):
        import cadquery as cq

        try:
            size = root.find("size_mm")
            center = root.find("center_mm")
            bounds = root.find("bounds_mm")
            if size is None or center is None:
                raise ValueError("Missing <size_mm> or <center_mm>")

            x = float(size.findtext("x") or 0)
            y = float(size.findtext("y") or 0)
            z = float(size.findtext("z") or 0)
            cx = float(center.findtext("x") or 0)
            cy = float(center.findtext("y") or 0)
            cz = float(center.findtext("z") or 0)

            if bounds is not None:
                xmin = float(bounds.findtext("xmin") or cx - x / 2)
                xmax = float(bounds.findtext("xmax") or cx + x / 2)
                ymin = float(bounds.findtext("ymin") or cy - y / 2)
                ymax = float(bounds.findtext("ymax") or cy + y / 2)
                zmin = float(bounds.findtext("zmin") or cz - z / 2)
                zmax = float(bounds.findtext("zmax") or cz + z / 2)
            else:
                xmin, xmax = cx - x / 2, cx + x / 2
                ymin, ymax = cy - y / 2, cy + y / 2
                zmin, zmax = cz - z / 2, cz + z / 2

            panel_name = (root.findtext("panel_name") or path.stem).strip()
            layer_name = (root.findtext("layer_name") or "Custom").strip()
            panel_class = (root.findtext("panel_class") or "p1").strip()

            solid = cq.Workplane("XY", origin=(cx, cy, cz)).box(x, y, z).val()
            polydata = self._cadquery_shape_to_vtk_polydata(solid)

        except Exception as exc:
            QMessageBox.critical(self, "Open Failed", f"Could not load XML part.\n\n{exc}")
            return

        new_order = max((i.get("panel_order", 0) for i in self.current_solid_infos), default=0) + 1
        info = {
            "index": len(self.current_solid_infos) + 1,
            "solid": solid,
            "solid_shape": getattr(solid, "wrapped", None),
            "x": x, "y": y, "z": z,
            "xmin": xmin, "xmax": xmax,
            "ymin": ymin, "ymax": ymax,
            "zmin": zmin, "zmax": zmax,
            "cx": cx, "cy": cy, "cz": cz,
            "footprint": x * y,
            "longest_xy": max(x, y),
            "shortest_xy": min(x, y),
            "panel_class": panel_class,
            "panel_layer": "top",
            "panel_name": panel_name,
            "panel_order": new_order,
            "layer_name": layer_name,
            "is_custom_layer": True,
        }

        self.viewer.add_step_solid(polydata, info)
        self.current_solid_infos.append(info)
        self.current_panels_count += 1
        self.total_badge.set_value(str(self.current_panels_count))
        self.panel_names_card.set_value(str(self.current_panels_count))
        self.file_card.set_value(path.name)
        self.status_label.setText(f"XML part loaded: {panel_name}  |  Layer: {layer_name}")

    def _load_xml_drawing_file(self, root, path: Path):
        name = root.get("name", path.stem)
        lines_loaded = 0

        lines_el = root.find("lines")
        if lines_el is not None:
            for line_el in lines_el.findall("line"):
                try:
                    width = float(line_el.get("width", 1))
                    color = (
                        float(line_el.get("color_r", 1.0)),
                        float(line_el.get("color_g", 1.0)),
                        float(line_el.get("color_b", 1.0)),
                    )
                    points = [
                        (float(pt.get("x", 0)), float(pt.get("y", 0)), float(pt.get("z", 0)))
                        for pt in line_el.findall("point")
                    ]
                    if len(points) < 2:
                        continue
                    self.viewer.cancel_current_line()
                    self.viewer.current_line_color = color
                    self.viewer.current_line_width = width
                    self.viewer.current_line_points = list(points)
                    self.viewer.finish_current_line()
                    lines_loaded += 1
                except Exception:
                    continue

        self.file_card.set_value(path.name)
        self.status_label.setText(f"Drawing '{name}' loaded: {lines_loaded} line(s)")

    def _show_settings_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Settings")
        dialog.setMinimumWidth(420)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = QLabel("Settings")
        title.setObjectName("panelTitle")
        layout.addWidget(title)

        unit_row = QHBoxLayout()
        unit_row.setSpacing(8)

        unit_label = QLabel("Default Unit")
        unit_label.setObjectName("miniLabel")
        unit_row.addWidget(unit_label)

        unit_combo = QComboBox()
        unit_combo.addItems(["cm", "mm", "m"])
        unit_combo.setCurrentText(self.unit_combo.currentText())
        unit_row.addWidget(unit_combo)
        layout.addLayout(unit_row)

        view_row = QHBoxLayout()
        view_row.setSpacing(8)

        view_label = QLabel("Default View")
        view_label.setObjectName("miniLabel")
        view_row.addWidget(view_label)

        view_combo = QComboBox()
        view_combo.addItems(["Wood", "CAD"])
        view_combo.setCurrentText("CAD" if self.material_card.value_label.text() == "CAD" else "Wood")
        view_row.addWidget(view_combo)
        layout.addLayout(view_row)

        buttons_row = QHBoxLayout()
        buttons_row.addStretch(1)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("secondaryButton")
        cancel_btn.clicked.connect(dialog.reject)
        buttons_row.addWidget(cancel_btn)

        apply_btn = QPushButton("Apply")
        apply_btn.setObjectName("primaryButton")
        buttons_row.addWidget(apply_btn)
        layout.addLayout(buttons_row)

        def apply_settings():
            self.unit_combo.setCurrentText(unit_combo.currentText())
            if view_combo.currentText() == "CAD":
                self.show_cad_view()
            else:
                self.show_wood_view()
            self.status_label.setText("Settings applied")
            dialog.accept()

        apply_btn.clicked.connect(apply_settings)
        dialog.exec()

    def _delete_selected_annotation(self):
        if self.viewer.selected_line is not None:
            self.viewer.delete_selected_line()
        elif self.viewer.selected_step_solid is not None:
            info = self.viewer.selected_step_solid["info"]
            self.viewer.delete_selected_step_solid()
            self.status_label.setText(f"Panel '{info.get('panel_name', '?')}' removed from view")

    # ------------------------------------------------------------------ object color

    def _obj_color_apply_all(self, rgb):
        self.viewer.set_all_solids_color(*rgb)

    def _obj_color_custom_all(self):
        cr, cg, cb = self.viewer.current_object_color
        from PySide6.QtGui import QColor
        initial = QColor(int(cr * 255), int(cg * 255), int(cb * 255))
        color = QColorDialog.getColor(initial, self, "Choose Panel Color")
        if color.isValid():
            self.viewer.set_all_solids_color(color.redF(), color.greenF(), color.blueF())

    def _obj_color_selected(self):
        if not self.viewer.group_selected_solids:
            self.status_label.setText("No panel selected — click a panel first.")
            return
        from PySide6.QtGui import QColor
        cr, cg, cb = self.viewer.current_object_color
        initial = QColor(int(cr * 255), int(cg * 255), int(cb * 255))
        color = QColorDialog.getColor(initial, self, "Choose Color for Selected Panel")
        if color.isValid():
            self.viewer.set_selected_solids_color(color.redF(), color.greenF(), color.blueF())

    def _obj_color_reset(self):
        self.viewer.set_all_solids_color(1.0, 0.97, 0.55)

    def _clear_all_annotations(self):
        self.viewer.clear()
        self.current_solid_infos.clear()
        self.current_panels_count = 0
        self.total_badge.set_value("0")
        self.panel_names_card.set_value("0")
        self.file_card.set_value("—")
        self.status_label.setText("Cleared")

    def _edit_selected_layer(self):
        groups = self.build_stacked_panels_groups()
        dlg = EditNailsDialog(groups, self, parent=self)
        dlg.exec()

    def _handle_step_solid_selected(self, info):
        if info is not None:
            name = info.get("panel_name", "?")
            layer = info.get("layer_name", "-")
            self.move_rotate_btn.setEnabled(True)
            self.status_label.setText(
                f"Panel '{name}'  |  Layer: {layer}  |  press Delete to remove from view"
            )
        else:
            # Exit edit-transform mode if active before deselecting
            if self.move_rotate_btn.isChecked():
                self.move_rotate_btn.setChecked(False)
            self.move_rotate_btn.setEnabled(False)
            self.move_rotate_btn.setText("Move / Rotate")
            self.status_label.setText("Ready")

    def _toggle_edit_transform(self, checked: bool):
        if checked:
            self.viewer.enter_edit_mode()
            self.move_rotate_btn.setText("Done Moving")
            self.status_label.setText("Edit mode — drag shape to move  |  drag cyan sphere to rotate  |  click 'Done Moving' to finish")
        else:
            self.viewer.exit_edit_mode()
            self.move_rotate_btn.setText("Move / Rotate")
            self.status_label.setText("Ready")

    def _toggle_show_only_selected(self, checked: bool):
        self.viewer.set_show_only_selected(checked)
        self.show_only_btn.setText("Showing Only Selected" if checked else "Show Only Selected")

    def _toggle_select_same_type(self, checked: bool):
        self.viewer.set_select_same_type_mode(checked)
        self.select_same_type_btn.setText("Same Type: ON" if checked else "Select Same Type")

    def _apply_panel_resize(self, _=None):
        info = getattr(self, "_active_panel_info", None)
        if info is None:
            return
        factor, _ = self._get_factor_and_unit()
        try:
            l_val = float(self.size_l_input.text()) / factor
            w_val = float(self.size_w_input.text()) / factor
            h_val = float(self.size_h_input.text()) / factor
        except ValueError:
            return
        self.viewer.resize_group_and_push(self.viewer.group_selected_solids, l_val, w_val, h_val)

    # ------------------------------------------------------------------ STEP export

    def _save_layer_to_xml(self):
        infos = [i for i in self.current_solid_infos if i.get("panel_name")]
        if not infos:
            self.status_label.setText("Nothing to save — load or add layer objects first.")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Save Layer")
        dialog.setMinimumWidth(360)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = QLabel("Save Layer as XML")
        title.setObjectName("panelTitle")
        layout.addWidget(title)

        name_label = QLabel("File name")
        name_label.setObjectName("miniLabel")
        layout.addWidget(name_label)

        name_input = QLineEdit("layer")
        layout.addWidget(name_input)

        buttons_row = QHBoxLayout()
        buttons_row.addStretch(1)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("secondaryButton")
        cancel_btn.clicked.connect(dialog.reject)
        buttons_row.addWidget(cancel_btn)

        save_btn = QPushButton("Save")
        save_btn.setObjectName("primaryButton")
        save_btn.setDefault(True)
        buttons_row.addWidget(save_btn)
        layout.addLayout(buttons_row)

        def do_save():
            raw_name = name_input.text().strip()
            if not raw_name:
                name_input.setFocus()
                return

            safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", raw_name).strip("._-") or "layer"
            output_dir = Path(__file__).resolve().parent / "layers"
            output_dir.mkdir(parents=True, exist_ok=True)
            xml_path = output_dir / f"{safe_name}.xml"

            from collections import defaultdict
            groups: Dict[str, List[Dict]] = defaultdict(list)
            for info in infos:
                groups[str(info.get("layer_name") or "unknown")].append(info)

            root = ET.Element("layers")
            root.set("name", raw_name)

            for layer_name, parts in groups.items():
                layer_el = ET.SubElement(root, "layer")
                layer_el.set("name", layer_name)

                for idx, info in enumerate(parts, start=1):
                    panel_name = str(info.get("panel_name") or f"part_{idx}")
                    part_el = ET.SubElement(layer_el, "part")
                    ET.SubElement(part_el, "panel_name").text = panel_name
                    ET.SubElement(part_el, "layer_name").text = layer_name
                    ET.SubElement(part_el, "panel_class").text = str(info.get("panel_class") or "other")

                    size = ET.SubElement(part_el, "size_mm")
                    ET.SubElement(size, "x").text = f"{float(info.get('x', 0.0)):.4f}"
                    ET.SubElement(size, "y").text = f"{float(info.get('y', 0.0)):.4f}"
                    ET.SubElement(size, "z").text = f"{float(info.get('z', 0.0)):.4f}"

                    center = ET.SubElement(part_el, "center_mm")
                    ET.SubElement(center, "x").text = f"{float(info.get('cx', 0.0)):.4f}"
                    ET.SubElement(center, "y").text = f"{float(info.get('cy', 0.0)):.4f}"
                    ET.SubElement(center, "z").text = f"{float(info.get('cz', 0.0)):.4f}"

                    bounds = ET.SubElement(part_el, "bounds_mm")
                    ET.SubElement(bounds, "xmin").text = f"{float(info.get('xmin', 0.0)):.4f}"
                    ET.SubElement(bounds, "xmax").text = f"{float(info.get('xmax', 0.0)):.4f}"
                    ET.SubElement(bounds, "ymin").text = f"{float(info.get('ymin', 0.0)):.4f}"
                    ET.SubElement(bounds, "ymax").text = f"{float(info.get('ymax', 0.0)):.4f}"
                    ET.SubElement(bounds, "zmin").text = f"{float(info.get('zmin', 0.0)):.4f}"
                    ET.SubElement(bounds, "zmax").text = f"{float(info.get('zmax', 0.0)):.4f}"

            ET.ElementTree(root).write(xml_path, encoding="utf-8", xml_declaration=True)

            total_parts = sum(len(v) for v in groups.values())
            self.status_label.setText(
                f"Saved {len(groups)} layer(s) ({total_parts} part(s)) → layers/{xml_path.name}"
            )
            dialog.accept()

        save_btn.clicked.connect(do_save)
        name_input.returnPressed.connect(do_save)
        dialog.exec()

    def _safe_xml_name(self, value: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", (value or "").strip())
        return safe.strip("._-") or "part"

    def _export_layer_parts_to_xml(self):
        infos = [i for i in self.current_solid_infos if i.get("panel_name")]
        if not infos:
            self.status_label.setText("No pallet parts available to export.")
            return

        output_dir = Path(__file__).resolve().parent / "layer_parts_xml"
        output_dir.mkdir(parents=True, exist_ok=True)

        # Group parts by layer_name — one file per unique layer
        from collections import defaultdict
        groups: Dict[str, List[Dict]] = defaultdict(list)
        for info in infos:
            groups[str(info.get("layer_name") or "unknown")].append(info)

        exported = 0
        for layer_name, parts in groups.items():
            filename = f"{self._safe_xml_name(layer_name)}.xml"

            root = ET.Element("layer")
            root.set("name", layer_name)

            for idx, info in enumerate(parts, start=1):
                panel_name = str(info.get("panel_name") or f"part_{idx}")
                part_el = ET.SubElement(root, "part")
                ET.SubElement(part_el, "panel_name").text = panel_name
                ET.SubElement(part_el, "layer_name").text = layer_name
                ET.SubElement(part_el, "panel_class").text = str(info.get("panel_class") or "other")

                size = ET.SubElement(part_el, "size_mm")
                ET.SubElement(size, "x").text = f"{float(info.get('x', 0.0)):.4f}"
                ET.SubElement(size, "y").text = f"{float(info.get('y', 0.0)):.4f}"
                ET.SubElement(size, "z").text = f"{float(info.get('z', 0.0)):.4f}"

                center = ET.SubElement(part_el, "center_mm")
                ET.SubElement(center, "x").text = f"{float(info.get('cx', 0.0)):.4f}"
                ET.SubElement(center, "y").text = f"{float(info.get('cy', 0.0)):.4f}"
                ET.SubElement(center, "z").text = f"{float(info.get('cz', 0.0)):.4f}"

                bounds = ET.SubElement(part_el, "bounds_mm")
                ET.SubElement(bounds, "xmin").text = f"{float(info.get('xmin', 0.0)):.4f}"
                ET.SubElement(bounds, "xmax").text = f"{float(info.get('xmax', 0.0)):.4f}"
                ET.SubElement(bounds, "ymin").text = f"{float(info.get('ymin', 0.0)):.4f}"
                ET.SubElement(bounds, "ymax").text = f"{float(info.get('ymax', 0.0)):.4f}"
                ET.SubElement(bounds, "zmin").text = f"{float(info.get('zmin', 0.0)):.4f}"
                ET.SubElement(bounds, "zmax").text = f"{float(info.get('zmax', 0.0)):.4f}"

            tree = ET.ElementTree(root)
            tree.write(output_dir / filename, encoding="utf-8", xml_declaration=True)
            exported += 1

        self.status_label.setText(
            f"Exported {exported} layer XML file(s) ({sum(len(v) for v in groups.values())} parts) to {output_dir.name}"
        )

    def showEvent(self, event):
        super().showEvent(event)
        self.viewer.initialize()

        if self.viewer.has_texture():
            self.material_card.set_value("Wood")
            self.status_label.setText("Ready | wood texture loaded")
        else:
            self.material_card.set_value("Wood (color fallback)")
            self.status_label.setText("Ready | wood texture missing, using fallback")

        default_xml = Path(__file__).resolve().parent / "default_layer.xml"
        if default_xml.exists():
            QTimer.singleShot(0, lambda: self._load_xml_layer_file(str(default_xml)))


def main():
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))

    window = StepViewerWindow()
    window.showMaximized()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
