import sys
import unittest
from pathlib import Path

import cv2
import numpy as np
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

import click_attack_button
import click_confirm_attack
import click_result_return
import auto_crab
import battle_attack_profile
import debug_settings
import landing_geometry
import skip_doctor_dialog
import template_click
import troop_status


def read_image(relative_path):
    path = PROJECT_ROOT / relative_path
    image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise AssertionError(f"测试图片读取失败：{path}")
    return image


class OfflineVisionTests(unittest.TestCase):
    def setUp(self):
        self.old_search_region = template_click.SEARCH_REGION
        template_click.SEARCH_REGION = (250, 70, 850, 560)

    def tearDown(self):
        template_click.SEARCH_REGION = self.old_search_region

    def test_automatic_error_screenshot_writes_are_disabled(self):
        self.assertFalse(debug_settings.SAVE_ERROR_SCREENSHOTS)
        screen = np.zeros((720, 1280, 3), dtype=np.uint8)
        match = battle_attack_profile.MatchResult(0.0, 0, 0, 10, 10)
        with mock.patch.object(cv2, "imencode", side_effect=AssertionError("must not encode")), \
             mock.patch.object(auto_crab.battle_attack_profile, "adb_screenshot",
                               side_effect=AssertionError("must not capture")):
            self.assertIsNone(auto_crab.save_error_screenshot("test"))
            self.assertIsNone(battle_attack_profile.save_beach_debug(screen, match, "test"))
            self.assertIsNone(battle_attack_profile.save_beach_debug_records([], "测试"))
            self.assertIsNone(troop_status.save_debug(screen[:80, :416], "test"))
            self.assertIsNone(template_click.save_debug_match(screen, match, PROJECT_ROOT / "unused.png"))
            self.assertIsNone(skip_doctor_dialog.save_debug(screen))

    def test_crab_color_detector_accepts_real_crab(self):
        screen = read_image("screenshots/raw/crab_entry_next_stage.png")
        match = template_click.find_crab(screen)
        self.assertGreaterEqual(match.score, template_click.MATCH_THRESHOLD)
        self.assertEqual(match.template.name, "purple_color_detector")
        self.assertTrue(540 <= match.center[0] <= 740)
        self.assertTrue(280 <= match.center[1] <= 470)

    def test_crab_detector_rejects_non_map_battle_screen(self):
        screen = read_image("screenshots/raw/attack_confirm.png")
        match = template_click.find_crab(screen)
        self.assertLess(match.score, template_click.MATCH_THRESHOLD)

    def test_doctor_dialog_detector(self):
        dialog = read_image("screenshots/raw/crab_doctor_dialog.png")
        clear_map = read_image("screenshots/raw/crab_entry_next_stage.png")
        self.assertTrue(skip_doctor_dialog.is_dialog_visible(dialog))
        self.assertFalse(skip_doctor_dialog.is_dialog_visible(clear_map))

    def test_troop_card_diagnostic(self):
        clear_map = read_image("screenshots/raw/crab_entry_next_stage.png")
        attack_popup = read_image("screenshots/raw/crab_main_ready.png")
        self.assertTrue(troop_status.is_troop_bar_visible(clear_map)[0])
        self.assertFalse(troop_status.is_troop_bar_visible(attack_popup)[0])

    def test_world_map_accepts_grouped_or_depleted_troops(self):
        for name in ("few", "depleted"):
            screen = read_image(f"tests/fixtures/world_map_{name}_troops_reinforce.png")
            with self.subTest(name=name):
                self.assertFalse(troop_status.is_troop_bar_visible(screen)[0])
                self.assertTrue(troop_status.is_world_map_visible(screen)[0])
                # 补兵后即使整条兵种栏消失，地图判定也不受影响。
                screen[640:720, :470] = 0
                self.assertTrue(troop_status.is_world_map_visible(screen)[0])

    def test_world_map_rejects_battle_dialog_loading_and_dimmed_map(self):
        for name in ("battle_entered", "battle_after_deploy", "battle_skills_unavailable",
                     "battle_result", "attack_confirm", "crab_main_ready", "crab_doctor_dialog"):
            with self.subTest(name=name):
                self.assertFalse(troop_status.is_world_map_visible(
                    read_image(f"screenshots/raw/{name}.png"))[0])
        clear_map = read_image("screenshots/raw/crab_entry_next_stage.png")
        self.assertTrue(troop_status.is_world_map_visible(clear_map)[0])
        self.assertFalse(troop_status.is_world_map_visible(np.zeros_like(clear_map))[0])
        self.assertFalse(troop_status.is_world_map_visible((clear_map * 0.5).astype(np.uint8))[0])
        self.assertFalse(troop_status.is_world_map_visible(clear_map[:360])[0])

    def test_visible_reinforce_icon_is_clicked_without_troop_card_gate(self):
        for name in ("few", "depleted"):
            screen = read_image(f"tests/fixtures/world_map_{name}_troops_reinforce.png")
            with self.subTest(name=name), \
                 mock.patch.object(auto_crab.click_reinforce, "adb_screenshot", return_value=screen), \
                 mock.patch.object(auto_crab.click_reinforce, "adb_tap") as tap, \
                 mock.patch.object(troop_status, "is_troop_bar_visible", side_effect=AssertionError("obsolete gate")), \
                 mock.patch.object(auto_crab.time, "sleep"), \
                 mock.patch.object(auto_crab.time, "monotonic", side_effect=[0, 0, 9]):
                self.assertTrue(auto_crab.wait_for_reinforce_icon())
                tap.assert_called_once_with(1237, 564)

    def test_reinforce_timeout_skips_without_clicking(self):
        screen = read_image("screenshots/raw/crab_entry_next_stage.png")
        with mock.patch.object(auto_crab.click_reinforce, "adb_screenshot", return_value=screen), \
             mock.patch.object(auto_crab.click_reinforce, "adb_tap") as tap, \
             mock.patch.object(auto_crab.time, "sleep"), \
             mock.patch.object(auto_crab.time, "monotonic", side_effect=[0, 0, 9]):
            self.assertEqual(auto_crab.wait_for_reinforce_icon(), "no_reinforce")
            tap.assert_not_called()

    def test_reinforce_clears_dialog_before_matching_fresh_screen(self):
        dialog = read_image("screenshots/raw/crab_doctor_dialog.png")
        screen = read_image("tests/fixtures/world_map_few_troops_reinforce.png")
        with mock.patch.object(auto_crab.click_reinforce, "adb_screenshot", side_effect=[dialog, screen]), \
             mock.patch.object(auto_crab.click_reinforce, "find_reinforce_icon",
                               wraps=auto_crab.click_reinforce.find_reinforce_icon) as match, \
             mock.patch.object(auto_crab.skip_doctor_dialog, "main", return_value=True) as clear, \
             mock.patch.object(auto_crab.click_reinforce, "adb_tap") as tap, \
             mock.patch.object(auto_crab.time, "sleep"), \
             mock.patch.object(auto_crab.time, "monotonic", side_effect=[0, 0, 1, 9]):
            self.assertTrue(auto_crab.wait_for_reinforce_icon())
            clear.assert_called_once()
            match.assert_called_once()
            self.assertIs(match.call_args.args[0], screen)
            tap.assert_called_once_with(1237, 564)

    def test_missing_reinforce_icon_continues_without_snapshot_check(self):
        with mock.patch.object(auto_crab, "wait_for_reinforce_icon", return_value="no_reinforce"):
            with mock.patch.object(auto_crab.battle_attack_profile, "adb_screenshot") as screenshot:
                result = auto_crab.click_basic_replenish(before_troop_snapshot=None)
        self.assertEqual(result, "no_reinforce")
        screenshot.assert_not_called()

    def test_landing_detector_rejects_minefield_false_positive(self):
        minefield = read_image(
            "tests/fixtures/stage20_no_beach.png"
        )
        match = battle_attack_profile.find_beach(minefield)
        self.assertLess(match.score, battle_attack_profile.BEACH_MATCH_THRESHOLD)

    def test_landing_detector_rejects_peripheral_water(self):
        peripheral_water = read_image(
            "tests/fixtures/stage20_peripheral_water.png"
        )
        match = battle_attack_profile.find_beach(peripheral_water)
        self.assertLess(match.score, battle_attack_profile.BEACH_MATCH_THRESHOLD)

    def test_landing_detector_accepts_front_facing_beach(self):
        beach = read_image("tests/fixtures/stage20_front_landing_zone.png")
        match = battle_attack_profile.find_beach(beach)
        self.assertGreaterEqual(match.score, battle_attack_profile.BEACH_MATCH_THRESHOLD)
        self.assertEqual(match.method, "verified_landing_zone")
        self.assertTrue(470 <= match.center[0] <= 880)
        self.assertTrue(395 <= match.center[1] <= 445)
        self.assertTrue(match.polygon)

        # 登陆点必须位于白色登陆范围中央的低饱和度滩面，不能落在范围外或海水中。
        center_x, center_y = match.center
        hsv = cv2.cvtColor(beach, cv2.COLOR_BGR2HSV)
        self.assertLess(int(hsv[center_y, center_x, 1]), 70)

    def test_landing_detector_accepts_left_slanted_apron(self):
        beach = read_image("screenshots/raw/battle_full_troops.png")
        match = battle_attack_profile.find_beach(beach)
        self.assertGreaterEqual(match.score, battle_attack_profile.BEACH_MATCH_THRESHOLD)
        self.assertEqual(match.method, "verified_landing_zone")
        self.assertTrue(350 <= match.center[0] <= 450)
        self.assertTrue(330 <= match.center[1] <= 460)

    def test_landing_detector_accepts_right_slanted_apron(self):
        beach = read_image("tests/fixtures/stage28_right_edge_beach.png")
        match = battle_attack_profile.find_beach(beach)
        self.assertGreaterEqual(match.score, battle_attack_profile.BEACH_MATCH_THRESHOLD)
        self.assertEqual(match.method, "verified_landing_zone")
        self.assertTrue(790 <= match.center[0] <= 870)
        self.assertTrue(210 <= match.center[1] <= 285)

        center_x, center_y = match.center
        hsv = cv2.cvtColor(beach, cv2.COLOR_BGR2HSV)
        self.assertLess(int(hsv[center_y, center_x, 1]), 70)

    def test_stage32_targets_apron_not_details_panel(self):
        screen = read_image("tests/fixtures/stage32_front_panel_false_positive.png")
        match = battle_attack_profile.find_beach(screen)
        self.assertGreaterEqual(match.score, battle_attack_profile.BEACH_MATCH_THRESHOLD)
        # Manually verified interior of the real landing strip in the supplied frame.
        x, y = match.center
        self.assertTrue(560 < x < 970 and 450 < y < 500, match.center)
        self.assertGreater(cv2.pointPolygonTest(np.array(match.polygon, np.int32),
                                               (float(x), float(y)), True), 15)

    def test_landing_evidence_requires_water_and_stripe(self):
        screen = read_image("tests/fixtures/stage32_front_panel_false_positive.png")
        hsv = cv2.cvtColor(screen, cv2.COLOR_BGR2HSV)
        water = (hsv[:, :, 0] >= 85) & (hsv[:, :, 0] <= 120) & (hsv[:, :, 1] >= 70)
        no_water = screen.copy()
        no_water[water] = (90, 40, 100)
        self.assertLess(battle_attack_profile.find_beach(no_water).score, 0.55)
        yellow = (hsv[:, :, 0] >= 18) & (hsv[:, :, 0] <= 40) & (hsv[:, :, 1] >= 60)
        no_stripe = screen.copy()
        no_stripe[yellow] = (90, 40, 100)
        self.assertLess(battle_attack_profile.find_beach(no_stripe).score, 0.55)

    def test_one_good_frame_is_not_enough_to_deploy(self):
        hit = battle_attack_profile.MatchResult(0.9, 600, 400, 100, 60)
        miss = battle_attack_profile.MatchResult(0, 0, 0, 0, 0)
        with mock.patch.object(battle_attack_profile, "adb_screenshot", return_value=None), \
             mock.patch.object(battle_attack_profile, "find_beach", side_effect=[hit, miss, hit, miss]), \
             mock.patch.object(battle_attack_profile.time, "sleep"):
            self.assertLess(battle_attack_profile.detect_landing_point("test", []).score, 0.55)

    def test_stable_two_frames_accept_latest_point(self):
        hits = [battle_attack_profile.MatchResult(0.9, 600, 400, 100, 60),
                battle_attack_profile.MatchResult(0.9, 605, 405, 100, 60)]
        with mock.patch.object(battle_attack_profile, "adb_screenshot", return_value=None), \
             mock.patch.object(battle_attack_profile, "find_beach", side_effect=hits), \
             mock.patch.object(battle_attack_profile.time, "sleep"):
            self.assertEqual(battle_attack_profile.detect_landing_point("test", []).center, (655, 435))

    def test_jumping_candidates_do_not_accept_single_high_score(self):
        left = battle_attack_profile.MatchResult(0.99, 400, 400, 100, 60)
        right = battle_attack_profile.MatchResult(0.99, 750, 400, 100, 60)
        with mock.patch.object(battle_attack_profile, "adb_screenshot", return_value=None), \
             mock.patch.object(battle_attack_profile, "find_beach", side_effect=[left, right, left, right]), \
             mock.patch.object(battle_attack_profile.time, "sleep"):
            self.assertLess(battle_attack_profile.detect_landing_point("test", []).score, 0.55)

    def test_stage40_tracks_valid_point_across_all_four_real_frames(self):
        frames = sorted((PROJECT_ROOT / "tests/fixtures/stage40_landing_sequence").glob("raw_right_*.png"))
        self.assertEqual(len(frames), 4)
        preferred = None
        for path in frames:
            match = battle_attack_profile.find_beach(read_image(path), preferred_point=preferred)
            self.assertGreaterEqual(match.score, battle_attack_profile.BEACH_MATCH_THRESHOLD)
            x, y = match.center
            self.assertTrue(200 <= x <= 565 and 295 <= y <= 340, (path.name, match.center))
            self.assertGreaterEqual(cv2.pointPolygonTest(np.array(match.polygon, np.int32),
                                                        (float(x), float(y)), True), 12)
            if preferred is not None:
                self.assertEqual(match.center, preferred)
            preferred = match.center

    def test_stage40_full_search_accepts_two_right_frames_without_adb(self):
        folder = PROJECT_ROOT / "tests/fixtures/stage40_landing_sequence"
        paths = [folder / f"raw_{side}_{i}.png"
                 for side in ("left_bottom", "bottom_center", "right_bottom")
                 for i in range(1, 5)]
        frames = [read_image(path) for path in paths]
        with mock.patch.object(battle_attack_profile, "adb_screenshot", side_effect=frames) as capture, \
             mock.patch.object(battle_attack_profile, "swipe"), \
             mock.patch.object(battle_attack_profile, "save_beach_debug_records"), \
             mock.patch.object(battle_attack_profile.time, "sleep"):
            point = battle_attack_profile.search_landing_point()
        self.assertTrue(200 <= point[0] <= 565 and 295 <= point[1] <= 340)
        self.assertEqual(capture.call_count, 10)  # 4 left + 4 center + 2 stable right frames

    def test_tracking_rejects_point_if_click_patch_becomes_obstructed(self):
        frame = read_image("tests/fixtures/stage40_landing_sequence/raw_right_bottom_1.png")
        first = battle_attack_profile.find_beach(frame)
        x, y = first.center
        frame[y-14:y+15, x-14:x+15] = (0, 0, 255)
        updated = battle_attack_profile.find_beach(frame, preferred_point=first.center)
        self.assertNotEqual(updated.center, first.center)

    def test_tracking_rejects_gray_point_without_current_shore_evidence(self):
        gray_screen = np.full((720, 1280, 3), 160, dtype=np.uint8)
        match = battle_attack_profile.find_beach(gray_screen, preferred_point=(495, 314))
        self.assertLess(match.score, battle_attack_profile.BEACH_MATCH_THRESHOLD)

    def test_stage47_hero_lands_last_at_same_point_as_troops(self):
        folder = PROJECT_ROOT / "tests/fixtures/stage47_hero_occlusion"
        frames = [read_image(folder / f"raw_{name}_{i}.png")
                  for name in ("left_bottom", "hero_selected") for i in range(1, 5)]
        reference = {}
        with mock.patch.object(battle_attack_profile, "adb_screenshot", side_effect=frames) as capture, \
             mock.patch.object(battle_attack_profile, "tap") as tap, \
             mock.patch.object(battle_attack_profile, "swipe"), \
             mock.patch.object(battle_attack_profile, "save_beach_debug_records"), \
             mock.patch.object(battle_attack_profile.time, "sleep"):
            point = battle_attack_profile.search_landing_point(reference_out=reference)
            battle_attack_profile.deploy_units_in_order(point, landing_reference=reference)
        self.assertEqual(capture.call_count, 4)
        self.assertTrue(np.array_equal(reference["screen"], frames[3]))
        calls = tap.call_args_list
        self.assertEqual(calls[0].args[0], battle_attack_profile.TROOP_CARD_POINTS[0])
        self.assertEqual(calls[2].args[0], battle_attack_profile.TROOP_CARD_POINTS[1])
        self.assertEqual(calls[16].args[0], battle_attack_profile.HERO_CARD_POINT)
        hero_point = calls[17].args[0]
        self.assertEqual(calls[1].args[0], point)
        self.assertEqual(calls[3].args[0], point)
        self.assertEqual(hero_point, point)

    def test_stage47_occlusion_recovery_validates_all_hero_frames(self):
        folder = PROJECT_ROOT / "tests/fixtures/stage47_hero_occlusion"
        reference = read_image(folder / "raw_left_bottom_4.png")
        zone = landing_geometry.find_landing_zone(reference)
        point = None
        for i in range(1, 5):
            frame = read_image(folder / f"raw_hero_selected_{i}.png")
            self.assertIsNone(landing_geometry.find_landing_zone(frame))
            recovered = landing_geometry.recover_occluded_zone(frame, reference, zone, point)
            self.assertIsNotNone(recovered)
            point = recovered.point
            self.assertGreaterEqual(cv2.pointPolygonTest(np.array(recovered.polygon, np.int32),
                                                        tuple(float(v) for v in point), True), 12)

    def test_occlusion_recovery_rejects_changed_scene_and_covered_apron(self):
        folder = PROJECT_ROOT / "tests/fixtures/stage47_hero_occlusion"
        reference = read_image(folder / "raw_left_bottom_4.png")
        zone = landing_geometry.find_landing_zone(reference)
        unrelated = read_image("screenshots/raw/attack_confirm.png")
        self.assertIsNone(landing_geometry.recover_occluded_zone(unrelated, reference, zone))
        covered = read_image(folder / "raw_hero_selected_1.png")
        cv2.fillConvexPoly(covered, np.array(zone.polygon, np.int32), (0, 0, 255))
        self.assertIsNone(landing_geometry.recover_occluded_zone(covered, reference, zone))
        far_shift = cv2.warpAffine(reference, np.float32([[1, 0, 120], [0, 1, 0]]), (1280, 720))
        self.assertIsNone(landing_geometry.recover_occluded_zone(far_shift, reference, zone))

    def test_hero_uses_first_two_boats_landing_point_without_recheck(self):
        with mock.patch.object(battle_attack_profile, "tap") as tap, \
             mock.patch.object(battle_attack_profile, "detect_landing_point") as detect, \
             mock.patch.object(battle_attack_profile.time, "sleep"):
            battle_attack_profile.deploy_hero((900, 200))
        self.assertEqual(tap.call_args_list[-1].args[0], (900, 200))
        detect.assert_not_called()

    def test_first_two_boats_and_hero_receive_identical_landing_point(self):
        point = (777, 444)
        with mock.patch.object(battle_attack_profile, "tap") as tap, \
             mock.patch.object(battle_attack_profile, "release_initial_hero_skill_once"), \
             mock.patch.object(battle_attack_profile.time, "sleep"):
            battle_attack_profile.deploy_units_in_order(point)
        self.assertEqual(tap.call_args_list[1].args[0], point)
        self.assertEqual(tap.call_args_list[3].args[0], point)
        self.assertEqual(tap.call_args_list[17].args[0], point)

    def test_landing_search_moves_left_center_right_in_order(self):
        misses_then_hit = [
            battle_attack_profile.MatchResult(0.2, 0, 0, 10, 10, "test"),
            battle_attack_profile.MatchResult(0.3, 0, 0, 10, 10, "test"),
            battle_attack_profile.MatchResult(0.9, 695, 395, 10, 10, "test"),
        ]
        with mock.patch.object(battle_attack_profile, "swipe") as swipe:
            with mock.patch.object(
                battle_attack_profile,
                "detect_landing_point",
                side_effect=misses_then_hit,
            ) as detect:
                with mock.patch.object(battle_attack_profile, "save_beach_debug_records"):
                    point = battle_attack_profile.search_landing_point()

        self.assertEqual(point, (700, 400))
        self.assertEqual(
            [call.args[0] for call in detect.call_args_list],
            ["left_bottom", "bottom_center", "right_bottom"],
        )
        expected_swipes = [
            action
            for _, actions in battle_attack_profile.BEACH_SEARCH_STEPS
            for action in actions
        ]
        self.assertEqual([call.args[0] for call in swipe.call_args_list], expected_swipes)

    def test_pause_after_argument_enables_stage_limit(self):
        with mock.patch("sys.argv", ["auto_crab.py", "--pause-after", "5"]):
            args = auto_crab.parse_args()
        self.assertEqual(args.pause_after, 5)

    def test_default_mode_is_continuous(self):
        self.assertTrue(auto_crab.AUTO_LOOP_CRAB)

    def test_once_argument_is_available(self):
        with mock.patch("sys.argv", ["auto_crab.py", "--once"]):
            args = auto_crab.parse_args()
        self.assertTrue(args.once)

    def test_pause_limit_uses_configured_stage_count(self):
        with mock.patch.object(auto_crab, "PAUSE_AFTER_STAGES", 3):
            self.assertFalse(auto_crab.reached_pause_limit(1))
            self.assertFalse(auto_crab.reached_pause_limit(2))
            self.assertTrue(auto_crab.reached_pause_limit(3))

    def test_default_main_continues_to_second_attack(self):
        screen = np.zeros((720, 1280, 3), dtype=np.uint8)
        with mock.patch.object(auto_crab, "AUTO_LOOP_CRAB", True):
            with mock.patch.object(auto_crab, "PAUSE_AFTER_STAGES", None):
                with mock.patch.object(auto_crab, "validate_runtime", return_value=screen):
                    with mock.patch.object(auto_crab, "is_on_crab_attack_page", return_value=True):
                        with mock.patch.object(auto_crab, "retry_step", return_value=True):
                            with mock.patch.object(
                                auto_crab,
                                "run_one_stage",
                                side_effect=["done", "no_attempts"],
                            ) as run_stage:
                                with mock.patch.object(
                                    auto_crab,
                                    "capture_map_troop_snapshot",
                                    return_value=None,
                                ):
                                    with mock.patch.object(auto_crab.time, "sleep"):
                                        auto_crab.main()

        self.assertEqual([call.args[0] for call in run_stage.call_args_list], [1, 2])

    def test_missing_crab_retries_without_moving_map(self):
        screen = read_image("screenshots/raw/crab_entry_next_stage.png")
        with mock.patch.object(
            auto_crab.battle_attack_profile,
            "adb_screenshot",
            return_value=screen,
        ):
            with mock.patch.object(auto_crab, "is_on_crab_attack_page", return_value=False):
                with mock.patch.object(auto_crab.battle_attack_profile, "run_adb") as run_adb:
                    with mock.patch.object(auto_crab.time, "sleep"), \
                         mock.patch.object(auto_crab, "find_crab_entry", return_value=mock.Mock(score=0.3)):
                        with self.assertRaisesRegex(RuntimeError, "check_crab_entry"):
                            auto_crab.retry_step("check_crab_entry", auto_crab.check_crab_entry)

        run_adb.assert_not_called()

    def test_visible_crab_skips_map_swipe(self):
        screen = read_image("screenshots/raw/crab_entry_next_stage.png")
        with mock.patch.object(auto_crab.battle_attack_profile, "adb_screenshot", return_value=screen), \
             mock.patch.object(auto_crab, "is_on_crab_attack_page", return_value=False), \
             mock.patch.object(auto_crab.battle_attack_profile, "run_adb") as run_adb:
            self.assertTrue(auto_crab.check_crab_entry())
        run_adb.assert_not_called()

    def test_known_ui_templates(self):
        attack = read_image("screenshots/raw/crab_main_ready.png")
        confirm = read_image("screenshots/raw/attack_confirm.png")
        result = read_image("screenshots/raw/battle_result.png")
        self.assertGreaterEqual(
            click_attack_button.find_template(
                attack,
                click_attack_button.ATTACK_BUTTON_TEMPLATE,
                click_attack_button.ATTACK_SEARCH_REGION,
                click_attack_button.ATTACK_MATCH_THRESHOLD,
            ).score,
            click_attack_button.ATTACK_MATCH_THRESHOLD,
        )
        self.assertGreaterEqual(
            click_confirm_attack.find_template(confirm).score,
            click_confirm_attack.MATCH_THRESHOLD,
        )
        self.assertGreaterEqual(
            click_result_return.find_template(result).score,
            click_result_return.MATCH_THRESHOLD,
        )

    def test_troop_snapshot_handles_boolean_thresholds(self):
        crop = np.zeros((80, 416, 3), dtype=np.uint8)
        snapshot = troop_status.TroopSnapshot(
            slot_count=troop_status.estimate_slot_count(crop),
            signature=troop_status.build_signature(crop),
        )
        self.assertEqual(snapshot.slot_count, 0)
        self.assertEqual(snapshot.signature.shape, (36, 416))

    def test_troop_snapshot_comparison(self):
        before_crop = read_image("screenshots/raw/crab_entry.png")[:, :416]
        after_crop = read_image("screenshots/raw/battle_entered.png")[:, :416]
        before = troop_status.TroopSnapshot(
            slot_count=troop_status.estimate_slot_count(before_crop),
            signature=troop_status.build_signature(before_crop),
        )
        after = troop_status.TroopSnapshot(
            slot_count=troop_status.estimate_slot_count(after_crop),
            signature=troop_status.build_signature(after_crop),
        )
        self.assertTrue(troop_status.is_same(before, before))
        self.assertFalse(troop_status.is_same(before, after))


if __name__ == "__main__":
    unittest.main()
