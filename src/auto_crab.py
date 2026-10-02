import argparse
import time
from datetime import datetime
from pathlib import Path

import cv2

import battle_attack_profile
import click_attack_button
import click_confirm_attack
import click_reinforce
import click_replenish_troops as replenish_troops_module
import click_result_return
import debug_settings
import skip_doctor_dialog
import template_click
import troop_status


PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 默认连续进攻，直到没有进攻次数；可用 --pause-after N 限制本次进攻次数。
AUTO_LOOP_CRAB = True
PAUSE_AFTER_STAGES = None

STEP_RETRY_TIMES = 3
STEP_RETRY_INTERVAL_SECONDS = 1.0
BATTLE_RESULT_TIMEOUT_SECONDS = 300
RESULT_POLL_INTERVAL_SECONDS = 5

AFTER_CRAB_CLICK_SECONDS = 1.5
AFTER_ATTACK_CLICK_SECONDS = 1.0
AFTER_CONFIRM_CLICK_SECONDS = 1.0
AFTER_RESULT_RETURN_SECONDS = 1.5
AFTER_DOCTOR_SKIP_SECONDS = 1.0
AFTER_REINFORCE_CLICK_SECONDS = 1.0
AFTER_REPLENISH_CLICK_SECONDS = 1.0

CRAB_ENTRY_SEARCH_REGION = (250, 70, 850, 560)
EXPECTED_SCREEN_SIZE = (1280, 720)
CRAB_PAGE_WAIT_SECONDS = 6.0
REINFORCE_WAIT_SECONDS = 8.0
STATE_POLL_INTERVAL_SECONDS = 0.6


def now_text():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def sync_adb_settings():
    modules = [
        click_attack_button,
        click_confirm_attack,
        click_reinforce,
        replenish_troops_module,
        click_result_return,
        skip_doctor_dialog,
        template_click,
    ]
    for module in modules:
        module.ADB_PATH = battle_attack_profile.ADB_PATH
        module.ADB_SERIAL = battle_attack_profile.ADB_SERIAL


def validate_runtime():
    sync_adb_settings()
    screen = battle_attack_profile.adb_screenshot()
    height, width = screen.shape[:2]
    if (width, height) != EXPECTED_SCREEN_SIZE:
        raise RuntimeError(
            f"当前截图分辨率是 {width}x{height}，脚本只校准了 "
            f"{EXPECTED_SCREEN_SIZE[0]}x{EXPECTED_SCREEN_SIZE[1]}。为避免误点，已停止。"
        )

    required_templates = [
        click_attack_button.ATTACK_BUTTON_TEMPLATE,
        click_confirm_attack.CONFIRM_ATTACK_TEMPLATE,
        click_result_return.RETURN_BUTTON_TEMPLATE,
        click_reinforce.REINFORCE_ICON_TEMPLATE,
        replenish_troops_module.REPLENISH_TEXT_TEMPLATE,
        battle_attack_profile.BEACH_TEMPLATE,
        troop_status.WORLD_MAP_REFERENCE,
    ]
    missing = [str(path) for path in required_templates if not path.exists()]
    if missing:
        raise FileNotFoundError("缺少运行模板：\n" + "\n".join(missing))

    # 进攻前检查双指触控支持和写入权限；这里只读检测，不发送手势。
    battle_attack_profile.map_zoom.discover_touch_device(battle_attack_profile.adb_command)

    print(
        f"运行前检查通过：device={battle_attack_profile.ADB_SERIAL or 'default'}, "
        f"resolution={width}x{height}, templates={len(required_templates)}"
    )
    return screen


def save_error_screenshot(step_name):
    if not debug_settings.SAVE_ERROR_SCREENSHOTS:
        return None
    try:
        screen = battle_attack_profile.adb_screenshot()
    except Exception as exc:
        print(f"保存异常截图失败：{exc}")
        return None

    output_dir = PROJECT_ROOT / "screenshots" / "errors"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"auto_crab_{step_name}_{now_text()}.png"
    cv2.imencode(".png", screen)[1].tofile(str(output_path))
    print(f"已保存异常截图：{output_path}")
    return output_path


