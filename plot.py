import sys
import os
import json
import numpy as np
import pandas as pd
from PyQt5 import QtWidgets, QtCore, QtGui
import pyarrow.parquet as pq
import pyqtgraph as pg
import faulthandler
from typing import Optional

CSV_FILE = "monitor_log.csv"
CONFIG_FILE = "plot_config.json"

PARQUET_FILE = "./tool-temp/monitor_log.parquet"
TIME_FILE = "./tool-temp/monitor_log.last_time"
CRASHTRACE_FILE = "./tool-temp/crash_trace.log"

class DraggableTextItem(pg.TextItem):
    """
    A text label item displayed on the plot that can be repositioned by dragging.
    Used to show the Y value at the cursor. Lockable to prevent movement.
    """
    
    def __init__(self, text, frame, color='w'):
        super().__init__(text, color=color, anchor=(0, 1))
        self.setZValue(2)
        self.setFlag(self.ItemIsMovable)
        self.ylabel_locked = False
        self.frame = frame
        self.plot_window = frame.plot_window

    def mousePressEvent(self, ev):
        if not self.plot_window.cursor_locked and self.plot_window and self.frame:
            plot_widget = self.frame.plot_widget
            self.plot_window.on_mouse_click(ev, plot_widget)
        super().mousePressEvent(ev)

    def mouseReleaseEvent(self, ev):
        super().mouseReleaseEvent(ev)
        self.ylabel_locked = True  # 🔒 Lock Y-label position after drag is detected

class DraggablePlotFrame(QtWidgets.QFrame):
    """
    Container for a single plot. Supports reordering via drag-and-drop and stores its UI elements.
    """
    
    def __init__(self, plot_window, plot_widget, var_name, parent=None):
        super().__init__(parent)
        self.plot_window = plot_window
        self.plot_widget = plot_widget
        self.var_name = var_name
        self.v_line = next(item for item in plot_widget.items() if isinstance(item, pg.InfiniteLine))
        self.cross_dot = next(item for item in plot_widget.items() if isinstance(item, pg.ScatterPlotItem))

        self.setFrameShape(QtWidgets.QFrame.Box)
        self.setLineWidth(1)
        self.setAcceptDrops(True)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.drag_bar = QtWidgets.QLabel("⇅ Drag to reorder")
        self.drag_bar.setStyleSheet("background-color: lightgray; padding: 4px;")
        self.drag_bar.setCursor(QtCore.Qt.OpenHandCursor)
        layout.addWidget(self.drag_bar)
        layout.addWidget(plot_widget)

        self.drag_bar.mousePressEvent = self.mousePressEvent
        self.drag_bar.mouseMoveEvent = self.mouseMoveEvent

        self.y_label = DraggableTextItem("", frame=self, color='w')
        self.plot_widget.addItem(self.y_label)  # Display on all frames

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.drag_start_pos = event.pos()

    def mouseMoveEvent(self, event):
        if event.buttons() & QtCore.Qt.LeftButton:
            if (event.pos() - self.drag_start_pos).manhattanLength() >= QtWidgets.QApplication.startDragDistance():
                drag = QtGui.QDrag(self)
                mime_data = QtCore.QMimeData()
                mime_data.setText("plot")
                drag.setMimeData(mime_data)
                drag.setPixmap(self.grab())
                drag.setHotSpot(event.pos())
                drag.exec_(QtCore.Qt.MoveAction)

    def dragEnterEvent(self, event):
        if event.mimeData().text() == "plot":
            event.acceptProposedAction()

    def dropEvent(self, event):
        source = event.source()
        if source and source != self:
            layout = self.parent().layout()
            source_index = layout.indexOf(source)
            target_index = layout.indexOf(self)
            layout.insertWidget(target_index, source)
            self.plot_window.reorder_plot_data()

