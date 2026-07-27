import json
import os
import uuid
from datetime import datetime
from platform import uname

from screeninfo import get_monitors


class MetadataManager:
    """
    Handles various system metadata collection.
    """
    
    def __init__(self, recording_path: str, natural_scrolling: bool):
        self.recording_path = recording_path
        
        self.metadata = uname()._asdict()
        
        self.metadata["id"] = uuid.getnode()

        try:
            detected_monitors = get_monitors()
            main_monitor = next((m for m in detected_monitors if getattr(m, "is_primary", False)), detected_monitors[0])
            self.metadata["screen_width"] = main_monitor.width
            self.metadata["screen_height"] = main_monitor.height
            self.metadata["monitors"] = [
                {
                    "id": f"display-{index}",
                    "x": monitor.x,
                    "y": monitor.y,
                    "width": monitor.width,
                    "height": monitor.height,
                    "primary": bool(getattr(monitor, "is_primary", False)),
                }
                for index, monitor in enumerate(detected_monitors)
            ]
            self.metadata["virtual_desktop"] = {
                "x": min(m.x for m in detected_monitors),
                "y": min(m.y for m in detected_monitors),
                "width": max(m.x + m.width for m in detected_monitors) - min(m.x for m in detected_monitors),
                "height": max(m.y + m.height for m in detected_monitors) - min(m.y for m in detected_monitors),
            }
        except Exception:
            self.metadata["screen_width"] = 0
            self.metadata["screen_height"] = 0
            self.metadata["monitors"] = []
            self.metadata["virtual_desktop"] = {"x": 0, "y": 0, "width": 0, "height": 0}
        
        self.metadata["model"] = "Unknown"
        try:
            system_name = self.metadata["system"]
            if system_name == "Windows" and os.environ.get("DUCKTRACK_COLLECT_WMI") == "1":
                import wmi
                for item in wmi.WMI().Win32_ComputerSystem():
                    self.metadata["model"] = item.Model
                    break
            elif system_name == "Darwin":
                import subprocess
                model = subprocess.check_output(["sysctl", "-n", "hw.model"]).decode().strip()
                self.metadata["model"] = model
            elif system_name == "Linux":
                with open("/sys/devices/virtual/dmi/id/product_name", "r") as f:
                    self.metadata["model"] = f.read().strip()
        except:
            self.metadata["model"] = "Unknown"
        
        self.metadata["scroll_direction"] = -1 if natural_scrolling else 1

    def save_metadata(self):
        metadata_path = os.path.join(self.recording_path, "metadata.json")
        with open(metadata_path, "w") as f:
            json.dump(self.metadata, f, indent=4)
    
    def collect(self):
        self.metadata["start_time"] = self._get_time_stamp()
        
    def end_collect(self):
        self.metadata["stop_time"] = self._get_time_stamp()
    
    def add_obs_record_state_timings(self, record_state_events: dict[str, float]):
        self.metadata["obs_record_state_timings"] = record_state_events

    def set_video_capture_backend(self, backend_name: str):
        self.metadata["video_capture_backend"] = backend_name

    def add_video_timing(self, video_timing: dict):
        self.metadata["video_timing"] = video_timing

    def _get_time_stamp(self):
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
