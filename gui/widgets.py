import logging
import time
from typing import Dict

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from client import MotaremClient, MotaremException, MovementParams
from gui.models import AxisData, AxisSnapshot

logger = logging.getLogger(__name__)


class AxisWindow(QMainWindow):
    """Separate window for axis control."""

    def __init__(self, axis_widget, controller: str, axis: str, main_window):
        super().__init__()
        self.controller = controller
        self.axis = axis
        self.main_window = main_window
        self.axis_widget = axis_widget

        self.setWindowTitle(f"Motarem - {controller}::{axis}")
        self.setMinimumSize(400, 600)
        self.axis_widget.is_in_separate_window = True
        self.setCentralWidget(self.axis_widget)

    def closeEvent(self, event):
        if (self.controller, self.axis) in self.main_window.separate_windows:
            self.main_window.return_axis_to_main(self.controller, self.axis)
        else:
            event.accept()


class AxisControlWidget(QWidget):
    """Widget for controlling a single axis."""

    def __init__(
        self,
        client: MotaremClient,
        controller: str,
        axis: str,
        axis_data: AxisData,
        main_window=None,
    ):
        super().__init__()
        self.client = client
        self.controller = controller
        self.axis = axis
        self.main_window = main_window
        self.current_position = 0.0
        self.virtual_zero_position = 0.0
        self.available_params = axis_data.available_params
        self.movement_params = axis_data.movement_params
        self.param_values = axis_data.param_values
        self.is_in_separate_window = False

        self.setup_ui()
        self.create_movement_parameter_inputs()
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)

    def setup_ui(self):
        layout = QVBoxLayout(self)

        header = QLabel(f"{self.controller} :: {self.axis}")
        header_font = QFont()
        header_font.setBold(True)
        header_font.setPointSize(12)
        header.setFont(header_font)
        layout.addWidget(header)

        status_group = QGroupBox("Status")
        status_layout = QGridLayout(status_group)
        self.status_layout = status_layout

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

        self.param_labels = {}
        self.status_row = 4
        layout.addWidget(status_group)

        move_group = QGroupBox("Movement")
        move_layout = QGridLayout(move_group)

        self.target_input = QDoubleSpinBox()
        self.target_input.setRange(-10000, 10000)
        self.target_input.setDecimals(4)

        move_layout.addWidget(QLabel("Target:"), 0, 0)
        move_layout.addWidget(self.target_input, 0, 1)

        self.param_inputs = {}
        self.param_row = 1

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

        self.move_layout = move_layout
        self.button_layout = button_layout
        layout.addWidget(move_group)

        relative_group = QGroupBox("Relative Movement")
        relative_layout = QGridLayout(relative_group)

        self.relative_input = QDoubleSpinBox()
        self.relative_input.setRange(0.001, 10000)
        self.relative_input.setDecimals(4)
        self.relative_input.setValue(1.0)

        relative_layout.addWidget(QLabel("Distance:"), 0, 0)
        relative_layout.addWidget(self.relative_input, 0, 1)

        relative_button_layout = QHBoxLayout()
        self.move_positive_button = QPushButton("Move +")
        self.move_negative_button = QPushButton("- Move")

        self.move_positive_button.clicked.connect(self.move_relative_positive)
        self.move_negative_button.clicked.connect(self.move_relative_negative)

        relative_button_layout.addWidget(self.move_negative_button)
        relative_button_layout.addWidget(self.move_positive_button)

        relative_layout.addLayout(relative_button_layout, 1, 0, 1, 2)
        layout.addWidget(relative_group)

        self.add_status_parameters()

    def add_status_parameters(self):
        excluded_params = {"position", "state", "limit_switches"}
        for param in self.available_params:
            if param not in excluded_params and param not in self.movement_params:
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
        for param in self.movement_params:
            input_widget = QDoubleSpinBox()
            if param == "velocity":
                input_widget.setRange(0.1, 1000)
                input_widget.setValue(self.param_values.get("velocity", 100))
            elif param == "acceleration":
                input_widget.setRange(0.1, 10000)
                input_widget.setValue(self.param_values.get("acceleration", 1000))
            elif param == "deceleration":
                input_widget.setRange(0.1, 10000)
                input_widget.setValue(self.param_values.get("deceleration", 1000))
            else:
                input_widget.setRange(-10000, 10000)
                input_widget.setValue(self.param_values.get(param, 0))
                input_widget.setDecimals(3)

            self.param_inputs[param] = input_widget
            self.move_layout.addWidget(QLabel(f"{param.title()}:"), self.param_row, 0)
            self.move_layout.addWidget(input_widget, self.param_row, 1)
            self.param_row += 1

        self.move_layout.addLayout(self.button_layout, self.param_row, 0, 1, 2)

    def update_state(self, state_data: AxisSnapshot):
        self.current_position = state_data.position
        virtual_position = self.current_position - self.virtual_zero_position

        if self.virtual_zero_position == 0.0:
            pos_text = f"{virtual_position:.4f}"
        else:
            pos_text = f"{virtual_position:.4f}\n({self.current_position:.4f})"

        self.position_label.setText(pos_text)
        self.state_label.setText(state_data.state)
        self.limit_switches_label.setText(state_data.limit_switches)
        self.message_label.setText(state_data.message or "None")

        current_time = time.time()
        if not hasattr(self, "_last_param_update"):
            self._last_param_update = 0.0
        if current_time - self._last_param_update >= 2.0:
            self._last_param_update = current_time
            self.update_status_parameters()

        if state_data.is_faulted:
            self.state_label.setStyleSheet("color: red; font-weight: bold;")
        elif state_data.is_moving:
            self.state_label.setStyleSheet("color: orange; font-weight: bold;")
        elif state_data.is_ready:
            self.state_label.setStyleSheet("color: green; font-weight: bold;")
        else:
            self.state_label.setStyleSheet("color: gray;")

    def update_status_parameters(self, param_values: Dict[str, object] | None = None):
        if param_values is None:
            if self.main_window and self.main_window.parameter_updater:
                self.main_window.parameter_updater.request_parameter_update(
                    self.controller,
                    self.axis,
                    list(self.param_labels.keys()),
                )
            return

        for param, value in param_values.items():
            if param in self.param_labels:
                self.param_labels[param].setText(
                    f"{value:.3f}" if isinstance(value, float) else str(value)
                )

    def show_context_menu(self, position):
        menu = QMenu(self)
        if not self.is_in_separate_window:
            menu.addAction("Pop out to separate window").triggered.connect(
                self.pop_out_to_window
            )
        else:
            menu.addAction("Return to main window").triggered.connect(
                self.return_to_main_window
            )
        menu.exec(self.mapToGlobal(position))

    def pop_out_to_window(self):
        if self.main_window:
            self.main_window.pop_out_axis(self.controller, self.axis)

    def return_to_main_window(self):
        if self.main_window:
            self.main_window.return_axis_to_main(self.controller, self.axis)

    def _build_movement_params(self) -> MovementParams:
        params = MovementParams()
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
        return params

    def _log_result(self, message: str):
        if self.main_window:
            self.main_window.log_message(message)
        else:
            logger.info(message)

    def move_axis(self):
        try:
            target = self.target_input.value() + self.virtual_zero_position
            result = self.client.move(
                self.controller,
                self.axis,
                target,
                self._build_movement_params(),
            )
            self._log_result(f"Move result for {self.controller}::{self.axis}: {result}")
        except MotaremException as e:
            QMessageBox.warning(self, "Move Error", f"Failed to move axis: {e}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Unexpected error: {e}")

    def home_axis(self):
        try:
            result = self.client.move(
                self.controller,
                self.axis,
                self.virtual_zero_position,
            )
            self._log_result(f"Home result for {self.controller}::{self.axis}: {result}")
        except MotaremException as e:
            QMessageBox.warning(self, "Home Error", f"Failed to home axis: {e}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Unexpected error: {e}")

    def move_relative_positive(self):
        self._move_relative(positive=True)

    def move_relative_negative(self):
        self._move_relative(positive=False)

    def _move_relative(self, positive: bool):
        try:
            distance = self.relative_input.value()
            if not positive:
                distance = -distance

            current_virtual_position = self.current_position - self.virtual_zero_position
            target_virtual = current_virtual_position + distance
            target = target_virtual + self.virtual_zero_position
            result = self.client.move(
                self.controller,
                self.axis,
                target,
                self._build_movement_params(),
            )
            direction = "+" if positive else "-"
            self._log_result(
                f"Relative move {direction}{abs(distance)} result for {self.controller}::{self.axis}: {result}"
            )
        except MotaremException as e:
            QMessageBox.warning(
                self, "Relative Move Error", f"Failed to move axis: {e}"
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Unexpected error: {e}")

    def set_virtual_zero(self):
        self.virtual_zero_position = self.current_position
        self.position_label.setText("0.0000")

    def reset_virtual_zero(self):
        self.virtual_zero_position = 0.0
        self.position_label.setText(f"{self.current_position:.4f}")

    def stop_axis(self):
        try:
            result = self.client.stop(self.controller, self.axis)
            self._log_result(f"Stop result for {self.controller}::{self.axis}: {result}")
        except MotaremException as e:
            QMessageBox.warning(self, "Stop Error", f"Failed to stop axis: {e}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Unexpected error: {e}")
