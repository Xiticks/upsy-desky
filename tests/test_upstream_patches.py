"""Exercise proposed upstream fixes in an isolated copy, never the dependency."""

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

import test_keypad as keypad


@unittest.skipUnless(os.environ.get("STANDING_DESK_SOURCE"),
                     "set STANDING_DESK_SOURCE to test proposed upstream patches")
class UpstreamPatchTests(unittest.TestCase):
    def test_invalid_height_rejection_and_movement_guard(self):
        with tempfile.TemporaryDirectory(prefix="upsy-upstream-patches-") as folder:
            project = Path(folder)
            source = Path(os.environ["STANDING_DESK_SOURCE"])
            for directory in ("components", "configs"):
                shutil.copytree(source / directory, project / directory,
                                ignore=shutil.ignore_patterns("__pycache__"))
            for name in ("jarvis-height-length", "detect-decoder-synchronous", "target-height-require-feedback"):
                proposed = keypad.ROOT / f"firmware/upstream/{name}.patch"
                subprocess.run(["git", "apply", "--check", str(proposed)], cwd=project, check=True)
                subprocess.run(["git", "apply", str(proposed)], cwd=project, check=True)
            template = (project / "configs/template.yaml").read_text()
            guard = re.search(r'lambda: "([^"]+)"', template.split("    set_action:\n", 1)[1])[1]
            keypad.KeypadTests().run_cpp(r"""
            #include "standing_desk_height.h"
            using namespace esphome::standing_desk_height;
            bool can_move(StandingDeskHeightSensor *desk_height) {
            """ + guard + r"""
            }
            void feed(StandingDeskHeightSensor &desk, std::initializer_list<uint8_t> frame) {
              for (uint8_t byte : frame) desk.rx.push_back(byte);
              desk.loop(); desk.update();
            }
            int main() {
              StandingDeskHeightSensor desk{};
              desk.set_decoder_variant(DECODER_VARIANT_JARVIS);
              desk.setup();
              assert(!can_move(&desk));
              // Checksummed but truncated HEIGHT payloads must not establish height.
              feed(desk, {0xF2,0xF2,0x01,0x00,0x01,0x7E});
              feed(desk, {0xF2,0xF2,0x01,0x01,0x01,0x03,0x7E});
              assert(desk.get_last_read() == -1 && desk.published.empty());
              assert(!can_move(&desk));
              feed(desk, {0xF2,0xF2,0x01,0x03,0x01,0x2C,0x07,0x38,0x7E});
              assert(can_move(&desk));
              feed(desk, {0xF2,0xF2,0x01,0x01,0x01,0x03,0x7E});
              assert(desk.get_last_read() == 30 && desk.published.size() == 1);
              feed(desk, {0xF2,0xF2,0x01,0x03,0x01,0x36,0x07,0x42,0x7E});
              assert((desk.published == std::vector<float>{30,31}));
              desk.set_decoder_variant(DECODER_VARIANT_UNKNOWN);
            }
            """, upstream=project / "components/standing_desk_height")
