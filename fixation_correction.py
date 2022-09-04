import os
import logging
from pyglui import ui
import file_methods as fm
import player_methods as pm
from methods import denormalize
from player_methods import transparent_circle
from plugin import Plugin
import numpy as np
import msgpack
logger = logging.getLogger(__name__)


class FixationCorrection(Plugin):
    """Fixation Correction Plugin"""

    icon_chr = chr(0xEC03)
    icon_font = "pupil_icons"

    @classmethod
    def parse_pretty_class_name(cls) -> str:
        """Specification of pretty plugin name"""
        return "Fixation Correction"

    def __init__(self, g_pool, *, x_corr=0.0, y_corr=0.0):
        super().__init__(g_pool)
        self.__x_corr = x_corr
        self.__y_corr = y_corr
        self.__fix_start_id = 0
        self.__fix_end_id = 0
        self.__frame = None
        self.__fixation_corrections = {}
        self.__data_dir = os.path.join(self.g_pool.rec_dir, "offline_data")
        # self._fixations_changed_announcer = data_changed.Announcer(
        #     "fixations", g_pool.rec_dir, plugin=Offline_Fixation_Detector
        # )

    def __serialize(self, fixation):
        """Standard serialization routine from Pupil Labs codebase"""
        serialization_hook = fm.Serialized_Dict.packing_hook
        fixation_serialized = msgpack.packb(
            fixation, use_bin_type=True, default=serialization_hook
        )
        return fixation_serialized

    def __modify_fixation(self, fixation_serialized, **kwargs):
        """Fixation modification and serialization routine"""
        fixation = {}
        for key, value in fixation_serialized.items():
            fixation[key] = value
        fixation.update(kwargs)
        return self.__serialize(fixation)

    def __save_online_fixations(self):
        """Method for saving fixations for current session
        (will be replaced with offline data during next startup)"""
        path_stop_ts = os.path.join(self.__data_dir, "fixations_stop_timestamps.npy")
        fixation_stop_ts = np.load(path_stop_ts)
        fixation_start_ts = self.g_pool.fixations.timestamps

        # Applying existing corrections to norm_pos of fixations
        fixations = []
        for fixation in self.g_pool.fixations.data:
            x_corr, y_corr = self.__fixation_corrections.get(fixation["id"], (0.0, 0.0))
            norm_pos = (fixation["norm_pos"][0] + x_corr, fixation["norm_pos"][1] + y_corr)
            fixation_serialized = fm.Serialized_Dict(msgpack_bytes=self.__modify_fixation(fixation, **{"norm_pos": norm_pos}))
            fixations.append(fixation_serialized)

        # Saving corrected fixations to application g_pool
        self.g_pool.fixations = pm.Affiliator(
            fixations, fixation_start_ts, fixation_stop_ts
        )
        # Clearing any corrections
        self.__fixation_corrections.clear()

    def __save_offline_fixations(self):
        """Saving data to files (will be loaded during next startup)"""
        offline_fixations = fm.load_pldata_file(self.__data_dir, "fixations")

        with fm.PLData_Writer(self.__data_dir, "fixations") as writer:
            for timestamp, fixation in zip(offline_fixations.timestamps, offline_fixations.data):
                x_corr, y_corr = self.__fixation_corrections.get(fixation["id"], (0.0, 0.0))
                norm_pos = (fixation["norm_pos"][0] + x_corr, fixation["norm_pos"][1] + y_corr)
                fixation_updated = fm.Serialized_Dict(msgpack_bytes=self.__modify_fixation(fixation, **{"norm_pos": norm_pos}))
                writer.append_serialized(timestamp, "fixation", fixation_updated.serialized)

    def get_init_dict(self):
        return {
            **super().get_init_dict(),
            # "x_corr": self.__x_corr,
            # "y_corr": self.__y_corr,
        }

    def recent_events(self, events):
        super().recent_events(events)

        frame = events.get("frame")
        if frame:
            self.__frame = frame
        else:
            return

        if len(self.__fixation_corrections.keys()) != 0 or self.__x_corr != 0 or self.__y_corr != 0:
            frame_width_height = frame.img.shape[:-1][::-1]
            frame_window = pm.enclosing_window(self.g_pool.timestamps, self.__frame.index)
            fixations = self.g_pool.fixations.by_ts_window(frame_window)

            points = []
            for fixation in fixations:
                if fixation["confidence"] < self.g_pool.min_data_confidence:
                    continue
                x_corr_existing, y_corr_existing = self.__fixation_corrections.get(fixation["id"], (0.0, 0.0))
                fixation_x_pos, fixation_y_pos = fixation["norm_pos"]
                mapped = (fixation_x_pos + self.x_corr + x_corr_existing, fixation_y_pos + self.y_corr + y_corr_existing)
                points.append(denormalize(mapped, frame_width_height, flip_y=True))

            for point in points:
                transparent_circle(
                    frame.img, point, radius=20, color=(0.0, 0.3, 1.0, 0.1), thickness=-1,
                )
                transparent_circle(
                    frame.img, point, radius=20, color=(0.0, 0.3, 1.0, 0.5), thickness=1,
                )

        else:
            return

    def init_ui(self):
        """UI init method"""
        # super().init_ui()
        self.add_menu()
        self.menu.label = "Fixation Correction"

        self.menu.append(
            ui.Info_Text(
                "Select a fixation and correct its coordinates using these sliders. "
                "Then use buttons below to apply these settings to fixation data.")
        )
        self.menu.append(
            ui.Slider(
                "x_corr",
                self,
                min=-0.2,
                step=0.001,
                max=0.2,
                label="X correction",
            )
        )
        self.menu.append(
            ui.Slider(
                "y_corr",
                self,
                min=-0.2,
                step=0.001,
                max=0.2,
                label="Y correction",
            )
        )
        self.menu.append(ui.Button("Apply Correction To Current Fixation", self.__apply_to_current_fixation))
        self.menu.append(ui.Button("Reset Correction For Current Fixation", self.__reset_corr_current_fixation))
        self.menu.append(ui.Separator())
        self.menu.append(ui.Button("Apply Correction to All Fixations", self.__apply_to_all_fixations))
        self.menu.append(ui.Separator())
        self.menu.append(
            ui.Info_Text("Specify fixation indices below to apply correction within the whole interval")
        )
        self.menu.append(
            ui.Text_Input(
                "fix_start_id",
                self,
                label="First Fixation Index",
            )
        )
        self.menu.append(
            ui.Text_Input(
                "fix_end_id",
                self,
                label="Last Fixation Index",
            )
        )
        self.menu.append(ui.Button("Apply Correction To Specified Fixations", self.__apply_corr_to_interval))
        self.menu.append(ui.Button("Reset Corrections For Specified Fixations", self.__reset_corr_for_interval))
        self.menu.append(ui.Separator())
        self.menu.append(ui.Button("Save All Fixations", self.__save_online_fixations))
        self.menu.append(ui.Button("Save All Fixations to File", self.__save_offline_fixations))
        # self.menu.append(ui.Separator())
        # self.menu.append(ui.Info_Text("Fixations corrections:"))

    def __apply_to_current_fixation(self):
        """Apply fixation correction to current fixation only"""
        # Getting current fixations
        frame_window = pm.enclosing_window(self.g_pool.timestamps, self.__frame.index)
        fixations = self.g_pool.fixations.by_ts_window(frame_window)

        if len(fixations) > 0:
            # Applying correction to each fixation
            for fixation in fixations:
                existing_correction = self.__fixation_corrections.get(fixation["id"], (0.0, 0.0))
                self.__fixation_corrections[fixation["id"]] = (
                    existing_correction[0] + self.__x_corr, existing_correction[1] + self.__y_corr)
                # self.menu.append(ui.Info_Text(f"Fixation {fixation['id']}: x = {existing_correction[0] + self.__x_corr}, y = {existing_correction[1] + self.__y_corr}"))

            # Finally resetting corrections
            self.__x_corr = 0.0
            self.__y_corr = 0.0

    def __apply_to_all_fixations(self):
        """Apply fixation correction to all fixations"""
        # Applying correction to all fixations
        for fixation in self.g_pool.fixations:
            # Using existing corrections as base
            existing_correction = self.__fixation_corrections.get(fixation["id"], (0.0, 0.0))
            self.__fixation_corrections[fixation["id"]] = (existing_correction[0] + self.__x_corr, existing_correction[1] + self.__y_corr)

    def __apply_corr_to_interval(self):
        """Apply fixation correction within specified interval"""
        # Applying correction to each fixation within interval (inclusively)
        for fid in range(self.__fix_start_id, min(len(self.g_pool.fixations), self.__fix_end_id + 1)):
            existing_correction = self.__fixation_corrections.get(fid, (0.0, 0.0))
            self.__fixation_corrections[fid] = (existing_correction[0] + self.__x_corr, existing_correction[1] + self.__y_corr)

    def __reset_corr_current_fixation(self):
        frame_window = pm.enclosing_window(self.g_pool.timestamps, self.__frame.index)
        fixations = self.g_pool.fixations.by_ts_window(frame_window)

        if len(fixations) > 0:
            # Resetting correction for each fixation
            for fixation in fixations:
                self.__fixation_corrections.pop(fixation["id"], None)

            # Finally resetting corrections
            self.__x_corr = 0.0
            self.__y_corr = 0.0

    def __reset_corr_for_interval(self):
        """Reset fixation correction within specified interval"""
        # Resetting correction for each fixation within interval (inclusively)
        for fid in range(self.__fix_start_id, min(len(self.g_pool.fixations), self.__fix_end_id + 1)):
            if fid in self.__fixation_corrections:
                del self.__fixation_corrections[fid]

    @property
    def fix_start_id(self):
        """First fixation ID within fixation interval (getter)"""
        return self.__fix_start_id

    @fix_start_id.setter
    def fix_start_id(self, val):
        """First fixation ID within fixation interval (setter)"""
        if val != self.__fix_start_id:
            self.__fix_start_id = val

    @property
    def fix_end_id(self):
        """Last fixation ID within fixation interval (getter)"""
        return self.__fix_end_id

    @fix_end_id.setter
    def fix_end_id(self, val):
        """Last fixation ID within fixation interval (setter)"""
        if val != self.__fix_end_id:
            self.__fix_end_id = val

    @property
    def x_corr(self):
        """Fixation X coordinate correction (getter)"""
        return self.__x_corr

    @x_corr.setter
    def x_corr(self, val):
        """Fixation X coordinate correction (setter)"""
        if val != self.__x_corr:
            self.__x_corr = val

    @property
    def y_corr(self):
        """Fixation Y coordinate correction (getter)"""
        return self.__y_corr

    @y_corr.setter
    def y_corr(self, val):
        """Fixation Y coordinate correction (setter)"""
        if val != self.__y_corr:
            self.__y_corr = val
