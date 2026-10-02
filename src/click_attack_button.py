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

TAP_WHEN_READY = True

ATTACK_BUTTON_TEMPLATE = PROJECT_ROOT / "screenshots" / "templates" / "attack_button.png"
ZERO_DIGIT_TEMPLATE = PROJECT_ROOT / "screenshots" / "templates" / "attempt_zero_digit.png"
DIGIT_TEMPLATE_PATHS = {
    "0": PROJECT_ROOT / "screenshots" / "templates" / "attempt_zero_digit.png",
    "4": PROJECT_ROOT / "screenshots" / "templates" / "attempt_digit_4.png",
}

# 1280x720 固定分辨率下，螃蟹详情页右下角区域。
ATTACK_SEARCH_REGION = (820, 520, 270, 170)
ATTEMPTS_REGION = (965, 535, 95, 30)

ATTACK_MATCH_THRESHOLD = 0.85
ZERO_MATCH_THRESHOLD = 0.75


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


def find_template(screen, template_path, search_region, threshold):
    template = read_image(template_path)
    search_x, search_y, _, _ = search_region
    search = crop_region(screen, search_region)

    search_gray = cv2.cvtColor(search, cv2.COLOR_BGR2GRAY)
    template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    result = cv2.matchTemplate(search_gray, template_gray, cv2.TM_CCOEFF_NORMED)
    _, score, _, location = cv2.minMaxLoc(result)

    match = MatchResult(
        score=float(score),
        x=int(location[0]) + search_x,
        y=int(location[1]) + search_y,
        width=template_gray.shape[1],
        height=template_gray.shape[0],
    )
    return match if match.score >= threshold else match


def is_no_attempts(screen):
    attempts = crop_region(screen, ATTEMPTS_REGION)
    zero_template = read_image(ZERO_DIGIT_TEMPLATE)

    # 左侧次数文本区域，跳过剑图标和右侧 "/40"。
    left_number = attempts[:, 20:58]
    left_gray = cv2.cvtColor(left_number, cv2.COLOR_BGR2GRAY)
    zero_gray = cv2.cvtColor(zero_template, cv2.COLOR_BGR2GRAY)

    result = cv2.matchTemplate(left_gray, zero_gray, cv2.TM_CCOEFF_NORMED)
    _, score, _, location = cv2.minMaxLoc(result)
    zero_x, zero_y = location

    # 如果是 40/40、30/40、10/40，0 的左边还会有一个数字。
    # 如果是 0/40，0 的左边基本是空的。
    _, bright = cv2.threshold(left_gray, 180, 255, cv2.THRESH_BINARY)
    left_of_zero = bright[:, : max(0, zero_x - 2)]
    left_pixels = int(cv2.countNonZero(left_of_zero))

    return score >= ZERO_MATCH_THRESHOLD and left_pixels < 8, score, left_pixels


def match_digit(digit_image):
    digit_gray = cv2.cvtColor(digit_image, cv2.COLOR_BGR2GRAY)
    best_digit = None
    best_score = -1.0

    for digit, template_path in DIGIT_TEMPLATE_PATHS.items():
        template = read_image(template_path)
        template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
        resized = cv2.resize(
            digit_gray,
            (template_gray.shape[1], template_gray.shape[0]),
            interpolation=cv2.INTER_AREA,
        )
        result = cv2.matchTemplate(resized, template_gray, cv2.TM_CCOEFF_NORMED)
        _, score, _, _ = cv2.minMaxLoc(result)
        if score > best_score:
            best_score = float(score)
            best_digit = digit

    if best_score < 0.65:
        return "?", best_score
    return best_digit, best_score


def read_attempts_text(screen):
    attempts = crop_region(screen, ATTEMPTS_REGION)
    left_number = attempts[:, 20:58]
    gray = cv2.cvtColor(left_number, cv2.COLOR_BGR2GRAY)
    _, bright = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)

    num, labels, stats, _ = cv2.connectedComponentsWithStats(bright, 8)
    boxes = []
    for i in range(1, num):
        x, y, width, height, area = stats[i]
        if area >= 20 and height >= 8:
            boxes.append((int(x), int(y), int(width), int(height)))

    boxes.sort(key=lambda item: item[0])
    digits = []
    scores = []
    for x, y, width, height in boxes:
        pad = 3
        x1 = max(0, x - pad)
        y1 = max(0, y - pad)
        x2 = min(left_number.shape[1], x + width + pad)
        y2 = min(left_number.shape[0], y + height + pad)
        digit, score = match_digit(left_number[y1:y2, x1:x2])
        digits.append(digit)
        scores.append(score)

    if not digits:
        return "?", scores
    return "".join(digits), scores


def adb_tap(x, y):
    subprocess.run(adb_command("shell", "input", "tap", str(int(x)), str(int(y))), check=True)


def save_debug(screen, attack_match, no_attempts, zero_score, left_pixels):
    if not debug_settings.SAVE_ERROR_SCREENSHOTS:
        return None
    debug = screen.copy()
    ax, ay, aw, ah = ATTACK_SEARCH_REGION
    cv2.rectangle(debug, (ax, ay), (ax + aw, ay + ah), (255, 0, 0), 2)

    tx, ty, tw, th = ATTEMPTS_REGION
    cv2.rectangle(debug, (tx, ty), (tx + tw, ty + th), (0, 255, 255), 2)

    cv2.rectangle(
        debug,
        (attack_match.x, attack_match.y),
        (attack_match.x + attack_match.width, attack_match.y + attack_match.height),
        (0, 0, 255),
        3,
    )
    cx, cy = attack_match.center
    cv2.circle(debug, (cx, cy), 8, (0, 255, 0), -1)

    text = f"no_attempts={no_attempts} zero={zero_score:.3f} left={left_pixels}"
    cv2.putText(debug, text, (20, 700), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)

    output_dir = PROJECT_ROOT / "screenshots" / "errors"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"attack_click_{time.strftime('%Y%m%d_%H%M%S')}.png"
    cv2.imencode(".png", debug)[1].tofile(str(output_path))
    return output_path


def main():
    print("开始检测螃蟹进攻界面。")
    screen = adb_screenshot()

    attempts_text, attempts_scores = read_attempts_text(screen)
    no_attempts, zero_score, left_pixels = is_no_attempts(screen)
    print(f"剩余进攻次数：{attempts_text}/40")
    print(f"次数判断：no_attempts={no_attempts}, zero_score={zero_score:.3f}, left_pixels={left_pixels}")
    if no_attempts:
        print("剩余进攻次数为 0，停止进攻。")
        return

    attack_match = find_template(
        screen,
        ATTACK_BUTTON_TEMPLATE,
        ATTACK_SEARCH_REGION,
        ATTACK_MATCH_THRESHOLD,
    )
    cx, cy = attack_match.center
    debug_path = save_debug(screen, attack_match, no_attempts, zero_score, left_pixels)

    print(f"攻击按钮匹配分数：{attack_match.score:.3f}")
    print(f"攻击按钮中心点：({cx}, {cy})")
    print(f"调试图：{debug_path}")

    if attack_match.score < ATTACK_MATCH_THRESHOLD:
        print("攻击按钮匹配分数低于阈值，暂不点击。")
        return

    if not TAP_WHEN_READY:
        print("当前设置为只定位，不自动点击。")
        return

    print("次数充足，点击攻击按钮。")
    adb_tap(cx, cy)


if __name__ == "__main__":
    main()
