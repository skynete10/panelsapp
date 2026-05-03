import copy
import math
from typing import Optional, Tuple, Dict, Any, List, Callable

from PySide6.QtWidgets import QFrame, QSizePolicy, QVBoxLayout

from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor
from vtkmodules.vtkCommonDataModel import vtkPolyData
from vtkmodules.vtkFiltersGeneral import vtkTransformPolyDataFilter
from vtkmodules.vtkFiltersSources import (
    vtkArrowSource, vtkConeSource, vtkCubeSource,
    vtkCylinderSource, vtkDiskSource, vtkLineSource, vtkSphereSource,
)
from vtkmodules.vtkCommonTransforms import vtkTransform
from vtkmodules.vtkInteractionStyle import (
    vtkInteractorStyleTrackballCamera,
    vtkInteractorStyleTrackballActor,
    vtkInteractorStyleUser,
)
from vtkmodules.vtkInteractionWidgets import vtkOrientationMarkerWidget
from vtkmodules.vtkRenderingAnnotation import vtkAxesActor
from vtkmodules.vtkRenderingCore import (
    vtkActor,
    vtkBillboardTextActor3D,
    vtkCamera,
    vtkCellPicker,
    vtkLight,
    vtkPolyDataMapper,
    vtkProperty,
    vtkRenderer,
    vtkRenderWindowInteractor,
    vtkWorldPointPicker,
)


class _ActorEditHandler:
    """Custom move/rotate handler for a single VTK actor.

    - Hovering over the selected shape or the rotate handle: hand cursor.
    - Left-drag on the shape: translates it in the XY plane.
    - Left-drag on the cyan rotate handle (sphere at the right edge): rotates
      around the actor's current Z-axis centre (2-D, full 360°).
    """

    _HANDLE_OFFSET = 20.0   # mm to the right of the actor's AABB
    _HANDLE_RADIUS = 8.0    # sphere radius in mm
    _CURSOR_HAND = 7
    _CURSOR_DEFAULT = 0

    def __init__(self, viewer: "VtkStepViewer", target_solid: dict):
        self._viewer = viewer
        self._target = target_solid
        self._actor: vtkActor = target_solid["actor"]
        self._renderer: vtkRenderer = viewer.renderer
        self._interactor = viewer.interactor
        self._rw = viewer.vtk_widget.GetRenderWindow()

        # Unified accumulating transform – only source of actor motion
        self._transform = vtkTransform()
        self._transform.Identity()
        self._actor.SetUserTransform(self._transform)

        self._drag_mode: Optional[str] = None   # 'translate' | 'rotate' | None
        self._last_xy: Optional[Tuple[int, int]] = None

        self._handle_actor: Optional[vtkActor] = None
        self._handle_sphere = None  # vtkSphereSource
        self._obs_ids: List[int] = []

        self._create_rotate_handle()
        self._add_observers()

    # ── rotate handle ────────────────────────────────────────────────────

    def _handle_world_pos(self) -> Tuple[float, float, float]:
        b = self._actor.GetBounds()
        return b[1] + self._HANDLE_OFFSET, (b[2] + b[3]) / 2.0, (b[4] + b[5]) / 2.0

    def _create_rotate_handle(self) -> None:
        hx, hy, hz = self._handle_world_pos()

        sphere = vtkSphereSource()
        sphere.SetCenter(hx, hy, hz)
        sphere.SetRadius(self._HANDLE_RADIUS)
        sphere.SetThetaResolution(20)
        sphere.SetPhiResolution(20)
        sphere.Update()

        mapper = vtkPolyDataMapper()
        mapper.SetInputConnection(sphere.GetOutputPort())

        actor = vtkActor()
        actor.SetMapper(mapper)
        actor.GetProperty().SetColor(0.0, 0.8, 1.0)    # cyan
        actor.GetProperty().SetOpacity(0.95)
        actor.PickableOn()

        self._renderer.AddActor(actor)
        self._handle_actor = actor
        self._handle_sphere = sphere
        self._rw.Render()

    def _reposition_handle(self) -> None:
        hx, hy, hz = self._handle_world_pos()
        self._handle_sphere.SetCenter(hx, hy, hz)
        self._handle_sphere.Modified()

    # ── observers ────────────────────────────────────────────────────────

    def _add_observers(self) -> None:
        iac = self._interactor
        self._obs_ids = [
            iac.AddObserver("MouseMoveEvent", self._on_mouse_move, 1.0),
            iac.AddObserver("LeftButtonPressEvent", self._on_left_down, 1.0),
            iac.AddObserver("LeftButtonReleaseEvent", self._on_left_up, 1.0),
        ]

    def _remove_observers(self) -> None:
        for oid in self._obs_ids:
            self._interactor.RemoveObserver(oid)
        self._obs_ids = []

    # ── event callbacks ───────────────────────────────────────────────────

    def _on_mouse_move(self, _obj, _event) -> None:
        x, y = self._interactor.GetEventPosition()
        if self._drag_mode == "translate":
            self._do_translate(x, y)
        elif self._drag_mode == "rotate":
            self._do_rotate(x, y)
        else:
            picked = self._pick_at(x, y)
            if picked is self._actor or picked is self._handle_actor:
                self._rw.SetCurrentCursor(self._CURSOR_HAND)
            else:
                self._rw.SetCurrentCursor(self._CURSOR_DEFAULT)
        self._last_xy = (x, y)

    def _on_left_down(self, _obj, _event) -> None:
        x, y = self._interactor.GetEventPosition()
        picked = self._pick_at(x, y)
        if picked is self._handle_actor:
            self._drag_mode = "rotate"
        elif picked is self._actor:
            self._drag_mode = "translate"
        self._last_xy = (x, y)

    def _on_left_up(self, _obj, _event) -> None:
        self._drag_mode = None
        self._sync_info()

    # ── picking ──────────────────────────────────────────────────────────

    def _pick_at(self, x: int, y: int) -> Optional[vtkActor]:
        picker = vtkCellPicker()
        picker.SetTolerance(0.005)
        picker.Pick(x, y, 0, self._renderer)
        return picker.GetActor()

    # ── coordinate helpers ────────────────────────────────────────────────

    def _display_to_world_xy(self, dx: int, dy: int) -> Tuple[float, float]:
        """Unproject screen point onto the actor's current centre-Z plane."""
        b = self._actor.GetBounds()
        z_w = (b[4] + b[5]) / 2.0
        r = self._renderer

        r.SetDisplayPoint(dx, dy, 0.0)
        r.DisplayToWorld()
        near = list(r.GetWorldPoint())
        w = near[3]
        if abs(w) > 1e-12:
            near = [near[i] / w for i in range(3)]

        r.SetDisplayPoint(dx, dy, 1.0)
        r.DisplayToWorld()
        far = list(r.GetWorldPoint())
        w = far[3]
        if abs(w) > 1e-12:
            far = [far[i] / w for i in range(3)]

        dz = far[2] - near[2]
        if abs(dz) < 1e-12:
            return near[0], near[1]
        t = (z_w - near[2]) / dz
        return near[0] + t * (far[0] - near[0]), near[1] + t * (far[1] - near[1])

    def _actor_centre_display(self) -> Tuple[float, float]:
        b = self._actor.GetBounds()
        cx_w = (b[0] + b[1]) / 2.0
        cy_w = (b[2] + b[3]) / 2.0
        cz_w = (b[4] + b[5]) / 2.0
        self._renderer.SetWorldPoint(cx_w, cy_w, cz_w, 1.0)
        self._renderer.WorldToDisplay()
        dp = self._renderer.GetDisplayPoint()
        return dp[0], dp[1]

    # ── transform operations ──────────────────────────────────────────────

    def _do_translate(self, x: int, y: int) -> None:
        if self._last_xy is None:
            return
        lx, ly = self._last_xy
        wx1, wy1 = self._display_to_world_xy(lx, ly)
        wx2, wy2 = self._display_to_world_xy(x, y)
        dx, dy = wx2 - wx1, wy2 - wy1
        # PreMultiply: new op applied AFTER existing → world-space translation
        self._transform.PreMultiply()
        self._transform.Translate(dx, dy, 0.0)
        self._reposition_handle()
        self._rw.Render()

    def _do_rotate(self, x: int, y: int) -> None:
        if self._last_xy is None:
            return
        lx, ly = self._last_xy
        cx_d, cy_d = self._actor_centre_display()
        prev_angle = math.atan2(ly - cy_d, lx - cx_d)
        curr_angle = math.atan2(y - cy_d, x - cx_d)
        delta_deg = math.degrees(curr_angle - prev_angle)

        # Rotate around actor's current world-space centre
        b = self._actor.GetBounds()
        cx_w = (b[0] + b[1]) / 2.0
        cy_w = (b[2] + b[3]) / 2.0
        cz_w = (b[4] + b[5]) / 2.0
        # PreMultiply builds T(c)*Rz*T(-c)*old — rotates around current world centre
        self._transform.PreMultiply()
        self._transform.Translate(-cx_w, -cy_w, -cz_w)
        self._transform.RotateZ(delta_deg)
        self._transform.Translate(cx_w, cy_w, cz_w)
        self._reposition_handle()
        self._rw.Render()

    # ── sync & cleanup ────────────────────────────────────────────────────

    def _sync_info(self) -> None:
        b = self._actor.GetBounds()
        info = self._target["info"]
        info["xmin"], info["xmax"] = b[0], b[1]
        info["ymin"], info["ymax"] = b[2], b[3]
        info["zmin"], info["zmax"] = b[4], b[5]
        info["cx"] = (b[0] + b[1]) / 2.0
        info["cy"] = (b[2] + b[3]) / 2.0
        info["cz"] = (b[4] + b[5]) / 2.0

    def cleanup(self) -> None:
        self._remove_observers()
        if self._handle_actor is not None:
            self._renderer.RemoveActor(self._handle_actor)
            self._handle_actor = None
        self._rw.SetCurrentCursor(self._CURSOR_DEFAULT)
        self._sync_info()
        self._rw.Render()


