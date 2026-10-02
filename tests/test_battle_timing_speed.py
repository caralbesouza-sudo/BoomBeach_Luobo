import sys
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import battle_attack_profile as battle


class BattleTimingSpeedTests(unittest.TestCase):
    def screen(self, name):
        return battle.read_image(ROOT / "screenshots" / "raw" / f"{name}.png")

    def test_speed_template_on_recorded_screens(self):
        for name in ("battle_after_deploy", "battle_skills_unavailable"):
            with self.subTest(name=name):
                match = battle.find_triple_speed_button(self.screen(name))
                self.assertGreaterEqual(match.score, battle.TRIPLE_SPEED_MATCH_THRESHOLD)
                self.assertEqual(match.center, (1189, 103))
        for name in ("battle_entered", "battle_full_troops", "battle_result",
                     "attack_confirm", "crab_main_ready", "crab_entry_next_stage"):
            with self.subTest(name=name):
                match = battle.find_triple_speed_button(self.screen(name))
                self.assertLess(match.score, battle.TRIPLE_SPEED_MATCH_THRESHOLD)

    def test_speed_only_searches_its_button_region(self):
        screen = np.zeros((720, 1280, 3), np.uint8)
        template = battle.read_image(battle.TRIPLE_SPEED_TEMPLATE)
        height, width = template.shape[:2]
        screen[400:400+height, 200:200+width] = template
        self.assertLess(battle.find_triple_speed_button(screen).score,
                        battle.TRIPLE_SPEED_MATCH_THRESHOLD)

    def test_full_deployment_then_single_speed_click(self):
        point = (867, 462)
        events = []
        with mock.patch.object(battle, "search_landing_point", side_effect=lambda **kwargs: events.append(("search", None)) or point), \
             mock.patch.object(battle.map_zoom, "zoom_out_once", side_effect=lambda *args, **kwargs: events.append(("zoom_once", None))), \
             mock.patch.object(battle, "adb_screenshot", return_value=self.screen("battle_after_deploy")) as capture, \
             mock.patch.object(battle, "tap", side_effect=lambda p, label, **kwargs: events.append(("tap", p))) as tap, \
             mock.patch.object(battle, "release_shock_once"), \
             mock.patch.object(battle, "release_robot_hero_cycle") as cycle, \
             mock.patch.object(battle.time, "monotonic", return_value=100.0), \
             mock.patch.object(battle.time, "sleep", side_effect=lambda s: events.append(("wait", s))):
            battle.main()
        points = [call.args[0] for call in tap.call_args_list]
        expected = [battle.ROBOT_SKILL_POINT, point]
        for card in battle.TROOP_CARD_POINTS:
            expected.extend([card, point])
        expected.extend([battle.HERO_CARD_POINT, point, battle.HERO_CARD_POINT])
        expected.extend([battle.ROBOT_SKILL_POINT, point])
        expected.append((1189, 103))
        self.assertEqual(points, expected)
        self.assertEqual(events.count(("zoom_once", None)), 1)
        self.assertLess(events.index(("zoom_once", None)), events.index(("search", None)))
        self.assertLess(events.index(("search", None)), events.index(("tap", battle.ROBOT_SKILL_POINT)))
        first_card = events.index(("tap", battle.TROOP_CARD_POINTS[0]))
        self.assertEqual(events[first_card-3:first_card+1], [
            ("tap", battle.ROBOT_SKILL_POINT), ("tap", point),
            ("wait", 1.0), ("tap", battle.TROOP_CARD_POINTS[0]),
        ])
        capture.assert_called_once()  # 按钮仍显示时也不会再次点击。
        cycle.assert_called_once_with(point, last_hero_at=100.0)

    def test_hero_and_robot_stay_adjacent_in_each_cycle(self):
        events = []
        point = (867, 462)
        def wait(seconds, label):
            events.append(("wait", seconds))
            return True
        with mock.patch.object(battle, "ROBOT_HERO_CYCLE_ROUNDS", 2), \
             mock.patch.object(battle.time, "monotonic", return_value=100.0), \
             mock.patch.object(battle, "wait_before_next_action", side_effect=wait), \
             mock.patch.object(battle, "tap", side_effect=lambda p, label: events.append(("tap", p))):
            battle.release_robot_hero_cycle(point)
        self.assertEqual(events, [
            ("wait", battle.FIRST_HERO_SKILL_SECONDS),
            ("tap", battle.HERO_CARD_POINT), ("tap", battle.ROBOT_SKILL_POINT), ("tap", point),
            ("wait", battle.NEXT_HERO_SKILL_SECONDS),
            ("tap", battle.HERO_CARD_POINT), ("tap", battle.ROBOT_SKILL_POINT), ("tap", point),
        ])

    def test_initial_robot_follows_hero_without_extra_wait(self):
        events = []
        point = (867, 462)
        with mock.patch.object(battle, "deploy_troop"), \
             mock.patch.object(battle, "deploy_hero"), \
             mock.patch.object(battle.time, "sleep", side_effect=lambda s: events.append(("wait", s))), \
             mock.patch.object(battle, "tap", side_effect=lambda p, label: events.append(("tap", p))):
            battle.deploy_units_in_order(point)
        self.assertEqual(events, [
            ("wait", battle.WAIT_AFTER_HERO_DEPLOY_SECONDS),
            ("tap", battle.HERO_CARD_POINT), ("tap", battle.ROBOT_SKILL_POINT), ("tap", point),
        ])

    def test_completed_battle_stops_skill_pairs(self):
        with mock.patch.object(battle, "wait_before_next_action", return_value=False), \
             mock.patch.object(battle, "tap") as tap:
            battle.release_robot_hero_cycle((867, 462))
        tap.assert_not_called()

    def test_three_second_cadence_includes_robot_and_speed_time(self):
        clock = [0.0]
        clicks = []
        point = (867, 462)

        def sleep(seconds):
            self.assertGreaterEqual(seconds, 0)
            clock[0] += seconds

        def tap(p, label):
            clicks.append((p, clock[0]))
            clock[0] += 0.12  # 每次 ADB 操作的模拟耗时。

        with mock.patch.object(battle.time, "monotonic", side_effect=lambda: clock[0]), \
             mock.patch.object(battle.time, "sleep", side_effect=sleep), \
             mock.patch.object(battle, "tap", side_effect=tap), \
             mock.patch.object(battle, "is_result_page_visible", return_value=False), \
             mock.patch.object(battle, "ROBOT_HERO_CYCLE_ROUNDS", 3):
            first_at = battle.release_initial_hero_skill_once()
            battle.release_robot_round(2, point)
            sleep(0.8)  # 开启三倍速等操作也应算在首次 3 秒间隔内。
            battle.release_robot_hero_cycle(point, last_hero_at=first_at)

        hero_times = [t for p, t in clicks if p == battle.HERO_CARD_POINT]
        self.assertEqual(len(hero_times), 4)
        for i, value in enumerate(hero_times):
            self.assertAlmostEqual(value, first_at + 3.0 * i)
        for i in range(0, len(clicks), 3):
            self.assertEqual([p for p, _ in clicks[i:i+3]],
                             [battle.HERO_CARD_POINT, battle.ROBOT_SKILL_POINT, point])
            self.assertAlmostEqual(clicks[i+1][1] - clicks[i][1], 0.12)

    def test_speed_retries_when_not_visible_then_clicks_once(self):
        screens = [self.screen("battle_full_troops"), self.screen("battle_after_deploy")]
        with mock.patch.object(battle, "adb_screenshot", side_effect=screens), \
             mock.patch.object(battle, "tap") as tap, \
             mock.patch.object(battle.time, "sleep"):
            self.assertTrue(battle.enable_triple_speed())
        tap.assert_called_once_with((1189, 103), "开启三倍速")

    def test_missing_speed_and_result_screen_never_click(self):
        for name, expected_captures in (("battle_full_troops", 3), ("battle_result", 1)):
            with self.subTest(name=name), \
                 mock.patch.object(battle, "adb_screenshot", return_value=self.screen(name)) as capture, \
                 mock.patch.object(battle, "tap") as tap, \
                 mock.patch.object(battle.time, "sleep"):
                self.assertFalse(battle.enable_triple_speed())
                tap.assert_not_called()
                self.assertEqual(capture.call_count, expected_captures)

    def test_deployment_interval_accounts_for_adb_latency(self):
        for latency, expected_interval in ((0.05, 0.4), (0.25, 0.62)):
            clock = [0.0]
            landing_times = []

            def sleep(seconds):
                self.assertGreaterEqual(seconds, 0)
                clock[0] += seconds

            def adb(*args):
                clock[0] += latency
                if args[-2:] == ("777", "444"):
                    landing_times.append(clock[0])

            with self.subTest(latency=latency), \
                 mock.patch.object(battle.time, "monotonic", side_effect=lambda: clock[0]), \
                 mock.patch.object(battle.time, "sleep", side_effect=sleep), \
                 mock.patch.object(battle, "run_adb", side_effect=adb):
                battle.deploy_troop(1, (777, 444))
                battle.deploy_troop(2, (777, 444))
                battle.deploy_hero((777, 444))
            self.assertEqual(len(landing_times), 3)
            self.assertAlmostEqual(landing_times[1] - landing_times[0], expected_interval)
            self.assertAlmostEqual(landing_times[2] - landing_times[1], expected_interval)
            self.assertAlmostEqual(clock[0], 3 * expected_interval)


if __name__ == "__main__":
    unittest.main()
