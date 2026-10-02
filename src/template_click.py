import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import debug_settings


PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 如果 adb devices 里显示的不是 emulator-5554，就改这里。
ADB_PATH = "adb"
ADB_SERIAL = "emulator-5554"

# 当前要测试的模板。完整模板用于正常情况，上半身模板用于文字遮挡螃蟹时。
TEMPLATE_PATHS = [
    PROJECT_ROOT / "screenshots" / "templates" / "crab_icon_masked.png",
    PROJECT_ROOT / "screenshots" / "templates" / "crab_icon_top_masked.png",
]

# 匹配分数阈值。越接近 1 越像。
# 当前螃蟹模板会受海水背景、缩放和文字遮挡影响，0.30 以上且红框准确就可以使用。
MATCH_THRESHOLD = 0.90

# 测试阶段先保持 False，只定位不点击。
# 你确认红框稳定框住螃蟹后，可以改成 True 测试自动点击。
TAP_WHEN_MATCHED = True

# 模板会自动按这些比例缩放后逐个匹配。
# 你的 crab_icon.png 比地图里的螃蟹大，所以必须做多尺度匹配。
SCALE_MIN = 0.18
SCALE_MAX = 1.05
SCALE_STEP = 0.03

# 只在地图中间区域找螃蟹，减少误点左下角基地、兵种栏、资源栏的概率。
# 格式是：左上角 x, 左上角 y, 宽, 高；如果想全屏匹配，就改成 None。
SEARCH_REGION = (430, 70, 500, 430)

# 惊奇螃蟹主体是稳定的紫色机械结构。模板在普通 NPC 基地上曾出现过高分误匹配，
# 因此自动点击只信任颜色和几何形状共同通过的候选；模板结果只用于调试。
CRAB_PURPLE_LOWER = np.array([120, 70, 35], dtype=np.uint8)
CRAB_PURPLE_UPPER = np.array([175, 255, 255], dtype=np.uint8)
CRAB_MIN_PURPLE_AREA = 2500
CRAB_MIN_WIDTH = 80
CRAB_MIN_HEIGHT = 60
CRAB_MAX_WIDTH = 350
CRAB_MAX_HEIGHT = 240
CRAB_MIN_ASPECT = 0.65
CRAB_MAX_ASPECT = 2.40


@dataclass
class MatchResult:
    template: Path
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


def adb_screenshot_bytes():
    result = subprocess.run(
        adb_command("exec-out", "screencap", "-p"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="ignore").strip()
        raise RuntimeError(f"ADB 截图失败：{message}")
    if not result.stdout.startswith(b"\x89PNG"):
        raise RuntimeError("ADB 截图结果不是 PNG，请检查 ADB_SERIAL。")
    return result.stdout


def decode_png(png_bytes):
    data = np.frombuffer(png_bytes, dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError("截图解码失败。")
    return image


def read_image(path, flags=cv2.IMREAD_COLOR):
    image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), flags)
    if image is None:
        raise FileNotFoundError(f"读取图片失败：{path}")
    return image


def find_template(screen, template_path):
    template = read_image(template_path, cv2.IMREAD_UNCHANGED)
    search_x = 0
    search_y = 0
    search_screen = screen
    if SEARCH_REGION is not None:
        search_x, search_y, search_width, search_height = SEARCH_REGION
        search_screen = screen[
            search_y : search_y + search_height,
            search_x : search_x + search_width,
        ]

    screen_gray = cv2.cvtColor(search_screen, cv2.COLOR_BGR2GRAY)
    if template.ndim == 3 and template.shape[2] == 4:
        template_gray = cv2.cvtColor(template[:, :, :3], cv2.COLOR_BGR2GRAY)
        template_mask = template[:, :, 3]
    else:
        template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
        template_mask = None
    screen_height, screen_width = screen_gray.shape[:2]

    best = None
    scale = SCALE_MIN
    while scale <= SCALE_MAX + 1e-9:
        scaled_width = int(template_gray.shape[1] * scale)
        scaled_height = int(template_gray.shape[0] * scale)
        scale += SCALE_STEP

        if scaled_width < 20 or scaled_height < 20:
            continue
        if scaled_width > screen_width or scaled_height > screen_height:
            continue

        resized = cv2.resize(template_gray, (scaled_width, scaled_height), interpolation=cv2.INTER_AREA)
        if template_mask is not None:
            resized_mask = cv2.resize(template_mask, (scaled_width, scaled_height), interpolation=cv2.INTER_AREA)
            _, resized_mask = cv2.threshold(resized_mask, 10, 255, cv2.THRESH_BINARY)
            result = cv2.matchTemplate(screen_gray, resized, cv2.TM_CCORR_NORMED, mask=resized_mask)
        else:
            result = cv2.matchTemplate(screen_gray, resized, cv2.TM_CCOEFF_NORMED)
        _, max_value, _, max_location = cv2.minMaxLoc(result)

        if best is None or max_value > best.score:
            best = MatchResult(
                template=template_path,
                score=float(max_value),
                x=int(max_location[0]) + search_x,
                y=int(max_location[1]) + search_y,
                width=scaled_width,
                height=scaled_height,
            )

    if best is None:
        raise RuntimeError("模板尺寸不适合当前截图，无法匹配。")
    return best
