import subprocess
from dataclasses import dataclass
from pathlib import Path
import time
from datetime import datetime

import cv2
import numpy as np

import click_result_return
import debug_settings
import landing_geometry
import map_zoom


ADB_PATH = "adb"
ADB_SERIAL = "emulator-5554"
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 第一次先保持 True，只打印动作不点击；坐标确认后再改成 False。
DRY_RUN = False
EXPECTED_SCREEN_SIZE = (1280, 720)

# 进入战斗后通常会有倒计时和战斗详情弹窗。
WAIT_FOR_BATTLE_START_SECONDS = 1

# 按“左下方 -> 正下方 -> 右下方”依次搜索。每组动作先完整移动到目标视角，
# 再稳定截图识别，避免旧逻辑在左下拖两次后只回退一次而把“中间”识别成偏左位置。
# 手指向右上拖动会让视角移向左下；再水平回移到正下，最后斜向移动到右下。
BEACH_SEARCH_STEPS = [
    ("left_bottom", [(640, 420, 760, 330, 280), (640, 420, 760, 330, 280)]),
    ("bottom_center", [(760, 360, 640, 360, 280), (760, 360, 640, 360, 280)]),
    ("right_bottom", [(640, 420, 520, 330, 280), (640, 420, 520, 330, 280)]),
]

# 卡片位置：这些是 1280x720 下的大致固定位置，后续可微调。
HERO_CARD_POINT = (640, 665)
TROOP_CARD_POINTS = [
    (50, 665),
    (150, 665),
    (250, 665),
    (350, 665),
    (50, 565),
    (150, 565),
    (250, 565),
    (350, 565),
]

# 下面这些需要根据第一种海滩截图填写。
HERO_LANDING_POINT = None
TROOP_LANDING_POINTS = []

# 机器小怪：技能按钮坐标 + 释放目标点。
ROBOT_SKILL_POINT = (1030, 565)
ROBOT_TARGET_POINTS = []
ROBOT_HERO_CYCLE_ROUNDS = 6
FIRST_HERO_SKILL_SECONDS = 3
NEXT_HERO_SKILL_SECONDS = 3
RESULT_CHECK_INTERVAL_SECONDS = 1.0

# 震爆弹：技能按钮坐标 + 释放目标点。没有就保持空。
SHOCK_SKILL_POINT = None
SHOCK_TARGET_POINTS = []

DEPLOY_INTERVAL_SECONDS = 0.4
WAIT_AFTER_CARD_SELECT_SECONDS = 0.12
WAIT_AFTER_HERO_DEPLOY_SECONDS = 1.0
WAIT_AFTER_OPENING_ROBOT_SECONDS = 1.0

TRIPLE_SPEED_TEMPLATE = PROJECT_ROOT / "screenshots" / "templates" / "3bei.png"
# 1280x720 下撤退按钮的下方，避免搜索到技能卡片和地图图案。
TRIPLE_SPEED_SEARCH_REGION = (1080, 65, 200, 90)
TRIPLE_SPEED_MATCH_THRESHOLD = 0.90
TRIPLE_SPEED_ATTEMPTS = 3
TRIPLE_SPEED_RETRY_SECONDS = 0.4

BEACH_TEMPLATE = PROJECT_ROOT / "screenshots" / "templates" / "beach_landing_sample_1.png"
BEACH_MATCH_THRESHOLD = 0.55
RIGHT_EDGE_TEMPLATE_FRACTION = 0.45
RIGHT_EDGE_MIN_CENTER_X = 700
BEACH_DETECT_ATTEMPTS = 4
BEACH_DETECT_INTERVAL_SECONDS = 0.3
LANDING_STABILITY_DISTANCE = 24
BEACH_DEBUG_DIR = PROJECT_ROOT / "screenshots" / "errors"
BEACH_DEBUG_STAGE_NAME = "未知阶段"