class VtkStepViewer(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("previewFrame")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        self.vtk_widget = QVTKRenderWindowInteractor(self)
        self.vtk_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.vtk_widget)

        self.renderer = vtkRenderer()
        self.renderer.SetBackground(0.93, 0.95, 0.98)
        self.renderer.SetBackground2(0.82, 0.86, 0.92)
        self.renderer.SetGradientBackground(True)

        render_window = self.vtk_widget.GetRenderWindow()
        render_window.AddRenderer(self.renderer)

        self.interactor: vtkRenderWindowInteractor = render_window.GetInteractor()
        self.interactor_style = vtkInteractorStyleTrackballCamera()
        self.interactor.SetInteractorStyle(self.interactor_style)

        self.current_mode = "wood"
        self.current_object_color: Tuple[float, float, float] = (1.0, 0.97, 0.55)
        self._polydata = None

        # Per-solid STEP actors (one actor per solid instead of one merged actor)
        self.step_solid_actors: List[Dict[str, Any]] = []
        self.selected_step_solid: Optional[Dict[str, Any]] = None
        self.on_step_solid_selected: Optional[Callable[[Optional[Dict[str, Any]]], None]] = None

        self.measurement_actors: List[vtkActor] = []
        self.measurement_text_actors: List[vtkBillboardTextActor3D] = []
        self.panel_label_actors: List[vtkActor] = []
        self.panel_text_actors: List[vtkBillboardTextActor3D] = []

        self.nail_actors: List[vtkActor] = []
        self.nail_text_actors: List[vtkBillboardTextActor3D] = []

        self.distances_visible = True
        self.panel_labels_visible = False
        self.nails_visible = False

        self.panel_pick_infos: List[Dict[str, Any]] = []
        self.on_panel_clicked: Optional[Callable[[Optional[Dict[str, Any]]], None]] = None
        self.on_layer_double_clicked: Optional[Callable[[Dict[str, Any]], None]] = None

        self.picker = vtkCellPicker()
        self.picker.SetTolerance(0.0005)

        # Shape drawing state
        self.drawn_shapes: List[Dict[str, Any]] = []
        self.selected_shape: Optional[Dict[str, Any]] = None
        self.draw_mode: bool = False
        self.current_shape_type: str = "sphere"
        self.current_shape_color: Tuple[float, float, float] = (0.23, 0.51, 0.96)
        self.current_shape_size: float = 20.0
        self.current_shape_opacity: float = 0.85
        self.on_shape_placed: Optional[Callable[[Dict[str, Any]], None]] = None
        self.on_shape_selected: Optional[Callable[[Optional[Dict[str, Any]]], None]] = None
        self.world_picker = vtkWorldPointPicker()

        # Drag-to-move state
        self._dragging_shape: Optional[Dict[str, Any]] = None
        self._drag_world_offset: Tuple[float, float] = (0.0, 0.0)
        self._drag_z: float = 0.0
        self._drag_moved: bool = False

        # Line drawing state
        self.line_draw_mode: bool = False
        self.current_line_points: List[Tuple[float, float, float]] = []
        self.line_in_progress_actors: List[vtkActor] = []
        self.line_preview_actor: Optional[vtkActor] = None
        self.drawn_lines: List[Dict[str, Any]] = []
        self.selected_line: Optional[Dict[str, Any]] = None
        self.current_line_color: Tuple[float, float, float] = (0.94, 0.27, 0.27)
        self.current_line_width: float = 3.0
        self.on_line_finished: Optional[Callable[[Dict[str, Any]], None]] = None
        self.on_line_selected: Optional[Callable[[Optional[Dict[str, Any]]], None]] = None

        self.layer_grid_actors: List[vtkActor] = []
        self.manual_nail_mode: bool = False
        self.manual_nail_z: float = 0.0
        self.manual_nail_rect: Optional[Tuple[float, float, float, float]] = None
        self.on_manual_nail_placed: Optional[Callable[[Tuple[float, float, float]], None]] = None

        self._add_lights()

        self.axes = vtkAxesActor()
        self.axes.SetTotalLength(0.7, 0.7, 0.7)
        self.axes.AxisLabelsOff()

        self.orientation_widget = vtkOrientationMarkerWidget()
        self.orientation_widget.SetOrientationMarker(self.axes)
        self.orientation_widget.SetInteractor(self.interactor)
        self.orientation_widget.SetViewport(0.0, 0.0, 0.18, 0.18)
        self.orientation_widget.SetEnabled(1)
        self.orientation_widget.InteractiveOff()

        self.show_only_selected: bool = False
        self.select_same_type_mode: bool = False
        self.group_selected_solids: List[Dict[str, Any]] = []
        self.clipboard_solid: Optional[Dict[str, Any]] = None  # copy/paste buffer

        self.renderer.ResetCamera()
        self._initialized = False

    def _add_lights(self):
        key_light = vtkLight()
        key_light.SetPosition(7, 8, 12)
        key_light.SetFocalPoint(0, 0, 0)
        key_light.SetIntensity(1.05)
        self.renderer.AddLight(key_light)

        fill_light = vtkLight()
        fill_light.SetPosition(-8, 4, 8)
        fill_light.SetFocalPoint(0, 0, 0)
        fill_light.SetIntensity(0.35)
        self.renderer.AddLight(fill_light)

        rim_light = vtkLight()
        rim_light.SetPosition(3, -6, 5)
        rim_light.SetFocalPoint(0, 0, 0)
        rim_light.SetIntensity(0.20)
        self.renderer.AddLight(rim_light)

        # Under-light: eliminates dark shadows on bottom-facing surfaces
        bottom_light = vtkLight()
        bottom_light.SetPosition(0, 0, -15)
        bottom_light.SetFocalPoint(0, 0, 0)
        bottom_light.SetIntensity(0.45)
        self.renderer.AddLight(bottom_light)

    def _apply_wood_material(self):
        self.current_mode = "wood"
        for solid in self.step_solid_actors:
            actor = solid["actor"]
            prop: vtkProperty = actor.GetProperty()
            prop.SetColor(*solid.get("color", self.current_object_color))
            prop.SetInterpolationToPhong()
            prop.SetSpecular(0.04)
            prop.SetSpecularPower(8)
            prop.SetAmbient(0.75)
            prop.SetDiffuse(0.30)
            prop.EdgeVisibilityOff()

    def _apply_cad_material(self):
        self.current_mode = "cad"
        for solid in self.step_solid_actors:
            actor = solid["actor"]
            prop: vtkProperty = actor.GetProperty()
            prop.SetColor(0.88, 0.90, 0.94)
            prop.SetInterpolationToPhong()
            prop.SetSpecular(0.20)
            prop.SetSpecularPower(24)
            prop.SetAmbient(0.20)
            prop.SetDiffuse(0.92)
            prop.EdgeVisibilityOn()
            prop.SetEdgeColor(0.45, 0.50, 0.58)
            prop.SetLineWidth(1.0)
    def set_all_solids_color(self, r: float, g: float, b: float):
        self.current_object_color = (r, g, b)
        for solid in self.step_solid_actors:
            solid["color"] = (r, g, b)
            solid["actor"].GetProperty().SetColor(r, g, b)
        self.vtk_widget.GetRenderWindow().Render()

    def set_selected_solids_color(self, r: float, g: float, b: float):
        for solid in self.group_selected_solids:
            solid["color"] = (r, g, b)
            solid["actor"].GetProperty().SetColor(r, g, b)
        self.vtk_widget.GetRenderWindow().Render()

    def set_view_mode(self, mode: str):
        if mode == "cad":
            self._apply_cad_material()
        else:
            self._apply_wood_material()
        for solid in self.step_solid_actors:
            solid["mapper"].SetInputData(solid["polydata"])
            solid["mapper"].Update()
        # Re-apply selection highlight if one is active
        if self.selected_step_solid is not None:
            self._apply_step_solid_select_style(self.selected_step_solid)
        self.vtk_widget.GetRenderWindow().Render()

    def initialize(self):
        if not self._initialized:
            self.vtk_widget.Initialize()
            self.vtk_widget.Start()
            self.interactor.AddObserver("LeftButtonPressEvent", self._on_left_button_press, 1.0)
            self.interactor.AddObserver("LeftButtonDoubleClickEvent", self._on_left_button_double_click, 1.0)
            self.interactor.AddObserver("LeftButtonReleaseEvent", self._on_left_button_release, 1.0)
            self.interactor.AddObserver("MouseMoveEvent", self._on_mouse_move, 0.5)
            self._initialized = True

    def shutdown(self) -> None:
        """Release all VTK resources cleanly.

        Must be called before the parent window closes to avoid
        wglMakeCurrent errors caused by the render timer firing after
        the OpenGL context is destroyed.
        """
        if not self._initialized:
            return
        # Mark as uninitialised first so no in-flight callbacks trigger a render
        self._initialized = False
        # Stop QVTKRenderWindowInteractor's internal QTimer (_Timer is the real
        # attribute name — see QVTKRenderWindowInteractor source).  This must
        # happen before Finalize() so the timer cannot fire wglMakeCurrent on
        # the already-destroyed OpenGL context.
        try:
            self.vtk_widget._Timer.stop()
        except Exception:
            pass
        try:
            self.interactor.RemoveAllObservers()
        except Exception:
            pass
        try:
            self.interactor.SetInteractorStyle(None)
        except Exception:
            pass
        try:
            self.vtk_widget.GetRenderWindow().Finalize()
        except Exception:
            pass

    def clear(self):
        self._polydata = None
        self.panel_pick_infos = []
        self._clear_step_solid_actors()
        self.clear_measurements()
        self.clear_panel_labels()
        self.clear_nails()
        self.clear_drawn_shapes()
        self.clear_drawn_lines()
        self.clear_layer_grid()
        self.renderer.ResetCamera()
        self.vtk_widget.GetRenderWindow().Render()

    # ------------------------------------------------------------------ per-solid STEP actors

    def set_step_solids(self, solids_data: List[Tuple[Any, Dict[str, Any]]]):
        """Load STEP model as one VTK actor per solid so each can be selected/deleted."""
        self._clear_step_solid_actors()

        for polydata, info in solids_data:
            mapper = vtkPolyDataMapper()
            mapper.SetInputData(polydata)
            mapper.Update()

            actor = vtkActor()
            actor.SetMapper(mapper)
            prop: vtkProperty = actor.GetProperty()
            solid_color = self.current_object_color
            if self.current_mode == "wood":
                prop.SetColor(*solid_color)
                prop.SetInterpolationToPhong()
                prop.SetSpecular(0.04)
                prop.SetSpecularPower(8)
                prop.SetAmbient(0.75)
                prop.SetDiffuse(0.30)
                prop.EdgeVisibilityOff()
            else:
                prop.SetColor(0.88, 0.90, 0.94)
                prop.SetInterpolationToPhong()
                prop.SetSpecular(0.20)
                prop.SetSpecularPower(24)
                prop.SetAmbient(0.20)
                prop.SetDiffuse(0.92)
                prop.EdgeVisibilityOn()
                prop.SetEdgeColor(0.45, 0.50, 0.58)
                prop.SetLineWidth(1.0)

            self.renderer.AddActor(actor)
            self.step_solid_actors.append({
                "actor": actor,
                "mapper": mapper,
                "polydata": polydata,
                "info": info,
                "color": solid_color,
                "orig_dims": (info["x"], info["y"], info["z"]),
                "orig_z_bottom": info["cz"] - info["z"] / 2.0,
                "push_z": 0.0,
            })

        self.renderer.ResetCamera()
        camera: vtkCamera = self.renderer.GetActiveCamera()
        camera.Azimuth(28)
        camera.Elevation(18)
        self.renderer.ResetCameraClippingRange()
        self.vtk_widget.GetRenderWindow().Render()

    def _clear_step_solid_actors(self):
        for solid in self.step_solid_actors:
            self.renderer.RemoveActor(solid["actor"])
        self.step_solid_actors.clear()
        self.group_selected_solids.clear()
        self.selected_step_solid = None
        if self.on_step_solid_selected is not None:
            self.on_step_solid_selected(None)

    def add_step_solid(self, polydata, info: Dict[str, Any]):
        """Append a single new solid actor without clearing existing ones."""
        mapper = vtkPolyDataMapper()
        mapper.SetInputData(polydata)
        mapper.Update()

        actor = vtkActor()
        actor.SetMapper(mapper)
        prop: vtkProperty = actor.GetProperty()
        solid_color = self.current_object_color
        if self.current_mode == "wood":
            prop.SetColor(*solid_color)
            prop.SetInterpolationToPhong()
            prop.SetSpecular(0.04)
            prop.SetSpecularPower(8)
            prop.SetAmbient(0.75)
            prop.SetDiffuse(0.30)
            prop.EdgeVisibilityOff()
        else:
            prop.SetColor(0.88, 0.90, 0.94)
            prop.SetInterpolationToPhong()
            prop.SetSpecular(0.20)
            prop.SetSpecularPower(24)
            prop.SetAmbient(0.20)
            prop.SetDiffuse(0.92)
            prop.EdgeVisibilityOn()
            prop.SetEdgeColor(0.45, 0.50, 0.58)
            prop.SetLineWidth(1.0)

        self.renderer.AddActor(actor)
        self.step_solid_actors.append({
            "actor": actor,
            "mapper": mapper,
            "polydata": polydata,
            "info": info,
            "color": solid_color,
            "orig_dims": (info["x"], info["y"], info["z"]),
            "orig_z_bottom": info["cz"] - info["z"] / 2.0,
            "push_z": 0.0,
        })
        self.renderer.ResetCameraClippingRange()
        self.vtk_widget.GetRenderWindow().Render()

    def select_step_solid(self, solid: Dict[str, Any]):
        # Deselect previous group
        for s in self.group_selected_solids:
            self._apply_step_solid_deselect_style(s)
        self.group_selected_solids.clear()

        self.selected_step_solid = solid

        if self.select_same_type_mode:
            layer_name = solid["info"].get("layer_name")
            for s in self.step_solid_actors:
                if s["info"].get("layer_name") == layer_name:
                    self.group_selected_solids.append(s)
                    self._apply_step_solid_select_style(s)
        else:
            self.group_selected_solids.append(solid)
            self._apply_step_solid_select_style(solid)

        self._apply_show_only_selected_visibility()
        self.vtk_widget.GetRenderWindow().Render()
        if self.on_step_solid_selected is not None:
            self.on_step_solid_selected(solid["info"])

    def deselect_step_solid(self):
        for s in self.group_selected_solids:
            self._apply_step_solid_deselect_style(s)
        self.group_selected_solids.clear()
        self.selected_step_solid = None
        self._apply_show_only_selected_visibility()
        self.vtk_widget.GetRenderWindow().Render()
        if self.on_step_solid_selected is not None:
            self.on_step_solid_selected(None)

    def _apply_step_solid_select_style(self, solid: Dict[str, Any]):
        prop: vtkProperty = solid["actor"].GetProperty()
        prop.EdgeVisibilityOn()
        prop.SetEdgeColor(1.0, 0.65, 0.0)
        prop.SetLineWidth(3.0)

    def _apply_step_solid_deselect_style(self, solid: Dict[str, Any]):
        prop: vtkProperty = solid["actor"].GetProperty()
        if self.current_mode == "cad":
            prop.EdgeVisibilityOn()
            prop.SetEdgeColor(0.45, 0.50, 0.58)
            prop.SetLineWidth(1.0)
        else:
            prop.EdgeVisibilityOff()

    def delete_selected_step_solid(self):
        if self.selected_step_solid is None:
            return
        target = self.selected_step_solid
        if target in self.group_selected_solids:
            self.group_selected_solids.remove(target)
        self.renderer.RemoveActor(target["actor"])
        self.step_solid_actors.remove(target)
        self.selected_step_solid = None
        self._apply_show_only_selected_visibility()
        self.vtk_widget.GetRenderWindow().Render()
        if self.on_step_solid_selected is not None:
            self.on_step_solid_selected(None)

    def copy_selected_step_solid(self) -> bool:
        """Store a snapshot of the selected solid in the clipboard. Returns True if copied."""
        if self.selected_step_solid is None:
            return False
        src = self.selected_step_solid
        pd_copy = vtkPolyData()
        pd_copy.DeepCopy(src["polydata"])
        self.clipboard_solid = {
            "polydata": pd_copy,
            "info": copy.deepcopy(src["info"]),
        }
        return True

    def paste_step_solid(self) -> Optional[Dict[str, Any]]:
        """Paste the clipboard solid as a new actor, offset by one panel-width in X.

        Returns the new solid dict (info already updated) or None if clipboard is empty.
        """
        if self.clipboard_solid is None:
            return None

        src_info = self.clipboard_solid["info"]
        src_pd = self.clipboard_solid["polydata"]

        # Offset: one panel width (x dimension) to the right, so it doesn't overlap
        offset_x = src_info.get("x", 20.0)

        transform = vtkTransform()
        transform.Translate(offset_x, 0.0, 0.0)
        tf = vtkTransformPolyDataFilter()
        tf.SetInputData(src_pd)
        tf.SetTransform(transform)
        tf.Update()

        new_pd = vtkPolyData()
        new_pd.DeepCopy(tf.GetOutput())

        new_info = copy.deepcopy(src_info)
        # Shift bounding-box and center in the info dict to match the translated geometry
        new_info["cx"] = src_info["cx"] + offset_x
        new_info["xmin"] = src_info["xmin"] + offset_x
        new_info["xmax"] = src_info["xmax"] + offset_x
        # Give it a distinct name so it doesn't shadow the original
        orig_name = src_info.get("panel_name", "copy")
        new_info["panel_name"] = f"{orig_name}_copy"

        # Update clipboard so repeated Ctrl+V chains the copies
        new_src_pd = vtkPolyData()
        new_src_pd.DeepCopy(new_pd)
        self.clipboard_solid = {"polydata": new_src_pd, "info": copy.deepcopy(new_info)}

        self.add_step_solid(new_pd, new_info)
        # Select the freshly pasted solid
        new_solid = self.step_solid_actors[-1]
        self.select_step_solid(new_solid)
        return new_solid

    def resize_solid_by_info(self, info: Dict[str, Any], new_x_mm: float, new_y_mm: float, new_z_mm: float):
        """Scale around the bottom face so the floor of the panel stays fixed."""
        target = next((s for s in self.step_solid_actors if s["info"] is info), None)
        if target is None:
            return
        ox, oy, oz = target["orig_dims"]
        sx = new_x_mm / ox if ox > 0 else 1.0
        sy = new_y_mm / oy if oy > 0 else 1.0
        sz = new_z_mm / oz if oz > 0 else 1.0
        actor = target["actor"]
        # SetOrigin at original bottom face: VTK applies T(origin)*S*T(-origin),
        # so the bottom face stays fixed and the top face moves with the scale.
        actor.SetOrigin(info["cx"], info["cy"], target["orig_z_bottom"])
        actor.SetScale(sx, sy, sz)
        info["x"] = new_x_mm
        info["y"] = new_y_mm
        info["z"] = new_z_mm
        info["cz"] = target["orig_z_bottom"] + new_z_mm / 2.0 + target["push_z"]

    def _push_solid_z(self, solid: Dict[str, Any], dz: float):
        solid["push_z"] += dz
        solid["actor"].SetPosition(0.0, 0.0, solid["push_z"])
        solid["info"]["cz"] = solid["orig_z_bottom"] + solid["info"]["z"] / 2.0 + solid["push_z"]

    def resize_group_and_push(self, group: List[Dict[str, Any]], new_x_mm: float, new_y_mm: float, new_z_mm: float):
        """Resize every solid in group and push layers above when height changes."""
        if not group:
            return
        old_z = group[0]["info"]["z"]
        dz = new_z_mm - old_z
        # Capture top of the layer BEFORE resize so the above-check is stable.
        layer_top_before = max(s["info"]["cz"] + s["info"]["z"] / 2.0 for s in group)

        for solid in group:
            self.resize_solid_by_info(solid["info"], new_x_mm, new_y_mm, new_z_mm)

        if abs(dz) > 0.001:
            group_ids = {id(s) for s in group}
            for solid in self.step_solid_actors:
                if id(solid) not in group_ids and solid["info"]["cz"] > layer_top_before - 0.5:
                    self._push_solid_z(solid, dz)

        self.vtk_widget.GetRenderWindow().Render()

    def set_show_only_selected(self, enabled: bool):
        self.show_only_selected = enabled
        self._apply_show_only_selected_visibility()
        self.vtk_widget.GetRenderWindow().Render()

    def set_select_same_type_mode(self, enabled: bool):
        self.select_same_type_mode = enabled
        if not enabled:
            # Shrink group back to just the primary selected solid
            for s in self.group_selected_solids:
                self._apply_step_solid_deselect_style(s)
            self.group_selected_solids.clear()
            if self.selected_step_solid is not None:
                self.group_selected_solids.append(self.selected_step_solid)
                self._apply_step_solid_select_style(self.selected_step_solid)
        else:
            # Immediately expand selection to same type if something is selected
            if self.selected_step_solid is not None:
                for s in self.group_selected_solids:
                    self._apply_step_solid_deselect_style(s)
                self.group_selected_solids.clear()
                layer_name = self.selected_step_solid["info"].get("layer_name")
                for s in self.step_solid_actors:
                    if s["info"].get("layer_name") == layer_name:
                        self.group_selected_solids.append(s)
                        self._apply_step_solid_select_style(s)
        self._apply_show_only_selected_visibility()
        self.vtk_widget.GetRenderWindow().Render()

    def _apply_show_only_selected_visibility(self):
        if self.show_only_selected and self.group_selected_solids:
            group_ids = {id(s) for s in self.group_selected_solids}
            for solid in self.step_solid_actors:
                solid["actor"].SetVisibility(1 if id(solid) in group_ids else 0)
        else:
            for solid in self.step_solid_actors:
                solid["actor"].SetVisibility(1)

    def show_layer_only(self, layer_name: str):
        for solid in self.step_solid_actors:
            visible = solid["info"].get("layer_name") == layer_name
            solid["actor"].SetVisibility(1 if visible else 0)
        self.vtk_widget.GetRenderWindow().Render()

    def show_solid_only(self, target_info: Dict[str, Any]):
        for solid in self.step_solid_actors:
            solid["actor"].SetVisibility(1 if solid["info"] is target_info else 0)
        self.vtk_widget.GetRenderWindow().Render()

    def show_all_layers(self):
        self.show_only_selected = False
        for solid in self.step_solid_actors:
            solid["actor"].SetVisibility(1)
        self.vtk_widget.GetRenderWindow().Render()

    def clear_layer_grid(self):
        for actor in self.layer_grid_actors:
            self.renderer.RemoveActor(actor)
        self.layer_grid_actors.clear()
        self.manual_nail_mode = False
        self.manual_nail_rect = None
        self.on_manual_nail_placed = None
        if self._initialized:
            self.vtk_widget.GetRenderWindow().Render()

    def show_layer_grid(self, layer_infos: List[Dict[str, Any]], z_offset: float = 1.8):
        self.clear_layer_grid()
        if not layer_infos:
            return

        xmin = min(info["xmin"] for info in layer_infos)
        xmax = max(info["xmax"] for info in layer_infos)
        ymin = min(info["ymin"] for info in layer_infos)
        ymax = max(info["ymax"] for info in layer_infos)
        z = max(info["zmax"] for info in layer_infos) + z_offset

        width = max(xmax - xmin, 1.0)
        depth = max(ymax - ymin, 1.0)
        # Target ~30 divisions along the shorter axis, clamped to 5–25 mm per cell
        step = min(max(min(width, depth) / 30.0, 5.0), 25.0)

        x = xmin
        while x <= xmax + 0.001:
            self._add_grid_line((x, ymin, z), (x, ymax, z))
            x += step

        y = ymin
        while y <= ymax + 0.001:
            self._add_grid_line((xmin, y, z), (xmax, y, z))
            y += step

        self.manual_nail_z = z
        self.manual_nail_rect = (xmin, xmax, ymin, ymax)
        self.vtk_widget.GetRenderWindow().Render()

    def _add_grid_line(self, start: Tuple[float, float, float], end: Tuple[float, float, float]):
        line = vtkLineSource()
        line.SetPoint1(*start)
        line.SetPoint2(*end)
        line.Update()

        mapper = vtkPolyDataMapper()
        mapper.SetInputConnection(line.GetOutputPort())

        actor = vtkActor()
        actor.SetMapper(mapper)
        prop = actor.GetProperty()
        prop.SetColor(0.15, 0.45, 0.95)
        prop.SetOpacity(0.35)
        prop.SetLineWidth(1.0)
        self.renderer.AddActor(actor)
        self.layer_grid_actors.append(actor)

    def start_manual_nail_placement(
        self,
        layer_infos: List[Dict[str, Any]],
        callback: Callable[[Tuple[float, float, float]], None],
    ):
        self.show_layer_grid(layer_infos)
        self.manual_nail_mode = True
        self.on_manual_nail_placed = callback
        self.interactor.SetInteractorStyle(None)

    def stop_manual_nail_placement(self):
        self.manual_nail_mode = False
        self.on_manual_nail_placed = None
        self.interactor.SetInteractorStyle(self.interactor_style)

    # ------------------------------------------------------------------ picking helpers

    def set_pick_infos(self, infos: List[Dict[str, Any]]):
        self.panel_pick_infos = list(infos or [])

    def _pick_3d_position(self, click_pos):
        self.picker.Pick(click_pos[0], click_pos[1], 0, self.renderer)
        if self.picker.GetActor() is not None:
            return self.picker.GetPickPosition()
        self.world_picker.Pick(click_pos[0], click_pos[1], 0, self.renderer)
        wpos = self.world_picker.GetPickPosition()
        if wpos == (0.0, 0.0, 0.0):
            return self.renderer.GetActiveCamera().GetFocalPoint()
        return wpos

    def _screen_to_world_at_z(self, screen_x: int, screen_y: int, target_z: float) -> Tuple[float, float, float]:
        r = self.renderer
        r.SetDisplayPoint(screen_x, screen_y, 0.0)
        r.DisplayToWorld()
        near = r.GetWorldPoint()
        r.SetDisplayPoint(screen_x, screen_y, 1.0)
        r.DisplayToWorld()
        far = r.GetWorldPoint()
        near = [c / near[3] for c in near[:3]] if near[3] != 0.0 else list(near[:3])
        far  = [c / far[3]  for c in far[:3]]  if far[3]  != 0.0 else list(far[:3])
        dz = far[2] - near[2]
        if abs(dz) < 1e-9:
            return (near[0], near[1], target_z)
        t = (target_z - near[2]) / dz
        return (near[0] + t * (far[0] - near[0]), near[1] + t * (far[1] - near[1]), target_z)

    def _on_left_button_press(self, obj, event):
        click_pos = self.interactor.GetEventPosition()

        if self.manual_nail_mode:
            pos = self._screen_to_world_at_z(click_pos[0], click_pos[1], self.manual_nail_z)
            x, y, z = pos
            if self.manual_nail_rect is not None:
                xmin, xmax, ymin, ymax = self.manual_nail_rect
                x = min(max(x, xmin), xmax)
                y = min(max(y, ymin), ymax)
            if self.on_manual_nail_placed is not None:
                self.on_manual_nail_placed((x, y, z))
            return

        if self.line_draw_mode:
            pos = self._pick_3d_position(click_pos)
            self._add_line_point(pos)
            self.vtk_widget.GetRenderWindow().Render()
            return

        if self.draw_mode:
            pos = self._pick_3d_position(click_pos)
            self._place_shape_at(pos)
            return

        self.picker.Pick(click_pos[0], click_pos[1], 0, self.renderer)
        picked_actor = self.picker.GetActor()

        # --- annotation shapes ---
        for shape in self.drawn_shapes:
            if shape["actor"] is picked_actor:
                if self.selected_line is not None:
                    self._apply_line_deselect_style(self.selected_line)
                    self.selected_line = None
                    if self.on_line_selected:
                        self.on_line_selected(None)
                if self.selected_step_solid is not None:
                    self._apply_step_solid_deselect_style(self.selected_step_solid)
                    self.selected_step_solid = None
                    if self.on_step_solid_selected:
                        self.on_step_solid_selected(None)
                if self.selected_shape is shape:
                    drag_z = shape["position"][2]
                    proj = self._screen_to_world_at_z(click_pos[0], click_pos[1], drag_z)
                    sx, sy, _ = shape["position"]
                    self._dragging_shape = shape
                    self._drag_world_offset = (proj[0] - sx, proj[1] - sy)
                    self._drag_z = drag_z
                    self._drag_moved = False
                    self.interactor.SetInteractorStyle(None)
                else:
                    self.select_shape(shape)
                    style = self.interactor.GetInteractorStyle()
                    if style is not None:
                        style.OnLeftButtonDown()
                return

        # --- drawn lines ---
        for line_dict in self.drawn_lines:
            for actor in line_dict["actors"]:
                if actor is picked_actor:
                    if self.selected_shape is not None:
                        self._apply_deselect_style(self.selected_shape["actor"])
                        self.selected_shape = None
                        if self.on_shape_selected:
                            self.on_shape_selected(None)
                    if self.selected_step_solid is not None:
                        self._apply_step_solid_deselect_style(self.selected_step_solid)
                        self.selected_step_solid = None
                        if self.on_step_solid_selected:
                            self.on_step_solid_selected(None)
                    if self.selected_line is line_dict:
                        self.deselect_line()
                    else:
                        self.select_line(line_dict)
                    style = self.interactor.GetInteractorStyle()
                    if style is not None:
                        style.OnLeftButtonDown()
                    return

        # --- STEP solid actors ---
        for solid in self.step_solid_actors:
            if solid["actor"] is picked_actor:
                if self.selected_shape is not None:
                    self._apply_deselect_style(self.selected_shape["actor"])
                    self.selected_shape = None
                    if self.on_shape_selected:
                        self.on_shape_selected(None)
                if self.selected_line is not None:
                    self._apply_line_deselect_style(self.selected_line)
                    self.selected_line = None
                    if self.on_line_selected:
                        self.on_line_selected(None)
                self.select_step_solid(solid)
                if self.on_panel_clicked is not None:
                    self.on_panel_clicked(solid["info"])
                style = self.interactor.GetInteractorStyle()
                if style is not None:
                    style.OnLeftButtonDown()
                return

        # --- empty space: deselect step solid ---
        if self.selected_step_solid is not None:
            self.deselect_step_solid()

        style = self.interactor.GetInteractorStyle()
        if style is not None:
            style.OnLeftButtonDown()

    def _on_left_button_double_click(self, obj, event):
        if self.draw_mode or self.line_draw_mode or self.manual_nail_mode:
            return

        click_pos = self.interactor.GetEventPosition()
        self.picker.Pick(click_pos[0], click_pos[1], 0, self.renderer)
        picked_actor = self.picker.GetActor()

        for solid in self.step_solid_actors:
            if solid["actor"] is picked_actor:
                if self.on_layer_double_clicked is not None:
                    self.on_layer_double_clicked(solid["info"])
                return

    def _find_panel_from_world_position(
        self, pos: Tuple[float, float, float]
    ) -> Optional[Dict[str, Any]]:
        if not self.panel_pick_infos:
            return None

        x, y, z = pos

        containing = []
        for info in self.panel_pick_infos:
            tol = max(1.5, min(info["x"], info["y"], info["z"]) * 0.12)
            if (
                info["xmin"] - tol <= x <= info["xmax"] + tol
                and info["ymin"] - tol <= y <= info["ymax"] + tol
                and info["zmin"] - tol <= z <= info["zmax"] + tol
            ):
                containing.append(info)

        if containing:
            containing.sort(
                key=lambda info: (
                    abs(z - info["cz"]),
                    abs(y - info["cy"]),
                    abs(x - info["cx"]),
                    info.get("panel_order", 99999),
                )
            )
            return containing[0]

        return min(
            self.panel_pick_infos,
            key=lambda info: (
                (x - info["cx"]) ** 2 + (y - info["cy"]) ** 2 + (z - info["cz"]) ** 2,
                info.get("panel_order", 99999),
            ),
        )

    def reset_view(self):
        self.renderer.ResetCamera()
        camera: vtkCamera = self.renderer.GetActiveCamera()
        camera.Azimuth(28)
        camera.Elevation(18)
        self.renderer.ResetCameraClippingRange()
        self.vtk_widget.GetRenderWindow().Render()

    # ------------------------------------------------------------------ edit-transform mode

    def enter_edit_mode(self):
        """Freeze camera; activate custom move/rotate handler for the selected actor."""
        target = self.selected_step_solid
        if target is None:
            return
        # Make every other solid non-pickable
        for s in self.step_solid_actors:
            if s["actor"] is not target["actor"]:
                s["actor"].PickableOff()
        # Passive style: camera stays frozen while handler processes all mouse events
        self.interactor.SetInteractorStyle(vtkInteractorStyleUser())
        self._edit_handler = _ActorEditHandler(self, target)

    def exit_edit_mode(self):
        """Remove handler, restore camera interaction."""
        if getattr(self, "_edit_handler", None) is not None:
            self._edit_handler.cleanup()
            self._edit_handler = None
        for s in self.step_solid_actors:
            s["actor"].PickableOn()
        self.interactor.SetInteractorStyle(self.interactor_style)
        self.vtk_widget.GetRenderWindow().Render()

    def zoom_in(self):
        self.zoom_by_factor(1.15)

    def zoom_out(self):
        self.zoom_by_factor(1.0 / 1.15)

    def zoom_by_factor(self, factor: float):
        if factor <= 0:
            return
        self.renderer.GetActiveCamera().Dolly(factor)
        self.renderer.ResetCameraClippingRange()
        self.vtk_widget.GetRenderWindow().Render()

    def has_texture(self) -> bool:
        return False

    def clear_measurements(self):
        for actor in self.measurement_actors:
            self.renderer.RemoveActor(actor)
        for actor in self.measurement_text_actors:
            self.renderer.RemoveActor(actor)
        self.measurement_actors.clear()
        self.measurement_text_actors.clear()

    def clear_panel_labels(self):
        for actor in self.panel_label_actors:
            self.renderer.RemoveActor(actor)
        for actor in self.panel_text_actors:
            self.renderer.RemoveActor(actor)
        self.panel_label_actors.clear()
        self.panel_text_actors.clear()

    def clear_nails(self):
        for actor in self.nail_actors:
            self.renderer.RemoveActor(actor)
        for actor in self.nail_text_actors:
            self.renderer.RemoveActor(actor)
        self.nail_actors.clear()
        self.nail_text_actors.clear()

    def set_panel_labels_visible(self, visible: bool):
        self.panel_labels_visible = visible
        for actor in self.panel_label_actors:
            actor.SetVisibility(1 if visible else 0)
        for actor in self.panel_text_actors:
            actor.SetVisibility(1 if visible else 0)
        self.vtk_widget.GetRenderWindow().Render()

    def set_nails_visible(self, visible: bool):
        self.nails_visible = visible
        for actor in self.nail_actors:
            actor.SetVisibility(1 if visible else 0)
        for actor in self.nail_text_actors:
            actor.SetVisibility(1 if visible else 0)
        self.vtk_widget.GetRenderWindow().Render()

    def _make_poly_actor(
        self,
        polydata: vtkPolyData,
        color=(1.0, 0.1, 0.1),
        line_width: float = 3.0,
    ) -> vtkActor:
        mapper = vtkPolyDataMapper()
        mapper.SetInputData(polydata)

        actor = vtkActor()
        actor.SetMapper(mapper)
        prop = actor.GetProperty()
        prop.SetColor(*color)
        prop.SetLineWidth(line_width)
        prop.SetAmbient(1.0)
        prop.SetDiffuse(0.0)
        prop.SetSpecular(0.0)
        return actor

    def _make_gray_poly_actor(
        self,
        polydata: vtkPolyData,
        line_width: float = 2.0,
    ) -> vtkActor:
        mapper = vtkPolyDataMapper()
        mapper.SetInputData(polydata)

        actor = vtkActor()
        actor.SetMapper(mapper)
        prop = actor.GetProperty()
        prop.SetColor(0.55, 0.58, 0.62)
        prop.SetLineWidth(line_width)
        prop.SetAmbient(1.0)
        prop.SetDiffuse(0.0)
        prop.SetSpecular(0.0)
        return actor

    def _create_arrow_head(
        self,
        position: Tuple[float, float, float],
        direction: Tuple[float, float, float],
        size_mm: float,
        color=(1.0, 0.1, 0.1),
    ) -> vtkActor:
        cone = vtkConeSource()
        cone.SetHeight(size_mm)
        cone.SetRadius(size_mm * 0.28)
        cone.SetResolution(24)
        cone.SetDirection(1.0, 0.0, 0.0)
        cone.CappingOn()
        cone.Update()

        dx, dy, dz = direction
        length = math.sqrt(dx * dx + dy * dy + dz * dz)
        if length <= 1e-9:
            dx, dy, dz = 1.0, 0.0, 0.0
            length = 1.0
        dx /= length
        dy /= length
        dz /= length

        yaw = math.degrees(math.atan2(dy, dx))
        pitch = -math.degrees(math.atan2(dz, math.sqrt(dx * dx + dy * dy)))

        transform = vtkTransform()
        transform.PostMultiply()
        transform.RotateZ(yaw)
        transform.RotateY(pitch)
        transform.Translate(*position)

        tf = vtkTransformPolyDataFilter()
        tf.SetInputConnection(cone.GetOutputPort())
        tf.SetTransform(transform)
        tf.Update()

        return self._make_poly_actor(tf.GetOutput(), color=color, line_width=1.0)

    def _create_gray_arrow_head(
        self,
        position: Tuple[float, float, float],
        direction: Tuple[float, float, float],
        size_mm: float = 6.0,
    ) -> vtkActor:
        cone = vtkConeSource()
        cone.SetHeight(size_mm)
        cone.SetRadius(size_mm * 0.28)
        cone.SetResolution(18)
        cone.SetDirection(1.0, 0.0, 0.0)
        cone.CappingOn()
        cone.Update()

        dx, dy, dz = direction
        length = math.sqrt(dx * dx + dy * dy + dz * dz)
        if length <= 1e-9:
            dx, dy, dz = 1.0, 0.0, 0.0
            length = 1.0

        dx /= length
        dy /= length
        dz /= length

        yaw = math.degrees(math.atan2(dy, dx))
        pitch = -math.degrees(math.atan2(dz, math.sqrt(dx * dx + dy * dy)))

        transform = vtkTransform()
        transform.PostMultiply()
        transform.RotateZ(yaw)
        transform.RotateY(pitch)
        transform.Translate(*position)

        tf = vtkTransformPolyDataFilter()
        tf.SetInputConnection(cone.GetOutputPort())
        tf.SetTransform(transform)
        tf.Update()

        return self._make_gray_poly_actor(tf.GetOutput(), line_width=1.0)

    def set_panel_labels(self, infos: List[Dict[str, Any]], z_offset: float = 8.0):
        self.clear_panel_labels()

        if not infos:
            self.vtk_widget.GetRenderWindow().Render()
            return

        xs = [info["cx"] for info in infos]
        ys = [info["cy"] for info in infos]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)

        x_span = max(max_x - min_x, 1.0)
        y_span = max(max_y - min_y, 1.0)

        x_margin = max(22.0, x_span * 0.12)
        y_step = max(12.0, y_span * 0.10)

        left_side = []
        right_side = []

        for info in infos:
            panel_name = info.get("panel_name")
            if not panel_name:
                continue

            if info["cx"] <= (min_x + max_x) / 2.0:
                left_side.append(info)
            else:
                right_side.append(info)

        left_side.sort(key=lambda i: i["cy"], reverse=True)
        right_side.sort(key=lambda i: i["cy"], reverse=True)

        def add_label(info: Dict[str, Any], side: str, slot_index: int):
            panel_name = info["panel_name"]

            start = (
                info["cx"],
                info["cy"],
                info["zmax"] + max(2.0, z_offset * 0.15),
            )

            target_y = max_y - slot_index * y_step

            if side == "left":
                elbow = (
                    info["cx"] - max(10.0, x_span * 0.05),
                    info["cy"],
                    info["zmax"] + z_offset * 0.55,
                )
                end = (
                    min_x - x_margin,
                    target_y,
                    info["zmax"] + z_offset,
                )
                text_pos = (end[0] - 2.0, end[1], end[2])
                text_justify = "right"
            else:
                elbow = (
                    info["cx"] + max(10.0, x_span * 0.05),
                    info["cy"],
                    info["zmax"] + z_offset * 0.55,
                )
                end = (
                    max_x + x_margin,
                    target_y,
                    info["zmax"] + z_offset,
                )
                text_pos = (end[0] + 2.0, end[1], end[2])
                text_justify = "left"

            line1 = vtkLineSource()
            line1.SetPoint1(*start)
            line1.SetPoint2(*elbow)
            line1.Update()

            line1_actor = self._make_gray_poly_actor(line1.GetOutput(), line_width=2.0)
            self.renderer.AddActor(line1_actor)
            self.panel_label_actors.append(line1_actor)

            line2 = vtkLineSource()
            line2.SetPoint1(*elbow)
            line2.SetPoint2(*end)
            line2.Update()

            line2_actor = self._make_gray_poly_actor(line2.GetOutput(), line_width=2.0)
            self.renderer.AddActor(line2_actor)
            self.panel_label_actors.append(line2_actor)

            direction = (
                end[0] - elbow[0],
                end[1] - elbow[1],
                end[2] - elbow[2],
            )
            head_actor = self._create_gray_arrow_head(end, direction, size_mm=6.0)
            self.renderer.AddActor(head_actor)
            self.panel_label_actors.append(head_actor)

            text_actor = vtkBillboardTextActor3D()
            text_actor.SetPosition(*text_pos)
            text_actor.SetInput(panel_name)

            tp = text_actor.GetTextProperty()
            tp.SetFontSize(16)
            tp.SetBold(True)
            tp.SetColor(0.9, 0.1, 0.1)
            if text_justify == "right":
                tp.SetJustificationToRight()
            else:
                tp.SetJustificationToLeft()
            tp.SetVerticalJustificationToCentered()
            tp.SetBackgroundColor(1.0, 1.0, 1.0)
            tp.SetBackgroundOpacity(0.65)

            self.renderer.AddActor(text_actor)
            self.panel_text_actors.append(text_actor)

        for idx, info in enumerate(left_side):
            add_label(info, "left", idx)

        for idx, info in enumerate(right_side):
            add_label(info, "right", idx)

        self.set_panel_labels_visible(self.panel_labels_visible)
        self.vtk_widget.GetRenderWindow().Render()

    def set_measurements_visible(self, visible: bool):
        self.distances_visible = visible
        for actor in self.measurement_actors:
            actor.SetVisibility(1 if visible else 0)
        for actor in self.measurement_text_actors:
            actor.SetVisibility(1 if visible else 0)
        self.vtk_widget.GetRenderWindow().Render()

    def toggle_measurements_visible(self) -> bool:
        self.set_measurements_visible(not self.distances_visible)
        return self.distances_visible

    def add_distance_measurement(
        self,
        start: Tuple[float, float, float],
        end: Tuple[float, float, float],
        text: str,
        color=(1.0, 0.1, 0.1),
    ):
        vx = end[0] - start[0]
        vy = end[1] - start[1]
        vz = end[2] - start[2]
        dist = math.sqrt(vx * vx + vy * vy + vz * vz)
        if dist <= 1e-9:
            return

        outside_start, outside_end = self._outside_measurement_points(start, end)

        for seg_start, seg_end, width in [
            (start, outside_start, 2.5),
            (outside_start, outside_end, 4.0),
            (outside_end, end, 2.5),
        ]:
            line = vtkLineSource()
            line.SetPoint1(*seg_start)
            line.SetPoint2(*seg_end)
            line.Update()

            line_actor = self._make_poly_actor(line.GetOutput(), color=color, line_width=width)
            line_actor.GetProperty().SetOpacity(0.88)
            line_actor.SetVisibility(1 if self.distances_visible else 0)
            self.renderer.AddActor(line_actor)
            self.measurement_actors.append(line_actor)

        ovx = outside_end[0] - outside_start[0]
        ovy = outside_end[1] - outside_start[1]
        ovz = outside_end[2] - outside_start[2]
        arrow_size = max(8.0, min(22.0, dist * 0.18))

        head1 = self._create_arrow_head(outside_start, (ovx, ovy, ovz), arrow_size, color=color)
        head2 = self._create_arrow_head(outside_end, (-ovx, -ovy, -ovz), arrow_size, color=color)
        head1.SetVisibility(1 if self.distances_visible else 0)
        head2.SetVisibility(1 if self.distances_visible else 0)

        self.renderer.AddActor(head1)
        self.renderer.AddActor(head2)
        self.measurement_actors.extend([head1, head2])

        mid_x = (outside_start[0] + outside_end[0]) / 2.0
        mid_y = (outside_start[1] + outside_end[1]) / 2.0
        mid_z = (outside_start[2] + outside_end[2]) / 2.0

        text_actor = vtkBillboardTextActor3D()
        text_actor.SetPosition(mid_x, mid_y, mid_z + max(8.0, arrow_size * 1.1))
        text_actor.SetInput(text)
        text_actor.SetVisibility(1 if self.distances_visible else 0)

        tp = text_actor.GetTextProperty()
        tp.SetFontSize(14)
        tp.SetBold(True)
        tp.SetColor(*color)
        tp.SetJustificationToCentered()
        tp.SetVerticalJustificationToBottom()
        tp.SetBackgroundColor(1.0, 1.0, 1.0)
        tp.SetBackgroundOpacity(0.35)

        self.renderer.AddActor(text_actor)
        self.measurement_text_actors.append(text_actor)

    def _outside_measurement_points(
        self,
        start: Tuple[float, float, float],
        end: Tuple[float, float, float],
    ) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
        vx = end[0] - start[0]
        vy = end[1] - start[1]
        length_xy = math.sqrt(vx * vx + vy * vy)
        if length_xy <= 1e-9:
            return start, end

        nx = -vy / length_xy
        ny = vx / length_xy

        xmin, xmax, ymin, ymax = self._solid_xy_bounds()
        cx = (xmin + xmax) / 2.0
        cy = (ymin + ymax) / 2.0
        span = max(xmax - xmin, ymax - ymin, length_xy)
        offset = max(45.0, min(180.0, span * 0.12))

        mid_x = (start[0] + end[0]) / 2.0
        mid_y = (start[1] + end[1]) / 2.0
        candidate_x = mid_x + nx * offset
        candidate_y = mid_y + ny * offset
        current_dist = (mid_x - cx) ** 2 + (mid_y - cy) ** 2
        candidate_dist = (candidate_x - cx) ** 2 + (candidate_y - cy) ** 2
        if candidate_dist < current_dist:
            nx = -nx
            ny = -ny

        return (
            (start[0] + nx * offset, start[1] + ny * offset, start[2]),
            (end[0] + nx * offset, end[1] + ny * offset, end[2]),
        )

    def _solid_xy_bounds(self) -> Tuple[float, float, float, float]:
        infos = [solid.get("info") for solid in self.step_solid_actors if solid.get("info")]
        if not infos:
            return (-1.0, 1.0, -1.0, 1.0)
        return (
            min(info["xmin"] for info in infos),
            max(info["xmax"] for info in infos),
            min(info["ymin"] for info in infos),
            max(info["ymax"] for info in infos),
        )

    def set_gap_measurements(
        self,
        gaps: List[Dict[str, Any]],
        unit_factor: float,
        unit_name: str,
    ):
        self.clear_measurements()

        type_colors = {
            "top_panel_gap": (1.0, 0.1, 0.1),
            "bottom_panel_gap": (0.1, 0.3, 1.0),
            "cubic_gap": (0.1, 0.7, 0.2),
        }

        for g in gaps:
            length_display = g["length_mm"] * unit_factor
            pair = g.get("pair_label", "")
            pair_prefix = f"{pair}: " if pair else ""
            text = f"{pair_prefix}{length_display:.1f} {unit_name}"
            color = type_colors.get(g.get("gap_type"), (1.0, 0.1, 0.1))
            self.add_distance_measurement(g["start"], g["end"], text, color=color)

        self.vtk_widget.GetRenderWindow().Render()

    def set_nail_positions(
        self,
        nail_items: List[Dict[str, Any]],
        unit_name: str = "mm",
    ):
        self.clear_nails()

        if not nail_items:
            self.vtk_widget.GetRenderWindow().Render()
            return

        for item in nail_items:
            point = item.get("point")
            nail_type = item.get("nail_type", item.get("type", "long"))
            side = item.get("side", "top")

            if not point:
                continue

            x, y, z = point

            is_long = nail_type == "long"
            radius = 4.5 if is_long else 2.8

            sphere = vtkSphereSource()
            sphere.SetCenter(x, y, z)
            sphere.SetRadius(radius)
            sphere.SetThetaResolution(24)
            sphere.SetPhiResolution(24)
            sphere.Update()

            mapper = vtkPolyDataMapper()
            mapper.SetInputConnection(sphere.GetOutputPort())

            actor = vtkActor()
            actor.SetMapper(mapper)
            prop = actor.GetProperty()
            if side == "bottom":
                prop.SetColor(0.05, 0.20, 0.70)
            elif is_long:
                prop.SetColor(0.08, 0.08, 0.08)   # near-black for long nails
            else:
                prop.SetColor(0.72, 0.40, 0.10)   # warm brown for short nails
            prop.SetAmbient(0.45)
            prop.SetDiffuse(0.65)
            prop.SetSpecular(0.05)
            prop.SetSpecularPower(8)

            actor.SetVisibility(1 if self.nails_visible else 0)
            self.renderer.AddActor(actor)
            self.nail_actors.append(actor)

            text_actor = vtkBillboardTextActor3D()
            text_actor.SetPosition(x, y, z + 10.0)
            text_actor.SetInput("")
            text_actor.SetVisibility(1 if self.nails_visible else 0)

            tp = text_actor.GetTextProperty()
            tp.SetFontSize(14)
            tp.SetBold(True)
            tp.SetColor(0.0, 0.0, 0.0)
            tp.SetJustificationToCentered()
            tp.SetVerticalJustificationToBottom()
            tp.SetBackgroundColor(1.0, 1.0, 1.0)
            tp.SetBackgroundOpacity(0.45)

            self.renderer.AddActor(text_actor)
            self.nail_text_actors.append(text_actor)

        self.vtk_widget.GetRenderWindow().Render()

    # ------------------------------------------------------------------ annotation shapes

    def set_draw_mode(self, enabled: bool):
        if enabled:
            self.line_draw_mode = False
            self.cancel_current_line()
        self.draw_mode = enabled

    def set_shape_type(self, shape_type: str):
        self.current_shape_type = shape_type

    def set_shape_color(self, r: float, g: float, b: float):
        self.current_shape_color = (r, g, b)

    def set_shape_size(self, size: float):
        self.current_shape_size = size

    def set_shape_opacity(self, opacity: float):
        self.current_shape_opacity = opacity

    def _make_shape_source(self, shape_type: str):
        if shape_type == "box":
            src = vtkCubeSource()
            src.SetXLength(2.0)
            src.SetYLength(2.0)
            src.SetZLength(2.0)
        elif shape_type == "cylinder":
            src = vtkCylinderSource()
            src.SetRadius(1.0)
            src.SetHeight(2.0)
            src.SetResolution(32)
            src.CappingOn()
        elif shape_type == "cone":
            src = vtkConeSource()
            src.SetRadius(1.0)
            src.SetHeight(2.0)
            src.SetResolution(32)
            src.CappingOn()
        elif shape_type == "disk":
            src = vtkDiskSource()
            src.SetInnerRadius(0.0)
            src.SetOuterRadius(1.0)
            src.SetRadialResolution(1)
            src.SetCircumferentialResolution(48)
        elif shape_type == "arrow":
            src = vtkArrowSource()
            src.SetShaftResolution(24)
            src.SetTipResolution(24)
            src.SetShaftRadius(0.04)
            src.SetTipRadius(0.12)
            src.SetTipLength(0.35)
        else:  # sphere (default)
            src = vtkSphereSource()
            src.SetRadius(1.0)
            src.SetThetaResolution(32)
            src.SetPhiResolution(32)
        src.Update()
        return src

    def _create_shape_actor(
        self,
        shape_type: str,
        position: Tuple[float, float, float],
        color: Tuple[float, float, float],
        size: float,
        opacity: float,
    ) -> vtkActor:
        src = self._make_shape_source(shape_type)

        mapper = vtkPolyDataMapper()
        mapper.SetInputConnection(src.GetOutputPort())

        actor = vtkActor()
        actor.SetMapper(mapper)
        actor.SetPosition(*position)
        actor.SetScale(size, size, size)

        if shape_type == "cylinder":
            actor.RotateX(90)
        elif shape_type == "cone":
            actor.RotateY(-90)
        elif shape_type == "arrow":
            actor.RotateY(-90)

        prop: vtkProperty = actor.GetProperty()
        prop.SetColor(*color)
        prop.SetOpacity(opacity)
        prop.SetInterpolationToPhong()
        prop.SetSpecular(0.25)
        prop.SetSpecularPower(20)
        prop.SetAmbient(0.2)
        prop.SetDiffuse(0.8)
        prop.EdgeVisibilityOff()
        return actor

    def _place_shape_at(self, position: Tuple[float, float, float]):
        actor = self._create_shape_actor(
            self.current_shape_type,
            position,
            self.current_shape_color,
            self.current_shape_size,
            self.current_shape_opacity,
        )
        shape = {
            "actor": actor,
            "shape_type": self.current_shape_type,
            "position": tuple(position),
            "color": self.current_shape_color,
            "size": self.current_shape_size,
            "opacity": self.current_shape_opacity,
        }
        self.drawn_shapes.append(shape)
        self.renderer.AddActor(actor)
        self.vtk_widget.GetRenderWindow().Render()
        if self.on_shape_placed is not None:
            self.on_shape_placed(shape)

    def select_shape(self, shape: Dict[str, Any]):
        if self.selected_shape is not None and self.selected_shape is not shape:
            self._apply_deselect_style(self.selected_shape["actor"])
        self.selected_shape = shape
        self._apply_select_style(shape["actor"])
        self.vtk_widget.GetRenderWindow().Render()
        if self.on_shape_selected is not None:
            self.on_shape_selected(shape)

    def deselect_shape(self):
        if self.selected_shape is not None:
            self._apply_deselect_style(self.selected_shape["actor"])
            self.selected_shape = None
        self.vtk_widget.GetRenderWindow().Render()
        if self.on_shape_selected is not None:
            self.on_shape_selected(None)

    def _apply_select_style(self, actor: vtkActor):
        prop: vtkProperty = actor.GetProperty()
        prop.EdgeVisibilityOn()
        prop.SetEdgeColor(1.0, 0.85, 0.0)
        prop.SetLineWidth(2.5)

    def _apply_deselect_style(self, actor: vtkActor):
        actor.GetProperty().EdgeVisibilityOff()

    def delete_selected_shape(self):
        if self.selected_shape is None:
            return
        actor = self.selected_shape["actor"]
        self.renderer.RemoveActor(actor)
        self.drawn_shapes.remove(self.selected_shape)
        self.selected_shape = None
        self.vtk_widget.GetRenderWindow().Render()
        if self.on_shape_selected is not None:
            self.on_shape_selected(None)

    def clear_drawn_shapes(self):
        for shape in self.drawn_shapes:
            self.renderer.RemoveActor(shape["actor"])
        self.drawn_shapes.clear()
        self.selected_shape = None
        self.vtk_widget.GetRenderWindow().Render()
        if self.on_shape_selected is not None:
            self.on_shape_selected(None)

    def update_selected_shape_color(self, r: float, g: float, b: float):
        if self.selected_shape is None:
            return
        self.selected_shape["color"] = (r, g, b)
        self.selected_shape["actor"].GetProperty().SetColor(r, g, b)
        self.vtk_widget.GetRenderWindow().Render()

    def update_selected_shape_size(self, size: float):
        if self.selected_shape is None:
            return
        self.selected_shape["size"] = size
        self.selected_shape["actor"].SetScale(size, size, size)
        self.vtk_widget.GetRenderWindow().Render()

    def update_selected_shape_opacity(self, opacity: float):
        if self.selected_shape is None:
            return
        self.selected_shape["opacity"] = opacity
        self.selected_shape["actor"].GetProperty().SetOpacity(opacity)
        self.vtk_widget.GetRenderWindow().Render()

    # ------------------------------------------------------------------ lines

    def set_line_draw_mode(self, enabled: bool):
        if enabled:
            self.draw_mode = False
        else:
            self.cancel_current_line()
            self._clear_preview_line()
        self.line_draw_mode = enabled

    def set_line_color(self, r: float, g: float, b: float):
        self.current_line_color = (r, g, b)

    def set_line_width(self, width: float):
        self.current_line_width = width

    def _on_mouse_move(self, obj, event):
        if self._dragging_shape is not None:
            mouse_pos = self.interactor.GetEventPosition()
            proj = self._screen_to_world_at_z(mouse_pos[0], mouse_pos[1], self._drag_z)
            ox, oy = self._drag_world_offset
            new_pos = (proj[0] - ox, proj[1] - oy, self._drag_z)
            self._dragging_shape["position"] = new_pos
            self._dragging_shape["actor"].SetPosition(*new_pos)
            self._drag_moved = True
            self.vtk_widget.GetRenderWindow().Render()
            return
        if not self.line_draw_mode or not self.current_line_points:
            return
        mouse_pos = self.interactor.GetEventPosition()
        self.picker.Pick(mouse_pos[0], mouse_pos[1], 0, self.renderer)
        if self.picker.GetActor() is not None:
            pos = self.picker.GetPickPosition()
        else:
            self.world_picker.Pick(mouse_pos[0], mouse_pos[1], 0, self.renderer)
            wpos = self.world_picker.GetPickPosition()
            pos = wpos if wpos != (0.0, 0.0, 0.0) else self.renderer.GetActiveCamera().GetFocalPoint()
        self._update_preview_line(pos)
        self.vtk_widget.GetRenderWindow().Render()

    def _on_left_button_release(self, _obj, _event):
        if self._dragging_shape is None:
            return
        if not self._drag_moved:
            self.deselect_shape()
        self._dragging_shape = None
        self._drag_world_offset = (0.0, 0.0)
        self._drag_z = 0.0
        self._drag_moved = False
        self.interactor.SetInteractorStyle(self.interactor_style)

    def _update_preview_line(self, end_pos):
        self._clear_preview_line()
        if not self.current_line_points:
            return
        start_pos = self.current_line_points[-1]
        line = vtkLineSource()
        line.SetPoint1(*start_pos)
        line.SetPoint2(*end_pos)
        line.Update()
        actor = self._make_poly_actor(
            line.GetOutput(),
            color=self.current_line_color,
            line_width=self.current_line_width,
        )
        actor.GetProperty().SetOpacity(0.45)
        self.renderer.AddActor(actor)
        self.line_preview_actor = actor

    def _clear_preview_line(self):
        if self.line_preview_actor is not None:
            self.renderer.RemoveActor(self.line_preview_actor)
            self.line_preview_actor = None

    def _add_line_point(self, pos: Tuple[float, float, float]):
        self.current_line_points.append(tuple(pos))
        self._clear_preview_line()

        sphere = vtkSphereSource()
        sphere.SetCenter(*pos)
        sphere.SetRadius(max(2.5, self.current_line_width * 1.1))
        sphere.SetThetaResolution(14)
        sphere.SetPhiResolution(14)
        sphere.Update()
        mapper = vtkPolyDataMapper()
        mapper.SetInputConnection(sphere.GetOutputPort())
        dot = vtkActor()
        dot.SetMapper(mapper)
        prop = dot.GetProperty()
        prop.SetColor(*self.current_line_color)
        prop.SetAmbient(1.0)
        prop.SetDiffuse(0.0)
        self.renderer.AddActor(dot)
        self.line_in_progress_actors.append(dot)

        if len(self.current_line_points) >= 2:
            p1 = self.current_line_points[-2]
            p2 = self.current_line_points[-1]
            seg = vtkLineSource()
            seg.SetPoint1(*p1)
            seg.SetPoint2(*p2)
            seg.Update()
            seg_actor = self._make_poly_actor(
                seg.GetOutput(),
                color=self.current_line_color,
                line_width=self.current_line_width,
            )
            self.renderer.AddActor(seg_actor)
            self.line_in_progress_actors.append(seg_actor)

    def finish_current_line(self):
        if len(self.current_line_points) < 2:
            self.cancel_current_line()
            return
        for actor in self.line_in_progress_actors:
            self.renderer.RemoveActor(actor)
        self.line_in_progress_actors.clear()
        self._clear_preview_line()

        actors = self._create_line_actors(
            self.current_line_points,
            self.current_line_color,
            self.current_line_width,
        )
        line_dict = {
            "points": list(self.current_line_points),
            "color": self.current_line_color,
            "line_width": self.current_line_width,
            "actors": actors,
        }
        self.drawn_lines.append(line_dict)
        self.current_line_points.clear()
        self.vtk_widget.GetRenderWindow().Render()
        if self.on_line_finished is not None:
            self.on_line_finished(line_dict)

    def cancel_current_line(self):
        for actor in self.line_in_progress_actors:
            self.renderer.RemoveActor(actor)
        self.line_in_progress_actors.clear()
        self._clear_preview_line()
        self.current_line_points.clear()
        if self._initialized:
            self.vtk_widget.GetRenderWindow().Render()

    def _create_line_actors(
        self,
        points: List[Tuple[float, float, float]],
        color: Tuple[float, float, float],
        line_width: float,
    ) -> List[vtkActor]:
        actors = []
        radius = max(2.5, line_width * 1.1)
        for pos in points:
            s = vtkSphereSource()
            s.SetCenter(*pos)
            s.SetRadius(radius)
            s.SetThetaResolution(14)
            s.SetPhiResolution(14)
            s.Update()
            m = vtkPolyDataMapper()
            m.SetInputConnection(s.GetOutputPort())
            a = vtkActor()
            a.SetMapper(m)
            prop = a.GetProperty()
            prop.SetColor(*color)
            prop.SetAmbient(0.9)
            prop.SetDiffuse(0.1)
            self.renderer.AddActor(a)
            actors.append(a)
        for i in range(len(points) - 1):
            seg = vtkLineSource()
            seg.SetPoint1(*points[i])
            seg.SetPoint2(*points[i + 1])
            seg.Update()
            seg_actor = self._make_poly_actor(seg.GetOutput(), color=color, line_width=line_width)
            self.renderer.AddActor(seg_actor)
            actors.append(seg_actor)
        return actors

    def select_line(self, line_dict: Dict[str, Any]):
        if self.selected_line is not None and self.selected_line is not line_dict:
            self._apply_line_deselect_style(self.selected_line)
        self.selected_line = line_dict
        self._apply_line_select_style(line_dict)
        self.vtk_widget.GetRenderWindow().Render()
        if self.on_line_selected is not None:
            self.on_line_selected(line_dict)

    def deselect_line(self):
        if self.selected_line is not None:
            self._apply_line_deselect_style(self.selected_line)
            self.selected_line = None
        self.vtk_widget.GetRenderWindow().Render()
        if self.on_line_selected is not None:
            self.on_line_selected(None)

    def _apply_line_select_style(self, line_dict: Dict[str, Any]):
        for actor in line_dict["actors"]:
            prop = actor.GetProperty()
            prop.SetColor(1.0, 0.85, 0.0)
            prop.SetAmbient(1.0)
            prop.SetDiffuse(0.0)

    def _apply_line_deselect_style(self, line_dict: Dict[str, Any]):
        color = line_dict["color"]
        lw = line_dict["line_width"]
        for actor in line_dict["actors"]:
            prop = actor.GetProperty()
            prop.SetColor(*color)
            prop.SetLineWidth(lw)
            prop.SetAmbient(0.9)
            prop.SetDiffuse(0.1)

    def delete_selected_line(self):
        if self.selected_line is None:
            return
        for actor in self.selected_line["actors"]:
            self.renderer.RemoveActor(actor)
        self.drawn_lines.remove(self.selected_line)
        self.selected_line = None
        self.vtk_widget.GetRenderWindow().Render()
        if self.on_line_selected is not None:
            self.on_line_selected(None)

    def clear_drawn_lines(self):
        for line_dict in self.drawn_lines:
            for actor in line_dict["actors"]:
                self.renderer.RemoveActor(actor)
        self.drawn_lines.clear()
        self.selected_line = None
        for actor in self.line_in_progress_actors:
            self.renderer.RemoveActor(actor)
        self.line_in_progress_actors.clear()
        self._clear_preview_line()
        self.current_line_points.clear()
        if self._initialized:
            self.vtk_widget.GetRenderWindow().Render()
        if self.on_line_selected is not None:
            self.on_line_selected(None)
