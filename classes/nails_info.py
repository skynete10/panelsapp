from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from classes.vtk_viewer import VtkStepViewer


class NailsInfoPage(QFrame):
    """Full-page nail editing panel embedded in the main window's QStackedWidget.

    Signals
    -------
    back_requested      — user clicked "Back to Viewer"
    nail_placed         — (point, layer_name, nail_type, source) user placed a nail
    nails_clear_requested — (layer_name) user wants to clear nails for a layer
    preset_requested    — (layer_name, count, layout) user wants a preset pattern
    add_layer_requested — user clicked "Add New Layer"
    layer_changed       — (layer_name) active layer selection changed or data refresh needed
    """

    back_requested = Signal()
    nail_placed = Signal(tuple, str, str, str)
    nails_clear_requested = Signal(str)
    preset_requested = Signal(str, int, str)
    add_layer_requested = Signal()
    layer_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("viewerPanel")
        self._active_group: str = ""
        self._session_count: int = 0
        self._build_ui()

    # ------------------------------------------------------------------ public API

    @property
    def active_group(self) -> str:
        return self._active_group

    @property
    def nail_type(self) -> str:
        return self.type_combo.currentText().lower()

    def populate(self, layer_names: List[str], active_group: str) -> None:
        """Set the shape-combo items and mark the active layer."""
        self.shape_combo.blockSignals(True)
        self.shape_combo.clear()
        self.shape_combo.addItems(layer_names)
        try:
            self.shape_combo.setCurrentIndex(layer_names.index(active_group))
        except ValueError:
            pass
        self.shape_combo.blockSignals(False)
        self._active_group = active_group

    def load_solids(
        self,
        solids_data: List[Tuple[Any, Dict[str, Any]]],
        nail_positions: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Load VTK solids (and optionally show nails) once the widget is visible."""
        def _init():
            if not self.viewer._initialized:
                self.viewer.initialize()
            if solids_data:
                self.viewer.set_step_solids(solids_data)
            self.viewer.show_layer_only(self._active_group)
            if nail_positions:
                self.sync_nails(nail_positions)
        QTimer.singleShot(0, _init)

    def refresh_count(self, nail_positions: List[Dict[str, Any]]) -> None:
        """Update the nail count label for the currently active layer."""
        count = sum(1 for n in nail_positions if n.get("group") == self._active_group)
        self.count_lbl.setText(f"Nails on layer: {count}")
        self.show_btn.setText(f"Show Nails ({count})")

    def sync_nails(self, nail_positions: List[Dict[str, Any]]) -> None:
        """Push the current nail list into the VTK viewer."""
        self.viewer.set_nail_positions(nail_positions)
        self.viewer.set_nails_visible(bool(nail_positions))

    def stop_placement(self) -> None:
        """Programmatically exit nail-placement mode (safe to call when inactive)."""
        if self.place_btn.isChecked():
            self.place_btn.setChecked(False)
        else:
            self._stop_placement_internal()

    def finalize(self) -> None:
        """Release the VTK render window — call this from the host's closeEvent."""
        self.viewer.shutdown()

    # ------------------------------------------------------------------ UI build

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        # Header row
        header_row = QHBoxLayout()
        back_btn = QPushButton("← Back to Viewer")
        back_btn.setObjectName("secondaryButton")
        back_btn.clicked.connect(self.back_requested)
        header_row.addWidget(back_btn)
        header_row.addStretch()
        title_lbl = QLabel("Edit Nails")
        title_lbl.setObjectName("panelTitle")
        header_row.addWidget(title_lbl)
        header_row.addStretch()
        layout.addLayout(header_row)

        # Body: viewer left, controls right
        body = QHBoxLayout()
        body.setSpacing(16)
        layout.addLayout(body, 1)

        self.viewer = VtkStepViewer()
        body.addWidget(self.viewer, 1)

        ctrl_widget = QWidget()
        ctrl_widget.setFixedWidth(265)
        ctrl = QVBoxLayout(ctrl_widget)
        ctrl.setContentsMargins(0, 0, 0, 0)
        ctrl.setSpacing(8)
        body.addWidget(ctrl_widget, 0)

        def _sep(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setObjectName("miniSectionTitle")
            return lbl

        # Shape / Layer selector
        ctrl.addWidget(_sep("Shape / Layer"))
        self.shape_combo = QComboBox()
        self.shape_combo.currentIndexChanged.connect(self._on_shape_selected)
        ctrl.addWidget(self.shape_combo)

        # Nail settings
        ctrl.addWidget(_sep("Nail Settings"))
        type_row = QHBoxLayout()
        type_lbl = QLabel("Type:")
        type_lbl.setFixedWidth(46)
        type_row.addWidget(type_lbl)
        self.type_combo = QComboBox()
        self.type_combo.addItems(["Long", "Short"])
        type_row.addWidget(self.type_combo, 1)
        ctrl.addLayout(type_row)

        count_row = QHBoxLayout()
        count_fixed_lbl = QLabel("Count:")
        count_fixed_lbl.setFixedWidth(46)
        count_row.addWidget(count_fixed_lbl)
        self.count_spin = QSpinBox()
        self.count_spin.setMinimum(1)
        self.count_spin.setMaximum(50)
        self.count_spin.setValue(1)
        count_row.addWidget(self.count_spin, 1)
        ctrl.addLayout(count_row)

        # Placement
        ctrl.addWidget(_sep("Placement"))
        place_hint = QLabel("Click the button, then click\nthe grid overlay to place nails.")
        place_hint.setObjectName("miniLabel")
        ctrl.addWidget(place_hint)

        self.place_btn = QPushButton("Add Nails by Positions")
        self.place_btn.setObjectName("drawButton")
        self.place_btn.setCheckable(True)
        self.place_btn.toggled.connect(self._toggle_placement)
        ctrl.addWidget(self.place_btn)

        self.placed_status_lbl = QLabel("")
        self.placed_status_lbl.setObjectName("miniLabel")
        ctrl.addWidget(self.placed_status_lbl)

        # Existing nails
        ctrl.addWidget(_sep("Existing Nails"))
        self.count_lbl = QLabel()
        self.count_lbl.setObjectName("miniLabel")
        ctrl.addWidget(self.count_lbl)

        self.show_btn = QPushButton()
        self.show_btn.setObjectName("secondaryButton")
        self.show_btn.clicked.connect(
            lambda: self.layer_changed.emit(self._active_group)
        )
        ctrl.addWidget(self.show_btn)

        clear_btn = QPushButton("Clear Layer Nails")
        clear_btn.setObjectName("secondaryButton")
        clear_btn.clicked.connect(
            lambda: self.nails_clear_requested.emit(self._active_group)
        )
        ctrl.addWidget(clear_btn)

        # Preset layout
        ctrl.addWidget(_sep("Preset Layout"))
        self.preset_count_combo = QComboBox()
        self.preset_count_combo.addItems(["1 nail", "2 nails", "3 nails"])
        ctrl.addWidget(self.preset_count_combo)
        self.preset_layout_combo = QComboBox()
        self.preset_layout_combo.addItems(["centered", "line", "triangle"])
        ctrl.addWidget(self.preset_layout_combo)

        add_preset_btn = QPushButton("Add Preset")
        add_preset_btn.setObjectName("primaryButton")
        add_preset_btn.clicked.connect(self._on_add_preset)
        ctrl.addWidget(add_preset_btn)

        # Add Layer shortcut
        sep_line = QFrame()
        sep_line.setFrameShape(QFrame.Shape.HLine)
        sep_line.setStyleSheet("color: #dbe3ee; margin: 4px 0;")
        ctrl.addWidget(sep_line)

        add_layer_btn = QPushButton("Add New Layer")
        add_layer_btn.setObjectName("secondaryButton")
        add_layer_btn.clicked.connect(self.add_layer_requested)
        ctrl.addWidget(add_layer_btn)

        ctrl.addStretch(1)

    # ------------------------------------------------------------------ internal slots

    def _on_shape_selected(self, _idx: int) -> None:
        name = self.shape_combo.currentText()
        if not name:
            return
        self._active_group = name
        if self.place_btn.isChecked():
            self.place_btn.setChecked(False)
        self.viewer.show_layer_only(name)
        self.layer_changed.emit(name)

    def _on_add_preset(self) -> None:
        count = int(self.preset_count_combo.currentText().split()[0])
        layout = self.preset_layout_combo.currentText()
        self.preset_requested.emit(self._active_group, count, layout)

    def _on_nail_placed(self, point: Tuple[float, float, float]) -> None:
        """Callback from the VTK interactor when the user clicks the placement grid."""
        nail_type = self.type_combo.currentText().lower()
        self.nail_placed.emit(point, self._active_group, nail_type, "manual")
        self._session_count += 1
        target = self.count_spin.value()
        self.placed_status_lbl.setText(f"Placed: {self._session_count} / {target}")
        if self._session_count >= target:
            self.place_btn.setChecked(False)

    def _toggle_placement(self, checked: bool) -> None:
        if checked:
            self._session_count = 0
            target = self.count_spin.value()
            self.placed_status_lbl.setText(f"Placed: 0 / {target}")
            layer_infos = [
                s["info"] for s in self.viewer.step_solid_actors
                if s["info"].get("layer_name") == self._active_group
            ]
            self.viewer.show_layer_only(self._active_group)
            self.viewer.start_manual_nail_placement(layer_infos, self._on_nail_placed)
            self.viewer.vtk_widget.setCursor(Qt.CursorShape.CrossCursor)
            self.place_btn.setText("Stop Placing  ✕")
        else:
            self._stop_placement_internal()

    def _stop_placement_internal(self) -> None:
        self.viewer.stop_manual_nail_placement()
        self.viewer.clear_layer_grid()
        self.viewer.vtk_widget.unsetCursor()
        self.place_btn.setText("Add Nails by Positions")
        self.placed_status_lbl.setText("")