def retry_step(step_name, action, retry_times=STEP_RETRY_TIMES):
    last_error = None
    for attempt in range(1, retry_times + 1):
        try:
            print(f"[{step_name}] 第 {attempt}/{retry_times} 次")
            result = action()
            if result:
                return result
        except Exception as exc:
            last_error = exc
            print(f"[{step_name}] 异常：{exc}")

        time.sleep(STEP_RETRY_INTERVAL_SECONDS)

    save_error_screenshot(step_name)
    if last_error:
        raise RuntimeError(f"{step_name} 连续失败：{last_error}")
    raise RuntimeError(f"{step_name} 连续 {retry_times} 次未成功。")


def tap_with_module(module, point):
    x, y = point
    if not (0 <= x < EXPECTED_SCREEN_SIZE[0] and 0 <= y < EXPECTED_SCREEN_SIZE[1]):
        raise ValueError(f"拒绝点击屏幕范围外坐标：{point}")
    module.adb_tap(point[0], point[1])


def is_on_crab_attack_page(screen=None):
    if screen is None:
        screen = click_attack_button.adb_screenshot()
    match = click_attack_button.find_template(
        screen,
        click_attack_button.ATTACK_BUTTON_TEMPLATE,
        click_attack_button.ATTACK_SEARCH_REGION,
        click_attack_button.ATTACK_MATCH_THRESHOLD,
    )
    print(f"进攻界面验证分数：{match.score:.3f}")
    return match.score >= click_attack_button.ATTACK_MATCH_THRESHOLD


def wait_for_crab_attack_page(timeout=CRAB_PAGE_WAIT_SECONDS):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_on_crab_attack_page():
            return True
        time.sleep(STATE_POLL_INTERVAL_SECONDS)
    return False


def find_crab_entry(screen):
    """统一使用入口搜索区域，保证移动视角和点击入口的判定一致。"""
    old_search_region = template_click.SEARCH_REGION
    try:
        template_click.SEARCH_REGION = CRAB_ENTRY_SEARCH_REGION
        return template_click.find_crab(screen)
    finally:
        template_click.SEARCH_REGION = old_search_region


def enter_crab():
    if is_on_crab_attack_page():
        print("已经在螃蟹进攻界面。")
        return True

    screen = template_click.decode_png(template_click.adb_screenshot_bytes())
    if skip_doctor_dialog.is_dialog_visible(screen):
        print("进入螃蟹前仍检测到博士对话，先清除对话。")
        skip_doctor_dialog.main()
        screen = template_click.decode_png(template_click.adb_screenshot_bytes())

    map_ready, map_scores = troop_status.is_world_map_visible(screen)
    if not map_ready:
        print(
            "当前不是可安全点击的世界地图界面，拒绝寻找螃蟹。"
            f"地图按钮分数={[round(value, 3) for value in map_scores]}"
        )
        return False

    match = find_crab_entry(screen)

    print(f"螃蟹入口匹配分数：{match.score:.3f}，模板：{match.template.name}，中心点：{match.center}")
    if match.score < template_click.MATCH_THRESHOLD:
        if debug_settings.SAVE_ERROR_SCREENSHOTS:
            output_dir = PROJECT_ROOT / "screenshots" / "errors"
            output_dir.mkdir(parents=True, exist_ok=True)
            debug_path = output_dir / f"crab_entry_match_{now_text()}.png"
            template_click.save_debug_match(screen, match, debug_path)
            print(f"螃蟹入口匹配失败调试图：{debug_path}")
        return False

    tap_with_module(template_click, match.center)
    time.sleep(AFTER_CRAB_CLICK_SECONDS)
    return wait_for_crab_attack_page()


