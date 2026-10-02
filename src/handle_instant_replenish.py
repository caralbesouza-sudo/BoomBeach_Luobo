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

TAP_WHEN_DECIDED = True
WAIT_AFTER_EXPENSIVE_SECONDS = 60

DIAMOND_BUTTON_TEMPLATE = PROJECT_ROOT / "screenshots" / "templates" / "instant_replenish_button_left.png"
CLOSE_X_TEMPLATE = PROJECT_ROOT / "screenshots" / "templates" / "popup_close_x.png"

# 立即补兵弹窗通常在屏幕中间，按钮在弹窗下方。
BUTTON_SEARCH_REGION = (350, 250, 600, 380)
CLOSE_SEARCH_REGION = (300, 80, 700, 260)

BUTTON_MATCH_THRESHOLD = 0.80
CLOSE_MATCH_THRESHOLD = 0.75


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


def read_image(path, flags=cv2.IMREAD_COLOR):
    image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), flags)
    if image is None:
        raise FileNotFoundError(f"读取图片失败：{path}")
    return image


def crop_region(image, region):
    x, y, width, height = region
    return image[y : y + height, x : x + width]


def find_template(screen, template_path, search_region, threshold):
    template = read_image(template_path, cv2.IMREAD_UNCHANGED)
    search_x, search_y, _, _ = search_region
    search = crop_region(screen, search_region)
    search_gray = cv2.cvtColor(search, cv2.COLOR_BGR2GRAY)

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
    match = MatchResult(
        score=float(score),
        x=int(location[0]) + search_x,
        y=int(location[1]) + search_y,
        width=template_gray.shape[1],
        height=template_gray.shape[0],
    )
    return match


def estimate_diamond_cost(screen, button_match):
    # 数字在钻石按钮右侧。只判断一位数/两位数即可：一位数 < 10，两位数 >= 10。
    number_x = button_match.x + 82
    number_y = button_match.y + 18
    number_w = 80
    number_h = 42
    number_area = screen[number_y : number_y + number_h, number_x : number_x + number_w]

    gray = cv2.cvtColor(number_area, cv2.COLOR_BGR2GRAY)
    dark = gray < 90
    ys, xs = np.where(dark)
    if len(xs) == 0:
        return "unknown", 0

    text_width = int(xs.max() - xs.min() + 1)
    if text_width >= 35:
        return ">=10", text_width
    if text_width >= 8:
        return "<10", text_width
    return "unknown", text_width


def adb_tap(x, y):
    subprocess.run(adb_command("shell", "input", "tap", str(int(x)), str(int(y))), check=True)


def save_debug(screen, button_match, close_match, decision, digit_count):
    if not debug_settings.SAVE_ERROR_SCREENSHOTS:
        return None
    debug = screen.copy()

    for region, color in [(BUTTON_SEARCH_REGION, (255, 0, 0)), (CLOSE_SEARCH_REGION, (255, 255, 0))]:
        x, y, width, height = region
        cv2.rectangle(debug, (x, y), (x + width, y + height), color, 2)

    cv2.rectangle(
        debug,
        (button_match.x, button_match.y),
        (button_match.x + button_match.width, button_match.y + button_match.height),
        (0, 0, 255),
        3,
    )
    bx, by = button_match.center
    cv2.circle(debug, (bx, by), 8, (0, 255, 0), -1)

    cv2.rectangle(
        debug,
        (close_match.x, close_match.y),
        (close_match.x + close_match.width, close_match.y + close_match.height),
        (0, 128, 255),
        3,
    )
    cx, cy = close_match.center
    cv2.circle(debug, (cx, cy), 8, (0, 255, 255), -1)

    text = f"decision={decision} digits={digit_count}"
    cv2.putText(debug, text, (20, 700), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)

    output_dir = PROJECT_ROOT / "screenshots" / "errors"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"instant_replenish_{time.strftime('%Y%m%d_%H%M%S')}.png"
    cv2.imencode(".png", debug)[1].tofile(str(output_path))
    return output_path


def main():
    print("开始处理立即完成兵力补充弹窗。")
    screen = adb_screenshot()
    button_match = find_template(screen, DIAMOND_BUTTON_TEMPLATE, BUTTON_SEARCH_REGION, BUTTON_MATCH_THRESHOLD)
    close_match = find_template(screen, CLOSE_X_TEMPLATE, CLOSE_SEARCH_REGION, CLOSE_MATCH_THRESHOLD)
    decision, digit_count = estimate_diamond_cost(screen, button_match)
    debug_path = save_debug(screen, button_match, close_match, decision, digit_count)

    print(f"钻石补兵按钮匹配分数：{button_match.score:.3f}")
    print(f"关闭按钮匹配分数：{close_match.score:.3f}")
    print(f"价格判断：{decision}，数字块数量：{digit_count}")
    print(f"调试图：{debug_path}")

    if button_match.score < BUTTON_MATCH_THRESHOLD:
        print("未检测到钻石补兵按钮，暂不点击。")
        return
    if close_match.score < CLOSE_MATCH_THRESHOLD:
        print("未检测到关闭按钮，暂不点击。")
        return

    should_wait_after_tap = False
    if decision == ">=10":
        target = close_match.center
        should_wait_after_tap = True
        print("钻石数量大于等于 10，点击关闭。")
    elif decision == "<10":
        target = (button_match.x + 110, button_match.y + button_match.height // 2)
        print("钻石数量小于 10，点击钻石补兵。")
    else:
        print("无法判断钻石数量，为安全起见暂不点击。")
        return

    if not TAP_WHEN_DECIDED:
        print(f"当前设置为只定位，不点击。目标点：{target}")
        return

    adb_tap(*target)

    if should_wait_after_tap:
        print(f"等待 {WAIT_AFTER_EXPENSIVE_SECONDS} 秒后再重新尝试补兵。")
        time.sleep(WAIT_AFTER_EXPENSIVE_SECONDS)


if __name__ == "__main__":
    main()