# 通用正面海滩检测：寻找画面中央、岸线下方的大面积连续海水。
WATER_SEARCH_REGION = (180, 260, 800, 360)
WATER_HSV_LOWER = np.array([90, 70, 40], dtype=np.uint8)
WATER_HSV_UPPER = np.array([115, 255, 220], dtype=np.uint8)
WATER_MIN_AREA = 20000
WATER_MIN_WIDTH = 300
WATER_MIN_HEIGHT = 120
WATER_TOP_MIN_Y = 330
WATER_TOP_MAX_Y = 520
WATER_CENTER_MIN_X = 400
WATER_CENTER_MAX_X = 850

# 白色半透明登陆范围通常由两条较长的水平边界包围。只在同时找到上、下边界
# 时才允许使用通用正面海滩检测，避免仅凭海水位置猜测英雄登陆点。
LANDING_ZONE_SEARCH_REGION = (220, 280, 800, 260)
LANDING_ZONE_MIN_LINE_LENGTH = 300
LANDING_ZONE_MIN_HEIGHT = 70
LANDING_ZONE_MAX_HEIGHT = 160
LANDING_ZONE_MIN_OVERLAP = 300


@dataclass
class MatchResult:
    score: float
    x: int
    y: int
    width: int
    height: int
    method: str = "unknown"
    landing_point: tuple = None
    polygon: tuple = ()

    @property
    def center(self):
        if self.landing_point is not None:
            return self.landing_point
        return self.x + self.width // 2, self.y + self.height // 2


def adb_command(*parts):
    command = [ADB_PATH]
    if ADB_SERIAL:
        command.extend(["-s", ADB_SERIAL])
    command.extend(parts)
    return command


def run_adb(*parts):
    if DRY_RUN:
        print("[DRY_RUN]", " ".join(adb_command(*parts)))
        return
    subprocess.run(adb_command(*parts), check=True)


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