def click_attack():
    screen = click_attack_button.adb_screenshot()
    attempts_text, _ = click_attack_button.read_attempts_text(screen)
    no_attempts, zero_score, left_pixels = click_attack_button.is_no_attempts(screen)
    print(f"剩余进攻次数：{attempts_text}/40")
    print(f"次数判断：no_attempts={no_attempts}, zero_score={zero_score:.3f}, left_pixels={left_pixels}")

    if no_attempts:
        return "no_attempts"

    match = click_attack_button.find_template(
        screen,
        click_attack_button.ATTACK_BUTTON_TEMPLATE,
        click_attack_button.ATTACK_SEARCH_REGION,
        click_attack_button.ATTACK_MATCH_THRESHOLD,
    )
    print(f"攻击按钮匹配分数：{match.score:.3f}，中心点：{match.center}")
    if match.score < click_attack_button.ATTACK_MATCH_THRESHOLD:
        return False

    tap_with_module(click_attack_button, match.center)
    time.sleep(AFTER_ATTACK_CLICK_SECONDS)
    return True


def confirm_attack():
    screen = click_confirm_attack.adb_screenshot()
    match = click_confirm_attack.find_template(screen)
    print(f"确认攻击匹配分数：{match.score:.3f}，中心点：{match.center}")
    if match.score < click_confirm_attack.MATCH_THRESHOLD:
        return False

    tap_with_module(click_confirm_attack, match.center)
    time.sleep(AFTER_CONFIRM_CLICK_SECONDS)
    return True


def run_battle_and_wait_result(stage_index):
    battle_started_at = time.monotonic()
    battle_attack_profile.set_beach_debug_stage(f"第{stage_index}阶段")
    battle_attack_profile.main()

    while True:
        elapsed = time.monotonic() - battle_started_at
        if elapsed > BATTLE_RESULT_TIMEOUT_SECONDS:
            save_error_screenshot("battle_timeout")
            raise RuntimeError("战斗开始后 5 分钟仍未识别到结算返回按钮，已停止。")

        screen = click_result_return.adb_screenshot()
        match = click_result_return.find_template(screen)
        print(f"结算返回匹配分数：{match.score:.3f}")
        if match.score >= click_result_return.MATCH_THRESHOLD:
            tap_with_module(click_result_return, match.center)
            time.sleep(AFTER_RESULT_RETURN_SECONDS)
            return True

        time.sleep(RESULT_POLL_INTERVAL_SECONDS)


def skip_doctor():
    if not skip_doctor_dialog.main():
        raise RuntimeError("博士对话未能清除。")
    time.sleep(AFTER_DOCTOR_SKIP_SECONDS)
    return True


def click_reinforce_if_needed():
    screen = click_reinforce.adb_screenshot()
    match = click_reinforce.find_reinforce_icon(screen)
    print(f"补兵图标匹配分数：{match.score:.3f}，模板：{match.template_name}，中心点：{match.center}")
    if match.score < click_reinforce.MATCH_THRESHOLD:
        return "no_reinforce"

    tap_with_module(click_reinforce, match.center)
    time.sleep(AFTER_REINFORCE_CLICK_SECONDS)
    return True


def wait_for_reinforce_icon(timeout=REINFORCE_WAIT_SECONDS):
    deadline = time.monotonic() + timeout
    best_score = 0.0
    while time.monotonic() < deadline:
        screen = click_reinforce.adb_screenshot()
        if skip_doctor_dialog.is_dialog_visible(screen):
            skip_doctor_dialog.main()
            continue

        # 普通补兵入口本身就是判断依据，不再由兵种卡片数量拦截。
        # find_reinforce_icon 限定右侧入口区域，并排除带钻石的加速图标。
        match = click_reinforce.find_reinforce_icon(screen)
        best_score = max(best_score, match.score)
        print(f"等待补兵图标：score={match.score:.3f}, best={best_score:.3f}")
        if match.score >= click_reinforce.MATCH_THRESHOLD:
            tap_with_module(click_reinforce, match.center)
            time.sleep(AFTER_REINFORCE_CLICK_SECONDS)
            return True
        time.sleep(STATE_POLL_INTERVAL_SECONDS)

    print(f"{timeout:.1f} 秒内未出现补兵图标，最高分 {best_score:.3f}。")
    return "no_reinforce"


