import logging
from datetime import datetime
from typing import Dict, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from client import MotaremClient
from gui.models import AxisData, AxisSnapshot, ControllerData
from gui.widgets import AxisControlWidget, AxisWindow
from gui.workers import ConnectionThread, ControllerMonitor, DataLoader, ParameterUpdater

logger = logging.getLogger(__name__)


class MotaremMainWindow(QMainWindow):
    """Main window for Motarem GUI application."""

    def __init__(self):
        super().__init__()
        self.client = None
        self.data_loader = None
        self.monitor_thread = None
        self.parameter_updater = None
        self.axis_widgets: Dict[Tuple[str, str], AxisControlWidget] = {}
        self.axis_tree_items: Dict[Tuple[str, str], QTreeWidgetItem] = {}
        self.selected_axes = set()
        self.all_data: Dict[str, ControllerData] = {}
        self.separate_windows: Dict[Tuple[str, str], AxisWindow] = {}

        self.setup_ui()
        self.setWindowTitle("Motarem Motor Controller")
        self.setMinimumSize(800, 600)

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        connection_group = QGroupBox("Connection")
        connection_layout = QHBoxLayout(connection_group)

        self.socket_path_input = QLineEdit("/tmp/slit_controller.sock")
        self.connect_button = QPushButton("Connect")
        self.disconnect_button = QPushButton("Disconnect")
        self.connection_status = QLabel("Disconnected")

        self.connect_button.clicked.connect(self.connect_to_server)
        self.disconnect_button.clicked.connect(self.disconnect_from_server)
        self.disconnect_button.setEnabled(False)

        connection_layout.addWidget(QLabel("Socket Path:"))
        connection_layout.addWidget(self.socket_path_input)
        connection_layout.addWidget(self.connect_button)
        connection_layout.addWidget(self.disconnect_button)
        connection_layout.addWidget(self.connection_status)
        layout.addWidget(connection_group)

        self.content_splitter = QSplitter(Qt.Orientation.Horizontal)

        controllers_widget = QWidget()
        controllers_layout = QVBoxLayout(controllers_widget)
        controllers_layout.addWidget(QLabel("Controllers & Axes"))

        self.controllers_tree = QTreeWidget()
        self.controllers_tree.setHeaderLabels(["Name", "Status"])
        self.controllers_tree.itemChanged.connect(self.on_tree_item_changed)
        controllers_layout.addWidget(self.controllers_tree)

        button_layout = QHBoxLayout()
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh_controllers)
        self.refresh_button.setEnabled(False)

        self.select_all_button = QPushButton("Select All")
        self.select_all_button.clicked.connect(self.select_all_axes)
        self.select_all_button.setEnabled(False)

        self.deselect_all_button = QPushButton("Deselect All")
        self.deselect_all_button.clicked.connect(self.deselect_all_axes)
        self.deselect_all_button.setEnabled(False)

        button_layout.addWidget(self.refresh_button)
        button_layout.addWidget(self.select_all_button)
        button_layout.addWidget(self.deselect_all_button)
        controllers_layout.addLayout(button_layout)

        self.content_splitter.addWidget(controllers_widget)

        self.axis_controls_widget = QTabWidget()
        self.axis_controls_widget.setTabsClosable(True)
        self.axis_controls_widget.tabCloseRequested.connect(self.close_axis_tab)
        self.content_splitter.addWidget(self.axis_controls_widget)
        self.content_splitter.setSizes([200, 600])

        self.progress_label = QLabel("Ready")
        layout.addWidget(self.progress_label)
        layout.addWidget(self.content_splitter, stretch=1)

        log_group = QGroupBox("Log")
        log_layout = QVBoxLayout(log_group)
        self.log_text = QTextEdit()
        self.log_text.setFixedHeight(80)
        self.log_text.setReadOnly(True)
        log_layout.addWidget(self.log_text)

        clear_log_button = QPushButton("Clear Log")
        clear_log_button.clicked.connect(self.log_text.clear)
        log_layout.addWidget(clear_log_button)
        layout.addWidget(log_group, stretch=0)

    def log_message(self, message: str):
        self.log_text.append(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

    def connect_to_server(self):
        socket_path = self.socket_path_input.text()
        self.connection_thread = ConnectionThread(socket_path)
        self.connection_thread.connected.connect(self.on_connection_result)
        self.connection_thread.error.connect(self.on_connection_error)
        self.connection_thread.start()

        self.connect_button.setEnabled(False)
        self.connection_status.setText("Connecting...")
        self.progress_label.setText("Connecting to server...")

    def on_connection_result(self, success: bool):
        if success:
            socket_path = self.socket_path_input.text()
            self.client = MotaremClient(socket_path)
            try:
                self.client.connect()
            except Exception as e:
                self.client = None
                self.on_connection_error(str(e))
                self.connection_status.setText("Connection Failed")
                self.connection_status.setStyleSheet("color: red; font-weight: bold;")
                self.connect_button.setEnabled(True)
                self.disconnect_button.setEnabled(False)
                self.progress_label.setText("Connection failed")
                return

            self.connection_status.setText("Connected")
            self.connection_status.setStyleSheet("color: green; font-weight: bold;")
            self.connect_button.setEnabled(False)
            self.disconnect_button.setEnabled(True)
            self.log_message("Connected to Motarem server")
            self.load_all_data()
        else:
            self.connection_status.setText("Connection Failed")
            self.connection_status.setStyleSheet("color: red; font-weight: bold;")
            self.connect_button.setEnabled(True)
            self.progress_label.setText("Connection failed")

    def on_connection_error(self, error_message: str):
        self.log_message(f"Connection error: {error_message}")
        self.progress_label.setText(f"Error: {error_message}")
        QMessageBox.critical(
            self, "Connection Error", f"Failed to connect: {error_message}"
        )

    def disconnect_from_server(self):
        if self.monitor_thread:
            self.monitor_thread.stop()
            self.monitor_thread.wait()
            self.monitor_thread = None

        if self.parameter_updater:
            self.parameter_updater.stop()
            self.parameter_updater.wait(3000)
            self.parameter_updater = None

        if self.client:
            try:
                self.client.disconnect()
            except Exception as e:
                self.log_message(f"Disconnect error: {e}")
            self.client = None

        self.connection_status.setText("Disconnected")
        self.connection_status.setStyleSheet("color: red;")
        self.connect_button.setEnabled(True)
        self.disconnect_button.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.select_all_button.setEnabled(False)
        self.deselect_all_button.setEnabled(False)

        self.axis_controls_widget.clear()
        self.axis_widgets.clear()
        self.axis_tree_items.clear()
        self.selected_axes.clear()
        self.controllers_tree.clear()
        self.all_data.clear()

        self.log_message("Disconnected from server")
        self.progress_label.setText("Disconnected")

    def on_tree_item_changed(self, item, column):
        if column != 0:
            return
        controller_axis = item.data(0, Qt.ItemDataRole.UserRole)
        if controller_axis is None:
            return

        controller, axis = controller_axis
        if item.checkState(0) == Qt.CheckState.Checked:
            self.selected_axes.add((controller, axis))
            self.add_axis_tab(controller, axis)
        else:
            self.selected_axes.discard((controller, axis))
            self.remove_axis_tab(controller, axis)

    def _axis_data_for(self, controller: str, axis: str) -> AxisData:
        controller_data = self.all_data.get(controller, ControllerData())
        return controller_data.axis_data.get(axis, AxisData())

    def add_axis_tab(self, controller: str, axis: str):
        if (controller, axis) not in self.axis_widgets:
            widget = AxisControlWidget(
                self.client,
                controller,
                axis,
                self._axis_data_for(controller, axis),
                self,
            )
            self.axis_widgets[(controller, axis)] = widget
            self.axis_controls_widget.addTab(widget, f"{controller}::{axis}")
            self.update_monitor_thread()

    def remove_axis_tab(self, controller: str, axis: str):
        widget = self.axis_widgets.get((controller, axis))
        if widget:
            if (controller, axis) in self.separate_windows:
                self.separate_windows[(controller, axis)].close()
                del self.separate_windows[(controller, axis)]

            index = self.axis_controls_widget.indexOf(widget)
            if index >= 0:
                self.axis_controls_widget.removeTab(index)
            del self.axis_widgets[(controller, axis)]
            self.update_monitor_thread()

    def close_axis_tab(self, index):
        widget = self.axis_controls_widget.widget(index)
        if widget:
            for (controller, axis), existing_widget in self.axis_widgets.items():
                if existing_widget == widget:
                    self.selected_axes.discard((controller, axis))
                    self.update_tree_item_check_state(controller, axis, False)
                    break

    def update_tree_item_check_state(self, controller: str, axis: str, checked: bool):
        axis_item = self.axis_tree_items.get((controller, axis))
        if axis_item:
            axis_item.setCheckState(
                0, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
            )

    def pop_out_axis(self, controller: str, axis: str):
        widget = self.axis_widgets.get((controller, axis))
        if widget and (controller, axis) not in self.separate_windows:
            index = self.axis_controls_widget.indexOf(widget)
            if index >= 0:
                self.axis_controls_widget.removeTab(index)

            window = AxisWindow(widget, controller, axis, self)
            self.separate_windows[(controller, axis)] = window
            window.show()

    def return_axis_to_main(self, controller: str, axis: str):
        if (controller, axis) in self.separate_windows:
            window = self.separate_windows[(controller, axis)]
            widget = window.axis_widget
            del self.separate_windows[(controller, axis)]
            widget.is_in_separate_window = False
            window.takeCentralWidget()
            window.close()
            self.axis_controls_widget.addTab(widget, f"{controller}::{axis}")

    def select_all_axes(self):
        for i in range(self.controllers_tree.topLevelItemCount()):
            controller_item = self.controllers_tree.topLevelItem(i)
            for j in range(controller_item.childCount()):
                controller_item.child(j).setCheckState(0, Qt.CheckState.Checked)

    def deselect_all_axes(self):
        for i in range(self.controllers_tree.topLevelItemCount()):
            controller_item = self.controllers_tree.topLevelItem(i)
            for j in range(controller_item.childCount()):
                controller_item.child(j).setCheckState(0, Qt.CheckState.Unchecked)

    def load_all_data(self):
        self.data_loader = DataLoader(self.socket_path_input.text())
        self.data_loader.data_loaded.connect(self.on_data_loaded)
        self.data_loader.progress.connect(self.on_loading_progress)
        self.data_loader.error.connect(self.on_loading_error)
        self.data_loader.start()

    def on_data_loaded(self, data: Dict[str, ControllerData]):
        self.all_data = data
        self.refresh_ui()
        self.refresh_button.setEnabled(True)
        self.select_all_button.setEnabled(True)
        self.deselect_all_button.setEnabled(True)
        self.progress_label.setText("Ready")
        self.log_message(f"Loaded {len(data)} controllers with all parameters")

    def on_loading_progress(self, message: str):
        self.progress_label.setText(message)

    def on_loading_error(self, error: str):
        self.log_message(f"Loading error: {error}")
        self.progress_label.setText(f"Error: {error}")

    def refresh_ui(self):
        self.controllers_tree.clear()
        self.axis_tree_items.clear()

        for controller, controller_data in self.all_data.items():
            controller_item = QTreeWidgetItem(
                [controller, f"{len(controller_data.axes)} axes"]
            )
            controller_item.setFlags(
                controller_item.flags() | Qt.ItemFlag.ItemIsAutoTristate
            )
            self.controllers_tree.addTopLevelItem(controller_item)

            for axis in controller_data.axes:
                axis_item = QTreeWidgetItem([axis, "Ready"])
                axis_item.setFlags(
                    axis_item.flags() | Qt.ItemFlag.ItemIsUserCheckable
                )
                axis_item.setCheckState(0, Qt.CheckState.Unchecked)
                axis_item.setData(0, Qt.ItemDataRole.UserRole, (controller, axis))
                controller_item.addChild(axis_item)
                self.axis_tree_items[(controller, axis)] = axis_item

                if (controller, axis) in self.selected_axes:
                    axis_item.setCheckState(0, Qt.CheckState.Checked)

        self.controllers_tree.expandAll()

    def update_monitor_thread(self):
        if self.monitor_thread and self.monitor_thread.isRunning():
            self.monitor_thread.set_monitored_axes(self.selected_axes)
        elif self.selected_axes:
            self.monitor_thread = ControllerMonitor(self.socket_path_input.text())
            self.monitor_thread.set_monitored_axes(self.selected_axes)
            self.monitor_thread.state_updated.connect(self.on_state_updated)
            self.monitor_thread.batch_state_updated.connect(self.on_batch_state_updated)
            self.monitor_thread.error.connect(self.log_message)
            self.monitor_thread.start()

        if self.client and (
            not self.parameter_updater or not self.parameter_updater.isRunning()
        ):
            self.parameter_updater = ParameterUpdater(self.socket_path_input.text())
            self.parameter_updater.parameters_updated.connect(self.on_parameters_updated)
            self.parameter_updater.error.connect(self.log_message)
            self.parameter_updater.start()

    def refresh_controllers(self):
        if not self.client:
            return
        self.log_message("Refreshing data from server...")
        self.load_all_data()

    def on_state_updated(self, controller: str, axis: str, state_data: AxisSnapshot):
        self._update_axis_state(controller, axis, state_data)

    def on_batch_state_updated(self, batch_data: Dict[Tuple[str, str], AxisSnapshot]):
        for (controller, axis), state_data in batch_data.items():
            self._update_axis_state(controller, axis, state_data)

    def _update_axis_state(
        self, controller: str, axis: str, state_data: AxisSnapshot
    ):
        widget = self.axis_widgets.get((controller, axis))
        if widget:
            widget.update_state(state_data)

        axis_item = self.axis_tree_items.get((controller, axis))
        if axis_item:
            status = state_data.state
            if state_data.is_faulted:
                status += " (FAULT)"
            elif state_data.is_moving:
                status += " (MOVING)"
            axis_item.setText(1, status)

    def on_parameters_updated(
        self, controller: str, axis: str, param_values: Dict[str, object]
    ):
        widget = self.axis_widgets.get((controller, axis))
        if widget:
            widget.update_status_parameters(param_values)

    def closeEvent(self, event):
        for window in list(self.separate_windows.values()):
            window.close()
        self.separate_windows.clear()

        if self.parameter_updater and self.parameter_updater.isRunning():
            self.parameter_updater.stop()
            self.parameter_updater.wait(3000)

        self.disconnect_from_server()
        event.accept()