class PlotWindow(QtWidgets.QMainWindow):
    """
    Main GUI window that visualizes CSV waveform data using PyQt and pyqtgraph.
    Allows plot interaction, syncing, delta calculation, and cursor tracking.
    """
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Real-time Waveform Viewer")
        self.resize(1200, 800)
        self.data = None
        self.plot_widgets = [] 
        self.current_time = 0.0
        self.cursor_locked = False
        self.delta_points = []
        self.delta_lines = []
        self.delta_plot = None
        self.syncing = False
        self.active_frame = None
        self.initializing = True

        self.main_widget = QtWidgets.QWidget()
        self.main_layout = QtWidgets.QVBoxLayout(self.main_widget)

        self.init_labels()
        self.init_buttons()
        self.init_plot_area()
        self.init_menu()
        self.setCentralWidget(self.main_widget)

        self.plots = []
        self.frames = []
        self.plot_order = []
        self.load_data_and_create_plots()

        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.update_plot)
        self.timer.start(1000)

    def init_menu(self):
        menu_bar = self.menuBar()
        help_menu = menu_bar.addMenu("Help")

        about_action = QtWidgets.QAction("About", self)
        about_action.triggered.connect(self.show_about_dialog)
        help_menu.addAction(about_action)

    def init_labels(self):
        font = QtWidgets.QLabel().font()
        font.setPointSize(11)
        self.time_label = QtWidgets.QLabel("Time: 0.000000  Y: 0.000000 (Var: -)")
        self.time_label.setFont(font)
        self.cursor_info_label = QtWidgets.QLabel("Δt=0.000 Δy=0.000 (X1=0.000 X2=0.000 Y1=0.000 Y2=0.000)")
        self.cursor_info_label.setFont(font)
        self.main_layout.addWidget(self.time_label)
        self.main_layout.addWidget(self.cursor_info_label)

    def init_buttons(self):
        button_layout = QtWidgets.QHBoxLayout()
        for label, callback in [
            ("Auto Fit (X & Y)", self.auto_fit_axes),
            ("⬅ Prev Edge", lambda: self.jump_to_edge(-1)),
            ("Next Edge ➡", lambda: self.jump_to_edge(1)),
            ("Clear Δ", self.clear_delta_lines)
        ]:
            btn = QtWidgets.QPushButton(label)
            btn.clicked.connect(callback)
            button_layout.addWidget(btn)
        self.main_layout.addLayout(button_layout)

    def init_plot_area(self):
        self.scroll_area = QtWidgets.QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.plot_container = QtWidgets.QWidget()
        self.plot_layout = QtWidgets.QVBoxLayout(self.plot_container)
        self.plot_layout.setSpacing(10)
        self.plot_layout.setContentsMargins(5, 5, 5, 5)
        self.scroll_area.setWidget(self.plot_container)
        self.main_layout.addWidget(self.scroll_area)

    def closeEvent(self, event):
        """
        Automatically called when the main window is closed.
        Saves plot config including current_time.
        """
        self.save_plot_config()
        event.accept()  # ensure the window closes

    def show_about_dialog(self):
        QtWidgets.QMessageBox.about(self, "About", 
            "Real-time Waveform Viewer\n"
            "Version 1.0.0\n\n"
            "Licensed under the MIT License.\n"
            "© 2025 misawa-san\n"
            "GitHub: https://github.com/misawa-san/")

    def refresh_parquet_from_csv(self):
        if not os.path.exists(CSV_FILE) or not os.path.exists(TIME_FILE):
            return
        try:
            with open(TIME_FILE) as tf:
                last_time = float(tf.read().strip())

            df_csv = pd.read_csv(CSV_FILE)
            df_csv.rename(columns={df_csv.columns[0]: "time"}, inplace=True)
            df_new = df_csv[df_csv["time"] > last_time]

            if not df_new.empty:
                df_parquet = pd.read_parquet(PARQUET_FILE)
                df_combined = pd.concat([df_parquet, df_new], ignore_index=True)
                df_combined.to_parquet(PARQUET_FILE, index=False)

                with open(TIME_FILE, "w") as tf:
                    tf.write(str(df_csv["time"].max()))
                print("[INFO] Real-time Parquet update applied.")
        except Exception as e:
            print(f"[Error] Parquet refresh failed: {e}")

    def load_data_window(self, center_time: float, window: float = 5.0) -> pd.DataFrame:
        """
        Load a windowed time range of data centered around `center_time` from the Parquet file.
        Only rows with 'time' in [center_time - window, center_time + window] are loaded.
        """
        try:
            self.refresh_parquet_from_csv()
            table = pq.read_table(PARQUET_FILE, filters=[
                ('time', '>=', center_time - window),
                ('time', '<=', center_time + window)
            ])
            return table.to_pandas()
        except Exception as e:
            print(f"[Error] Failed to load Parquet data window: {e}")
            return pd.DataFrame()

    def make_sync_handler(self, source_plot_item):
        def handler():
            if self.syncing or self.initializing:
                return
            self.syncing = True
            try:
                vb = source_plot_item.getViewBox()
                xmin, xmax = vb.viewRange()[0]
                center = (xmin + xmax) / 2
                window = (xmax - xmin) / 2 + 1.0

                self.data = self.load_data_window(center_time=center, window=window)
                self.redraw_all_plots()

                # 🛠 X axis sync to all plots
                for frame in self.frames:
                    frame.plot_widget.setXRange(xmin, xmax, padding=0)
            except Exception as e:
                print(f"[Error] Failed to sync and reload data: {e}")
            finally:
                self.syncing = False
        return handler

    def finish_initialization(self):
        self.initializing = False

    def load_data_and_create_plots(self):
        try:
            self.data = self.load_data_window(center_time=self.current_time, window=60.0)
            if self.data is None or self.data.empty:
                print("[Error] No data loaded. Aborting plot creation.")
                return

            # Load saved config (MUST be done BEFORE computing ranges)
            self.plot_order = self.load_plot_config() or list(self.data.columns[1:])

            # Require one dummy plot widget to calculate view range
            temp_pw = pg.PlotWidget()
            vb = temp_pw.getViewBox()
            if self.x_range:
                vb.setXRange(self.x_range[0], self.x_range[1], padding=0)
            else:
                # fallback to default centered range
                vb.setXRange(self.current_time - 60, self.current_time + 60)

            xmin, xmax = vb.viewRange()[0]
            center = (xmin + xmax) / 2
            window = (xmax - xmin) / 2 + 1.0
            self.data = self.load_data_window(center_time=center, window=window)

        except Exception as e:
            print(f"[Error] Failed to load CSV: {e}")
            return

        time_col = self.data.columns[0]

        for col in self.plot_order:
            if col not in self.data.columns:
                continue
            pw = pg.PlotWidget()
            pw.setMinimumHeight(200)
            pw.plot(self.data[time_col], self.data[col], pen='y', autoDownsample=True)
            pw.setTitle(col)
            pw.setLabel('bottom', 'Time (s)')
            pw.getAxis('left').setWidth(80)

            y = self.data[col].to_numpy()
            ymin, ymax = self.calculate_y_range(y)
            pw.setYRange(ymin, ymax)

            pw.plotItem.showGrid(x=True, y=True)

            v_line = pg.InfiniteLine(angle=90, movable=False, pen='r')
            cross_dot = pg.ScatterPlotItem(pen=None, brush='w', size=10)
            pw.addItem(v_line, ignoreBounds=True)
            pw.addItem(cross_dot)

            frame = DraggablePlotFrame(self, pw, col)
            self.plot_layout.addWidget(frame)

            self.plot_widgets.append(pw)
            self.plots.append(pw)
            self.frames.append(frame)

            pw.scene().sigMouseMoved.connect(lambda pos, f=frame: self.handle_mouse_move(pos, f))
            pw.scene().sigMouseClicked.connect(lambda e, p=pw: self.on_mouse_click(e, p))
            pw.plotItem.sigXRangeChanged.connect(self.make_sync_handler(pw.plotItem))

        # After all plot widgets are created
        if self.x_range:
            for pw in self.plot_widgets:
                pw.setXRange(self.x_range[0], self.x_range[1], padding=0)

        QtCore.QTimer.singleShot(1000, self.finish_initialization)

    def update_top_label(self) -> None:
        """
        Update the top status label and per-frame Y-value label using current cursor position.
        Intended to reflect the value under the cursor across all plots.
        """
        if self.data is None or self.active_frame is None:
            return
        var = self.active_frame.var_name
        idx = (self.data[self.data.columns[0]] - self.current_time).abs().idxmin()
        x_actual = self.data.iloc[idx, 0]
        y_val = self.data[var].iloc[idx]
        self.time_label.setText(f"Time: {x_actual:.6f}  Y: {y_val:.6f} (Var: {var})")

        for frame in self.frames:
            y_val_i = self.data[frame.var_name].iloc[idx]
            self.update_y_label(frame, x_actual, y_val_i)

    def update_y_label(self, frame: DraggablePlotFrame, x: float, y: float) -> None:
        frame.y_label.setText(f"{y:.2f}")
        if not frame.y_label.ylabel_locked:
            frame.y_label.setPos(x, y)

    def update_plot(self) -> None:
        """
        Periodically reload the CSV file and update all plots if data has changed.
        Also refreshes delta markers and updates the top info label.
        """
        try:
            # Dynamically determine visible X range and reload only necessary data
            vb = self.plot_widgets[0].getViewBox()
            xmin, xmax = vb.viewRange()[0]
            center = (xmin + xmax) / 2
            window = (xmax - xmin) / 2 + 1.0
            self.data = self.load_data_window(center_time=center, window=window)

        except Exception as e:
            print(f"[Error] Failed to reload CSV: {e}")
            return

        self.redraw_all_plots()

        # Redraw delta lines if necessary
        if self.delta_plot and len(self.delta_points) == 2:
            self.draw_delta_lines(self.delta_plot)

        # Update top label according to active plot
        self.update_top_label()

    def redraw_all_plots(self) -> None:
        if self.data is None:
            return
        time_col = self.data.columns[0]

        # Calculate downsampling factor based on plot area width (e.g., twice the pixel width)
        view_width_px = self.width()
        max_points = view_width_px * 2  # Add a safety margin

        for frame in self.frames:
            pw = frame.plot_widget

            pw.plotItem.blockSignals(True)
            var = frame.var_name
            x = self.data[time_col].to_numpy()
            y = self.data[var].to_numpy()

            pw.clear()

            # Downsample data if necessary
            if len(x) > max_points:
                stride = len(x) // max_points
                x_ds = x[::stride]
                y_ds = y[::stride]
            else:
                x_ds = x
                y_ds = y
            pw.plot(x_ds, y_ds, pen='y')

            if frame.v_line not in pw.items():
                pw.addItem(frame.v_line, ignoreBounds=True)
            if frame.cross_dot not in pw.items():
                pw.addItem(frame.cross_dot)
            frame.v_line.setPos(self.current_time)

            if frame.y_label not in pw.items():
                pw.addItem(frame.y_label)

            pw.plotItem.blockSignals(False)

        if self.delta_plot and self.delta_points and len(self.delta_points) == 2:
            self.draw_delta_lines(self.delta_plot)

    def calculate_y_range(self, y: np.ndarray) -> tuple[float, float]:
        """
        Compute Y-axis range with padding for visualization.
        Returns (ymin, ymax) with ±20% margin based on value magnitude.
        """
        ymin = np.min(y)
        ymax = np.max(y)
        if np.isnan(ymin) or np.isnan(ymax):
            return -0.5, 1.5
        padding = max(abs(ymin), abs(ymax)) * 0.2
        return ymin - padding, ymax + padding

    def auto_fit_axes(self) -> None:
        if self.data is None:
            return
        time_col = self.data.columns[0]
        xmin = self.data[time_col].min()
        xmax = self.data[time_col].max()

        for frame in self.frames:
            var = frame.var_name
            plot = frame.plot_widget
            try:
                # Fit X-axis with padding and auto-enable Y-axis scaling
                plot.setXRange(xmin, xmax, padding=0.05)
                y = self.data[var].to_numpy()
                ymin, ymax = self.calculate_y_range(y)
                plot.setYRange(ymin, ymax)

            except Exception as e:
                print(f"[Warning] Failed to set Y-axis scale for {var}: {e}")

    def jump_to_edge(self, direction: int) -> None:
        """
        Jump to the next or previous edge (change) in signal data across all plots.
        Searches for where the variable value changes and updates the cursor accordingly.
        """
        if self.data is None:
            return
        time_col = self.data.columns[0]

        idx_now = np.argmin(np.abs(self.data[time_col] - self.current_time))
        candidates = []
        for frame in self.frames:
            vals = self.data[frame.var_name].to_numpy()
            changes = np.nonzero(np.diff(vals))[0] + 1
            if direction > 0:
                nexts = changes[changes > idx_now]
                if nexts.size > 0:
                    candidates.append(nexts[0])
            else:
                prevs = changes[changes < idx_now]
                if prevs.size > 0:
                    candidates.append(prevs[-1])
        if not candidates:
            return
        new_idx = min(candidates) if direction > 0 else max(candidates)
        self.current_time = self.data.iloc[new_idx][time_col]
        for frame in self.frames:
            x = self.current_time
            y = self.data[frame.var_name].iloc[new_idx]
            frame.v_line.setPos(x)
            frame.cross_dot.setData([x], [y])

        self.update_top_label()

    def clear_delta_lines(self) -> None:
        for frame in self.frames:
            for line in self.delta_lines:
                frame.plot_widget.removeItem(line)
        self.delta_lines.clear()
        self.delta_points.clear()
        self.cursor_info_label.setText("Δt=0.000 Δy=0.000 (X1=0.000 X2=0.000 Y1=0.000 Y2=0.000)")

    def on_mouse_click(self, event, plot_widget):
        """
        Handle mouse click interaction on the plot.
        - CTRL+Click adds a delta measurement point.
        - Normal click moves the red line and activates the clicked plot.
        """
        if event.modifiers() == QtCore.Qt.ControlModifier:
            if plot_widget.sceneBoundingRect().contains(event.scenePos()):
                x = plot_widget.plotItem.vb.mapSceneToView(event.scenePos()).x()
                self.delta_points.append(x)
                self.delta_plot = plot_widget
                if len(self.delta_points) > 2:
                    self.clear_delta_lines()
                    self.delta_points = [x]
                self.draw_delta_lines(plot_widget)
        else:
            # 🔁 Toggle lock state for cursor movement
            self.cursor_locked = not self.cursor_locked

            # 🔁 Set the clicked plot as active
            self.active_frame = next((f for f in self.frames if f.plot_widget == plot_widget), None)

            if self.active_frame:
                x = plot_widget.plotItem.vb.mapSceneToView(event.scenePos()).x()
                self.current_time = x

                time_col = self.data.columns[0]
                idx = (self.data[time_col] - x).abs().idxmin()
                x_actual = self.data.iloc[idx, 0]
                y_val = self.data[self.active_frame.var_name].iloc[idx]

                # 🔁 Update red line and white circle position for all plots
                for frame in self.frames:
                    frame.v_line.setPos(x_actual)
                    frame.cross_dot.setData([x_actual], [self.data[frame.var_name].iloc[idx]])
                    frame.y_label.ylabel_locked = False

                # 🔁 Update the label as well
                self.update_top_label()

    def draw_delta_lines(self, plot_widget: pg.PlotWidget) -> None:
        """
        Draw vertical and horizontal delta (Δ) lines between two selected X points on the plot.
        These lines visualize the Δt and Δy between cursor clicks.
        """
        if len(self.delta_points) != 2 or self.data is None:
            return

        # Remove existing delta lines
        for line in self.delta_lines:
            if line in plot_widget.items():
                plot_widget.removeItem(line)
        self.delta_lines.clear()

        frame = next((f for f in self.frames if f.plot_widget == plot_widget), None)
        if frame is None:
            return

        x1, x2 = sorted(self.delta_points)
        time_col = self.data.columns[0]
        idx1 = (self.data[time_col] - x1).abs().idxmin()
        idx2 = (self.data[time_col] - x2).abs().idxmin()
        x1, x2 = self.data[time_col].iloc[idx1], self.data[time_col].iloc[idx2]
        y1 = self.data[frame.var_name].iloc[idx1]
        y2 = self.data[frame.var_name].iloc[idx2]

        pen = pg.mkPen(color=(0, 180, 255), style=QtCore.Qt.DashLine, width=2)
        v1 = pg.InfiniteLine(pos=x1, angle=90, pen=pen)
        v2 = pg.InfiniteLine(pos=x2, angle=90, pen=pen)
        h1 = pg.InfiniteLine(pos=y1, angle=0, pen=pen)
        h2 = pg.InfiniteLine(pos=y2, angle=0, pen=pen)

        for line in (v1, v2, h1, h2):
            plot_widget.addItem(line)

        self.delta_lines = [v1, v2, h1, h2]

        self.cursor_info_label.setText(
            f"Δt={abs(x2 - x1):.3f}s Δy={y2 - y1:.3f} (X1={x1:.3f} X2={x2:.3f} Y1={y1:.3f} Y2={y2:.3f})"
        )

    def handle_mouse_move(self, pos, frame):
        """
        Track mouse movement across the plot.
        If cursor is not locked, update current_time and refresh the cursor indicators.
        """
        if self.data is None:
            return

        plot = frame.plot_widget

        if plot.sceneBoundingRect().contains(pos):
            x_hover = plot.plotItem.vb.mapSceneToView(pos).x()

            if not self.cursor_locked:
                # 🔓 Only update current_time if it is not locked
                self.current_time = x_hover
                self.active_frame = frame

        # ✅ Always draw based on current_time
        idx = (self.data[self.data.columns[0]] - self.current_time).abs().idxmin()
        x_actual = self.data.iloc[idx, 0]

        for f in self.frames:
            y_val = self.data[f.var_name].iloc[idx]
            f.v_line.setPos(x_actual)
            f.cross_dot.setData([x_actual], [y_val])

        self.update_top_label()

    def reorder_plot_data(self) -> None:
        self.plot_order = []
        self.frames = []
        self.plots = []
        for i in range(self.plot_layout.count()):
            frame = self.plot_layout.itemAt(i).widget()
            self.frames.append(frame)
            self.plot_order.append(frame.var_name)
            self.plots.append(frame.plot_widget)

            if frame.y_label not in frame.plot_widget.items():
                frame.plot_widget.addItem(frame.y_label)

        self.redraw_all_plots()
        self.update_top_label()
        self.save_plot_config()

    def save_plot_config(self) -> None:
        try:
            vb = self.plot_widgets[0].getViewBox()
            xmin, xmax = vb.viewRange()[0]

            config = {
                "plot_order": self.plot_order,
                "current_time": self.current_time,
                "x_range": [xmin, xmax]
            }

            with open(CONFIG_FILE, 'w') as f:
                json.dump(config, f)
        except Exception as e:
            print(f"[Error] Failed to save plot config: {e}")

    def load_plot_config(self) -> Optional[list[str]]:
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
                self.current_time = config.get("current_time", 0.0)
                self.x_range = config.get("x_range", None)
                return config.get("plot_order", [])
        except Exception:
            print("[DEBUG] Failed to load config.")
            return None

