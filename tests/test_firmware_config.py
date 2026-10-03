"""Validate device-owned credentials and the actual merged firmware packages."""

import base64
from contextlib import redirect_stdout
import importlib.util
import io
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
# Public test data, never a device credential.
TEST_KEY = base64.b64encode(bytes(range(32))).decode()


@unittest.skipUnless(importlib.util.find_spec("esphome"), "install ESPHome to check package merging")
class FirmwareConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix="upsy-firmware-config-")
        cls.addClassCleanup(cls.folder.cleanup)
        cls.project = Path(cls.folder.name)
        for directory in ("firmware", "reversing-firmware"):
            shutil.copytree(ROOT / directory, cls.project / directory,
                            ignore=shutil.ignore_patterns(".esphome", "__pycache__"))
        cls.cache = os.environ.get("ESPHOME_DATA_DIR", str(ROOT / "firmware/.esphome"))

    def load(self, name, contents=None):
        from esphome.config import read_config
        from esphome.core import CORE

        path = self.project / name
        if contents is not None:
            path.write_text(contents)
        CORE.reset()
        CORE.config_path = path
        # Avoid printing merged credentials, including test credentials.
        with patch.dict(os.environ, {"ESPHOME_DATA_DIR": self.cache}), redirect_stdout(io.StringIO()):
            config = read_config({}, skip_external_update=True)
        self.assertIsNotNone(config, name)
        return config

    def native_ota(self, config):
        instances = [item for item in config.get("ota", []) if item["platform"] == "esphome"]
        self.assertEqual(len(instances), 1)
        return instances[0]

    def test_base_does_not_require_api_or_ota(self):
        config = self.load("firmware/base.yaml")
        self.assertNotIn("api", config)
        self.assertNotIn("ota", config)
        # Package UART mappings merge into one duplex bus; height uses that bus.
        self.assertEqual(len(config["uart"]), 1)
        uart = config["uart"][0]
        height = next(item for item in config["sensor"] if str(item["id"]) == "desk_height")
        self.assertEqual(str(height["uart_id"]), str(uart["id"]))
        self.assertEqual(uart["rx_pin"]["number"], 17)
        self.assertEqual(uart["tx_pin"]["number"], 16)

    def test_stock_debug_and_legacy_packages_leave_credentials_to_device(self):
        for package in ("stock", "debug", "config"):
            with self.subTest(package=package):
                config = self.load(f"firmware/{package}.yaml")
                ota = self.native_ota(config)
                self.assertNotIn("password", ota)
                self.assertNotIn("encryption", ota)
                self.assertNotIn("encryption", config["api"])
                self.assertFalse(config["web_server"]["ota"])
                self.assertIn("captive_portal", config)

    def test_device_can_require_encrypted_ota_with_same_api_key(self):
        for package in ("stock", "debug"):
            with self.subTest(package=package):
                config = self.load("firmware/check-encrypted.yaml", f"""
packages:
  firmware: !include {package}.yaml
api:
  encryption:
    key: {TEST_KEY}
ota:
  - platform: esphome
    encryption:
""")
                ota = self.native_ota(config)
                self.assertNotIn("password", ota)
                self.assertEqual(ota["encryption"]["key"], config["api"]["encryption"]["key"])
                self.assertFalse(config["web_server"]["ota"])

    def test_legacy_device_can_supply_ota_password(self):
        config = self.load("firmware/check-password.yaml", """
packages:
  firmware: !include stock.yaml
ota:
  - platform: esphome
    password: test-ota-password
""")
        ota = self.native_ota(config)
        self.assertEqual(ota["password"], "test-ota-password")
        self.assertNotIn("encryption", ota)

    def test_disabled_scanner_preserves_output_modes(self):
        config = self.load("firmware/check-disabled.yaml", """
packages:
  firmware: !include stock.yaml
substitutions:
  upsy_keypad_gpio_enabled: "false"
""")
        for output in config["output"]:
            with self.subTest(output=str(output["id"])):
                mode = output["pin"]["mode"]
                self.assertFalse(mode["input"])
                self.assertTrue(mode["output"])
                self.assertFalse(mode["open_drain"])

    def test_height_entity_extensions_still_merge(self):
        config = self.load("firmware/check-entities.yaml", """
packages:
  firmware: !include stock.yaml
sensor:
  - id: !extend desk_height
    unit_of_measurement: cm
number:
  - id: !extend target_desk_height
    unit_of_measurement: cm
""")
        for domain, entity_id in (("sensor", "desk_height"), ("number", "target_desk_height")):
            entities = [item for item in config[domain] if str(item["id"]) == entity_id]
            self.assertEqual(len(entities), 1)
            self.assertEqual(entities[0]["unit_of_measurement"], "cm")

    def test_reversing_firmware_has_native_ota_and_both_uart_directions(self):
        config = self.load("reversing-firmware/config.yaml")
        ota = self.native_ota(config)
        self.assertNotIn("password", ota)
        self.assertNotIn("encryption", ota)
        self.assertEqual(len(config["uart"]), 2)
        for uart, direction in zip(config["uart"], ("TX", "RX")):
            self.assertTrue(uart["debug"]["dummy_receiver"])
            self.assertEqual(uart["debug"]["after"]["timeout"].total_milliseconds, 50)
            sequence = uart["debug"]["sequence"]
            self.assertEqual(len(sequence), 1)
            self.assertIn(f"UART_DIRECTION_{direction}", sequence[0]["then"][0]["lambda"].value)
