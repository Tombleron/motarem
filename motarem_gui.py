import sys
import time
import logging
from PyQt6.QtCore import QMutex, QMutexLocker

logger = logging.getLogger(__name__)
import traceback
from typing import Optional, Dict, List
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
    QLabel,
    QPushButton,
    QLineEdit,
    QComboBox,
    QGroupBox,
    QGridLayout,
    QSpinBox,
    QDoubleSpinBox,
    QTextEdit,
    QMessageBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QProgressBar,
    QCheckBox,
    QSplitter,
    QFrame,
    QTreeWidget,
    QTreeWidgetItem,
    QMenu,
)
from PyQt6.QtCore import QTimer, QThread, pyqtSignal, Qt
from PyQt6.QtGui import QFont, QPalette, QColor

from motarem_client import (
    MotaremClient,
    MovementParams,
    AxisStateInfo,
    AxisState,
    LimitSwitches,
    MotaremException,
)


class ConnectionThread(QThread):
    """Thread for handling Motarem connection to avoid blocking GUI."""

    connected = pyqtSignal(bool)
    error = pyqtSignal(str)

    def __init__(self, socket_path: str):
        super().__init__()
        self.socket_path = socket_path
        self.client = None

    def run(self):
        try:
            self.client = MotaremClient(self.socket_path)
            self.client.connect()
            # Test connection with ping
            self.client.ping()
            self.connected.emit(True)
        except Exception as e:
            self.error.emit(str(e))
            self.connected.emit(False)


class DataLoader(QThread):
    """Thread for loading all controllers, axes and parameters at connect."""

    data_loaded = pyqtSignal(
        dict
    )  # {controller: {axes: [...], axis_data: {axis: {...}}}}
    progress = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, client: MotaremClient):
        super().__init__()
        self.client = client

    def run(self):
        try:
            self.progress.emit("Loading controllers...")
            controllers = self.client.get_controllers()

            all_data = {}
            total_axes = 0

            # First pass: count axes for progress
            for controller in controllers:
                axes = self.client.get_axes(controller)
                total_axes += len(axes)

            processed_axes = 0

            for controller in controllers:
                self.progress.emit(f"Loading {controller}...")

                axes = self.client.get_axes(controller)
                axis_data = {}

                for axis in axes:
                    self.progress.emit(
                        f"Loading {controller}::{axis} parameters..."
                    )

                    try:
                        # Load all axis information
                        available_params = (
                            self.client.get_axis_available_params(
                                controller, axis
                            )
                        )
                        movement_params = self.client.get_axis_movement_params(
                            controller, axis
                        )

                        # Load parameter values
                        param_values = {}
                        for param in available_params:
                            try:
                                value = self.client.get_axis_attribute_value(
                                    controller, axis, param
                                )
                                param_values[param] = value
                            except:
                                param_values[param] = 0.0

                        axis_data[axis] = {
                            "available_params": available_params,
                            "movement_params": movement_params,
                            "param_values": param_values,
                        }

                        processed_axes += 1
                        progress_percent = (processed_axes / total_axes) * 100
                        self.progress.emit(
                            f"Loading parameters... {progress_percent:.0f}%"
                        )

                    except Exception as e:
                        self.error.emit(
                            f"Error loading {controller}::{axis}: {e}"
                        )

                all_data[controller] = {"axes": axes, "axis_data": axis_data}

            self.data_loaded.emit(all_data)
            self.progress.emit("Loading complete!")

        except Exception as e:
            self.error.emit(f"Failed to load data: {e}")


class AxisWindow(QMainWindow):
    """Separate window for axis control."""

    def __init__(
        self, client, controller: str, axis: str, axis_data: dict, main_window
    ):
        super().__init__()
        self.controller = controller
        self.axis = axis
        self.main_window = main_window

        self.setWindowTitle(f"Motarem - {controller}::{axis}")
        self.setMinimumSize(400, 600)

        # Create a new widget instance for the separate window
        self.axis_widget = AxisControlWidget(
            client, controller, axis, axis_data, main_window
        )
        self.axis_widget.is_in_separate_window = True
        self.setCentralWidget(self.axis_widget)

    def closeEvent(self, event):
        """Handle window close - return widget to main window."""
        if (self.controller, self.axis) in self.main_window.separate_windows:
            self.main_window.return_axis_to_main(self.controller, self.axis)
        event.accept()