def click_replenish_troops_button():
    screen = replenish_troops_module.adb_screenshot()
    match = replenish_troops_module.find_template(screen)
    print(f"补充兵力匹配分数：{match.score:.3f}，中心点：{match.center}")
    if match.score < replenish_troops_module.MATCH_THRESHOLD:
        return False

    tap_with_module(replenish_troops_module, match.center)
    time.sleep(AFTER_REPLENISH_CLICK_SECONDS)
    return True


def click_basic_replenish(before_troop_snapshot):
    reinforce_result = wait_for_reinforce_icon()
    if reinforce_result == "no_reinforce":
        print("未检测到补兵图标，按当前策略跳过补兵并继续攻击。")
        return "no_reinforce"

    retry_step("click_replenish_troops", click_replenish_troops_button)
    return True


def finish_reinforce(before_troop_snapshot):
    replenish_result = click_basic_replenish(before_troop_snapshot)
    if replenish_result == "no_reinforce":
        return True
    if replenish_result is True:
        print("普通补兵操作已完成，继续流程。")
        return True
    raise RuntimeError(f"未知补兵结果：{replenish_result}")


def run_one_stage(stage_index, before_troop_snapshot=None):
    print(f"========== 开始第 {stage_index} 次自动进攻 ==========")
    attack_result = retry_step("click_attack", click_attack)
    if attack_result == "no_attempts":
        print("没有剩余攻击次数，自动停止。")
        return "no_attempts"

    retry_step("confirm_attack", confirm_attack)
    run_battle_and_wait_result(stage_index)
    skip_doctor()
    finish_reinforce(before_troop_snapshot)

    print(f"========== 第 {stage_index} 次自动进攻结束 ==========")
    return "done"


def capture_map_troop_snapshot(label):
    screen = battle_attack_profile.adb_screenshot()
    if skip_doctor_dialog.is_dialog_visible(screen):
        raise RuntimeError("地图上仍有博士对话，不能采集可靠的兵力快照。")
    if is_on_crab_attack_page(screen):
        print("当前已经在螃蟹进攻弹窗，无法采集地图兵力快照。")
        return None
    map_ready, map_scores = troop_status.is_world_map_visible(screen)
    if not map_ready:
        raise RuntimeError(
            "当前不是稳定的世界地图界面，不能采集兵力快照："
            f"地图按钮分数={[round(value, 3) for value in map_scores]}"
        )
    return troop_status.capture(screen, label)


def check_crab_entry():
    screen = battle_attack_profile.adb_screenshot()
    if is_on_crab_attack_page(screen):
        print("当前已经在螃蟹进攻界面。")
        return True

    if skip_doctor_dialog.is_dialog_visible(screen):
        print("博士对话仍在显示，等待后重试入口识别。")
        return False

    map_ready, map_scores = troop_status.is_world_map_visible(screen)
    if not map_ready:
        print(
            "当前不是稳定的世界地图界面，等待后重试入口识别："
            f"地图按钮分数={[round(value, 3) for value in map_scores]}"
        )
        return False

    match = find_crab_entry(screen)
    if match.score >= template_click.MATCH_THRESHOLD:
        print(f"已找到螃蟹入口：score={match.score:.3f}, center={match.center}。")
        return True

    print(f"未识别到螃蟹入口：score={match.score:.3f}，保持当前视角，等待后重试。")
    return False


def reached_pause_limit(stage_index):
    return PAUSE_AFTER_STAGES is not None and stage_index >= PAUSE_AFTER_STAGES


