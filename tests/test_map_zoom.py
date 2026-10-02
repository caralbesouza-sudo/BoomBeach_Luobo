import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import map_zoom
import battle_attack_profile as battle


CAPABILITIES = '''add device 1: /dev/input/event4
  name: "mouse"
  events: REL_X REL_Y
  input props: INPUT_PROP_DIRECT
add device 2: /dev/input/event2
  name: "input"
  events: BTN_TOUCH BTN_TOOL_FINGER
    ABS_MT_SLOT : value 0, min 0, max 15, fuzz 0
    ABS_MT_POSITION_X : value 0, min 0, max 1279, fuzz 0
    ABS_MT_POSITION_Y : value 0, min 0, max 719, fuzz 0
    ABS_MT_TRACKING_ID : value 0, min 0, max 65535, fuzz 0
    ABS_MT_PRESSURE : value 0, min 0, max 2, fuzz 0
  input props: INPUT_PROP_DIRECT
'''


class MapZoomTests(unittest.TestCase):
    def test_detects_multitouch_not_mouse(self):
        device = map_zoom.parse_touch_device(CAPABILITIES)
        self.assertEqual(device.path, "/dev/input/event2")
        self.assertTrue(device.pressure and device.touch_key and device.finger_key)

    def test_rejects_single_touch_or_unmapped_coordinates(self):
        for value in (CAPABILITIES.replace("max 15", "max 0"),
                      CAPABILITIES.replace("max 1279", "max 32767"), ""):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                map_zoom.parse_touch_device(value)

    def test_single_pinch_converges_and_releases_contacts(self):
        script = map_zoom.build_zoom_script(map_zoom.parse_touch_device(CAPABILITIES))
        gestures = script.split("echo '地图缩小 ")[1:]
        self.assertEqual(len(gestures), 1)
        for gesture in gestures:
            slots = {}
            slot = None
            separations = []
            for line in gesture.splitlines():
                values = line.split()
                if values[:3] == ["ev", "3", "47"]:
                    slot = int(values[3])
                elif values[:3] == ["ev", "3", "53"]:
                    slots[slot] = int(values[3])
                elif line == "ev 0 0 0":
                    self.assertEqual(set(slots), {0, 1})
                    separations.append(slots[1] - slots[0])
            self.assertEqual(separations[0], 480)
            self.assertEqual(separations[-1], 400)
            self.assertEqual(len(separations), 17)
            self.assertTrue(all(a > b for a, b in zip(separations, separations[1:])))
            self.assertTrue(gesture.endswith("release_contacts\nsleep 0.6\n"))
        self.assertIn("trap 'release_contacts' EXIT", script)

    def test_read_only_probe_does_not_send_gestures(self):
        command = lambda *parts: ["adb", "-s", "emulator-5554", *parts]
        with mock.patch.object(map_zoom.subprocess, "run", side_effect=[
            subprocess.CompletedProcess([], 0, stdout=CAPABILITIES),
            subprocess.CompletedProcess([], 0),
        ]) as run:
            device = map_zoom.discover_touch_device(command)
        self.assertEqual(device.path, "/dev/input/event2")
        self.assertEqual([c.args[0][3:] for c in run.call_args_list], [
            ["shell", "getevent", "-lp"], ["shell", "test", "-w", "/dev/input/event2"],
        ])

    def test_timeout_releases_contacts_and_stops(self):
        device = map_zoom.parse_touch_device(CAPABILITIES)
        with mock.patch.object(map_zoom, "discover_touch_device", return_value=device), \
             mock.patch.object(map_zoom.subprocess, "run", side_effect=[
                 subprocess.TimeoutExpired("adb", 20), subprocess.CompletedProcess([], 0),
             ]) as run:
            with self.assertRaises(RuntimeError):
                map_zoom.zoom_out_once(lambda *parts: ["adb", *parts])
        self.assertEqual(run.call_count, 2)
        self.assertIn("3 57 -1", run.call_args_list[1].kwargs["input"])

    def test_zoom_failure_prevents_search_and_deployment(self):
        with mock.patch.object(battle, "validate_profile"), \
             mock.patch.object(battle.time, "sleep"), \
             mock.patch.object(map_zoom, "zoom_out_once", side_effect=RuntimeError("zoom failed")), \
             mock.patch.object(battle, "search_landing_point") as search, \
             mock.patch.object(battle, "deploy_units_in_order") as deploy:
            with self.assertRaisesRegex(RuntimeError, "zoom failed"):
                battle.main()
        search.assert_not_called()
        deploy.assert_not_called()

    def test_dry_run_never_connects_to_device(self):
        with mock.patch.object(map_zoom.subprocess, "run") as run:
            map_zoom.zoom_out_once(lambda *parts: ["adb", *parts], dry_run=True)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
