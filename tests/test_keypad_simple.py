"""Run the shared regressions and explicitly compare the simpler scanner.

Partial-mask transitions below are synthetic, not captures from a handset.
"""

import importlib.util
import os
from pathlib import Path
import re
import shutil
import tempfile
import unittest
from unittest.mock import patch

import test_keypad as keypad


class SimpleKeypadTests(keypad.KeypadTests):
    addon = (keypad.ROOT / "firmware/addons/button-record-simple.yaml").read_text()

    # Configuration assertions belong to the normal scanner only.
    test_invalid_physical_memory_mask = None
    test_scanner_rejects_uart_and_wake_pins = None

    def test_physical_memory_masks_without_custom_validation(self):
        for mask in ("0x00", "0x10"):
            with self.subTest(mask=mask):
                self.run_cpp(r"""
                int main() {
                  reset_scanner(); hold(10);
                  expect("unknown", "physical_keypad", 10);
                }
                """, {"upsy_keypad_physical_memory_mask": mask})
        self.run_cpp(r"""
        int main() {
          reset_scanner(); hold(2);
          expect("memory", "physical_keypad", 2); // A collision is no longer rejected.
        }
        """, {"upsy_keypad_physical_memory_mask": "0x02"})

    def test_uart_and_wake_pin_checks_are_not_enforced(self):
        for pin in ("standing_desk_up_pin", "standing_desk_down_pin",
                    "button_bit1_pin", "button_bit2_pin", "button_bit4_pin", "button_m_pin"):
            for uart_pin in ("16", "17"):
                with self.subTest(pin=pin, uart_pin=uart_pin):
                    # Compile only: do not simulate activity on the wrong pins.
                    self.run_cpp("int main() {}", {pin: uart_pin})

    def test_contact_bounce_and_partial_release(self):
        self.run_cpp(r"""
        int main() {
          reset_scanner();
          sample(1, 0); sample(0, 0); sample(2, 0); sample(3, 0);
          assert(publisher.events.empty());
          hold(3);
          assert(publisher.events.size() == 1);
          expect("preset_1", "physical_keypad", 3);
          hold(1); // A stable partial release now emits Up.
          assert(publisher.events.size() == 2);
          expect("up", "physical_keypad", 1);
          sample(0, 0); sample(1, 0); // Short bounce still emits nothing.
          hold(1);
          assert(publisher.events.size() == 2);
          hold(0); hold(1);
          assert(publisher.events.size() == 3);
          expect("up", "physical_keypad", 1);
        }
        """)

    def test_physical_memory_with_separate_virtual_store_mask(self):
        self.run_cpp(r"""
        int main() {
          reset_scanner();
          hold(10);
          expect("memory", "physical_keypad", 10);
          hold(10, 0, 200);
          assert(publisher.events.size() == 1);
          hold(2); // Stable partial release now emits Down.
          assert(publisher.events.size() == 2);
          expect("down", "physical_keypad", 2);
          hold(0); hold(10);
          assert(publisher.events.size() == 3);
          expect("memory", "physical_keypad", 10);

          reset_scanner();
          hold(0, 8);
          expect("memory", "virtual_control", 8);
          hold(0, 3);
          assert(publisher.events.size() == 2);
          expect("preset_1", "virtual_control", 3);

          reset_scanner();
          hold(2, 8);
          assert(publisher.events.size() == 1);
          expect("unknown", "mixed_input", 10);
          hold(2, 8);
          assert(publisher.events.size() == 1);

          reset_scanner();
          hold(8);
          expect("unknown", "physical_keypad", 8);
        }
        """, {"upsy_keypad_physical_memory_mask": "0x0A"})

    def test_compare_physical_transitions_without_full_release(self):
        body = r"""
        int main() {
          reset_scanner();
          hold(3); hold(4); // Two stable physical buttons without a release.
          assert(publisher.events.size() == EVENT_COUNT);
          expect(LAST_BUTTON, "physical_keypad", LAST_MASK);
        }
        """
        keypad.KeypadTests().run_cpp(body.replace("EVENT_COUNT", "1")
                                     .replace("LAST_BUTTON", '"preset_1"')
                                     .replace("LAST_MASK", "3"))
        self.run_cpp(body.replace("EVENT_COUNT", "2")
                     .replace("LAST_BUTTON", '"preset_2"')
                     .replace("LAST_MASK", "4"))

    def test_compare_same_raw_mask_changing_source(self):
        body = r"""
        int main() {
          reset_scanner();
          hold(0, 1); hold(1, 0); // Firmware releases a wire held by the handset.
          assert(publisher.events.size() == EVENT_COUNT);
          expect("up", LAST_SOURCE, 1);
        }
        """
        keypad.KeypadTests().run_cpp(body.replace("EVENT_COUNT", "1")
                                     .replace("LAST_SOURCE", '"virtual_control"'))
        self.run_cpp(body.replace("EVENT_COUNT", "2")
                     .replace("LAST_SOURCE", '"physical_keypad"'))

    def test_compare_physical_memory_then_preset_without_release(self):
        body = r"""
        int main() {
          reset_scanner();
          hold(10); hold(3);
          assert(publisher.events.size() == EVENT_COUNT);
          assert(publisher.events.front().button == "memory");
          expect(LAST_BUTTON, "physical_keypad", LAST_MASK);
        }
        """
        overrides = {"upsy_keypad_physical_memory_mask": "0x0A"}
        keypad.KeypadTests().run_cpp(body.replace("EVENT_COUNT", "1")
                                     .replace("LAST_BUTTON", '"memory"')
                                     .replace("LAST_MASK", "10"), overrides)
        self.run_cpp(body.replace("EVENT_COUNT", "2")
                     .replace("LAST_BUTTON", '"preset_1"')
                     .replace("LAST_MASK", "3"), overrides)