if __name__ == "__main__":
    # ✅ Ensure the tool-temp directory exists
    os.makedirs("./tool-temp", exist_ok=True)

    log_file = open(CRASHTRACE_FILE, "w")

    df_csv = []
    if not os.path.exists(PARQUET_FILE) or not os.path.exists(TIME_FILE):
        # First launch: either Parquet or last_time file is missing (or both)
        df_csv = pd.read_csv(CSV_FILE)
        df_csv.rename(columns={df_csv.columns[0]: "time"}, inplace=True)  # Treat the leftmost CSV column as 'time'

        df_csv.to_parquet(PARQUET_FILE, index=False)
        with open(TIME_FILE, "w") as tf:
            tf.write(str(df_csv["time"].max()))
        print("[INFO] Created Parquet and last_time from CSV.")

    else:
        # Subsequent runs: both Parquet and last_time exist → perform incremental update
        with open(TIME_FILE) as tf:
            last_time = float(tf.read().strip())

        df_csv = pd.read_csv(CSV_FILE)
        df_csv.rename(columns={df_csv.columns[0]: "time"}, inplace=True)  # Treat the leftmost CSV column as 'time'
        df_new = df_csv[df_csv["time"] > last_time]

        if not df_new.empty:
            df_parquet = pd.read_parquet(PARQUET_FILE)
            df_combined = pd.concat([df_parquet, df_new], ignore_index=True)
            df_combined.to_parquet(PARQUET_FILE, index=False)
            with open(TIME_FILE, "w") as tf:
                tf.write(str(df_csv["time"].max()))
            print("[INFO] Appended new rows to Parquet.")

    # Create default config file if it does not exist
    if not os.path.exists(CONFIG_FILE):
        xmin = df_csv["time"].min()
        xmax = df_csv["time"].max()
        config = {
            "plot_order": list(df_csv.columns[1:]),
            "current_time": xmin,
            "x_range": [xmin, xmax]
        }
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f)
        print("[INFO] Default config created.")

    faulthandler.enable(file=log_file)

    app = QtWidgets.QApplication(sys.argv)
    win = PlotWindow()
    win.show()
    sys.exit(app.exec_())
