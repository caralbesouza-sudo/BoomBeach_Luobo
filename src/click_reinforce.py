import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import debug_settings


PROJECT_ROOT = Path(__file__).resolve().parents[1]

ADB_PATH = "adb"
ADB_SERIAL = "emulator-5554"

TAP_WHEN_MATCHED = True

REINFORCE_ICON_TEMPLATE = PROJECT_ROOT / "screenshots" / "templates" / "reinforce_icon.png"
DIAMOND_SPEEDUP_TEMPLATE = PROJECT_ROOT / "screenshots" / "templates" / "reinforce_diamond_generic.png"

REINFORCE_TEMPLATES = [
    REINFORCE_ICON_TEMPLATE,
    DIAMOND_SPEEDUP_TEMPLATE,
]

# 补兵标志在主界面右侧边缘。
REINFORCE_SEARCH_REGIONS = [
    (1180, 70, 100, 140),
    (1160, 380, 120, 180),
    (1160, 500, 120, 180),
]
DIAMOND_SPEEDUP_SEARCH_REGIONS = [
    (1160, 380, 120, 180),
    (1180, 70, 100, 140),
]
SEARCH_REGION = (1160, 70, 120, 490)
MATCH_THRESHOLD = 0.80


@dataclass
class MatchResult:
    template_name: str
    score: float
    x: int
    y: int
    width: int
    height: int

    @property
    def center(self):
        return self.x + self.width // 2, self.y + self.height // 2


def adb_command(*parts):
    command = [ADB_PATH]
    if ADB_SERIAL:
        command.extend(["-s", ADB_SERIAL])
    command.extend(parts)
    return command


def adb_screenshot():
    result = subprocess.run(
        adb_command("exec-out", "screencap", "-p"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="ignore").strip()
        raise RuntimeError(f"ADB 截图失败：{message}")
    image = cv2.imdecode(np.frombuffer(result.stdout, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError("ADB 截图解码失败。")
    return image


def read_image(path, flags=cv2.IMREAD_COLOR):
    image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), flags)
    if image is None:
        raise FileNotFoundError(f"读取图片失败：{path}")
    return image


def crop_region(image, region):
    x, y, width, height = region
    return image[y : y + height, x : x + width]


def has_diamond_badge(screen, match):
    y1 = max(0, match.y + match.height // 2)
    y2 = min(screen.shape[0], match.y + match.height + 8)
    x1 = max(0, match.x + match.width // 2)
    x2 = min(screen.shape[1], match.x + match.width + 8)
    badge_area = screen[y1:y2, x1:x2]
    if badge_area.size == 0:
        return False

    hsv = cv2.cvtColor(badge_area, cv2.COLOR_BGR2HSV)
    magenta = cv2.inRange(hsv, np.array([135, 45, 100]), np.array([175, 255, 255]))
    return cv2.countNonZero(magenta) >= 12


def find_template_in_region(screen, template_path, search_region):
    search_x, search_y, _, _ = search_region
    search = crop_region(screen, search_region)
    search_gray = cv2.cvtColor(search, cv2.COLOR_BGR2GRAY)
    template = read_image(template_path, cv2.IMREAD_UNCHANGED)

    if template.ndim == 3 and template.shape[2] == 4:
        template_gray = cv2.cvtColor(template[:, :, :3], cv2.COLOR_BGR2GRAY)
        template_mask = template[:, :, 3]
    else:
        template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
        template_mask = None

    if template_mask is not None:
        _, template_mask = cv2.threshold(template_mask, 10, 255, cv2.THRESH_BINARY)
        result = cv2.matchTemplate(search_gray, template_gray, cv2.TM_CCORR_NORMED, mask=template_mask)
    else:
        result = cv2.matchTemplate(search_gray, template_gray, cv2.TM_CCOEFF_NORMED)
    _, score, _, location = cv2.minMaxLoc(result)

    return MatchResult(
        template_name=template_path.name,
        score=float(score),
        x=int(location[0]) + search_x,
        y=int(location[1]) + search_y,
        width=template_gray.shape[1],
        height=template_gray.shape[0],
    )


def find_best(screen, template_paths, search_regions, require_diamond=False, reject_diamond=False):
    best = None
    for template_path in template_paths:
        for search_region in search_regions:
            match = find_template_in_region(screen, template_path, search_region)
            if require_diamond and match.score >= MATCH_THRESHOLD and not has_diamond_badge(screen, match):
                print(f"钻石加速候选缺少钻石徽标，忽略：{match.template_name}，分数：{match.score:.3f}")
                match.score = 0.0
            if reject_diamond and match.score >= MATCH_THRESHOLD and has_diamond_badge(screen, match):
                print(f"普通补兵候选带有钻石徽标，忽略：{match.template_name}，分数：{match.score:.3f}")
                match.score = 0.0
            if best is None or match.score > best.score:
                best = match
    return best


def find_reinforce_icon(screen):
    return find_best(screen, [REINFORCE_ICON_TEMPLATE], REINFORCE_SEARCH_REGIONS, reject_diamond=True)


def find_diamond_speedup_icon(screen):
    return find_best(screen, [DIAMOND_SPEEDUP_TEMPLATE], DIAMOND_SPEEDUP_SEARCH_REGIONS, require_diamond=True)


def find_template(screen):
    return find_best(screen, REINFORCE_TEMPLATES, [SEARCH_REGION])


def adb_tap(x, y):
    subprocess.run(adb_command("shell", "input", "tap", str(int(x)), str(int(y))), check=True)


def save_debug(screen, match):
    if not debug_settings.SAVE_ERROR_SCREENSHOTS:
        return None
    debug = screen.copy()
    sx, sy, sw, sh = SEARCH_REGION
    cv2.rectangle(debug, (sx, sy), (sx + sw, sy + sh), (255, 0, 0), 2)
    cv2.rectangle(
        debug,
        (match.x, match.y),
        (match.x + match.width, match.y + match.height),
        (0, 0, 255),
        3,
    )
    cx, cy = match.center
    cv2.circle(debug, (cx, cy), 8, (0, 255, 0), -1)

    output_dir = PROJECT_ROOT / "screenshots" / "errors"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"reinforce_{time.strftime('%Y%m%d_%H%M%S')}.png"
    cv2.imencode(".png", debug)[1].tofile(str(output_path))
    return output_path


def main():
    print("开始检测补兵图标。")
    screen = adb_screenshot()
    match = find_template(screen)
    cx, cy = match.center
    debug_path = save_debug(screen, match)

    print(f"补兵图标匹配分数：{match.score:.3f}")
    print(f"使用模板：{match.template_name}")
    print(f"补兵图标中心点：({cx}, {cy})")
    print(f"调试图：{debug_path}")

    if match.score < MATCH_THRESHOLD:
        print("没有检测到需要补兵的图标，暂不点击。")
        return

    if not TAP_WHEN_MATCHED:
        print("当前设置为只定位，不自动点击。")
        return

    print("检测到需要补兵，点击补兵图标。")
    adb_tap(cx, cy)


if __name__ == "__main__":
    main()
