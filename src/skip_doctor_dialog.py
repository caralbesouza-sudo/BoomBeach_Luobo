import subprocess
import time
from pathlib import Path

import cv2
import numpy as np
import debug_settings


PROJECT_ROOT = Path(__file__).resolve().parents[1]

ADB_PATH = "adb"
ADB_SERIAL = "emulator-5554"

# 博士对话可能连续出现多页。只有检测到右侧白色气泡时才点击，避免盲点地图。
DIALOG_REGION = (680, 340, 340, 190)
DIALOG_BRIGHT_RATIO_THRESHOLD = 0.35
TAP_X = 850
TAP_Y = 430

WAIT_BEFORE_CHECK_SECONDS = 0.8
CHECK_INTERVAL_SECONDS = 0.55
DISMISS_TIMEOUT_SECONDS = 12.0
MIN_MONITOR_SECONDS = 2.5
CLEAR_FRAMES_REQUIRED = 2


def adb_command(*parts):
    command = [ADB_PATH]
    if ADB_SERIAL:
        command.extend(["-s", ADB_SERIAL])
    command.extend(parts)
    return command


def adb_tap(x, y):
    subprocess.run(adb_command("shell", "input", "tap", str(int(x)), str(int(y))), check=True)


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


def crop_region(image, region):
    if image is None or image.ndim != 3:
        raise ValueError("博士对话检测收到的截图无效。")
    x, y, width, height = region
    if x < 0 or y < 0 or x + width > image.shape[1] or y + height > image.shape[0]:
        raise ValueError(f"博士对话检测区域超出截图范围：region={region}, image={image.shape[:2]}")
    return image[y : y + height, x : x + width]


def dialog_bright_ratio(screen):
    region = crop_region(screen, DIALOG_REGION)
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    bubble_mask = cv2.inRange(
        hsv,
        np.array([0, 0, 170], dtype=np.uint8),
        np.array([179, 80, 255], dtype=np.uint8),
    )
    return float(cv2.countNonZero(bubble_mask)) / max(1, bubble_mask.size)


def is_dialog_visible(screen):
    return dialog_bright_ratio(screen) >= DIALOG_BRIGHT_RATIO_THRESHOLD


def save_debug(screen, label="doctor_dialog_timeout"):
    if not debug_settings.SAVE_ERROR_SCREENSHOTS:
        return None
    output_dir = PROJECT_ROOT / "screenshots" / "errors"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{label}_{time.strftime('%Y%m%d_%H%M%S')}.png"
    cv2.imencode(".png", screen)[1].tofile(str(output_path))
    return output_path


def main():
    print("开始检测并跳过博士多页对话。")
    time.sleep(WAIT_BEFORE_CHECK_SECONDS)

    started_at = time.monotonic()
    clear_frames = 0
    dismissed_pages = 0
    last_screen = None

    while time.monotonic() - started_at < DISMISS_TIMEOUT_SECONDS:
        last_screen = adb_screenshot()
        ratio = dialog_bright_ratio(last_screen)
        elapsed = time.monotonic() - started_at

        if ratio >= DIALOG_BRIGHT_RATIO_THRESHOLD:
            clear_frames = 0
            dismissed_pages += 1
            print(f"检测到博士对话第 {dismissed_pages} 页（气泡占比 {ratio:.3f}），点击继续。")
            adb_tap(TAP_X, TAP_Y)
        else:
            clear_frames += 1
            if elapsed >= MIN_MONITOR_SECONDS and clear_frames >= CLEAR_FRAMES_REQUIRED:
                print(f"博士对话已清除，共处理 {dismissed_pages} 页。")
                return True

        time.sleep(CHECK_INTERVAL_SECONDS)

    if last_screen is not None and is_dialog_visible(last_screen):
        debug_path = save_debug(last_screen)
        suffix = f"：{debug_path}" if debug_path else "（调试截图保存已关闭）"
        raise RuntimeError(f"博士对话在 {DISMISS_TIMEOUT_SECONDS:.0f} 秒内未清除{suffix}")

    print(f"博士对话检测结束，共处理 {dismissed_pages} 页。")
    return True


if __name__ == "__main__":
    main()