def find_beach_by_warning_line(screen):
    height, width = screen.shape[:2]
    search_top = 260
    search_bottom = min(620, height - 80)
    search_left = 180
    search_right = width - 180
    region = screen[search_top:search_bottom, search_left:search_right]

    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    yellow = cv2.inRange(hsv, np.array([18, 65, 90]), np.array([45, 255, 255]))

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 3))
    yellow = cv2.morphologyEx(yellow, cv2.MORPH_CLOSE, kernel, iterations=2)

    contours, _ = cv2.findContours(yellow, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    best_score = 0.0

    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w < 120 or h < 5:
            continue
        if y + search_top > height - 160:
            continue

        box = yellow[max(0, y - 4): min(yellow.shape[0], y + h + 4), x: x + w]
        yellow_density = cv2.countNonZero(box) / max(1, box.size)
        water_top = min(region.shape[0], y + h + 55)
        water_bottom = min(region.shape[0], y + h + 160)
        water_box = hsv[water_top:water_bottom, x: x + w]
        if water_box.size == 0:
            continue

        water_mask = cv2.inRange(water_box, np.array([75, 35, 55]), np.array([105, 255, 255]))
        water_density = cv2.countNonZero(water_mask) / max(1, water_mask.size)
        if water_density < 0.08:
            continue

        aspect_score = min(1.0, w / 320)
        density_score = min(1.0, yellow_density / 0.18)
        water_score = min(1.0, water_density / 0.22)
        score = 0.66 + 0.20 * aspect_score + 0.06 * density_score + 0.08 * water_score

        if score > best_score:
            landing_x = search_left + x + w // 2
            landing_y = search_top + y + 48
            match_width = max(260, min(width - search_left, w + 80))
            match_height = 130
            match_x = max(0, landing_x - match_width // 2)
            match_y = max(0, search_top + y - 12)
            best = MatchResult(
                score=float(score),
                x=int(match_x),
                y=int(match_y),
                width=int(min(match_width, width - match_x)),
                height=int(min(match_height, height - match_y)),
                method="warning_line",
            )
            best_score = score

    return best


def find_beach_by_template(screen):
    template = read_image(BEACH_TEMPLATE)
    screen_gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
    template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    result = cv2.matchTemplate(screen_gray, template_gray, cv2.TM_CCOEFF_NORMED)
    _, score, _, location = cv2.minMaxLoc(result)
    return MatchResult(
        score=float(score),
        x=int(location[0]),
        y=int(location[1]),
        width=template_gray.shape[1],
        height=template_gray.shape[0],
        method="template",
    )


def find_right_edge_beach_by_template(screen):
    """识别右下角被战斗详情遮挡的镜像斜向海滩。"""
    template = read_image(BEACH_TEMPLATE)
    screen_gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
    template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)

    visible_width = max(1, int(round(template_gray.shape[1] * RIGHT_EDGE_TEMPLATE_FRACTION)))
    visible_right = template_gray[:, -visible_width:]
    mirrored = cv2.flip(visible_right, 1)
    result = cv2.matchTemplate(screen_gray, mirrored, cv2.TM_CCOEFF_NORMED)
    _, score, _, location = cv2.minMaxLoc(result)

    match = MatchResult(
        score=float(score),
        x=int(location[0]),
        y=int(location[1]),
        width=mirrored.shape[1],
        height=mirrored.shape[0],
        method="right_edge_template",
    )
    if match.center[0] < RIGHT_EDGE_MIN_CENTER_X:
        match.score = min(match.score, BEACH_MATCH_THRESHOLD - 0.01)
    return match


def find_landing_zone_by_outline(screen):
    search_x, search_y, search_width, search_height = LANDING_ZONE_SEARCH_REGION
    region = screen[
        search_y : search_y + search_height,
        search_x : search_x + search_width,
    ]
    if region.shape[:2] != (search_height, search_width):
        return None

    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 70, 160)
    lines = cv2.HoughLinesP(
        edges,
        1,
        np.pi / 180,
        threshold=90,
        minLineLength=LANDING_ZONE_MIN_LINE_LENGTH,
        maxLineGap=35,
    )
    if lines is None:
        return None

    horizontal_lines = []
    for raw_line in lines[:, 0]:
        x1, y1, x2, y2 = [int(value) for value in raw_line]
        if x2 < x1:
            x1, x2, y1, y2 = x2, x1, y2, y1
        if x2 - x1 < LANDING_ZONE_MIN_LINE_LENGTH or abs(y2 - y1) > 8:
            continue
        horizontal_lines.append(
            (
                x1 + search_x,
                int(round((y1 + y2) / 2)) + search_y,
                x2 + search_x,
            )
        )

    best_pair = None
    best_pair_score = None
    for top in horizontal_lines:
        for bottom in horizontal_lines:
            zone_height = bottom[1] - top[1]
            if not (LANDING_ZONE_MIN_HEIGHT <= zone_height <= LANDING_ZONE_MAX_HEIGHT):
                continue

            overlap_left = max(top[0], bottom[0])
            overlap_right = min(top[2], bottom[2])
            overlap_width = overlap_right - overlap_left
            if overlap_width < LANDING_ZONE_MIN_OVERLAP:
                continue

            # 优先选择横向重叠最多的边界对；同宽时选择更高的登陆带，避免点到
            # 靠近海水的下边缘。
            pair_score = (overlap_width, zone_height)
            if best_pair_score is None or pair_score > best_pair_score:
                best_pair = (overlap_left, top[1], overlap_right, bottom[1])
                best_pair_score = pair_score

    if best_pair is None:
        return None

    left, top, right, bottom = best_pair
    return MatchResult(
        score=min(0.99, 0.86 + (right - left) / 5000.0),
        x=int(left),
        y=int(top),
        width=int(right - left),
        height=int(bottom - top),
        method="landing_zone",
    )


