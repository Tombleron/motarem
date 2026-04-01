import logging
from typing import Dict, List, Set, Tuple

from PyQt6.QtCore import QMutex, QMutexLocker, QThread, pyqtSignal

from client import MotaremClient, MotaremException
from gui.models import AxisData, AxisSnapshot, ControllerData

logger = logging.getLogger(__name__)


class ConnectionThread(QThread):
    connected = pyqtSignal(bool)
    error = pyqtSignal(str)

    def __init__(self, socket_path: str):
        super().__init__()
        self.socket_path = socket_path

    def run(self):
        try:
            with MotaremClient(self.socket_path) as client:
                client.ping()
            self.connected.emit(True)
        except Exception as e:
            self.error.emit(str(e))
            self.connected.emit(False)


class DataLoader(QThread):
    data_loaded = pyqtSignal(object)
    progress = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, socket_path: str):
        super().__init__()
        self.socket_path = socket_path

    def run(self):
        try:
            with MotaremClient(self.socket_path) as client:
                self.progress.emit("Loading controllers...")
                controllers = client.get_controllers()

                all_data: Dict[str, ControllerData] = {}
                total_axes = 0

                for controller in controllers:
                    total_axes += len(client.get_axes(controller))

                processed_axes = 0

                for controller in controllers:
                    self.progress.emit(f"Loading {controller}...")
                    axes = client.get_axes(controller)
                    axis_data: Dict[str, AxisData] = {}

                    for axis in axes:
                        self.progress.emit(
                            f"Loading {controller}::{axis} parameters..."
                        )

                        try:
                            available_params = client.get_axis_available_params(
                                controller, axis
                            )
                            movement_params = client.get_axis_movement_params(
                                controller, axis
                            )

                            param_values = {}
                            for param in available_params:
                                try:
                                    param_values[param] = client.get_axis_attribute_value(
                                        controller, axis, param
                                    )
                                except MotaremException:
                                    param_values[param] = 0.0

                            axis_data[axis] = AxisData(
                                available_params=available_params,
                                movement_params=movement_params,
                                param_values=param_values,
                            )

                            processed_axes += 1
                            progress_percent = (
                                (processed_axes / total_axes) * 100
                                if total_axes
                                else 100
                            )
                            self.progress.emit(
                                f"Loading parameters... {progress_percent:.0f}%"
                            )
                        except Exception as e:
                            self.error.emit(
                                f"Error loading {controller}::{axis}: {e}"
                            )

                    all_data[controller] = ControllerData(
                        axes=axes,
                        axis_data=axis_data,
                    )

            self.data_loaded.emit(all_data)
            self.progress.emit("Loading complete!")
        except Exception as e:
            self.error.emit(f"Failed to load data: {e}")


class ParameterUpdater(QThread):
    parameters_updated = pyqtSignal(str, str, object)
    error = pyqtSignal(str)

    def __init__(self, socket_path: str):
        super().__init__()
        self.socket_path = socket_path
        self.running = False
        self.update_queue: List[Tuple[str, str, List[str]]] = []
        self.mutex = QMutex()

    def request_parameter_update(self, controller: str, axis: str, params: List[str]):
        with QMutexLocker(self.mutex):
            self.update_queue = [
                (c, a, p)
                for c, a, p in self.update_queue
                if not (c == controller and a == axis)
            ]
            self.update_queue.append((controller, axis, params))

    def run(self):
        self.running = True
        try:
            with MotaremClient(self.socket_path) as client:
                while self.running:
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
                                        param_values[param] = client.get_axis_attribute_value(
                                            controller, axis, param
                                        )
                                    except Exception:
                                        param_values[param] = "Error"

                            if param_values and self.running:
                                self.parameters_updated.emit(
                                    controller, axis, param_values
                                )
                        except Exception as e:
                            self.error.emit(
                                f"Error updating parameters for {controller}::{axis}: {e}"
                            )

                    self.msleep(1000)
        except Exception as e:
            self.error.emit(f"Parameter updater failed: {e}")

    def stop(self):
        self.running = False


class ControllerMonitor(QThread):
    state_updated = pyqtSignal(str, str, object)
    batch_state_updated = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, socket_path: str):
        super().__init__()
        self.socket_path = socket_path
        self.running = False
        self.monitored_axes: Set[Tuple[str, str]] = set()
        self.update_interval = 250
        self.batch_updates = True

    def set_monitored_axes(self, monitored_axes: Set[Tuple[str, str]]):
        self.monitored_axes = monitored_axes

    def _read_snapshot(
        self, client: MotaremClient, controller: str, axis: str
    ) -> AxisSnapshot:
        position = client.get_axis_position(controller, axis)
        state_info = client.get_axis_state_info(controller, axis)
        return AxisSnapshot(
            position=position,
            state=state_info.state.value,
            message=state_info.message,
            limit_switches=state_info.limit_switches.value,
            is_moving=state_info.is_moving(),
            is_ready=state_info.is_ready(),
            is_faulted=state_info.is_faulted(),
        )

    def run(self):
        self.running = True
        try:
            with MotaremClient(self.socket_path) as client:
                while self.running:
                    try:
                        if self.batch_updates and len(self.monitored_axes) > 1:
                            batch_data = {}
                            for controller, axis in self.monitored_axes:
                                if not self.running:
                                    break
                                try:
                                    batch_data[(controller, axis)] = self._read_snapshot(
                                        client, controller, axis
                                    )
                                except Exception as e:
                                    self.error.emit(
                                        f"Error reading {controller}::{axis}: {e}"
                                    )

                            if batch_data and self.running:
                                self.batch_state_updated.emit(batch_data)
                        else:
                            for controller, axis in self.monitored_axes:
                                if not self.running:
                                    break
                                try:
                                    self.state_updated.emit(
                                        controller,
                                        axis,
                                        self._read_snapshot(client, controller, axis),
                                    )
                                except Exception as e:
                                    self.error.emit(
                                        f"Error reading {controller}::{axis}: {e}"
                                    )

                        self.msleep(self.update_interval)
                    except Exception as e:
                        self.error.emit(f"Monitor error: {e}")
                        self.msleep(1000)
        except Exception as e:
            self.error.emit(f"Monitor connection failed: {e}")

    def stop(self):
        self.running = False