@unittest.skipUnless(importlib.util.find_spec("esphome"), "install ESPHome to check package merging")
class SimpleKeypadConfigTests(unittest.TestCase):
    def test_consuming_config_overrides_physical_memory(self):
        from esphome.config import read_config
        from esphome.core import CORE

        cache = os.environ.get("ESPHOME_DATA_DIR", str(
            keypad.ROOT / "firmware/.esphome"))
        with tempfile.TemporaryDirectory(prefix="upsy-keypad-memory-") as folder:
            project = Path(folder)
            shutil.copytree(keypad.ROOT / "firmware", project / "firmware",
                            ignore=shutil.ignore_patterns(".esphome", "__pycache__"))
            device = project / "device.yaml"
            for memory_mask in ("0x0A", "0x08"):
                with self.subTest(memory_mask=memory_mask):
                    contents = "packages:\n  stock: !include firmware/stock.yaml\n"
                    if memory_mask == "0x08":
                        contents += "substitutions:\n  upsy_keypad_physical_memory_mask: \"0x08\"\n"
                    device.write_text(contents)
                    CORE.reset()
                    CORE.config_path = device
                    with patch.dict(os.environ, {"ESPHOME_DATA_DIR": cache}):
                        config = read_config({}, skip_external_update=True)
                    self.assertIsNotNone(config)
                    scan = next(item for item in config["interval"]
                                if str(item["id"]) == "upsy_keypad_simple_scan")
                    code = scan["then"][0]["lambda"].value
                    self.assertIn(f"source == 1 && mask == {memory_mask}", code)
                    self.assertIn("case 0x08:", code)

    def test_only_scanner_changes(self):
        from esphome.config import read_config
        from esphome.core import CORE, ID, Lambda

        def load(path):
            CORE.reset()
            CORE.config_path = path
            config = read_config({}, skip_external_update=True)
            self.assertIsNotNone(config)
            return config

        def normalize(value):
            if isinstance(value, ID):
                return value.id if value.is_manual else None
            if isinstance(value, Lambda):
                return value.value
            if isinstance(value, dict):
                return {key: normalize(item) for key, item in value.items()}
            if isinstance(value, list):
                return [normalize(item) for item in value]
            if isinstance(value, (str, int, float, bool)) or value is None:
                return value
            return str(value)

        # Exercise either addon as the sole keypad include in the real base.
        # Keep remote dependency caches outside the temporary configuration.
        cache = os.environ.get("ESPHOME_DATA_DIR", str(
            keypad.ROOT / "firmware/.esphome"))
        with tempfile.TemporaryDirectory(prefix="upsy-keypad-config-") as folder:
            project = Path(folder)
            shutil.copytree(keypad.ROOT / "firmware", project / "firmware",
                            ignore=shutil.ignore_patterns(".esphome", "__pycache__"))
            (project / "tests").mkdir()
            shutil.copy2(keypad.ROOT / "tests/keypad-simple.yaml",
                         project / "tests/keypad-simple.yaml")
            base_path = project / "firmware/base.yaml"
            base = base_path.read_text()
            with patch.dict(os.environ, {"ESPHOME_DATA_DIR": cache}):
                base_path.write_text(re.sub(r"addon_button_record: !include addons/button-record[^\n]*",
                                            "addon_button_record: !include addons/button-record.yaml", base))
                normal = load(project / "firmware/stock.yaml")
                base_path.write_text(re.sub(r"addon_button_record: !include addons/button-record[^\n]*",
                                            "addon_button_record: !include addons/button-record-simple.yaml", base))
                simple = load(project / "tests/keypad-simple.yaml")
        for domain in ("output", "uart", "button", "number", "sensor", "event", "text_sensor", "script"):
            with self.subTest(domain=domain):
                self.assertEqual(
                    normalize(normal[domain]), normalize(simple[domain]))
        self.assertEqual(normalize(normal["esphome"]["on_boot"]),
                         normalize(simple["esphome"]["on_boot"]))
        self.assertEqual(len(normal["interval"]), 2)
        self.assertEqual(len(simple["interval"]), 2)
        self.assertEqual(sum(str(item["id"]) == "upsy_keypad_simple_scan"
                             for item in simple["interval"]), 1)
        self.assertFalse(any(str(item["id"]) == "upsy_keypad_scan"
                             for item in simple["interval"]))
        self.assertFalse(any(str(item["id"]) == "upsy_keypad_armed"
                             for item in simple["globals"]))
        self.assertEqual({str(item["id"]) for item in simple["globals"]},
                         {str(item["id"]) for item in normal["globals"]} - {"upsy_keypad_armed"})
        self.assertEqual(sum(str(item["id"]) == "upsy_keypad_publish_action"
                             for item in simple["script"]), 1)
        scan = next(item for item in simple["interval"]
                    if str(item["id"]) == "upsy_keypad_simple_scan")
        self.assertEqual(len(scan["then"]), 1)
        normal_retry = [item for item in normal["interval"]
                        if str(item["id"]) != "upsy_keypad_scan"]
        simple_retry = [item for item in simple["interval"]
                        if str(item["id"]) != "upsy_keypad_simple_scan"]
        self.assertEqual(normalize(normal_retry), normalize(simple_retry))