def find_beach_by_water_shore(screen):
    search_x, search_y, search_width, search_height = WATER_SEARCH_REGION
    region = screen[
        search_y : search_y + search_height,
        search_x : search_x + search_width,
    ]
    if region.shape[:2] != (search_height, search_width):
        return None

    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    water = cv2.inRange(hsv, WATER_HSV_LOWER, WATER_HSV_UPPER)
    water = cv2.morphologyEx(
        water,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)),
    )
    water = cv2.morphologyEx(
        water,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15)),
        iterations=2,
    )

    component_count, _, stats, centroids = cv2.connectedComponentsWithStats(water, 8)
    best = None
    best_area = 0
    for index in range(1, component_count):
        x, y, width, height, area = [int(value) for value in stats[index]]
        absolute_x = x + search_x
        absolute_y = y + search_y
        center_x = float(centroids[index][0] + search_x)

        if area < WATER_MIN_AREA:
            continue
        if width < WATER_MIN_WIDTH or height < WATER_MIN_HEIGHT:
            continue
        if not (WATER_TOP_MIN_Y <= absolute_y <= WATER_TOP_MAX_Y):
            continue
        if not (WATER_CENTER_MIN_X <= center_x <= WATER_CENTER_MAX_X):
            continue
        if area <= best_area:
            continue

        landing_zone = find_landing_zone_by_outline(screen)
        if landing_zone is None:
            continue

        best = landing_zone
        best_area = area

    return best


def find_beach(screen, preferred_point=None):
    # 所有方向都要同时验证岸线、灰色滩面和外侧海水。旧模板函数仅供离线诊断，
    # 模板匹配框中心不能证明是可登陆点，不再用它直接驱动点击。
    zone = landing_geometry.find_landing_zone(screen, preferred_point=preferred_point)
    return landing_zone_match(zone)


def landing_zone_match(zone, method="verified_landing_zone"):
    if zone is None:
        return MatchResult(0.0, 0, 0, 0, 0, "no_verified_landing_zone")
    points = np.array(zone.polygon, dtype=np.int32)
    x, y, width, height = cv2.boundingRect(points)
    return MatchResult(float(zone.score), x, y, width, height,
                       method, zone.point, zone.polygon)


