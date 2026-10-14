"""
Cleaning path map for robots without LIDAR (gyro / "yw_es" robots).

Mirrors the WeBack app (6.1.7) rendering:
- legacy firmware reports the whole path in the thing status
  (`robot_x` / `robot_y`: base64 byte arrays on a 256x256 grid),
- newer firmware (yugong_software_version >= 6.0.5) pushes a `map_data`
  websocket message (base64 + zlib JSON, "ProMapData") with the cleaned cells,
  the cleaning path and the robot position.
"""

import base64
import binascii
import io
import logging

from PIL import Image, ImageDraw

_LOGGER = logging.getLogger(__name__)

LEGACY_GRID = 256

# Colours used by the WeBack app (GyroMarkView / mapfactory.f)
COLOR_CLEANED = (0x45, 0x3E, 0x7E, 0xFF)
COLOR_AREA_2 = (0x74, 0x6F, 0xA9, 0xFF)
COLOR_AREA_3 = (0x63, 0x57, 0xBF, 0x66)
COLOR_PATH = (0x27, 0xC0, 0xAF, 0xFF)
COLOR_ROBOT = (0xDA, 0x36, 0x25, 0xFF)
COLOR_START = (0x1C, 0xE3, 0x78, 0xFF)
AREA_COLORS = {1: COLOR_CLEANED, 2: COLOR_AREA_2, 3: COLOR_AREA_3}

# Longest side of the rendered image in pixels
TARGET_SIZE = 512
MARGIN = 4


class GyroPathMap:
    """Cleaning path of a gyro robot, in map units"""

    def __init__(self):
        # Each segment is a list of (x, y); a new segment starts after a jump
        self.segments = []
        # (x, y) -> area value (1 cleaned, 2/3 other cell kinds)
        self.cells = {}
        self.robot = None
        # Width of the cleaning swath, in map units
        self.swath = 0
        self.signature = None

    @property
    def has_data(self) -> bool:
        return any(len(segment) > 1 for segment in self.segments) or bool(self.cells)

    def update_from_status(self, status) -> bool:
        """Legacy firmware: path bytes in thing status. Returns True if changed."""
        robot_x = status.get("robot_x")
        robot_y = status.get("robot_y")
        if not robot_x or not robot_y:
            return False
        signature = ("status", robot_x, robot_y)
        if signature == self.signature:
            return False
        try:
            xs = base64.b64decode(robot_x)
            ys = base64.b64decode(robot_y)
        except (binascii.Error, ValueError):
            _LOGGER.debug("GyroPathMap: invalid robot_x/robot_y payload")
            return False
        if len(xs) != len(ys):
            return False

        # GyroMarkView.C(): canvas x = 256 - y, canvas y = 256 - x
        points = [(LEGACY_GRID - y, LEGACY_GRID - x) for x, y in zip(xs, ys)]
        self.segments = [points]
        self.cells = {}
        self.robot = points[-1] if points else None
        self.swath = 2
        self.signature = signature
        return True

    def update_from_pro_map(self, data) -> bool:
        """Newer firmware: decoded `map_data` websocket payload (ProMapData)."""
        map_data = data.get("map_data")
        if not isinstance(map_data, dict):
            return False
        size = max(int(data.get("map_row") or 0), int(data.get("map_col") or 0))
        if size <= 0:
            return False

        # mapfactory.h.u(): cell (row, col) -> (N - (col_off + col), N - (row_off + row))
        roi_pos = map_data.get("roi_pos") or []
        row_off, col_off = (roi_pos[0], roi_pos[1]) if len(roi_pos) > 1 else (0, 0)
        cells = {}
        for row, values in enumerate(map_data.get("clean_area") or []):
            for col, value in enumerate(values):
                if value in AREA_COLORS:
                    cells[(size - (col_off + col), size - (row_off + row))] = value

        # "pathFill" maps (yw_es_old) keep x/y, the others swap them (h.I() / h.d())
        path_fill = map_data.get("map_render_type") == "pathFill"
        segments = []
        current = []
        for point in map_data.get("clean_path") or []:
            map_type = point.get("map_type")
            x_info = point.get("x_info", -1)
            y_info = point.get("y_info", -1)
            if map_type == -1 or x_info < 0 or y_info < 0:
                if current:
                    segments.append(current)
                current = []
                continue
            if map_type not in (3, 4):
                continue
            if path_fill:
                current.append((size - x_info, size - y_info))
            else:
                current.append((size - y_info, size - x_info))
        if current:
            segments.append(current)

        robot_pos = map_data.get("robot_pos")
        if robot_pos and len(robot_pos) > 1 and (robot_pos[0] or robot_pos[1]):
            robot = (size - robot_pos[0], size - robot_pos[1])
        else:
            robot = segments[-1][-1] if segments else None

        self.cells = cells
        self.segments = segments
        self.robot = robot
        self.swath = 3 if path_fill else 0
        self.signature = None
        return True

    def render_png(self) -> bytes | None:
        """Render the path as a PNG, cropped to the cleaned area."""
        points = [p for segment in self.segments for p in segment]
        points.extend(self.cells)
        if self.robot:
            points.append(self.robot)
        if not points:
            return None

        min_x = min(p[0] for p in points) - MARGIN
        min_y = min(p[1] for p in points) - MARGIN
        max_x = max(p[0] for p in points) + MARGIN
        max_y = max(p[1] for p in points) + MARGIN
        scale = max(1.0, min(16.0, TARGET_SIZE / max(max_x - min_x, max_y - min_y)))

        def px(point):
            return ((point[0] - min_x) * scale, (point[1] - min_y) * scale)

        img = Image.new(
            "RGBA",
            (round((max_x - min_x) * scale), round((max_y - min_y) * scale)),
            (0, 0, 0, 0),
        )
        draw = ImageDraw.Draw(img, "RGBA")

        half = scale / 2
        for cell, value in self.cells.items():
            x, y = px(cell)
            draw.rectangle((x - half, y - half, x + half, y + half), AREA_COLORS[value])

        if self.swath:
            width = max(1, round(self.swath * scale))
            for segment in self.segments:
                if len(segment) > 1:
                    draw.line([px(p) for p in segment], COLOR_CLEANED, width, joint="curve")

        line_width = max(1, round(scale / 3))
        for segment in self.segments:
            if len(segment) > 1:
                draw.line([px(p) for p in segment], COLOR_PATH, line_width, joint="curve")

        radius = max(3, scale)
        if self.segments and self.segments[0]:
            x, y = px(self.segments[0][0])
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), COLOR_START)
        if self.robot:
            x, y = px(self.robot)
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), COLOR_ROBOT)

        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        return buffer.getvalue()
