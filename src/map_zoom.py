"""Two-finger pinch for the calibrated emulator, using Linux type-B touch slots.

Protocol: https://www.kernel.org/doc/html/latest/input/multi-touch-protocol.html
No device files or screenshot files are created.
"""
from dataclasses import dataclass
import re
import subprocess


# 每根手指向内移动的像素数；越小，缩小幅度越轻。
PINCH_INWARD_PIXELS = 40


@dataclass(frozen=True)
class TouchDevice:
    path: str
    pressure: bool
    touch_key: bool
    finger_key: bool


def parse_touch_device(capabilities, screen_size=(1280, 720)):
    for block in re.split(r"(?=add device \d+:)", capabilities):
        path = re.search(r"add device \d+: (/dev/input/event\d+)", block)
        if not path or "INPUT_PROP_DIRECT" not in block:
            continue
        axes = {
            name: (int(low), int(high))
            for name, low, high in re.findall(
                r"(ABS_MT_\w+)\s*:\s*value\s+-?\d+,\s*min\s+(-?\d+),\s*max\s+(-?\d+)", block)
        }
        if axes.get("ABS_MT_SLOT", (0, 0))[1] < 1 or "ABS_MT_TRACKING_ID" not in axes:
            continue
        # 此项目只校准了横屏 1280x720，拒绝猜测其它触摸坐标系。
        if (axes.get("ABS_MT_POSITION_X") != (0, screen_size[0] - 1)
                or axes.get("ABS_MT_POSITION_Y") != (0, screen_size[1] - 1)):
            continue
        return TouchDevice(path.group(1), "ABS_MT_PRESSURE" in axes,
                           "BTN_TOUCH" in block, "BTN_TOOL_FINGER" in block)
    raise RuntimeError("未找到与 1280x720 横屏对应的双指触摸设备，无法缩小地图。")


def discover_touch_device(adb_command):
    result = subprocess.run(adb_command("shell", "getevent", "-lp"),
                            capture_output=True, text=True, check=True, timeout=10)
    device = parse_touch_device(result.stdout)
    subprocess.run(adb_command("shell", "test", "-w", device.path),
                   capture_output=True, text=True, check=True, timeout=10)
    return device


def release_commands(device):
    commands = []
    for slot in (0, 1):
        commands.append(f"sendevent {device.path} 3 47 {slot} || :")
        if device.pressure:
            commands.append(f"sendevent {device.path} 3 58 0 || :")
        commands.append(f"sendevent {device.path} 3 57 -1 || :")
    if device.touch_key:
        commands.append(f"sendevent {device.path} 1 330 0 || :")
    if device.finger_key:
        commands.append(f"sendevent {device.path} 1 325 0 || :")
    commands.append(f"sendevent {device.path} 0 0 0 || :")
    return commands


def build_zoom_script(device):
    if not re.fullmatch(r"/dev/input/event\d+", device.path):
        raise ValueError("触摸设备路径无效。")
    lines = ["set -e", f"ev() {{ sendevent {device.path} \"$@\"; }}",
             "release_contacts() {", *release_commands(device), "}",
             "trap 'release_contacts' EXIT", "trap 'exit 1' HUP INT TERM"]
    # 两根手指同时从中央两侧向内合拢，避开顶部按钮和底部兵种卡。
    # 仅轻微缩小一次：16 步，默认间距由 480 缩至 400 像素。
    for gesture in range(1):
        lines.append("echo '地图缩小 1/1'")
        if device.touch_key:
            lines.append("ev 1 330 1")
        if device.finger_key:
            lines.append("ev 1 325 1")
        for slot, x in ((0, 400), (1, 880)):
            lines.extend([f"ev 3 47 {slot}", f"ev 3 57 {100 + gesture * 2 + slot}",
                          f"ev 3 53 {x}", "ev 3 54 360"])
            if device.pressure:
                lines.append("ev 3 58 1")
        lines.append("ev 0 0 0")
        for step in range(1, 17):
            delta = round(PINCH_INWARD_PIXELS * step / 16)
            lines.append("sleep 0.035")
            for slot, x in ((0, 400 + delta), (1, 880 - delta)):
                lines.extend([f"ev 3 47 {slot}", f"ev 3 53 {x}", "ev 3 54 360"])
            lines.append("ev 0 0 0")
        lines.extend(["release_contacts", "sleep 0.6"])
    return "\n".join(lines) + "\n"


def zoom_out_once(adb_command, dry_run=False):
    if dry_run:
        print("[DRY_RUN] 战斗地图双指缩小 1 次。")
        return
    device = discover_touch_device(adb_command)
    script = build_zoom_script(device)
    try:
        result = subprocess.run(adb_command("shell", "sh"), input=script,
                                text=True, capture_output=True, check=True, timeout=20)
    except (subprocess.SubprocessError, OSError) as exc:
        # 超时/中断时再尝试抬起双指，不让未完成的触摸留在屏幕上。
        try:
            subprocess.run(adb_command("shell", "sh"),
                           input="\n".join(release_commands(device)) + "\n",
                           text=True, capture_output=True, timeout=5)
        except (subprocess.SubprocessError, OSError):
            pass
        raise RuntimeError("地图缩小手势执行失败，已停止本次下兵。") from exc
    print(result.stdout.strip())