def main(): 
    print("开始自动打螃蟹稳健流程。")
    initial_screen = validate_runtime()
    before_troop_snapshot = None
    if not is_on_crab_attack_page(initial_screen):
        if skip_doctor_dialog.is_dialog_visible(initial_screen):
            skip_doctor()
        before_troop_snapshot = capture_map_troop_snapshot("before_stage_1")

    retry_step("enter_crab", enter_crab)

    stage_index = 1
    while True:
        result = run_one_stage(stage_index, before_troop_snapshot)
        if result == "no_attempts":
            break

        if reached_pause_limit(stage_index):
            print(f"已完成 {stage_index} 次进攻，达到自动暂停设置，流程暂停。")
            break

        if not AUTO_LOOP_CRAB:
            print("单次模式已完成 1 次进攻，自动停止。")
            break

        stage_index += 1
        retry_step("check_crab_entry", check_crab_entry)
        before_troop_snapshot = capture_map_troop_snapshot(f"before_stage_{stage_index}")
        retry_step("enter_crab_next_stage", enter_crab)
        time.sleep(1)

    print("自动流程结束。")


def parse_args():
    parser = argparse.ArgumentParser(description="海岛奇兵惊奇螃蟹自动进攻脚本")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--loop", action="store_true", help="连续进攻（默认行为）")
    mode_group.add_argument("--once", action="store_true", help="只进攻一次后退出")
    parser.add_argument(
        "--pause-after",
        "--max-stages",
        dest="pause_after",
        type=int,
        default=None,
        help="完成指定次数的进攻后自动暂停；设置后自动启用连续模式",
    )
    parser.add_argument("--device", default="emulator-5554", help="ADB 设备序列号")
    parser.add_argument("--adb", default="adb", help="ADB 可执行文件路径")
    parser.add_argument("--check", action="store_true", help="只做运行环境与当前画面检查，不点击")
    return parser.parse_args()


if __name__ == "__main__":
    # ===== 战斗技能节奏 =====
    # 从登陆后的第一次英雄技能起，间隔 3 秒再次释放。
    battle_attack_profile.FIRST_HERO_SKILL_SECONDS = 3

    # 后续英雄技能点击之间的目标间隔（现实秒）。
    battle_attack_profile.NEXT_HERO_SKILL_SECONDS = 3

    # 每次英雄技能后立即释放机械小兵。

    # 英雄技能/机器小怪循环释放几轮。
    battle_attack_profile.ROBOT_HERO_CYCLE_ROUNDS = 20

    # ===== 其它等待参数 =====
    # 战斗开始后最多等待多少秒识别结算返回按钮。
    BATTLE_RESULT_TIMEOUT_SECONDS = 100

    args = parse_args()
    battle_attack_profile.ADB_PATH = args.adb
    battle_attack_profile.ADB_SERIAL = args.device
    if args.loop:
        AUTO_LOOP_CRAB = True
    if args.once:
        AUTO_LOOP_CRAB = False
    if args.pause_after is not None:
        if args.pause_after < 1:
            raise ValueError("--pause-after 必须大于等于 1。")
        AUTO_LOOP_CRAB = True
        PAUSE_AFTER_STAGES = args.pause_after

    if args.check:
        screen = validate_runtime()
        map_ready, map_scores = troop_status.is_world_map_visible(screen)
        print(f"当前博士对话：{skip_doctor_dialog.is_dialog_visible(screen)}")
        print(f"当前螃蟹进攻页：{is_on_crab_attack_page(screen)}")
        print(f"当前世界地图：{map_ready}，地图按钮分数={[round(value, 3) for value in map_scores]}")
        old_search_region = template_click.SEARCH_REGION
        try:
            template_click.SEARCH_REGION = CRAB_ENTRY_SEARCH_REGION
            crab_match = template_click.find_crab(screen)
        finally:
            template_click.SEARCH_REGION = old_search_region
        print(
            f"当前螃蟹入口：score={crab_match.score:.3f}, "
            f"center={crab_match.center}, detector={crab_match.template.name}, "
            f"clickable={map_ready and crab_match.score >= template_click.MATCH_THRESHOLD}"
        )
        raise SystemExit(0)

    # ===== 正常运行主流程 =====
    main()

    # 只测试普通补兵：先截图记录兵力，再点普通补兵图标和“补充兵力”。
    # before = troop_status.capture(battle_attack_profile.adb_screenshot(), "manual_before_replenish")
    # click_basic_replenish(before)