def find_best_template(screen):
    best = None
    for template_path in TEMPLATE_PATHS:
        match = find_template(screen, template_path)
        if best is None or match.score > best.score:
            best = match
    return best


def find_crab_by_color(screen):
    if screen is None or screen.ndim != 3:
        raise ValueError("螃蟹检测收到的截图无效。")

    search_x = 0
    search_y = 0
    search_screen = screen
    if SEARCH_REGION is not None:
        search_x, search_y, search_width, search_height = SEARCH_REGION
        if (
            search_x < 0
            or search_y < 0
            or search_x + search_width > screen.shape[1]
            or search_y + search_height > screen.shape[0]
        ):
            raise ValueError(f"螃蟹搜索区域超出截图范围：{SEARCH_REGION}, image={screen.shape[:2]}")
        search_screen = screen[
            search_y : search_y + search_height,
            search_x : search_x + search_width,
        ]

    hsv = cv2.cvtColor(search_screen, cv2.COLOR_BGR2HSV)
    purple = cv2.inRange(hsv, CRAB_PURPLE_LOWER, CRAB_PURPLE_UPPER)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    purple = cv2.morphologyEx(purple, cv2.MORPH_OPEN, kernel)
    purple = cv2.morphologyEx(purple, cv2.MORPH_CLOSE, kernel, iterations=2)

    contours, _ = cv2.findContours(purple, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    best_area = 0
    for contour in contours:
        area = int(cv2.contourArea(contour))
        x, y, width, height = cv2.boundingRect(contour)
        aspect = width / max(1, height)
        if area < CRAB_MIN_PURPLE_AREA:
            continue
        if not (CRAB_MIN_WIDTH <= width <= CRAB_MAX_WIDTH):
            continue
        if not (CRAB_MIN_HEIGHT <= height <= CRAB_MAX_HEIGHT):
            continue
        if not (CRAB_MIN_ASPECT <= aspect <= CRAB_MAX_ASPECT):
            continue
        if area <= best_area:
            continue

        # 通过面积和形状门槛后才给出可点击分数。
        score = min(1.0, 0.92 + area / 100000.0)
        best = MatchResult(
            template=Path("purple_color_detector"),
            score=float(score),
            x=int(x) + search_x,
            y=int(y) + search_y,
            width=int(width),
            height=int(height),
        )
        best_area = area

    return best


def find_crab(screen):
    color_match = find_crab_by_color(screen)
    if color_match is not None:
        return color_match

    # 保留模板最佳位置以便调试，但把分数压到阈值以下，禁止仅凭模板自动点击。
    template_match = find_best_template(screen)
    template_match.score = min(template_match.score, MATCH_THRESHOLD - 0.01)
    return template_match


def adb_tap(x, y):
    subprocess.run(adb_command("shell", "input", "tap", str(int(x)), str(int(y))), check=True)


def save_debug_match(screen, match, output_path):
    if not debug_settings.SAVE_ERROR_SCREENSHOTS:
        return None
    debug = screen.copy()
    cv2.rectangle(
        debug,
        (match.x, match.y),
        (match.x + match.width, match.y + match.height),
        (0, 0, 255),
        3,
    )
    cx, cy = match.center
    cv2.circle(debug, (cx, cy), 8, (0, 255, 0), -1)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imencode(".png", debug)[1].tofile(str(output_path))


def main():
    print("开始 ADB 截图并匹配模板。")
    print("模板：")
    for template_path in TEMPLATE_PATHS:
        print(f"  {template_path}")
    screen = decode_png(adb_screenshot_bytes())
    match = find_crab(screen)
    cx, cy = match.center

    print(f"使用模板：{match.template.name}")
    print(f"匹配分数：{match.score:.3f}")
    print(f"左上角：({match.x}, {match.y})")
    print(f"中心点：({cx}, {cy})")
    print(f"模板尺寸：{match.width} x {match.height}")

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    debug_path = PROJECT_ROOT / "screenshots" / "errors" / f"template_match_{timestamp}.png"
    if save_debug_match(screen, match, debug_path) is not None:
        print(f"已保存调试图：{debug_path}")
    else:
        print("调试截图保存已关闭。")

    if match.score < MATCH_THRESHOLD:
        print("匹配分数低于阈值，暂不点击。")
        return

    if not TAP_WHEN_MATCHED:
        print("匹配成功。当前设置为只定位，不自动点击。")
        print("如需测试自动点击，把 TAP_WHEN_MATCHED 改成 True。")
        return

    print("匹配成功，执行 ADB 点击。")
    adb_tap(cx, cy)


if __name__ == "__main__":
    main()