class ParameterUpdater(QThread):
    """Thread for updating additional parameters without blocking UI."""

    parameters_updated = pyqtSignal(str, str, dict)  # controller, axis, param_values
    error = pyqtSignal(str)

    def __init__(self, client: MotaremClient):
        super().__init__()
        self.client = client
        self.running = False
        self.update_queue = []
        self.mutex = QMutex()

    def request_parameter_update(self, controller: str, axis: str, params: list):
        """Request parameter update for specific axis."""
        with QMutexLocker(self.mutex):
            # Remove any existing request for this axis to avoid duplicates
            self.update_queue = [(c, a, p) for c, a, p in self.update_queue
                               if not (c == controller and a == axis)]
            self.update_queue.append((controller, axis, params))

    def run(self):
        self.running = True
        while self.running:
            update_requests = []
            with QMutexLocker(self.mutex):
                update_requests = self.update_queue.copy()
                self.update_queue.clear()

            for controller, axis, params in update_requests:
                if not self.running:
                    break

                try:
                    param_values = {}
                    excluded_params = {"position", "state", "limit_switches"}

                    for param in params:
                        if param not in excluded_params and self.running:
                            try:
                                value = self.client.get_axis_attribute_value(
                                    controller, axis, param
                                )
                                param_values[param] = value
                            except Exception:
                                param_values[param] = "Error"

                    if param_values and self.running:
                        logger.debug(f"Parameter updater thread: updating {controller}::{axis} with {len(param_values)} parameters")
                        self.parameters_updated.emit(controller, axis, param_values)

                except Exception as e:
                    self.error.emit(f"Error updating parameters for {controller}::{axis}: {str(e)}")

            self.msleep(1000)  # Update parameters less frequently to reduce load

    def stop(self):
        self.running = False


class ControllerMonitor(QThread):
    """Thread for monitoring controller state."""

    state_updated = pyqtSignal(str, str, dict)  # controller, axis, state_data
    batch_state_updated = pyqtSignal(dict)  # {(controller, axis): state_data}
    error = pyqtSignal(str)

    def __init__(self, client: MotaremClient):
        super().__init__()
        self.client = client
        self.running = False
        self.monitored_axes = set()  # {(controller, axis)}
        self.update_interval = 250  # ms between updates
        self.batch_updates = True

    def set_monitored_axes(self, monitored_axes: set):
        self.monitored_axes = monitored_axes

    def run(self):
        self.running = True
        logger.debug(f"ControllerMonitor thread starting with {len(self.monitored_axes)} axes")
        while self.running:
            try:
                if self.batch_updates and len(self.monitored_axes) > 1:
                    # Batch mode - collect all updates and emit together
                    batch_data = {}
                    for controller, axis in self.monitored_axes:
                        if not self.running:
                            break
                        try:
                            position = self.client.get_axis_position(
                                controller, axis
                            )
                            state_info = self.client.get_axis_state_info(
                                controller, axis
                            )

                            state_data = {
                                "position": position,
                                "state": state_info.state.value,
                                "message": state_info.message,
                                "limit_switches": state_info.limit_switches.value,
                                "is_moving": state_info.is_moving(),
                                "is_ready": state_info.is_ready(),
                                "is_faulted": state_info.is_faulted(),
                            }
                            batch_data[(controller, axis)] = state_data
                        except Exception as e:
                            self.error.emit(
                                f"Error reading {controller}::{axis}: {str(e)}"
                            )

                    if batch_data and self.running:
                        logger.debug(f"ControllerMonitor thread: batch updating {len(batch_data)} axes")
                        self.batch_state_updated.emit(batch_data)
                else:
                    # Individual mode - emit each update separately
                    for controller, axis in self.monitored_axes:
                        if not self.running:
                            break
                        try:
                            position = self.client.get_axis_position(
                                controller, axis
                            )
                            state_info = self.client.get_axis_state_info(
                                controller, axis
                            )

                            state_data = {
                                "position": position,
                                "state": state_info.state.value,
                                "message": state_info.message,
                                "limit_switches": state_info.limit_switches.value,
                                "is_moving": state_info.is_moving(),
                                "is_ready": state_info.is_ready(),
                                "is_faulted": state_info.is_faulted(),
                            }
                            self.state_updated.emit(controller, axis, state_data)
                        except Exception as e:
                            self.error.emit(
                                f"Error reading {controller}::{axis}: {str(e)}"
                            )

                self.msleep(self.update_interval)
            except Exception as e:
                self.error.emit(f"Monitor error: {str(e)}")
                self.msleep(1000)

    def stop(self):
        self.running = False


