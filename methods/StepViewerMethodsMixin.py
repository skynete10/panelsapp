from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import cadquery as cq
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QMessageBox,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QTextEdit,
)
from vtkmodules.vtkCommonCore import vtkPoints
from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkPolyData, vtkTriangle
from vtkmodules.vtkFiltersCore import vtkCleanPolyData, vtkPolyDataNormals

from OCP.Bnd import Bnd_Box
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
from OCP.BRepBndLib import BRepBndLib
from OCP.GeomAbs import GeomAbs_Plane
from OCP.TopAbs import TopAbs_FACE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS


class StepViewerMethodsMixin:
    def open_step_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Layer File",
            "",
            "Layer Files (*.step *.stp *.xml);;STEP Files (*.step *.stp);;XML Files (*.xml)",
        )
        if not file_path:
            return
        if file_path.lower().endswith(".xml"):
            self._load_xml_layer_file(file_path)
        else:
            self.load_step(file_path)

    def reload_current_file(self):
        if not self.current_file:
            QMessageBox.information(self, "Reload", "No STEP file is currently loaded.")
            return
        self.load_step(str(self.current_file))

    def reset_view(self):
        self.viewer.reset_view()
        self.status_label.setText("View reset")

    def show_cad_view(self):
        self.viewer.set_view_mode("cad")
        self.material_card.set_value("CAD")

    def show_wood_view(self):
        self.viewer.set_view_mode("wood")
        if self.viewer.has_texture():
            self.material_card.set_value("Wood")
        else:
            self.material_card.set_value("Wood (color fallback)")

    def toggle_distances(self):
        visible = self.viewer.toggle_measurements_visible()
        if visible:
            self.toggle_dist_btn.setText("Hide Distances")
            self.status_label.setText("Distances shown")
        else:
            self.toggle_dist_btn.setText("Show Distances")
            self.status_label.setText("Distances hidden")

    def toggle_panel_names(self):
        self.panel_names_visible = not getattr(self, "panel_names_visible", True)

        if hasattr(self.viewer, "toggle_panel_names"):
            self.viewer.toggle_panel_names(self.panel_names_visible)
        elif hasattr(self.viewer, "set_panel_labels_visible"):
            self.viewer.set_panel_labels_visible(self.panel_names_visible)

        if self.panel_names_visible:
            self.toggle_names_btn.setText("Hide Panel Names")
            self.status_label.setText("Panel names shown")
        else:
            self.toggle_names_btn.setText("Show Panel Names")
            self.status_label.setText("Panel names hidden")

    def handle_panel_clicked(self, info):
        factor, unit = self._get_factor_and_unit()

        if not info:
            self.clicked_panel_card.set_value("Not found")
            self.clicked_panel_text.setPlainText("No panel detected from click.")
            self._active_panel_info = None
            self.size_l_input.setText("")
            self.size_w_input.setText("")
            self.size_h_input.setText("")
            return

        panel_name = info.get("panel_name", "?")
        layer_name = info.get("layer_name", "-")
        self._active_panel_info = info
        self.clicked_panel_card.set_value(panel_name)
        self.clicked_panel_text.setPlainText(
            f"Name:    {panel_name}\n"
            f"Layer:   {layer_name}\n"
            f"Order:   {info.get('panel_order', '--')}\n"
            f"Class:   {info.get('panel_class', 'other')}\n"
            f"Center:  ({info['cx'] * factor:.1f}, {info['cy'] * factor:.1f}, {info['cz'] * factor:.1f}) {unit}"
        )
        self.size_l_input.setText(f"{info['x'] * factor:.1f}")
        self.size_w_input.setText(f"{info['y'] * factor:.1f}")
        self.size_h_input.setText(f"{info['z'] * factor:.1f}")
        self.status_label.setText(f"Clicked panel: {panel_name}  |  Layer: {layer_name}")

    def load_step(self, file_path: str):
        try:
            model = cq.importers.importStep(file_path)
            if model is None or model.size() == 0:
                raise RuntimeError("The STEP file was read, but no shape was found.")

            shape = model.val()
            bb = shape.BoundingBox()

            solid_infos = self.get_solid_infos(model)
            counts = self.classify_panels(solid_infos)
            self.assign_panel_names(solid_infos)
            self.assign_layer_names(solid_infos)
            sets_4 = self.detect_4_panel_sets(solid_infos)

            top_gaps = self.detect_top_panel_spaces(solid_infos, bb)
            bottom_gaps = self.detect_bottom_panel_spaces(solid_infos, bb)
            cubic_gaps = self.detect_cubic_panel_spaces(solid_infos, bb)
            gaps = top_gaps + bottom_gaps + cubic_gaps

            solids_data = []
            for info in solid_infos:
                try:
                    solid_polydata = self._cadquery_shape_to_vtk_polydata(info["solid"])
                    if solid_polydata is not None and solid_polydata.GetNumberOfPoints() > 0:
                        solids_data.append((solid_polydata, info))
                except Exception:
                    pass

            if not solids_data:
                raise RuntimeError("Could not generate a renderable mesh from the STEP file.")

            self.current_model = model
            self.current_base_bb = bb
            self.current_solid_infos = solid_infos
            self.current_gap_measurements = gaps
            self.current_nail_positions = []

            self.current_panels_count = counts["total"]
            self.current_rectangular_count = counts["p1"]
            self.current_cubique_count = counts["p2"]
            self.current_other_count = counts["other"]
            self.current_4panel_sets_count = len(sets_4)

            self.current_top_gaps_count = len(top_gaps)
            self.current_bottom_gaps_count = len(bottom_gaps)
            self.current_cubic_gaps_count = len(cubic_gaps)

            self.current_file = Path(file_path)

            self.viewer.set_step_solids(solids_data)
            self.viewer.set_pick_infos(self.current_solid_infos)
            self.viewer.set_panel_labels(
                self.current_solid_infos,
                z_offset=max(6.0, bb.zlen * 0.012),
            )
            self.viewer.clear_nails()
            self.viewer.set_nails_visible(False)

            self.panel_names_visible = False
            if hasattr(self.viewer, "toggle_panel_names"):
                self.viewer.toggle_panel_names(False)
            elif hasattr(self.viewer, "set_panel_labels_visible"):
                self.viewer.set_panel_labels_visible(False)

            if hasattr(self, "toggle_names_btn"):
                self.toggle_names_btn.setText("Show Panel Names")

            self.update_dimension_cards()
            self.refresh_gap_measurements()

            self.viewer.set_measurements_visible(False)
            self.toggle_dist_btn.setText("Show Distances")

            self.file_card.set_value(self.current_file.name)
            self.total_badge.set_value(str(self.current_panels_count))
            self.rect_badge.set_value(str(self.current_rectangular_count))
            self.cubique_badge.set_value(str(self.current_cubique_count))
            self.sets_card.set_value(str(self.current_4panel_sets_count))
            self.gaps_card.set_value(str(len(self.current_gap_measurements)))
            self.top_gaps_card.set_value(str(self.current_top_gaps_count))
            self.bottom_gaps_card.set_value(str(self.current_bottom_gaps_count))
            self.cubic_gaps_card.set_value(str(self.current_cubic_gaps_count))
            self.panel_names_card.set_value(str(self.current_panels_count))
            self.clicked_panel_card.set_value("Click a panel")
            self.clicked_panel_text.setPlainText(
                "Click any panel in the 3D view to detect its name."
            )

            self.update_panel_side_lists()

            self.status_label.setText(
                f"All: {self.current_panels_count} | "
                f"Rectangular: {self.current_rectangular_count} | "
                f"Cubique: {self.current_cubique_count} | "
                f"4-Panel Sets: {self.current_4panel_sets_count} | "
                f"Top Spaces: {self.current_top_gaps_count} | "
                f"Bottom Spaces: {self.current_bottom_gaps_count} | "
                f"Cubique Spaces: {self.current_cubic_gaps_count}"
            )

        except Exception as exc:
            QMessageBox.critical(self, "Open Failed", f"Could not open STEP file.\n\n{exc}")

    def _cadquery_shape_to_vtk_polydata(self, shape) -> vtkPolyData:
        vertices, triangles = shape.tessellate(0.12, 0.12)

        points = vtkPoints()
        for v in vertices:
            points.InsertNextPoint(float(v.x), float(v.y), float(v.z))

        cells = vtkCellArray()
        for tri in triangles:
            triangle = vtkTriangle()
            triangle.GetPointIds().SetId(0, int(tri[0]))
            triangle.GetPointIds().SetId(1, int(tri[1]))
            triangle.GetPointIds().SetId(2, int(tri[2]))
            cells.InsertNextCell(triangle)

        polydata = vtkPolyData()
        polydata.SetPoints(points)
        polydata.SetPolys(cells)

        cleaner = vtkCleanPolyData()
        cleaner.SetInputData(polydata)
        cleaner.Update()

        normals = vtkPolyDataNormals()
        normals.SetInputConnection(cleaner.GetOutputPort())
        normals.ComputePointNormalsOn()
        normals.ComputeCellNormalsOn()
        normals.SplittingOff()
        normals.ConsistencyOn()
        normals.AutoOrientNormalsOn()
        normals.Update()

        return normals.GetOutput()

    def _get_factor_and_unit(self) -> Tuple[float, str]:
        unit = self.unit_combo.currentText()
        if unit == "mm":
            return 1.0, "mm"
        if unit == "cm":
            return 0.1, "cm"
        if unit == "m":
            return 0.001, "m"
        return 1.0, "mm"

    def on_unit_changed(self):
        self.update_dimension_cards()
        self.refresh_gap_measurements()
        self.update_panel_side_lists()

        if self.clicked_panel_card.value_label.text() not in ["Click a panel", "Not found", "--"]:
            selected_name = self.clicked_panel_card.value_label.text()
            selected = next(
                (i for i in self.current_solid_infos if i.get("panel_name") == selected_name),
                None,
            )
            self.handle_panel_clicked(selected)

    def update_dimension_cards(self):
        factor, unit = self._get_factor_and_unit()
        self.unit_card.set_value(unit)

        if self.current_base_bb is None:
            self.length_card.set_value("--")
            self.width_card.set_value("--")
            self.height_card.set_value("--")
            return

        self.length_card.set_value(f"{self.current_base_bb.xlen * factor:.3f} {unit}")
        self.width_card.set_value(f"{self.current_base_bb.ylen * factor:.3f} {unit}")
        self.height_card.set_value(f"{self.current_base_bb.zlen * factor:.3f} {unit}")

    def refresh_gap_measurements(self):
        factor, unit = self._get_factor_and_unit()
        was_visible = self.viewer.distances_visible
        self.viewer.set_gap_measurements(self.current_gap_measurements, factor, unit)
        self.viewer.set_measurements_visible(was_visible)
        self.gaps_card.set_value(str(len(self.current_gap_measurements)))

    def update_panel_side_lists(self):
        factor, unit = self._get_factor_and_unit()

        if not self.current_solid_infos:
            if hasattr(self, "stacked_panels_text"):
                self.stacked_panels_text.setPlainText("")
            self.distance_list_text.setPlainText("")
            return

        stack_lines = self.build_stacked_panels_lines()
        if hasattr(self, "stacked_panels_text"):
            if not stack_lines:
                self.stacked_panels_text.setPlainText("No stacked panels detected.")
            else:
                self.stacked_panels_text.setPlainText("\n".join(stack_lines))

        distance_lines = []
        gap_entries = sorted(
            self.current_gap_measurements,
            key=lambda g: (
                g.get("panel_a_order", 99999),
                g.get("panel_b_order", 99999),
            ),
        )

        for gap in gap_entries:
            pair = gap.get("pair_label", "?")
            deck = gap.get("label_prefix", "")
            distance_lines.append(
                f"{pair}: {gap['length_mm'] * factor:.1f} {unit} {deck}".strip()
            )

        if not distance_lines:
            distance_lines.append("No panel-to-panel spaces detected.")

        self.distance_list_text.setPlainText("\n".join(distance_lines))

    def get_solid_infos(self, model) -> List[Dict[str, Any]]:
        infos = []
        try:
            solids = model.solids().vals()
        except Exception:
            return infos

        for idx, solid in enumerate(solids, start=1):
            try:
                bb = solid.BoundingBox()

                x = float(bb.xlen)
                y = float(bb.ylen)
                z = float(bb.zlen)
                xmin = float(bb.xmin)
                xmax = float(bb.xmax)
                ymin = float(bb.ymin)
                ymax = float(bb.ymax)
                zmin = float(bb.zmin)
                zmax = float(bb.zmax)

                wrapped = getattr(solid, "wrapped", None)

                infos.append(
                    {
                        "index": idx,
                        "solid": solid,
                        "solid_shape": wrapped,
                        "x": x,
                        "y": y,
                        "z": z,
                        "xmin": xmin,
                        "xmax": xmax,
                        "ymin": ymin,
                        "ymax": ymax,
                        "zmin": zmin,
                        "zmax": zmax,
                        "cx": (xmin + xmax) / 2.0,
                        "cy": (ymin + ymax) / 2.0,
                        "cz": (zmin + zmax) / 2.0,
                        "footprint": x * y,
                        "longest_xy": max(x, y),
                        "shortest_xy": min(x, y),
                    }
                )
            except Exception:
                pass

        return infos

    def classify_panels(self, infos: List[Dict[str, Any]]) -> Dict[str, int]:
        result = {"total": len(infos), "p1": 0, "p2": 0, "other": 0}
        if not infos:
            return result

        max_footprint = max(i["footprint"] for i in infos)
        max_longest_xy = max(i["longest_xy"] for i in infos)

        for info in infos:
            thin_ratio = info["z"] / max(info["longest_xy"], 1e-9)
            is_large_xy = (
                info["footprint"] >= max_footprint * 0.35
                or info["longest_xy"] >= max_longest_xy * 0.55
            )
            is_thin = thin_ratio <= 0.20

            if is_large_xy and is_thin:
                info["panel_class"] = "p1"
                continue

            is_small_xy = info["footprint"] < max_footprint * 0.35
            z_vs_xy = info["z"] / max(info["shortest_xy"], 1e-9)
            is_blockish = 0.35 <= z_vs_xy <= 4.0

            if (not is_thin) and is_small_xy and is_blockish:
                info["panel_class"] = "p2"
            else:
                info["panel_class"] = "other"

        p1_list = [i for i in infos if i.get("panel_class") == "p1"]

        if p1_list:
            zmax_values = [i["zmax"] for i in p1_list]
            min_p1_z = min(zmax_values)
            max_p1_z = max(zmax_values)
            spread = max_p1_z - min_p1_z

            if spread > 1.0:
                z_mid = (min_p1_z + max_p1_z) / 2.0
                for i in p1_list:
                    i["panel_layer"] = "top" if i["zmax"] >= z_mid else "bottom"
            else:
                for i in p1_list:
                    i["panel_layer"] = "top"

        for i in infos:
            if i.get("panel_class") == "p1":
                result["p1"] += 1
            elif i.get("panel_class") == "p2":
                result["p2"] += 1
            else:
                result["other"] += 1

        return result

    def _sort_panels_for_naming(
        self,
        items: List[Dict[str, Any]],
        reverse_primary: bool = False,
    ) -> List[Dict[str, Any]]:
        if not items:
            return []

        x_spread = max(i["cx"] for i in items) - min(i["cx"] for i in items)
        y_spread = max(i["cy"] for i in items) - min(i["cy"] for i in items)
        primary_axis = "cx" if x_spread >= y_spread else "cy"
        secondary_axis = "cy" if primary_axis == "cx" else "cx"

        if reverse_primary:
            return sorted(
                items,
                key=lambda i: (
                    -round(i[primary_axis], 6),
                    round(i[secondary_axis], 6),
                    round(i["cz"], 6),
                    i["index"],
                ),
            )

        return sorted(
            items,
            key=lambda i: (
                round(i[primary_axis], 6),
                round(i[secondary_axis], 6),
                round(i["cz"], 6),
                i["index"],
            ),
        )

    def assign_panel_names(self, infos: List[Dict[str, Any]]):
        bottom_rect = [
            i for i in infos
            if i.get("panel_class") == "p1" and i.get("panel_layer") == "bottom"
        ]
        cubic_panels = [i for i in infos if i.get("panel_class") == "p2"]
        top_rect = [
            i for i in infos
            if i.get("panel_class") == "p1" and i.get("panel_layer") == "top"
        ]
        others = [
            i for i in infos
            if i not in bottom_rect and i not in cubic_panels and i not in top_rect
        ]

        ordered = []
        ordered.extend(self._sort_panels_for_naming(bottom_rect, reverse_primary=True))
        ordered.extend(self._sort_panels_for_naming(cubic_panels, reverse_primary=True))
        ordered.extend(self._sort_panels_for_naming(top_rect, reverse_primary=False))
        ordered.extend(
            sorted(
                others,
                key=lambda i: (
                    round(i["cz"], 6),
                    round(i["cy"], 6),
                    round(i["cx"], 6),
                    i["index"],
                ),
            )
        )

        for order, info in enumerate(ordered, start=1):
            info["panel_name"] = f"p{order}"
            info["panel_order"] = order

    _LAYER_NAMES = ["Deck", "Stringer", "Blocker", "Perimetral"]

    def assign_layer_names(self, infos: List[Dict[str, Any]]):
        """Cluster panels by z-centre from top to bottom and assign layer names."""
        if not infos:
            return

        sorted_by_z = sorted(infos, key=lambda i: -i["cz"])

        z_top = sorted_by_z[0]["cz"]
        z_bot = sorted_by_z[-1]["cz"]
        z_range = z_top - z_bot
        gap_threshold = max(z_range * 0.05, 3.0)

        layers: List[List[Dict[str, Any]]] = [[sorted_by_z[0]]]
        for info in sorted_by_z[1:]:
            if abs(layers[-1][-1]["cz"] - info["cz"]) > gap_threshold:
                layers.append([info])
            else:
                layers[-1].append(info)

        n = len(layers)
        if n == 1:
            labels = ["Deck"]
        elif n == 2:
            labels = ["Deck", "Perimeter"]
        elif n == 3:
            labels = ["Deck", "Stringer", "Perimeter"]
        else:
            labels = self._LAYER_NAMES[:n] if n <= len(self._LAYER_NAMES) else (
                self._LAYER_NAMES + [f"Layer {i + 1}" for i in range(len(self._LAYER_NAMES), n)]
            )

        for i, layer in enumerate(layers):
            for panel in layer:
                panel["layer_name"] = labels[i]

    def _shape_bbox_tuple(self, shape, gap: float = 0.0) -> Optional[Tuple[float, float, float, float, float, float]]:
        if shape is None:
            return None

        try:
            box = Bnd_Box()
            if gap > 0:
                box.SetGap(gap)
            BRepBndLib.Add(shape, box)
            xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
            if xmax < xmin or ymax < ymin or zmax < zmin:
                return None
            return (
                float(xmin),
                float(xmax),
                float(ymin),
                float(ymax),
                float(zmin),
                float(zmax),
            )
        except Exception:
            return None

    def _explore_planar_faces(self, solid_shape) -> List[Dict[str, Any]]:
        faces = []
        if solid_shape is None:
            return faces

        try:
            exp = TopExp_Explorer(solid_shape, TopAbs_FACE)
            while exp.More():
                face = TopoDS.Face_s(exp.Current())
                surf = BRepAdaptor_Surface(face, True)
                if surf.GetType() == GeomAbs_Plane:
                    bbox = self._shape_bbox_tuple(face)
                    if bbox is None:
                        exp.Next()
                        continue

                    xmin, xmax, ymin, ymax, zmin, zmax = bbox
                    xlen = max(0.0, xmax - xmin)
                    ylen = max(0.0, ymax - ymin)
                    zlen = max(0.0, zmax - zmin)

                    area_hint = xlen * ylen
                    faces.append(
                        {
                            "face": face,
                            "xmin": xmin,
                            "xmax": xmax,
                            "ymin": ymin,
                            "ymax": ymax,
                            "zmin": zmin,
                            "zmax": zmax,
                            "xlen": xlen,
                            "ylen": ylen,
                            "zlen": zlen,
                            "area_hint": area_hint,
                            "is_horizontal": zlen <= 1.0,
                        }
                    )
                exp.Next()
        except Exception:
            return []

        return faces

    def _get_top_planar_face_info(self, info: Dict[str, Any], z_tol: float = 1.0) -> Optional[Dict[str, Any]]:
        solid_shape = info.get("solid_shape")
        if solid_shape is None:
            return None

        candidates = []
        for f in self._explore_planar_faces(solid_shape):
            if not f["is_horizontal"]:
                continue
            if abs(f["zmax"] - info["zmax"]) <= z_tol:
                candidates.append(f)

        if not candidates:
            return None

        candidates.sort(
            key=lambda f: (
                -f["area_hint"],
                abs(f["zmax"] - info["zmax"]),
            )
        )
        return candidates[0]

    def _get_bottom_planar_face_info(self, info: Dict[str, Any], z_tol: float = 1.0) -> Optional[Dict[str, Any]]:
        solid_shape = info.get("solid_shape")
        if solid_shape is None:
            return None

        candidates = []
        for f in self._explore_planar_faces(solid_shape):
            if not f["is_horizontal"]:
                continue
            if abs(f["zmin"] - info["zmin"]) <= z_tol:
                candidates.append(f)

        if not candidates:
            return None

        candidates.sort(
            key=lambda f: (
                -f["area_hint"],
                abs(f["zmin"] - info["zmin"]),
            )
        )
        return candidates[0]

    def _face_xy_rect(self, face_info: Dict[str, Any]) -> Tuple[float, float, float, float]:
        return (
            face_info["xmin"],
            face_info["xmax"],
            face_info["ymin"],
            face_info["ymax"],
        )

    def _exact_face_common_rect(
        self,
        face_a,
        face_b,
        z_value: float,
    ) -> Optional[Dict[str, Any]]:
        try:
            common = BRepAlgoAPI_Common(face_a, face_b)
            common.Build()
            result_shape = common.Shape()
            if result_shape is None or result_shape.IsNull():
                return None

            bbox = self._shape_bbox_tuple(result_shape)
            if bbox is None:
                return None

            xmin, xmax, ymin, ymax, zmin, zmax = bbox
            if xmax <= xmin or ymax <= ymin:
                return None

            rect = (xmin, xmax, ymin, ymax)
            area = self._rect_area(rect)

            return {
                "rect": rect,
                "area": area,
                "z": z_value,
                "shape": result_shape,
            }
        except Exception:
            return None

    def _projected_overlap_from_faces(
        self,
        face_a_info: Dict[str, Any],
        face_b_info: Dict[str, Any],
        z_value: float,
    ) -> Optional[Dict[str, Any]]:
        rect = self._intersect_rects(
            self._face_xy_rect(face_a_info),
            self._face_xy_rect(face_b_info),
        )
        if rect is None:
            return None

        area = self._rect_area(rect)
        if area <= 0:
            return None

        return {
            "rect": rect,
            "area": area,
            "z": z_value,
            "shape": None,
        }

    def _exact_contact_surface_info(
        self,
        top_panel: Dict[str, Any],
        bottom_panel: Dict[str, Any],
        contact_z_tol: float = 2.0,
    ) -> Optional[Dict[str, Any]]:
        top_bottom_face = self._get_bottom_planar_face_info(top_panel)
        bottom_top_face = self._get_top_planar_face_info(bottom_panel)

        if not top_bottom_face or not bottom_top_face:
            return None

        face_gap = abs(top_bottom_face["zmin"] - bottom_top_face["zmax"])
        if face_gap > contact_z_tol:
            return None

        z_value = (top_bottom_face["zmin"] + bottom_top_face["zmax"]) / 2.0

        exact = self._exact_face_common_rect(
            top_bottom_face["face"],
            bottom_top_face["face"],
            z_value=z_value,
        )

        if exact is None:
            exact = self._projected_overlap_from_faces(
                top_bottom_face,
                bottom_top_face,
                z_value=z_value,
            )

        if exact is None:
            return None

        top_area = max(top_panel["x"] * top_panel["y"], 1e-9)
        bottom_area = max(bottom_panel["x"] * bottom_panel["y"], 1e-9)
        smaller_area = min(top_area, bottom_area)
        ratio = exact["area"] / smaller_area

        pts = self._surface_rect_points(exact["rect"], exact["z"], factor=1.0)
        cx = (exact["rect"][0] + exact["rect"][1]) / 2.0
        cy = (exact["rect"][2] + exact["rect"][3]) / 2.0

        return {
            "rect": exact["rect"],
            "area": exact["area"],
            "ratio": ratio,
            "z": exact["z"],
            "points": pts,
            "center": (cx, cy, exact["z"]),
            "method": "occt_exact",
        }

    def contains_xy(self, solid_info: Dict[str, Any], x: float, y: float, tol: float = 1.0) -> bool:
        return (
            solid_info["xmin"] - tol <= x <= solid_info["xmax"] + tol
            and solid_info["ymin"] - tol <= y <= solid_info["ymax"] + tol
        )

    def unique_layers(self, stacked: List[Dict[str, Any]], z_tol: float = 1.0) -> List[Dict[str, Any]]:
        if not stacked:
            return []

        stacked = sorted(stacked, key=lambda s: (s["zmin"], s["zmax"]))
        layers = []

        for s in stacked:
            if not layers:
                layers.append(s)
                continue

            prev = layers[-1]
            same_layer = (
                abs(s["zmin"] - prev["zmin"]) <= z_tol
                and abs(s["zmax"] - prev["zmax"]) <= z_tol
            )
            if not same_layer:
                layers.append(s)

        return layers

    def detect_4_panel_sets(self, infos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not infos:
            return []

        groups = []
        used_points: List[Tuple[float, float]] = []

        for base in infos:
            x = base["cx"]
            y = base["cy"]

            stacked = [i for i in infos if self.contains_xy(i, x, y, tol=1.0)]
            layers = self.unique_layers(stacked, z_tol=1.0)
            layer_count = len(layers)

            if layer_count != 4:
                continue

            duplicate = False
            for ux, uy in used_points:
                if ((x - ux) ** 2 + (y - uy) ** 2) ** 0.5 < 18.0:
                    duplicate = True
                    break

            if duplicate:
                continue

            used_points.append((x, y))
            groups.append(
                {
                    "x": x,
                    "y": y,
                    "layer_count": layer_count,
                    "layers": layers,
                }
            )

        return groups

    def _cluster_by_axis(
        self,
        items: List[Dict[str, Any]],
        axis_key: str,
        tol: float,
    ) -> List[List[Dict[str, Any]]]:
        if not items:
            return []

        items = sorted(items, key=lambda p: p[axis_key])
        groups = [[items[0]]]

        for item in items[1:]:
            if abs(item[axis_key] - groups[-1][-1][axis_key]) <= tol:
                groups[-1].append(item)
            else:
                groups.append([item])

        return groups

    def _rect_xy_intersection(
        self,
        a: Dict[str, Any],
        b: Dict[str, Any],
    ) -> Optional[Tuple[float, float, float, float]]:
        xmin = max(a["xmin"], b["xmin"])
        xmax = min(a["xmax"], b["xmax"])
        ymin = max(a["ymin"], b["ymin"])
        ymax = min(a["ymax"], b["ymax"])

        if xmax <= xmin or ymax <= ymin:
            return None
        return (xmin, xmax, ymin, ymax)

    def _rect_area(self, rect: Optional[Tuple[float, float, float, float]]) -> float:
        if not rect:
            return 0.0
        xmin, xmax, ymin, ymax = rect
        return max(0.0, xmax - xmin) * max(0.0, ymax - ymin)

    def _intersect_rects(
        self,
        rect1: Optional[Tuple[float, float, float, float]],
        rect2: Optional[Tuple[float, float, float, float]],
    ) -> Optional[Tuple[float, float, float, float]]:
        if rect1 is None or rect2 is None:
            return None

        xmin = max(rect1[0], rect2[0])
        xmax = min(rect1[1], rect2[1])
        ymin = max(rect1[2], rect2[2])
        ymax = min(rect1[3], rect2[3])

        if xmax <= xmin or ymax <= ymin:
            return None
        return (xmin, xmax, ymin, ymax)

    def _panel_xy_rect(self, info: Dict[str, Any]) -> Tuple[float, float, float, float]:
        return (info["xmin"], info["xmax"], info["ymin"], info["ymax"])

    def _surface_rect_points(
        self,
        rect: Tuple[float, float, float, float],
        z: float,
        factor: float = 1.0,
    ) -> List[Tuple[float, float, float]]:
        xmin, xmax, ymin, ymax = rect
        zf = z * factor
        return [
            (xmin * factor, ymin * factor, zf),
            (xmax * factor, ymin * factor, zf),
            (xmax * factor, ymax * factor, zf),
            (xmin * factor, ymax * factor, zf),
        ]

    def panels_intersect_xy(
        self,
        a: Dict[str, Any],
        b: Dict[str, Any],
        min_overlap_ratio: float = 0.15,
    ) -> bool:
        rect = self._rect_xy_intersection(a, b)
        overlap_area = self._rect_area(rect)
        if overlap_area <= 0:
            return False

        a_area = max(a["x"] * a["y"], 1e-9)
        b_area = max(b["x"] * b["y"], 1e-9)
        smaller_area = min(a_area, b_area)

        return overlap_area >= smaller_area * min_overlap_ratio

    def panels_are_stacked(
        self,
        top_panel: Dict[str, Any],
        bottom_panel: Dict[str, Any],
        min_z_gap: float = 1.0,
        min_overlap_ratio: float = 0.15,
    ) -> bool:
        if top_panel["panel_name"] == bottom_panel["panel_name"]:
            return False

        exact = self._exact_contact_surface_info(top_panel, bottom_panel)
        if exact and exact["ratio"] >= min_overlap_ratio:
            return True

        if not self.panels_intersect_xy(
            top_panel,
            bottom_panel,
            min_overlap_ratio=min_overlap_ratio,
        ):
            return False

        return top_panel["cz"] > bottom_panel["cz"] + min_z_gap

    def _surface_intersection_info(
        self,
        top_panel: Dict[str, Any],
        bottom_panel: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        exact = self._exact_contact_surface_info(top_panel, bottom_panel)
        if exact is not None:
            return exact

        rect = self._rect_xy_intersection(top_panel, bottom_panel)
        if rect is None:
            return None

        area = self._rect_area(rect)
        if area <= 0:
            return None

        top_area = max(top_panel["x"] * top_panel["y"], 1e-9)
        bottom_area = max(bottom_panel["x"] * bottom_panel["y"], 1e-9)
        smaller_area = min(top_area, bottom_area)
        ratio = area / smaller_area

        z_value = min(top_panel["zmin"], bottom_panel["zmax"])
        cx = (rect[0] + rect[1]) / 2.0
        cy = (rect[2] + rect[3]) / 2.0

        return {
            "rect": rect,
            "area": area,
            "ratio": ratio,
            "points": self._surface_rect_points(rect, z_value, factor=1.0),
            "center": (cx, cy, z_value),
            "z": z_value,
            "method": "projected_fallback",
        }

    def _get_direct_stacked_pairs(
        self,
        min_overlap_ratio: float = 0.03,
        min_z_gap: float = 1.0,
    ) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
        infos = [i for i in self.current_solid_infos if i.get("panel_name")]
        if not infos:
            return []

        infos = sorted(
            infos,
            key=lambda x: (-x["cz"], x.get("panel_order", 99999)),
        )

        pairs: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
        seen = set()

        for top in infos:
            for bottom in infos:
                if top["panel_name"] == bottom["panel_name"]:
                    continue

                if top["cz"] <= bottom["cz"] + min_z_gap:
                    continue

                info = self._surface_intersection_info(top, bottom)
                if not info:
                    continue

                if info["ratio"] < min_overlap_ratio:
                    continue

                key = (top["panel_name"], bottom["panel_name"])
                if key in seen:
                    continue

                seen.add(key)
                pairs.append((top, bottom))

        pairs.sort(
            key=lambda pair: (
                pair[0].get("panel_order", 99999),
                pair[1].get("panel_order", 99999),
            )
        )
        return pairs

    def _candidate_bottom_panels_for_chain(
        self,
        current_panel: Dict[str, Any],
        infos: List[Dict[str, Any]],
        current_rect: Tuple[float, float, float, float],
        used_names: Set[str],
        min_overlap_ratio: float = 0.03,
        min_z_gap: float = 1.0,
    ) -> List[Tuple[Dict[str, Any], Tuple[float, float, float, float]]]:
        candidates = []

        for candidate in infos:
            cand_name = candidate.get("panel_name")
            if not cand_name or cand_name in used_names:
                continue

            if candidate["cz"] >= current_panel["cz"] - min_z_gap:
                continue

            exact = self._surface_intersection_info(current_panel, candidate)
            if exact:
                next_rect = self._intersect_rects(current_rect, exact["rect"])
                if next_rect is not None:
                    overlap_area = self._rect_area(next_rect)
                    if overlap_area > 0:
                        cand_area = max(candidate["x"] * candidate["y"], 1e-9)
                        current_area = max(self._rect_area(current_rect), 1e-9)
                        smaller_area = min(cand_area, current_area)
                        ratio = overlap_area / smaller_area
                        if ratio >= min_overlap_ratio:
                            z_gap = current_panel["cz"] - candidate["cz"]
                            candidates.append((candidate, next_rect, z_gap, -overlap_area))
                            continue

            next_rect = self._intersect_rects(current_rect, self._panel_xy_rect(candidate))
            if next_rect is None:
                continue

            overlap_area = self._rect_area(next_rect)
            if overlap_area <= 0:
                continue

            cand_area = max(candidate["x"] * candidate["y"], 1e-9)
            current_area = max(self._rect_area(current_rect), 1e-9)
            smaller_area = min(cand_area, current_area)

            ratio = overlap_area / smaller_area
            if ratio < min_overlap_ratio:
                continue

            z_gap = current_panel["cz"] - candidate["cz"]
            candidates.append((candidate, next_rect, z_gap, -overlap_area))

        candidates.sort(
            key=lambda item: (
                item[2],
                item[3],
                item[0].get("panel_order", 99999),
            )
        )
        return [(item[0], item[1]) for item in candidates]

    def _build_surface_intersection_paths_from_panel(
        self,
        start_panel: Dict[str, Any],
        infos: List[Dict[str, Any]],
        min_overlap_ratio: float = 0.03,
        min_z_gap: float = 1.0,
    ) -> List[List[Dict[str, Any]]]:
        results: List[List[Dict[str, Any]]] = []

        def dfs(
            current_panel: Dict[str, Any],
            current_rect: Tuple[float, float, float, float],
            path: List[Dict[str, Any]],
            used_names: Set[str],
        ):
            next_candidates = self._candidate_bottom_panels_for_chain(
                current_panel=current_panel,
                infos=infos,
                current_rect=current_rect,
                used_names=used_names,
                min_overlap_ratio=min_overlap_ratio,
                min_z_gap=min_z_gap,
            )

            if not next_candidates:
                if len(path) >= 2:
                    results.append(path[:])
                return

            extended = False
            for next_panel, next_rect in next_candidates:
                next_name = next_panel["panel_name"]
                if next_name in used_names:
                    continue

                extended = True
                path.append(next_panel)
                used_names.add(next_name)

                dfs(
                    current_panel=next_panel,
                    current_rect=next_rect,
                    path=path,
                    used_names=used_names,
                )

                used_names.remove(next_name)
                path.pop()

            if not extended and len(path) >= 2:
                results.append(path[:])

        start_rect = self._panel_xy_rect(start_panel)
        dfs(
            current_panel=start_panel,
            current_rect=start_rect,
            path=[start_panel],
            used_names={start_panel["panel_name"]},
        )

        return results

    def build_stacked_panels_groups(self) -> List[List[Dict[str, Any]]]:
        infos = [i for i in self.current_solid_infos if i.get("panel_name")]
        if not infos:
            return []

        infos = sorted(
            infos,
            key=lambda x: (-x["cz"], x.get("panel_order", 99999)),
        )

        all_paths: List[List[Dict[str, Any]]] = []
        seen_signatures = set()

        for start_panel in infos:
            paths = self._build_surface_intersection_paths_from_panel(
                start_panel,
                infos,
                min_overlap_ratio=0.03,
                min_z_gap=1.0,
            )

            for path in paths:
                if len(path) < 2:
                    continue

                signature = tuple(item["panel_name"] for item in path)
                if signature in seen_signatures:
                    continue

                seen_signatures.add(signature)
                all_paths.append(path)

        filtered_paths: List[List[Dict[str, Any]]] = []
        signatures = [tuple(item["panel_name"] for item in p) for p in all_paths]

        for i, sig in enumerate(signatures):
            is_subpath = False

            for j, other_sig in enumerate(signatures):
                if i == j:
                    continue
                if len(other_sig) <= len(sig):
                    continue

                for k in range(0, len(other_sig) - len(sig) + 1):
                    if other_sig[k: k + len(sig)] == sig:
                        is_subpath = True
                        break

                if is_subpath:
                    break

            if not is_subpath:
                filtered_paths.append(all_paths[i])

        if filtered_paths:
            filtered_paths.sort(
                key=lambda grp: (
                    -len(grp),
                    -grp[0]["cz"],
                    min(item.get("panel_order", 99999) for item in grp),
                    tuple(item["panel_name"] for item in grp),
                )
            )
            return filtered_paths

        direct_pairs = self._get_direct_stacked_pairs(
            min_overlap_ratio=0.03,
            min_z_gap=1.0,
        )
        return [[top, bottom] for top, bottom in direct_pairs]

    def build_stacked_panels_lines(self) -> List[str]:
        groups = self.build_stacked_panels_groups()
        lines: List[str] = []
        seen = set()

        for group in groups:
            names = [item["panel_name"] for item in group if item.get("panel_name")]
            if len(names) < 2:
                continue

            line = " > ".join(names)
            if line in seen:
                continue

            seen.add(line)
            lines.append(line)

        return lines

    def build_intersected_panels_report(self) -> str:
        factor, unit = self._get_factor_and_unit()
        infos = [i for i in self.current_solid_infos if i.get("panel_name")]

        if not infos:
            return "No panels loaded."

        lines: List[str] = []

        groups = self.build_stacked_panels_groups()

        if groups:
            lines.append("Direct Surface Intersections")
            lines.append("----------------------------")

            for group in groups:
                if len(group) < 2:
                    continue

                names = [item["panel_name"] for item in group if item.get("panel_name")]
                if len(names) < 2:
                    continue

                chain_label = " > ".join(names)

                common_rect = self._panel_xy_rect(group[0])
                method_used = "projected"
                display_z = group[0]["zmax"]

                for i in range(len(group) - 1):
                    top_panel = group[i]
                    bottom_panel = group[i + 1]
                    inter = self._surface_intersection_info(top_panel, bottom_panel)
                    if inter:
                        common_rect = self._intersect_rects(common_rect, inter["rect"])
                        display_z = inter.get("z", display_z)
                        if inter.get("method") == "occt_exact":
                            method_used = "occt_exact"
                    else:
                        common_rect = self._intersect_rects(common_rect, self._panel_xy_rect(bottom_panel))

                    if common_rect is None:
                        break

                if common_rect is None:
                    lines.append(f"{chain_label} : no common intersection surface")
                    lines.append("")
                    continue

                area = self._rect_area(common_rect)
                ix = (common_rect[1] - common_rect[0]) * factor
                iy = (common_rect[3] - common_rect[2]) * factor
                area_scaled = area * (factor ** 2)

                pts = self._surface_rect_points(common_rect, display_z, factor=factor)
                center_pt = (
                    ((common_rect[0] + common_rect[1]) / 2.0) * factor,
                    ((common_rect[2] + common_rect[3]) / 2.0) * factor,
                    display_z * factor,
                )

                lines.append(f"{chain_label} : Surface Points (A,B,C,D)")
                lines.append(f"A{pts[0]}")
                lines.append(f"B{pts[1]}")
                lines.append(f"C{pts[2]}")
                lines.append(f"D{pts[3]}")
                lines.append(f"center / nail point: {center_pt}")
                lines.append(
                    f"size: {ix:.1f} × {iy:.1f} {unit} | area: {area_scaled:.1f} {unit}² | method: {method_used}"
                )
                lines.append("")

        else:
            lines.append("Direct Surface Intersections")
            lines.append("----------------------------")
            lines.append("No direct surface intersections found.")
            lines.append("")

        lines.append("Intersected Panels By Top Panel")
        lines.append("-------------------------------")

        for top in sorted(infos, key=lambda x: x.get("panel_order", 99999)):
            below_items = []

            for bottom in infos:
                if top["panel_name"] == bottom["panel_name"]:
                    continue
                if top["cz"] <= bottom["cz"] + 1.0:
                    continue

                info = self._surface_intersection_info(top, bottom)
                if not info or info["ratio"] < 0.03:
                    continue

                below_items.append((bottom, info))

            below_items.sort(key=lambda item: item[0].get("panel_order", 99999))

            if below_items:
                lines.append(f"{top['panel_name']}:")
                for bottom, info in below_items:
                    pts = self._surface_rect_points(info["rect"], info.get("z", top["zmax"]), factor=factor)
                    center_pt = info.get("center")
                    if center_pt:
                        center_pt = (
                            center_pt[0] * factor,
                            center_pt[1] * factor,
                            center_pt[2] * factor,
                        )

                    lines.append(
                        f"  - {bottom['panel_name']} "
                        f"(overlap {info['ratio'] * 100:.1f}%, method {info.get('method', 'unknown')})"
                    )
                    lines.append(
                        f"    A{pts[0]}  B{pts[1]}  C{pts[2]}  D{pts[3]}"
                    )
                    if center_pt:
                        lines.append(f"    center / nail point {center_pt}")

        return "\n".join(lines).strip()

    def get_panel_top_face_points(
        self,
        info: Dict[str, Any],
        factor: float = 1.0,
    ) -> List[Tuple[float, float, float]]:
        z = info["zmax"] * factor
        return [
            (info["xmin"] * factor, info["ymin"] * factor, z),
            (info["xmax"] * factor, info["ymin"] * factor, z),
            (info["xmax"] * factor, info["ymax"] * factor, z),
            (info["xmin"] * factor, info["ymax"] * factor, z),
        ]

    def get_panel_dimensions_info(
        self,
        info: Dict[str, Any],
        factor: float = 1.0,
    ) -> Tuple[float, float, float, float]:
        length = max(info["x"], info["y"]) * factor
        width = min(info["x"], info["y"]) * factor
        height = info["z"] * factor
        surface = length * width
        return length, width, height, surface

    def _build_common_rect_for_group(
        self,
        group: List[Dict[str, Any]],
    ) -> Optional[Tuple[float, float, float, float]]:
        if len(group) < 2:
            return None

        common_rect = self._panel_xy_rect(group[0])
        for panel in group[1:]:
            common_rect = self._intersect_rects(common_rect, self._panel_xy_rect(panel))
            if common_rect is None:
                return None
        return common_rect

    def _build_nail_points_for_rect(
        self,
        rect: Tuple[float, float, float, float],
        z: float,
        count: int,
    ) -> List[Tuple[float, float, float]]:
        xmin, xmax, ymin, ymax = rect
        cx = (xmin + xmax) / 2.0
        cy = (ymin + ymax) / 2.0

        width = xmax - xmin
        depth = ymax - ymin

        if count <= 1:
            return [(cx, cy, z)]

        x_margin = width * 0.22
        y_margin = depth * 0.22
        if width >= depth:
            return [
                (xmin + x_margin, ymin + y_margin, z),
                (xmax - x_margin, ymin + y_margin, z),
                (cx, ymax - y_margin, z),
            ]

        return [
            (xmin + x_margin, ymin + y_margin, z),
            (xmax - x_margin, cy, z),
            (xmin + x_margin, ymax - y_margin, z),
        ]

    def calculate_nail_positions(self) -> List[Dict[str, Any]]:
        groups = self.build_stacked_panels_groups()
        if not groups:
            return []

        nail_items: List[Dict[str, Any]] = []
        seen = set()
        nail_surfaces: List[Dict[str, Any]] = []

        for group in groups:
            if len(group) < 2:
                continue

            common_rect = self._build_common_rect_for_group(group)
            if common_rect is None:
                continue

            nail_count = 1 if len(group) == 2 else 3
            group_label = " > ".join(p["panel_name"] for p in group)
            nail_layers = [
                ("top", float(group[0]["zmax"]) + 1.2),
                ("bottom", float(group[-1]["zmin"]) - 1.2),
            ]

            for side, draw_z in nail_layers:
                nail_surfaces.append(
                    {
                        "rect": common_rect,
                        "z": draw_z,
                        "side": side,
                        "group": group_label,
                        "count": nail_count,
                    }
                )

        accepted_surfaces: List[Dict[str, Any]] = []
        for surface in sorted(
            nail_surfaces,
            key=lambda item: (
                -int(item["count"]),
                -self._rect_area(item["rect"]),
                item["side"],
                round(item["z"], 3),
                item["group"],
            ),
        ):
            overlaps_existing = False
            for existing in accepted_surfaces:
                if surface["side"] != existing["side"]:
                    continue
                if abs(surface["z"] - existing["z"]) > 3.0:
                    continue

                overlap = self._intersect_rects(surface["rect"], existing["rect"])
                if overlap is None:
                    continue

                overlap_area = self._rect_area(overlap)
                smaller_area = max(
                    min(self._rect_area(surface["rect"]), self._rect_area(existing["rect"])),
                    1e-9,
                )
                if overlap_area / smaller_area >= 0.65:
                    overlaps_existing = True
                    break

            if not overlaps_existing:
                accepted_surfaces.append(surface)

        for surface in accepted_surfaces:
            count = min(int(surface["count"]), 3)
            for point in self._build_nail_points_for_rect(surface["rect"], surface["z"], count):
                key = (surface["side"], *tuple(round(v, 3) for v in point))
                if key in seen:
                    continue
                seen.add(key)

                nail_items.append(
                    {
                        "point": point,
                        "group": surface["group"],
                        "side": surface["side"],
                        "count_in_group": count,
                    }
                )

        return nail_items

    def check_nails_from_dialog(self, dialog: QDialog):
        if not self.current_solid_infos:
            dialog.accept()
            return

        nail_items = self.calculate_nail_positions()
        self.current_nail_positions = nail_items

        dialog.accept()

        self.viewer.set_nail_positions(nail_items)
        self.viewer.set_nails_visible(bool(nail_items))

        if nail_items:
            self.status_label.setText(f"Nails shown: {len(nail_items)}")
        else:
            self.status_label.setText("No nail positions found.")

    def show_panels_info_dialog(self):
        if not self.current_solid_infos:
            QMessageBox.information(self, "Panels Info", "No STEP file is currently loaded.")
            return

        factor, unit = self._get_factor_and_unit()

        dialog = QDialog(self)
        dialog.setWindowTitle("Panels Info")
        dialog.resize(1700, 780)

        outer_layout = QVBoxLayout(dialog)
        outer_layout.setContentsMargins(16, 16, 16, 16)
        outer_layout.setSpacing(12)

        title = QLabel("Panels Information")
        title.setStyleSheet("font-size: 18px; font-weight: 700; padding: 4px;")
        outer_layout.addWidget(title)

        content_layout = QHBoxLayout()
        content_layout.setSpacing(14)
        outer_layout.addLayout(content_layout)

        table = QTableWidget(dialog)
        table.setColumnCount(9)
        table.setHorizontalHeaderLabels(
            [
                "Panel",
                "Length",
                "Width",
                "Height",
                "Surface",
                "A",
                "B",
                "C",
                "D",
            ]
        )

        named_infos = sorted(
            [i for i in self.current_solid_infos if i.get("panel_name")],
            key=lambda x: x.get("panel_order", 99999),
        )

        table.setRowCount(len(named_infos))

        for row, info in enumerate(named_infos):
            panel_name = info.get("panel_name", f"panel-{row + 1}")
            length, width, height, surface = self.get_panel_dimensions_info(info, factor=factor)
            pts = self.get_panel_top_face_points(info, factor=factor)

            table.setItem(row, 0, QTableWidgetItem(panel_name))
            table.setItem(row, 1, QTableWidgetItem(f"{length:.2f} {unit}"))
            table.setItem(row, 2, QTableWidgetItem(f"{width:.2f} {unit}"))
            table.setItem(row, 3, QTableWidgetItem(f"{height:.2f} {unit}"))
            table.setItem(row, 4, QTableWidgetItem(f"{surface:.2f} {unit}²"))
            table.setItem(row, 5, QTableWidgetItem(f"({pts[0][0]:.2f}, {pts[0][1]:.2f}, {pts[0][2]:.2f})"))
            table.setItem(row, 6, QTableWidgetItem(f"({pts[1][0]:.2f}, {pts[1][1]:.2f}, {pts[1][2]:.2f})"))
            table.setItem(row, 7, QTableWidgetItem(f"({pts[2][0]:.2f}, {pts[2][1]:.2f}, {pts[2][2]:.2f})"))
            table.setItem(row, 8, QTableWidgetItem(f"({pts[3][0]:.2f}, {pts[3][1]:.2f}, {pts[3][2]:.2f})"))

        table.setWordWrap(True)
        table.verticalHeader().setDefaultSectionSize(50)

        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.Stretch)
        header.setSectionResizeMode(6, QHeaderView.Stretch)
        header.setSectionResizeMode(7, QHeaderView.Stretch)
        header.setSectionResizeMode(8, QHeaderView.Stretch)

        content_layout.addWidget(table, 3)

        right_widget = QWidget(dialog)
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)

        intersect_title = QLabel("Intersected Panels / Surface A-B-C-D")
        intersect_title.setStyleSheet("font-size: 16px; font-weight: 700; padding: 4px;")
        right_layout.addWidget(intersect_title)

        intersect_text = QTextEdit(dialog)
        intersect_text.setReadOnly(True)
        intersect_text.setPlainText(self.build_intersected_panels_report())
        right_layout.addWidget(intersect_text)

        content_layout.addWidget(right_widget, 2)

        buttons_row = QHBoxLayout()
        buttons_row.addStretch(1)

        check_nails_btn = QPushButton("Check Nails")
        check_nails_btn.setCursor(Qt.PointingHandCursor)
        check_nails_btn.setStyleSheet(
            """
            QPushButton {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 #111827,
                    stop:1 #374151
                );
                color: white;
                border: 1px solid #111827;
                border-radius: 14px;
                padding: 12px 22px;
                font-size: 13px;
                font-weight: 800;
            }
            QPushButton:hover {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 #1f2937,
                    stop:1 #4b5563
                );
            }
            QPushButton:pressed {
                padding-top: 13px;
                padding-bottom: 11px;
            }
            """
        )
        check_nails_btn.clicked.connect(lambda: self.check_nails_from_dialog(dialog))
        buttons_row.addWidget(check_nails_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        buttons_row.addWidget(close_btn)

        outer_layout.addLayout(buttons_row)

        dialog.exec()

    def _detect_linear_gaps_for_panels(
        self,
        panels: List[Dict[str, Any]],
        bb,
        deck_name: str,
        z_text_offset: float,
    ) -> List[Dict[str, Any]]:
        if len(panels) < 2:
            return []

        avg_x = sum(p["x"] for p in panels) / len(panels)
        avg_y = sum(p["y"] for p in panels) / len(panels)
        slats_run_along_x = avg_x >= avg_y

        gaps = []

        if slats_run_along_x:
            slats = sorted(panels, key=lambda p: p["cy"])
            min_gap = max(2.0, bb.ylen * 0.005)

            x_line = min(p["xmax"] for p in slats) - max(8.0, bb.xlen * 0.06)
            x_line = max(bb.xmin + 5.0, x_line)

            z_line = (
                max(p["zmax"] for p in slats) + z_text_offset
                if deck_name == "top"
                else min(p["zmin"] for p in slats) - z_text_offset
            )

            for a, b in zip(slats[:-1], slats[1:]):
                gap_start = a["ymax"]
                gap_end = b["ymin"]
                gap_len = gap_end - gap_start

                if gap_len <= min_gap:
                    continue

                margin = min(max(1.5, gap_len * 0.10), 8.0)
                start_pt = (x_line, gap_start + margin, z_line)
                end_pt = (x_line, gap_end - margin, z_line)

                if end_pt[1] <= start_pt[1]:
                    start_pt = (x_line, gap_start, z_line)
                    end_pt = (x_line, gap_end, z_line)

                gaps.append(
                    {
                        "start": start_pt,
                        "end": end_pt,
                        "length_mm": gap_len,
                        "axis": "y",
                        "gap_type": "top_panel_gap" if deck_name == "top" else "bottom_panel_gap",
                        "label_prefix": "(top)" if deck_name == "top" else "(bottom)",
                        "panel_a": a["panel_name"],
                        "panel_b": b["panel_name"],
                        "panel_a_order": a["panel_order"],
                        "panel_b_order": b["panel_order"],
                        "pair_label": f"{a['panel_name']}-{b['panel_name']}",
                    }
                )
        else:
            slats = sorted(panels, key=lambda p: p["cx"])
            min_gap = max(2.0, bb.xlen * 0.005)

            y_line = min(p["ymax"] for p in slats) - max(8.0, bb.ylen * 0.06)
            y_line = max(bb.ymin + 5.0, y_line)

            z_line = (
                max(p["zmax"] for p in slats) + z_text_offset
                if deck_name == "top"
                else min(p["zmin"] for p in slats) - z_text_offset
            )

            for a, b in zip(slats[:-1], slats[1:]):
                gap_start = a["xmax"]
                gap_end = b["xmin"]
                gap_len = gap_end - gap_start

                if gap_len <= min_gap:
                    continue

                margin = min(max(1.5, gap_len * 0.10), 8.0)
                start_pt = (gap_start + margin, y_line, z_line)
                end_pt = (gap_end - margin, y_line, z_line)

                if end_pt[0] <= start_pt[0]:
                    start_pt = (gap_start, y_line, z_line)
                    end_pt = (gap_end, y_line, z_line)

                gaps.append(
                    {
                        "start": start_pt,
                        "end": end_pt,
                        "length_mm": gap_len,
                        "axis": "x",
                        "gap_type": "top_panel_gap" if deck_name == "top" else "bottom_panel_gap",
                        "label_prefix": "(top)" if deck_name == "top" else "(bottom)",
                        "panel_a": a["panel_name"],
                        "panel_b": b["panel_name"],
                        "panel_a_order": a["panel_order"],
                        "panel_b_order": b["panel_order"],
                        "pair_label": f"{a['panel_name']}-{b['panel_name']}",
                    }
                )

        return gaps

    def detect_top_panel_spaces(self, infos: List[Dict[str, Any]], bb) -> List[Dict[str, Any]]:
        p1_top = [
            i for i in infos
            if i.get("panel_class") == "p1" and i.get("panel_layer") == "top"
        ]
        if len(p1_top) < 2:
            return []
        return self._detect_linear_gaps_for_panels(
            p1_top,
            bb,
            deck_name="top",
            z_text_offset=max(3.0, bb.zlen * 0.01),
        )

    def detect_bottom_panel_spaces(self, infos: List[Dict[str, Any]], bb) -> List[Dict[str, Any]]:
        p1_bottom = [
            i for i in infos
            if i.get("panel_class") == "p1" and i.get("panel_layer") == "bottom"
        ]
        if len(p1_bottom) < 2:
            return []
        return self._detect_linear_gaps_for_panels(
            p1_bottom,
            bb,
            deck_name="bottom",
            z_text_offset=max(6.0, bb.zlen * 0.02),
        )

    def detect_cubic_panel_spaces(self, infos: List[Dict[str, Any]], bb) -> List[Dict[str, Any]]:
        cubes = [i for i in infos if i.get("panel_class") == "p2"]
        if len(cubes) < 2:
            return []

        tol_y = max(6.0, bb.ylen * 0.03)
        row_groups = self._cluster_by_axis(cubes, "cy", tol_y)

        gaps = []
        min_gap = max(2.0, min(bb.xlen, bb.ylen) * 0.004)

        for row in row_groups:
            if len(row) < 2:
                continue

            row_sorted = sorted(row, key=lambda c: c["cx"])
            row_avg_zmax = sum(c["zmax"] for c in row_sorted) / len(row_sorted)
            z_line = row_avg_zmax + max(8.0, bb.zlen * 0.025)
            y_line = sum(c["cy"] for c in row_sorted) / len(row_sorted)

            for a, b in zip(row_sorted[:-1], row_sorted[1:]):
                gap_x1 = a["xmax"]
                gap_x2 = b["xmin"]
                gap_len = gap_x2 - gap_x1

                if gap_len <= min_gap:
                    continue

                overlap_y = min(a["ymax"], b["ymax"]) - max(a["ymin"], b["ymin"])
                if overlap_y <= 0:
                    continue

                margin = min(max(1.5, gap_len * 0.10), 8.0)
                start_pt = (gap_x1 + margin, y_line, z_line)
                end_pt = (gap_x2 - margin, y_line, z_line)

                if end_pt[0] <= start_pt[0]:
                    start_pt = (gap_x1, y_line, z_line)
                    end_pt = (gap_x2, y_line, z_line)

                gaps.append(
                    {
                        "start": start_pt,
                        "end": end_pt,
                        "length_mm": gap_len,
                        "axis": "x",
                        "gap_type": "cubic_gap",
                        "label_prefix": "(cubic)",
                        "panel_a": a["panel_name"],
                        "panel_b": b["panel_name"],
                        "panel_a_order": a["panel_order"],
                        "panel_b_order": b["panel_order"],
                        "pair_label": f"{a['panel_name']}-{b['panel_name']}",
                    }
                )

        return gaps
