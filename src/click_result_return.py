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

RETURN_BUTTON_TEMPLATE = PROJECT_ROOT / "screenshots" / "templates" / "result_return_button.png"

# 结算页按钮通常在屏幕下方中间偏左。
SEARCH_REGION = (300, 520, 420, 150)
MATCH_THRESHOLD = 0.85


@dataclass
class MatchResult:
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


def read_image(path):
    image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise FileNotFoundError(f"读取图片失败：{path}")
    return image


def crop_region(image, region):
    x, y, width, height = region
    return image[y : y + height, x : x + width]


def find_template(screen):
    template = read_image(RETURN_BUTTON_TEMPLATE)
    search_x, search_y, _, _ = SEARCH_REGION
    search = crop_region(screen, SEARCH_REGION)

    search_gray = cv2.cvtColor(search, cv2.COLOR_BGR2GRAY)
    template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    result = cv2.matchTemplate(search_gray, template_gray, cv2.TM_CCOEFF_NORMED)
    _, score, _, location = cv2.minMaxLoc(result)

    return MatchResult(
        score=float(score),
        x=int(location[0]) + search_x,
        y=int(location[1]) + search_y,
        width=template_gray.shape[1],
        height=template_gray.shape[0],
    )


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
    output_path = output_dir / f"result_return_{time.strftime('%Y%m%d_%H%M%S')}.png"
    cv2.imencode(".png", debug)[1].tofile(str(output_path))
    return output_path


def main():
    print("开始检测结算页返回按钮。")
    screen = adb_screenshot()
    match = find_template(screen)
    cx, cy = match.center
    debug_path = save_debug(screen, match)

    print(f"返回按钮匹配分数：{match.score:.3f}")
    print(f"返回按钮中心点：({cx}, {cy})")
    print(f"调试图：{debug_path}")

    if match.score < MATCH_THRESHOLD:
        print("返回按钮匹配分数低于阈值，暂不点击。")
        return

    if not TAP_WHEN_MATCHED:
        print("当前设置为只定位，不自动点击。")
        return

    print("点击返回按钮。")
    adb_tap(cx, cy)


if __name__ == "__main__":
    main()