def save_beach_debug(screen, match, step_name):
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
    if match.polygon:
        cv2.polylines(debug, [np.array(match.polygon, dtype=np.int32)], True, (0, 255, 255), 2)
    cv2.putText(debug, f"{match.method} {match.score:.3f} point={match.center}",
                (190, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
    BEACH_DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    output_path = BEACH_DEBUG_DIR / f"battle_beach_match_{step_name}.png"
    cv2.imencode(".png", debug)[1].tofile(str(output_path))
    cv2.imencode(".png", screen)[1].tofile(str(BEACH_DEBUG_DIR / f"raw_{step_name}.png"))
    return output_path


def set_beach_debug_stage(stage_name):
    global BEACH_DEBUG_STAGE_NAME
    BEACH_DEBUG_STAGE_NAME = str(Path(stage_name) / datetime.now().strftime("%Y%m%d_%H%M%S_%f"))


def detect_landing_point(step_name, debug_records, landing_reference=None):
    previous_match = None

    for attempt in range(1, BEACH_DETECT_ATTEMPTS + 1):
        screen = adb_screenshot()
        previous_point = previous_match.center if previous_match is not None else None
        match = find_beach(screen, preferred_point=previous_point)
        if match.score < BEACH_MATCH_THRESHOLD and landing_reference:
            zone = landing_geometry.recover_occluded_zone(
                screen, landing_reference["screen"], landing_reference["zone"], previous_point)
            match = landing_zone_match(zone, "registered_landing_zone")
        print(
            f"[{step_name}] 第 {attempt} 次登陆区域验证分数：{match.score:.3f}，"
            f"方式：{match.method}，中心点：{match.center}"
        )

        debug_records.append((f"{step_name}_{attempt}", screen, match))
        if match.score >= BEACH_MATCH_THRESHOLD:
            if previous_match is not None:
                distance = np.linalg.norm(np.array(match.center) - previous_match.center)
                if distance <= LANDING_STABILITY_DISTANCE:
                    print(f"[{step_name}] 连续两帧确认登陆点：{match.center}")
                    return match
            previous_match = match
        else:
            previous_match = None
        time.sleep(BEACH_DETECT_INTERVAL_SECONDS)

    print(f"[{step_name}] 未得到稳定的可登陆区域，继续调整视角。")
    return MatchResult(0.0, 0, 0, 0, 0, "unstable_or_missing_zone")


def save_beach_debug_records(debug_records, result_label):
    if not debug_settings.SAVE_ERROR_SCREENSHOTS:
        print(f"海滩识别{result_label}，调试截图保存已关闭。")
        return None
    output_dir = BEACH_DEBUG_DIR / BEACH_DEBUG_STAGE_NAME
    output_dir.mkdir(parents=True, exist_ok=True)

    for step_name, screen, match in debug_records:
        old_debug_dir = BEACH_DEBUG_DIR
        try:
            globals()["BEACH_DEBUG_DIR"] = output_dir
            save_beach_debug(screen, match, step_name)
        finally:
            globals()["BEACH_DEBUG_DIR"] = old_debug_dir

    print(f"海滩识别{result_label}，调试图已保存到：{output_dir}")
    return output_dir


def search_landing_point(reference_out=None):
    debug_records = []
    if reference_out is not None:
        reference_out.clear()

    for step_name, swipes in BEACH_SEARCH_STEPS:
        print(f"移动视角到 {step_name}。")
        for index, action in enumerate(swipes, start=1):
            swipe(action, f"{step_name} 视角调整 {index}")

        match = detect_landing_point(step_name, debug_records)
        if match.score >= BEACH_MATCH_THRESHOLD:
            print(f"已在 {step_name} 找到海滩，停止继续拖动。")
            save_beach_debug_records(debug_records, "成功")
            if reference_out is not None:
                # Keep only this battle's final verified frame; it is passed
                # explicitly to deployment, never retained across stages.
                reference_out.update(screen=debug_records[-1][1].copy(), zone=match)
            return match.center

        print(f"{step_name} 未找到海滩，继续搜索下一个方向。")

    save_beach_debug_records(debug_records, "失败")
    raise RuntimeError("左下方、正下方、右下方都没有识别到海滩，暂不下兵。")


def tap(point, label, settle_seconds=0.12):
    if point is None:
        raise ValueError(f"{label} 坐标还没有填写。")
    x, y = point
    if not (0 <= x < EXPECTED_SCREEN_SIZE[0] and 0 <= y < EXPECTED_SCREEN_SIZE[1]):
        raise ValueError(f"{label} 坐标超出屏幕范围：{point}")
    print(f"点击 {label}: ({x}, {y})")
    run_adb("shell", "input", "tap", str(int(x)), str(int(y)))
    if settle_seconds > 0:
        time.sleep(settle_seconds)


def swipe(action, label):
    start_x, start_y, end_x, end_y, duration_ms = action
    print(f"拖动 {label}: ({start_x}, {start_y}) -> ({end_x}, {end_y}), {duration_ms}ms")
    run_adb(
        "shell",
        "input",
        "swipe",
        str(int(start_x)),
        str(int(start_y)),
        str(int(end_x)),
        str(int(end_y)),
        str(int(duration_ms)),
    )
    time.sleep(0.25)


def validate_profile():
    if not BEACH_TEMPLATE.exists():
        raise FileNotFoundError(f"海滩模板不存在：{BEACH_TEMPLATE}")
    template = read_image(TRIPLE_SPEED_TEMPLATE)
    _, _, width, height = TRIPLE_SPEED_SEARCH_REGION
    if template.shape[0] > height or template.shape[1] > width:
        raise ValueError("3bei.png 必须是 1280x720 画面中裁出的三倍速按钮，不能是完整截图。")


def deploy_card(card_point, landing_point, card_label, landing_label):
    started_at = time.monotonic()
    tap(card_point, card_label, settle_seconds=0)
    time.sleep(WAIT_AFTER_CARD_SELECT_SECONDS)
    tap(landing_point, landing_label, settle_seconds=0)
    # 每船总节奏包含两次 ADB 点击及选卡等待；设备较慢时不叠加额外等待。
    remaining = DEPLOY_INTERVAL_SECONDS - (time.monotonic() - started_at)
    if remaining > 0:
        time.sleep(remaining)


def deploy_hero(landing_point, landing_reference=None):
    # 英雄最后登陆，复用普通登陆艇已经确认的登陆坐标。
    # 选中英雄后重新识别会受已登陆部队遮挡，容易把英雄点击到另一处。
    deploy_card(HERO_CARD_POINT, landing_point, "英雄卡片", "英雄登陆点（与普通登陆艇相同）")


def release_initial_hero_skill_once():
    print(f"等待 {WAIT_AFTER_HERO_DEPLOY_SECONDS} 秒后释放英雄技能。")
    time.sleep(WAIT_AFTER_HERO_DEPLOY_SECONDS)
    released_at = time.monotonic()
    tap(HERO_CARD_POINT, "英雄登陆后技能（一次）")
    return released_at


def deploy_troop(index, landing_point):
    card_point = TROOP_CARD_POINTS[index - 1]
    deploy_card(card_point, landing_point, f"兵种卡片 {index}", f"兵种 {index} 登陆点")


def deploy_units_in_order(landing_point, landing_reference=None):
    for index in range(1, len(TROOP_CARD_POINTS) + 1):
        deploy_troop(index, landing_point)

    deploy_hero(landing_point, landing_reference=landing_reference)
    last_hero_at = release_initial_hero_skill_once()
    release_robot_round(2, landing_point)
    return last_hero_at


def find_triple_speed_button(screen):
    if screen is None or screen.shape != (720, 1280, 3):
        return MatchResult(0.0, 0, 0, 0, 0, "invalid_screen")
    template = read_image(TRIPLE_SPEED_TEMPLATE)
    x, y, width, height = TRIPLE_SPEED_SEARCH_REGION
    region = screen[y:y+height, x:x+width]
    if template.shape[0] > height or template.shape[1] > width:
        raise ValueError("三倍速模板超出搜索区域。")
    result = cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED)
    _, score, _, location = cv2.minMaxLoc(result)
    return MatchResult(float(score), x + location[0], y + location[1],
                       template.shape[1], template.shape[0], "triple_speed_button")


def enable_triple_speed():
    for attempt in range(1, TRIPLE_SPEED_ATTEMPTS + 1):
        screen = adb_screenshot()
        result = click_result_return.find_template(screen)
        if result.score >= click_result_return.MATCH_THRESHOLD:
            print("已进入结算页，跳过三倍速。")
            return False
        match = find_triple_speed_button(screen)
        print(f"三倍速按钮 {attempt}/{TRIPLE_SPEED_ATTEMPTS}：score={match.score:.3f}")
        if match.score >= TRIPLE_SPEED_MATCH_THRESHOLD:
            # 只点击一次；不因图标仍显示而重试，避免把加速再次关闭。
            tap(match.center, "开启三倍速")
            print("已点击三倍速按钮。")
            return True
        if attempt < TRIPLE_SPEED_ATTEMPTS:
            time.sleep(TRIPLE_SPEED_RETRY_SECONDS)
    print("未识别到三倍速按钮，继续等待战斗结果。")
    return False


def release_robot_round(round_index, landing_point):
    if ROBOT_SKILL_POINT is None:
        print("未配置机器小怪按钮，跳过。")
        return
    target = ROBOT_TARGET_POINTS[(round_index - 1) % len(ROBOT_TARGET_POINTS)] if ROBOT_TARGET_POINTS else landing_point
    tap(ROBOT_SKILL_POINT, f"机器小怪技能 第 {round_index} 次")
    tap(target, f"机器小怪目标 第 {round_index} 次")


def release_cycle_hero_skill(round_index):
    tap(HERO_CARD_POINT, f"循环英雄技能 第 {round_index} 次")


def is_result_page_visible(label):
    try:
        screen = adb_screenshot()
        match = click_result_return.find_template(screen)
        is_visible = match.score >= click_result_return.MATCH_THRESHOLD
        if is_visible:
            print(f"{label} 检测到结算返回按钮，匹配分数：{match.score:.3f}")
        return is_visible
    except Exception as exc:
        print(f"{label} 结算检测异常，继续执行：{exc}")
        return False


def wait_before_next_action(seconds, label):
    print(f"等待 {seconds} 秒后{label}。")
    deadline = time.monotonic() + seconds
    while True:
        if is_result_page_visible(label):
            print("已检测到结算页，停止后续技能释放。")
            return False

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True

        time.sleep(min(RESULT_CHECK_INTERVAL_SECONDS, remaining))


def release_robot_hero_cycle(landing_point, last_hero_at=None):
    if last_hero_at is None:
        last_hero_at = time.monotonic()
    for round_index in range(1, ROBOT_HERO_CYCLE_ROUNDS + 1):
        interval = FIRST_HERO_SKILL_SECONDS if round_index == 1 else NEXT_HERO_SKILL_SECONDS
        # 以相邻两次英雄点击为间隔，计入机械小兵、三倍速和 ADB 操作耗时。
        hero_wait = max(0.0, last_hero_at + interval - time.monotonic())

        if not wait_before_next_action(hero_wait, "释放英雄技能"):
            return

        try:
            last_hero_at = time.monotonic()
            release_cycle_hero_skill(round_index)
        except Exception as exc:
            print(f"第 {round_index} 次英雄技能点击异常，继续计时：{exc}")

        try:
            # 开场和英雄首次技能后各释放过一次，后续继续按次数轮换目标。
            release_robot_round(round_index + 2, landing_point)
        except Exception as exc:
            print(f"第 {round_index} 次机器小怪点击异常，继续计时：{exc}")

def release_shock_once():
    if SHOCK_SKILL_POINT is None or not SHOCK_TARGET_POINTS:
        print("未配置震爆弹，跳过。")
        return
    for index, target in enumerate(SHOCK_TARGET_POINTS, start=1):
        tap(SHOCK_SKILL_POINT, f"震爆弹技能 {index}")
        tap(target, f"震爆弹目标 {index}")


def main():
    print("开始执行第一种海滩固定打法。")
    validate_profile()

    print(f"已进入战斗界面，等待 {WAIT_FOR_BATTLE_START_SECONDS} 秒后先缩小地图一次。")
    time.sleep(WAIT_FOR_BATTLE_START_SECONDS)
    map_zoom.zoom_out_once(adb_command, dry_run=DRY_RUN)

    landing_reference = {}
    landing_point = search_landing_point(reference_out=landing_reference)
    release_robot_round(1, landing_point)
    print(f"开场机械小兵投放后，等待 {WAIT_AFTER_OPENING_ROBOT_SECONDS} 秒再开始登陆。")
    time.sleep(WAIT_AFTER_OPENING_ROBOT_SECONDS)
    last_hero_at = deploy_units_in_order(landing_point, landing_reference=landing_reference)
    enable_triple_speed()
    release_shock_once()
    release_robot_hero_cycle(landing_point, last_hero_at=last_hero_at)

    print("固定打法动作执行完成。后续等待结算脚本接管。")


if __name__ == "__main__":
    main()