class AxisControlWidget(QWidget):
    """Widget for controlling a single axis."""

    def __init__(
        self, client: MotaremClient, controller: str, axis: str, axis_data: dict, main_window=None
    ):
        super().__init__()
        self.client = client
        self.controller = controller
        self.axis = axis
        self.main_window = main_window
        self.current_position = 0.0
        self.virtual_zero_position = 0.0
        self.state_info = None
        self.available_params = axis_data.get("available_params", [])
        self.movement_params = axis_data.get("movement_params", [])
        self.param_values = axis_data.get("param_values", {})
        self.is_in_separate_window = False
        self.separate_window = None

        self.setup_ui()
        self.create_movement_parameter_inputs()
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)

    def setup_ui(self):
        layout = QVBoxLayout(self)

        # Header
        header = QLabel(f"{self.controller} :: {self.axis}")
        header_font = QFont()
        header_font.setBold(True)
        header_font.setPointSize(12)
        header.setFont(header_font)
        layout.addWidget(header)

        # Status display
        status_group = QGroupBox("Status")
        status_layout = QGridLayout(status_group)
        self.status_layout = status_layout  # Store reference for later use

        self.position_label = QLabel("Position: --")
        self.state_label = QLabel("State: --")
        self.limit_switches_label = QLabel("Limits: --")
        self.message_label = QLabel("Message: --")

        status_layout.addWidget(QLabel("Position:"), 0, 0)
        status_layout.addWidget(self.position_label, 0, 1)

        self.set_virtual_zero_button = QPushButton("Set Virtual Zero")
        self.set_virtual_zero_button.clicked.connect(self.set_virtual_zero)
        status_layout.addWidget(self.set_virtual_zero_button, 0, 2)

        self.reset_virtual_zero_button = QPushButton("Reset Virtual Zero")
        self.reset_virtual_zero_button.clicked.connect(self.reset_virtual_zero)
        status_layout.addWidget(self.reset_virtual_zero_button, 0, 3)

        status_layout.addWidget(QLabel("State:"), 1, 0)
        status_layout.addWidget(self.state_label, 1, 1)
        status_layout.addWidget(QLabel("Limits:"), 2, 0)
        status_layout.addWidget(self.limit_switches_label, 2, 1)
        status_layout.addWidget(QLabel("Message:"), 3, 0)
        status_layout.addWidget(self.message_label, 3, 1)

        # Additional parameter labels (will be populated dynamically)
        self.param_labels = {}
        self.status_row = 4

        layout.addWidget(status_group)

        # Movement control
        move_group = QGroupBox("Movement")
        move_layout = QGridLayout(move_group)

        self.target_input = QDoubleSpinBox()
        self.target_input.setRange(-10000, 10000)
        self.target_input.setDecimals(4)
        # self.target_input.setSuffix(" units")

        move_layout.addWidget(QLabel("Target:"), 0, 0)
        move_layout.addWidget(self.target_input, 0, 1)

        # Dynamic parameter inputs will be added here
        self.param_inputs = {}
        self.param_row = 1

        # Buttons
        button_layout = QHBoxLayout()
        self.move_button = QPushButton("Move")
        self.stop_button = QPushButton("Stop")
        self.home_button = QPushButton("Go to 0")

        self.move_button.clicked.connect(self.move_axis)
        self.stop_button.clicked.connect(self.stop_axis)
        self.home_button.clicked.connect(self.home_axis)

        button_layout.addWidget(self.move_button)
        button_layout.addWidget(self.stop_button)
        button_layout.addWidget(self.home_button)

        # Buttons will be added after parameters are loaded
        self.move_group = move_group
        self.move_layout = move_layout
        self.button_layout = button_layout
        layout.addWidget(move_group)

        # Relative movement control
        relative_group = QGroupBox("Relative Movement")
        relative_layout = QGridLayout(relative_group)

        self.relative_input = QDoubleSpinBox()
        self.relative_input.setRange(0.001, 10000)
        self.relative_input.setDecimals(4)
        self.relative_input.setValue(1.0)

        relative_layout.addWidget(QLabel("Distance:"), 0, 0)
        relative_layout.addWidget(self.relative_input, 0, 1)

        # Relative movement buttons
        relative_button_layout = QHBoxLayout()
        self.move_positive_button = QPushButton("Move +")
        self.move_negative_button = QPushButton("- Move")

        self.move_positive_button.clicked.connect(self.move_relative_positive)
        self.move_negative_button.clicked.connect(self.move_relative_negative)

        relative_button_layout.addWidget(self.move_negative_button)
        relative_button_layout.addWidget(self.move_positive_button)

        relative_layout.addLayout(relative_button_layout, 1, 0, 1, 2)
        layout.addWidget(relative_group)

        # Add additional parameters to status display
        self.add_status_parameters()

    def add_status_parameters(self):
        """Add additional parameters to status display (excluding duplicates)."""
        # Parameters to exclude from status display (already shown elsewhere)
        excluded_params = {"position", "state", "limit_switches"}

        for param in self.available_params:
            if (
                param not in excluded_params
                and param not in self.movement_params
            ):
                label = QLabel(f"{param.replace('_', ' ').title()}: --")
                self.param_labels[param] = label

                self.status_layout.addWidget(
                    QLabel(f"{param.replace('_', ' ').title()}:"),
                    self.status_row,
                    0,
                )
                self.status_layout.addWidget(label, self.status_row, 1)
                self.status_row += 1

    def create_movement_parameter_inputs(self):
        """Create input fields for movement parameters."""
        for param in self.movement_params:
            if param == "velocity":
                input_widget = QDoubleSpinBox()
                input_widget.setRange(0.1, 1000)
                input_widget.setValue(self.param_values.get("velocity", 100))
                # input_widget.setSuffix(" u/s")
            elif param == "acceleration":
                input_widget = QDoubleSpinBox()
                input_widget.setRange(0.1, 10000)
                input_widget.setValue(
                    self.param_values.get("acceleration", 1000)
                )
                # input_widget.setSuffix(" u/s²")
            elif param == "deceleration":
                input_widget = QDoubleSpinBox()
                input_widget.setRange(0.1, 10000)
                input_widget.setValue(
                    self.param_values.get("deceleration", 1000)
                )
                # input_widget.setSuffix(" u/s²")
            else:
                # Generic parameter
                input_widget = QDoubleSpinBox()
                input_widget.setRange(-10000, 10000)
                input_widget.setValue(self.param_values.get(param, 0))
                input_widget.setDecimals(3)

            self.param_inputs[param] = input_widget
            self.move_layout.addWidget(
                QLabel(f"{param.title()}:"), self.param_row, 0
            )
            self.move_layout.addWidget(input_widget, self.param_row, 1)
            self.param_row += 1

        # Add buttons after parameters
        self.move_layout.addLayout(self.button_layout, self.param_row, 0, 1, 2)

    def update_state(self, state_data: Dict):
        """Update the widget with new state data."""
        self.current_position = state_data["position"]
        virtual_position = self.current_position - self.virtual_zero_position

        if self.virtual_zero_position == 0.0:
            pos_text = f"{virtual_position:.4f}"
        else:
            pos_text = f"{virtual_position:.4f}\n({self.current_position:.4f})"


        self.position_label.setText(pos_text)
        self.state_label.setText(state_data["state"])
        self.limit_switches_label.setText(state_data["limit_switches"])
        self.message_label.setText(state_data.get("message", "None") or "None")

        # Request parameter update in background thread (less frequently)
        current_time = time.time()
        if not hasattr(self, '_last_param_update'):
            self._last_param_update = 0

        if current_time - self._last_param_update >= 2.0:  # Only update every 2 seconds
            self._last_param_update = current_time
            self.update_status_parameters()

        # Color coding for state
        if state_data["is_faulted"]:
            self.state_label.setStyleSheet("color: red; font-weight: bold;")
        elif state_data["is_moving"]:
            self.state_label.setStyleSheet("color: orange; font-weight: bold;")
        elif state_data["is_ready"]:
            self.state_label.setStyleSheet("color: green; font-weight: bold;")
        else:
            self.state_label.setStyleSheet("color: gray;")

        # Enable/disable buttons based on state
        # can_move = state_data['is_ready'] and not state_data['is_moving']
        # self.move_button.setEnabled(can_move)
        # self.home_button.setEnabled(can_move)
        # self.move_positive_button.setEnabled(can_move)
        # self.move_negative_button.setEnabled(can_move)

    def update_status_parameters(self, param_values=None):
        """Update additional parameter values in status display."""
        if param_values is None:
            # If no values provided, request update from parameter updater
            if self.main_window and hasattr(self.main_window, 'parameter_updater') and self.main_window.parameter_updater:
                params = list(self.param_labels.keys())
                logger.debug(f"Requesting parameter update for {self.controller}::{self.axis} from background thread")
                self.main_window.parameter_updater.request_parameter_update(
                    self.controller, self.axis, params
                )
            return

        # Update UI with provided parameter values
        for param, value in param_values.items():
            if param in self.param_labels:
                if isinstance(value, float):
                    self.param_labels[param].setText(f"{value:.3f}")
                else:
                    self.param_labels[param].setText(str(value))

    def show_context_menu(self, position):
        """Show context menu for axis widget."""
        menu = QMenu(self)

        if not self.is_in_separate_window:
            pop_out_action = menu.addAction("Pop out to separate window")
            pop_out_action.triggered.connect(self.pop_out_to_window)
        else:
            return_action = menu.addAction("Return to main window")
            return_action.triggered.connect(self.return_to_main_window)

        menu.exec(self.mapToGlobal(position))

    def pop_out_to_window(self):
        """Pop out this widget to a separate window."""
        if self.main_window:
            self.main_window.pop_out_axis(self.controller, self.axis)

    def return_to_main_window(self):
        """Return this widget to the main window."""
        if self.main_window:
            self.main_window.return_axis_to_main(
                self.controller, self.axis
            )

    def move_axis(self):
        """Move axis to target position."""
        try:
            virtual_target = self.target_input.value()
            target = virtual_target + self.virtual_zero_position
            params = MovementParams()

            # Set parameters from input fields
            for param_name, input_widget in self.param_inputs.items():
                value = input_widget.value()
                if param_name == "velocity":
                    params = params.with_velocity(value)
                elif param_name == "acceleration":
                    params = params.with_acceleration(value)
                elif param_name == "deceleration":
                    params = params.with_deceleration(value)
                else:
                    params = params.with_custom_param(param_name, value)

            result = self.client.move(
                self.controller, self.axis, target, params
            )
            print(f"Move result for {self.controller}::{self.axis}: {result}")

        except MotaremException as e:
            QMessageBox.warning(self, "Move Error", f"Failed to move axis: {e}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Unexpected error: {e}")

    def home_axis(self):
        """Move axis to virtual position 0."""
        try:
            result = self.client.move(self.controller, self.axis, self.virtual_zero_position)
            print(f"Home result for {self.controller}::{self.axis}: {result}")
        except MotaremException as e:
            QMessageBox.warning(self, "Home Error", f"Failed to home axis: {e}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Unexpected error: {e}")

    def move_relative_positive(self):
        """Move axis relative in positive direction."""
        self._move_relative(positive=True)

    def move_relative_negative(self):
        """Move axis relative in negative direction."""
        self._move_relative(positive=False)

    def _move_relative(self, positive: bool):
        """Move axis relative by the specified distance."""
        try:
            distance = self.relative_input.value()
            if not positive:
                distance = -distance

            current_virtual_position = self.current_position - self.virtual_zero_position
            target_virtual = current_virtual_position + distance
            target = target_virtual + self.virtual_zero_position
            params = MovementParams()

            # Set parameters from input fields
            for param_name, input_widget in self.param_inputs.items():
                value = input_widget.value()
                if param_name == "velocity":
                    params = params.with_velocity(value)
                elif param_name == "acceleration":
                    params = params.with_acceleration(value)
                elif param_name == "deceleration":
                    params = params.with_deceleration(value)
                else:
                    params = params.with_custom_param(param_name, value)

            result = self.client.move(
                self.controller, self.axis, target, params
            )
            direction = "+" if positive else "-"
            print(
                f"Relative move {direction}{abs(distance)} result for {self.controller}::{self.axis}: {result}"
            )

        except MotaremException as e:
            QMessageBox.warning(
                self, "Relative Move Error", f"Failed to move axis: {e}"
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Unexpected error: {e}")

    def set_virtual_zero(self):
        """Set the current position as virtual zero."""
        self.virtual_zero_position = self.current_position
        virtual_position = self.current_position - self.virtual_zero_position
        self.position_label.setText(f"{virtual_position:.4f}")

    def reset_virtual_zero(self):
        """Reset virtual zero to absolute zero."""
        self.virtual_zero_position = 0.0
        virtual_position = self.current_position - self.virtual_zero_position
        self.position_label.setText(f"{virtual_position:.4f}")

    def stop_axis(self):
        """Stop axis movement."""
        try:
            result = self.client.stop(self.controller, self.axis)
            print(f"Stop result for {self.controller}::{self.axis}: {result}")
        except MotaremException as e:
            QMessageBox.warning(self, "Stop Error", f"Failed to stop axis: {e}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Unexpected error: {e}")


class MotaremMainWindow(QMainWindow):
    """Main window for Motarem GUI application."""

    def __init__(self):
        super().__init__()
        self.client = None
        self.data_loader = None
        self.monitor_thread = None
        self.parameter_updater = None
        self.axis_widgets = {}  # {(controller, axis): widget}
        self.selected_axes = set()  # {(controller, axis)}
        self.all_data = {}  # Preloaded controller/axis data
        self.separate_windows = {}  # {(controller, axis): AxisWindow}

        self.setup_ui()
        self.setWindowTitle("Motarem Motor Controller")
        self.setMinimumSize(800, 600)

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        layout = QVBoxLayout(central_widget)

        # Connection section
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

        # Main content area
        self.content_splitter = QSplitter(Qt.Orientation.Horizontal)

        # Controllers and axes selection
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

        # Axis controls area
        self.axis_controls_widget = QTabWidget()
        self.axis_controls_widget.setTabsClosable(True)
        self.axis_controls_widget.tabCloseRequested.connect(self.close_axis_tab)
        self.content_splitter.addWidget(self.axis_controls_widget)

        self.content_splitter.setSizes([200, 600])

        # Progress bar for loading
        self.progress_label = QLabel("Ready")
        layout.addWidget(self.progress_label)

        # Main content gets most space
        layout.addWidget(self.content_splitter, stretch=1)

        # Log area - fixed size, no stretch
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
        """Add message to log."""
        self.log_text.append(f"[{QTimer().remainingTime()}] {message}")

    def connect_to_server(self):
        """Connect to Motarem server."""
        socket_path = self.socket_path_input.text()

        self.connection_thread = ConnectionThread(socket_path)
        self.connection_thread.connected.connect(self.on_connection_result)
        self.connection_thread.error.connect(self.on_connection_error)
        self.connection_thread.start()

        self.connect_button.setEnabled(False)
        self.connection_status.setText("Connecting...")
        self.progress_label.setText("Connecting to server...")

    def on_connection_result(self, success: bool):
        """Handle connection result."""
        if success:
            self.client = self.connection_thread.client
            self.connection_status.setText("Connected")
            self.connection_status.setStyleSheet(
                "color: green; font-weight: bold;"
            )
            self.connect_button.setEnabled(False)
            self.disconnect_button.setEnabled(True)
            self.log_message("Connected to Motarem server")

            # Start loading all data
            self.load_all_data()

        else:
            self.connection_status.setText("Connection Failed")
            self.connection_status.setStyleSheet(
                "color: red; font-weight: bold;"
            )
            self.connect_button.setEnabled(True)
            self.progress_label.setText("Connection failed")

    def on_connection_error(self, error_message: str):
        """Handle connection error."""
        self.log_message(f"Connection error: {error_message}")
        self.progress_label.setText(f"Error: {error_message}")
        QMessageBox.critical(
            self, "Connection Error", f"Failed to connect: {error_message}"
        )

    def disconnect_from_server(self):
        """Disconnect from Motarem server."""
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
            except:
                pass
            self.client = None

        self.connection_status.setText("Disconnected")
        self.connection_status.setStyleSheet("color: red;")
        self.connect_button.setEnabled(False)
        self.disconnect_button.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.select_all_button.setEnabled(False)
        self.deselect_all_button.setEnabled(False)

        # Clear axis controls
        self.axis_controls_widget.clear()
        self.axis_widgets.clear()
        self.selected_axes.clear()
        self.controllers_tree.clear()

        self.log_message("Disconnected from server")
        self.progress_label.setText("Disconnected")
        self.all_data.clear()

    def on_tree_item_changed(self, item, column):
        """Handle tree item selection changes."""
        if column != 0:  # Only respond to name column changes
            return

        controller_axis = item.data(0, Qt.ItemDataRole.UserRole)
        if controller_axis is None:  # Controller item, not axis
            return

        controller, axis = controller_axis
        if item.checkState(0) == Qt.CheckState.Checked:
            self.selected_axes.add((controller, axis))
            self.add_axis_tab(controller, axis)
        else:
            self.selected_axes.discard((controller, axis))
            self.remove_axis_tab(controller, axis)

    def add_axis_tab(self, controller: str, axis: str):
        """Add axis control tab."""
        if (controller, axis) not in self.axis_widgets:
            axis_data = (
                self.all_data.get(controller, {})
                .get("axis_data", {})
                .get(axis, {})
            )
            widget = AxisControlWidget(self.client, controller, axis, axis_data, self)
            self.axis_widgets[(controller, axis)] = widget
            tab_name = f"{controller}::{axis}"
            self.axis_controls_widget.addTab(widget, tab_name)

            # Update monitor thread
            self.update_monitor_thread()

    def remove_axis_tab(self, controller: str, axis: str):
        """Remove axis control tab."""
        widget = self.axis_widgets.get((controller, axis))
        if widget:
            # Close separate window if exists
            if (controller, axis) in self.separate_windows:
                self.separate_windows[(controller, axis)].close()
                print(self.separate_windows)
                del self.separate_windows[(controller, axis)]

            index = self.axis_controls_widget.indexOf(widget)
            if index >= 0:
                self.axis_controls_widget.removeTab(index)
            del self.axis_widgets[(controller, axis)]

            # Update monitor thread
            self.update_monitor_thread()

    def close_axis_tab(self, index):
        """Handle tab close button click."""
        widget = self.axis_controls_widget.widget(index)
        if widget:
            # Find which axis this widget belongs to
            for (controller, axis), w in self.axis_widgets.items():
                if w == widget:
                    self.selected_axes.discard((controller, axis))
                    # Update tree view
                    self.update_tree_item_check_state(controller, axis, False)
                    break

    def update_tree_item_check_state(
        self, controller: str, axis: str, checked: bool
    ):
        """Update tree item check state programmatically."""
        for i in range(self.controllers_tree.topLevelItemCount()):
            controller_item = self.controllers_tree.topLevelItem(i)
            if controller_item.text(0) == controller:
                for j in range(controller_item.childCount()):
                    axis_item = controller_item.child(j)
                    axis_data = axis_item.data(0, Qt.ItemDataRole.UserRole)
                    if axis_data and axis_data[1] == axis:
                        axis_item.setCheckState(
                            0,
                            Qt.CheckState.Checked
                            if checked
                            else Qt.CheckState.Unchecked,
                        )
                        break
                break

    def pop_out_axis(self, controller: str, axis: str):
        """Pop out axis control to separate window."""
        widget = self.axis_widgets.get((controller, axis))
        if widget and (controller, axis) not in self.separate_windows:
            # Remove from tab widget
            index = self.axis_controls_widget.indexOf(widget)
            if index >= 0:
                self.axis_controls_widget.removeTab(index)

            # Get axis data for creating new widget
            axis_data = (
                self.all_data.get(controller, {})
                .get("axis_data", {})
                .get(axis, {})
            )

            # Create separate window with new widget instance
            window = AxisWindow(self.client, controller, axis, axis_data, self)
            self.separate_windows[(controller, axis)] = window

            # Replace the widget reference to point to the new one in separate window
            self.axis_widgets[(controller, axis)] = window.axis_widget
            window.show()

    def return_axis_to_main(self, controller: str, axis: str):
        """Return axis control from separate window to main window."""
        if (controller, axis) in self.separate_windows:
            window = self.separate_windows[(controller, axis)]

            # Remove from separate windows dict first to avoid double removal
            del self.separate_windows[(controller, axis)]

            # Close window
            window.close()

            # Create new widget instance for main window
            axis_data = (
                self.all_data.get(controller, {})
                .get("axis_data", {})
                .get(axis, {})
            )
            widget = AxisControlWidget(self.client, controller, axis, axis_data, self)
            widget.is_in_separate_window = False

            # Replace widget reference and add to main window
            self.axis_widgets[(controller, axis)] = widget
            tab_name = f"{controller}::{axis}"
            self.axis_controls_widget.addTab(widget, tab_name)

    def select_all_axes(self):
        """Select all available axes."""
        for i in range(self.controllers_tree.topLevelItemCount()):
            controller_item = self.controllers_tree.topLevelItem(i)
            for j in range(controller_item.childCount()):
                axis_item = controller_item.child(j)
                axis_item.setCheckState(0, Qt.CheckState.Checked)

    def deselect_all_axes(self):
        """Deselect all axes."""
        for i in range(self.controllers_tree.topLevelItemCount()):
            controller_item = self.controllers_tree.topLevelItem(i)
            for j in range(controller_item.childCount()):
                axis_item = controller_item.child(j)
                axis_item.setCheckState(0, Qt.CheckState.Unchecked)

    def load_all_data(self):
        """Load all controllers, axes and parameters."""
        self.data_loader = DataLoader(self.client)
        self.data_loader.data_loaded.connect(self.on_data_loaded)
        self.data_loader.progress.connect(self.on_loading_progress)
        self.data_loader.error.connect(self.on_loading_error)
        self.data_loader.start()

    def on_data_loaded(self, data: dict):
        """Handle loaded data."""
        self.all_data = data
        self.refresh_ui()
        self.refresh_button.setEnabled(True)
        self.select_all_button.setEnabled(True)
        self.deselect_all_button.setEnabled(True)
        self.progress_label.setText("Ready")
        self.log_message(f"Loaded {len(data)} controllers with all parameters")

    def on_loading_progress(self, message: str):
        """Handle loading progress."""
        self.progress_label.setText(message)

    def on_loading_error(self, error: str):
        """Handle loading error."""
        self.log_message(f"Loading error: {error}")
        self.progress_label.setText(f"Error: {error}")

    def refresh_ui(self):
        """Refresh UI with loaded data."""
        self.controllers_tree.clear()

        for controller, controller_data in self.all_data.items():
            axes = controller_data["axes"]

            # Create controller item
            controller_item = QTreeWidgetItem([controller, f"{len(axes)} axes"])
            controller_item.setFlags(
                controller_item.flags() | Qt.ItemFlag.ItemIsAutoTristate
            )
            self.controllers_tree.addTopLevelItem(controller_item)

            # Create axis items
            for axis in axes:
                axis_item = QTreeWidgetItem([axis, "Ready"])
                axis_item.setFlags(
                    axis_item.flags() | Qt.ItemFlag.ItemIsUserCheckable
                )
                axis_item.setCheckState(0, Qt.CheckState.Unchecked)
                axis_item.setData(
                    0, Qt.ItemDataRole.UserRole, (controller, axis)
                )
                controller_item.addChild(axis_item)

                # If this axis was previously selected, restore selection
                if (controller, axis) in self.selected_axes:
                    axis_item.setCheckState(0, Qt.CheckState.Checked)

        # Expand all controller items
        self.controllers_tree.expandAll()

    def update_monitor_thread(self):
        """Update monitor thread with currently selected axes."""
        if self.monitor_thread and self.monitor_thread.isRunning():
            self.monitor_thread.set_monitored_axes(self.selected_axes)
        elif self.selected_axes:
            # Start monitoring if we have selected axes
            self.monitor_thread = ControllerMonitor(self.client)
            self.monitor_thread.set_monitored_axes(self.selected_axes)
            self.monitor_thread.state_updated.connect(self.on_state_updated)
            self.monitor_thread.batch_state_updated.connect(self.on_batch_state_updated)
            self.monitor_thread.error.connect(self.log_message)
            self.monitor_thread.start()

        # Start parameter updater if not already running
        if self.client and (not self.parameter_updater or not self.parameter_updater.isRunning()):
            logger.debug("Starting parameter updater thread")
            self.parameter_updater = ParameterUpdater(self.client)
            self.parameter_updater.parameters_updated.connect(self.on_parameters_updated)
            self.parameter_updater.error.connect(self.log_message)
            self.parameter_updater.start()

    def refresh_controllers(self):
        """Re-load all data from server."""
        if not self.client:
            return

        self.log_message("Refreshing data from server...")
        self.load_all_data()

    def on_state_updated(self, controller: str, axis: str, state_data: dict):
        """Handle single state update from monitor thread."""
        self._update_axis_state(controller, axis, state_data)

    def on_batch_state_updated(self, batch_data: dict):
        """Handle batch state updates from monitor thread."""
        for (controller, axis), state_data in batch_data.items():
            self._update_axis_state(controller, axis, state_data)

    def _update_axis_state(self, controller: str, axis: str, state_data: dict):
        """Update single axis state in UI."""
        widget = self.axis_widgets.get((controller, axis))
        if widget:
            widget.update_state(state_data)

        # Update tree item status
        for i in range(self.controllers_tree.topLevelItemCount()):
            controller_item = self.controllers_tree.topLevelItem(i)
            if controller_item.text(0) == controller:
                for j in range(controller_item.childCount()):
                    axis_item = controller_item.child(j)
                    axis_data = axis_item.data(0, Qt.ItemDataRole.UserRole)
                    if axis_data and axis_data[1] == axis:
                        status = state_data["state"]
                        if state_data["is_faulted"]:
                            status += " (FAULT)"
                        elif state_data["is_moving"]:
                            status += " (MOVING)"
                        axis_item.setText(1, status)
                        break
                break

    def on_parameters_updated(self, controller: str, axis: str, param_values: dict):
        """Handle parameter updates from parameter updater thread."""
        logger.debug(f"UI thread: received parameter updates for {controller}::{axis}")
        widget = self.axis_widgets.get((controller, axis))
        if widget:
            widget.update_status_parameters(param_values)

    def closeEvent(self, event):
        """Handle application close."""
        # Close all separate windows
        for window in list(self.separate_windows.values()):
            window.close()
        self.separate_windows.clear()

        # Stop parameter updater thread
        if self.parameter_updater and self.parameter_updater.isRunning():
            self.parameter_updater.stop()
            self.parameter_updater.wait(3000)

        self.disconnect_from_server()
        event.accept()


def main():
    app = QApplication(sys.argv)

    # Set application style
    app.setStyle("Fusion")

    window = MotaremMainWindow()
    window.show()

    try:
        sys.exit(app.exec())
    except Exception as e:
        print(f"Application error: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    main()
