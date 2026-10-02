"""Compile and exercise the actual YAML scanner, without an ESP32.

Run with python3 -m unittest discover -s tests -v.
Set STANDING_DESK_SOURCE to an upstream checkout to also test UART.
Only Python's standard library and a C++ compiler are required.
"""

import os
from pathlib import Path
import re
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
ADDON = (ROOT / "firmware/addons/button-record.yaml").read_text()
PUBLISHER = (ROOT / "firmware/addons/keypad-events.yaml").read_text()
BASE = (ROOT / "firmware/base.yaml").read_text()


def substitutions(text):
    return dict(re.findall(r'^  (\w+): "([^"]+)"$', text, re.MULTILINE))


def substitute(code, overrides=None, addon=ADDON):
    values = substitutions(BASE) | substitutions(addon) | (overrides or {})
    def resolve(match):
        return re.sub(r"\$\{(\w+)\}", resolve, values[match[1]])
    return re.sub(r"\$\{(\w+)\}", resolve, code)


def scanner(overrides=None, addon=ADDON):
    # Extract production code rather than maintaining a test copy of it.
    code = textwrap.dedent(addon.split("interval:\n", 1)[1].split("lambda: |-\n", 1)[1])
    globals_code = "\n".join(
        f"{kind} {name} = {initial};"
        for name, kind, initial in re.findall(
            r"  - id: (\w+)\n    type: ([^\n]+)\n"
            r"    restore_value: no\n    initial_value: '([^']+)'", addon
        )
    )
    return globals_code + "\nvoid scan() {\n" + substitute(code, overrides, addon) + "\n}\n"


PRELUDE = r"""
#include <cassert>
#include <cmath>
#include <cstdint>
#include <deque>
#include <string>
#include <vector>
#define id(x) x
uint32_t now_ms = 0;
namespace esphome { uint32_t millis() { return now_ms; } }
using esphome::millis;
using gpio_num_t = int;
bool low[40] = {};
int gpio_reads = 0;
int gpio_get_level(gpio_num_t pin) { ++gpio_reads; return !low[pin]; }
struct { uint32_t out = ~0U; struct { uint32_t val = ~0U; } out1; } GPIO;
struct Event { std::string button; int mask; std::string source; };
struct Publisher {
  std::vector<Event> events;
  void execute(std::string button, int mask, std::string source) {
    events.push_back({button, mask, source});
  }
} publisher;
Publisher *upsy_keypad_publish_action = &publisher;
"""

HELPERS = r"""
void reset_scanner() {
  upsy_keypad_candidate_mask = upsy_keypad_stable_mask = 0;
  upsy_keypad_candidate_source = upsy_keypad_stable_source = 0;
  upsy_keypad_candidate_since = 0;
  upsy_keypad_armed = true;
  publisher.events.clear();
  now_ms = 0;
}
void sample(uint8_t physical, uint8_t firmware, uint32_t elapsed = 5) {
  const int pins[] = {21, 22, 19, 18};
  GPIO.out = ~0U;
  for (int bit = 0; bit < 4; ++bit) {
    low[pins[bit]] = (physical | firmware) & (1 << bit);
    if (firmware & (1 << bit)) GPIO.out &= ~(1U << pins[bit]);
  }
  now_ms += elapsed;
  scan();
}
void hold(uint8_t physical, uint8_t firmware = 0, int count = 10) {
  for (int n = 0; n < count; ++n) sample(physical, firmware);
}
void expect(const char *button, const char *source, int mask) {
  const auto &event = publisher.events.back();
  assert(event.button == button && event.source == source && event.mask == mask);
}
"""

STUBS = {
    "esphome/core/component.h": """
        #pragma once
        #include <cstdint>
        namespace esphome {
        uint32_t millis();
        class PollingComponent {
         public:
          virtual void setup() {}
          virtual void loop() {}
          virtual void update() {}
          virtual void dump_config() {}
        };
        }
    """,
    "esphome/core/log.h": """
        #pragma once
        namespace esphome { using LogString = char; }
        #define LOG_STR(s) s
        #define LOG_STR_ARG(s) s
        #define ESP_LOGE(...) ((void)0)
        #define ESP_LOGW(...) ((void)0)
        #define ESP_LOGI(...) ((void)0)
        #define ESP_LOGD(...) ((void)0)
        #define ESP_LOGVV(...) ((void)0)
        #define ESP_LOGCONFIG(...) ((void)0)
        #define LOG_SENSOR(...) ((void)0)
        #define LOG_UPDATE_INTERVAL(...) ((void)0)
    """,
    "esphome/components/sensor/sensor.h": """
        #pragma once
        #include <vector>
        namespace esphome { namespace sensor {
        class Sensor {
         public:
          std::vector<float> published;
          void publish_state(float value) { published.push_back(value); }
        };
        } }
    """,
    "esphome/components/uart/uart.h": """
        #pragma once
        #include <cstdint>
        #include <deque>
        namespace esphome { namespace uart {
        class UARTDevice {
         public:
          std::deque<uint8_t> rx;
          int available() { return rx.size(); }
          bool read_byte(uint8_t *b) { *b = rx.front(); rx.pop_front(); return true; }
        };
        } }
    """,
}


class KeypadTests(unittest.TestCase):
    addon = ADDON

    def run_cpp(self, body, overrides=None, upstream=None, expected_error=None):
        with tempfile.TemporaryDirectory(prefix="upsy-test-") as folder:
            folder = Path(folder)
            sources = []
            if upstream:
                for name, content in STUBS.items():
                    path = folder / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(textwrap.dedent(content))
                sources = [str(upstream / name) for name in (
                    "standing_desk_height.cpp", "decoder_variant.cpp",
                    "jarvis_decoder.cpp", "uplift_decoder.cpp", "omnidesk_decoder.cpp",
                )]
            cpp = folder / "test.cpp"
            helpers = HELPERS
            if "  - id: upsy_keypad_armed\n" not in self.addon:
                helpers = helpers.replace("  upsy_keypad_armed = true;\n", "")
            cpp.write_text(PRELUDE + scanner(overrides, self.addon) + helpers + body)
            binary = folder / "test"
            command = ["g++", "-std=c++17", "-g",
                       "-I", str(folder)]
            if upstream:
                command += ["-I", str(upstream)]
            command += [str(cpp), *sources, "-o", str(binary)]
            if expected_error:
                result = subprocess.run(command, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected_error, result.stderr)
                return
            subprocess.run(command, check=True)
            subprocess.run([str(binary)], check=True)

    def test_all_buttons_and_repeated_presses(self):
        self.run_cpp(r"""
        int main() {
          const uint8_t masks[] = {1, 2, 3, 4, 6, 5, 8, 15};
          const char *buttons[] = {"up", "down", "preset_1", "preset_2",
                                   "preset_3", "preset_4", "memory", "unknown"};
          for (int n = 0; n < 8; ++n) {
            reset_scanner();
            for (int press = 0; press < 2; ++press) {
              hold(masks[n]);
              expect(buttons[n], "physical_keypad", masks[n]);
              hold(masks[n], 0, 200); // Holding does not repeat events.
              assert(publisher.events.size() == static_cast<size_t>(press + 1));
              hold(0);
            }
          }
        }
        """)

    def test_contact_bounce_and_partial_release(self):
        self.run_cpp(r"""
        int main() {
          reset_scanner();
          sample(1, 0); sample(0, 0); sample(2, 0); sample(3, 0);
          assert(publisher.events.empty());
          hold(3);
          assert(publisher.events.size() == 1);
          expect("preset_1", "physical_keypad", 3);
          hold(1); // Slow release of one preset contact is not an Up press.
          assert(publisher.events.size() == 1);
          sample(0, 0); sample(1, 0); // Release bounce must not re-arm.
          hold(1);
          assert(publisher.events.size() == 1);
          hold(0); hold(1);
          assert(publisher.events.size() == 2);
          expect("up", "physical_keypad", 1);
        }
        """)

    def test_virtual_memory_and_mixed_sources(self):
        self.run_cpp(r"""
        int main() {
          reset_scanner();
          hold(0, 8); hold(0, 3); // Set Preset has no full release between actions.
          assert(publisher.events.size() == 2);
          expect("preset_1", "virtual_control", 3);
          hold(4, 3);
          assert(publisher.events.size() == 3);
          expect("unknown", "mixed_input", 7);
          hold(4, 3);
          assert(publisher.events.size() == 3);
          hold(0); hold(4);
          expect("preset_2", "physical_keypad", 4);
        }
        """)

    def test_physical_memory_with_separate_virtual_store_mask(self):
        self.run_cpp(r"""
        int main() {
          reset_scanner();
          hold(10); // Observed Jarvis handset: Memory + Down, decimal 10.
          expect("memory", "physical_keypad", 10);
          hold(10, 0, 200);
          assert(publisher.events.size() == 1);
          hold(2); // Partial contact release must not emit Down.
          assert(publisher.events.size() == 1);
          hold(0); hold(10);
          assert(publisher.events.size() == 2);
          expect("memory", "physical_keypad", 10);

          reset_scanner();
          hold(0, 8); // Firmware store still drives Memory alone.
          expect("memory", "virtual_control", 8);
          hold(0, 3); // No full release before the preset command.
          assert(publisher.events.size() == 2);
          expect("preset_1", "virtual_control", 3);

          reset_scanner();
          hold(2, 8); // Same raw 10, but mixed sources must stay unknown.
          assert(publisher.events.size() == 1);
          expect("unknown", "mixed_input", 10);
          hold(2, 8);
          assert(publisher.events.size() == 1);

          reset_scanner();
          hold(8); // Physical Memory is assigned only to 10 in this configuration.
          expect("unknown", "physical_keypad", 8);
        }
        """, {"upsy_keypad_physical_memory_mask": "0x0A"})

    def test_invalid_physical_memory_mask(self):
        for mask in ("0x00", "0x10"):
            with self.subTest(mask=mask):
                self.run_cpp("int main() {}", {"upsy_keypad_physical_memory_mask": mask},
                             expected_error="Physical Memory mask must be a nonzero four-line mask")
        self.run_cpp("int main() {}", {"upsy_keypad_physical_memory_mask": "0x02"},
                     expected_error="Physical Memory mask must differ")

    def test_memory_mask_override_remains_backward_compatible(self):
        self.run_cpp(r"""
        int main() {
          reset_scanner(); hold(10);
          expect("memory", "physical_keypad", 10);
          hold(0); hold(0, 10);
          expect("memory", "virtual_control", 10);
        }
        """, {"upsy_keypad_memory_mask": "0x0A"})

    def test_clock_wraparound(self):
        self.run_cpp(r"""
        int main() {
          reset_scanner();
          now_ms = UINT32_MAX - 15;
          hold(2);
          assert(publisher.events.size() == 1);
          expect("down", "physical_keypad", 2);
        }
        """)

    def test_different_handset_mapping(self):
        self.run_cpp(r"""
        int main() {
          reset_scanner(); hold(5);
          expect("preset_3", "physical_keypad", 5);
          hold(0); hold(6);
          expect("preset_4", "physical_keypad", 6);
        }
        """, {"upsy_keypad_preset_3_mask": "0x05", "upsy_keypad_preset_4_mask": "0x06"})

    def test_disabled_scanner_does_not_observe_gpio(self):
        self.run_cpp(r"""
        int main() {
          reset_scanner();
          hold(15); hold(0, 8); hold(0, 3);
          assert(publisher.events.empty());
          assert(gpio_reads == 0);
        }
        """, {"upsy_keypad_gpio_enabled": "false"})

    def test_scanner_rejects_uart_and_wake_pins(self):
        for pin in ("standing_desk_up_pin", "standing_desk_down_pin",
                    "button_bit1_pin", "button_bit2_pin", "button_bit4_pin", "button_m_pin"):
            for uart_pin in ("16", "17"):
                with self.subTest(pin=pin, uart_pin=uart_pin):
                    self.run_cpp("int main() {}", {pin: uart_pin},
                                 expected_error="Keypad pins must not overlap")

    def test_native_publication_order_and_repeated_events(self):
        code = textwrap.dedent(PUBLISHER.split("script:\n", 1)[1]
                                   .split("lambda: |-\n", 1)[1])
        self.run_cpp(r"""
        struct TextState {
          std::string state;
          void publish_state(std::string value) { state = value; }
        } upsy_keypad_last_button, upsy_keypad_last_source;
        struct MaskState {
          float state;
          void publish_state(float value) { state = value; }
        } upsy_keypad_last_raw_mask;
        struct NativeEvent {
          int count = 0;
          void trigger(std::string button) {
            assert(upsy_keypad_last_button.state == button);
            assert(upsy_keypad_last_source.state == "physical_keypad");
            assert(upsy_keypad_last_raw_mask.state == 1);
            ++count;
          }
        } upsy_keypad_button_event;
        void publish(std::string button_name, int raw_mask, std::string action_source) {
        """ + code + r"""
        }
        int main() {
          publish("up", 1, "physical_keypad");
          publish("up", 1, "physical_keypad");
          assert(upsy_keypad_button_event.count == 2);
        }
        """)

    @unittest.skipUnless(os.environ.get("STANDING_DESK_SOURCE"),
                         "set STANDING_DESK_SOURCE to test actual upstream UART code")
    def test_height_movement_and_late_startup(self):
        upstream = Path(os.environ["STANDING_DESK_SOURCE"]) / "components/standing_desk_height"
        # Also compile the retry condition from base.yaml for auto and fixed variants.
        condition = textwrap.dedent(BASE.split("interval:\n", 1)[1]
                                   .split("lambda: |-\n", 1)[1].split("          then:", 1)[0])
        retry_auto = substitute(condition)
        retry_fixed = substitute(condition, {"standing_desk_variant": "jarvis"})
        self.run_cpp(r"""
        #include "standing_desk_height.h"
        using namespace esphome::standing_desk_height;
        bool retry_auto(StandingDeskHeightSensor *desk_height) {
        """ + retry_auto + "\n}\n" +
        "bool retry_fixed(StandingDeskHeightSensor *desk_height) {\n" + retry_fixed + r"""
        }
        void feed(StandingDeskHeightSensor &desk, std::initializer_list<uint8_t> bytes) {
          for (uint8_t b : bytes) {
            desk.rx.push_back(b);
            sample(1, 0); // Physical activity interleaved with every UART byte.
            desk.loop();
          }
          desk.update();
          assert(desk.rx.empty());
        }
        int main() {
          // Value-initialize just as ESPHome new_Pvariable does. Upstream's
          // decoder pointer has no in-class initializer.
          StandingDeskHeightSensor jarvis{};
          jarvis.set_decoder_variant(DECODER_VARIANT_JARVIS);
          jarvis.setup();
          reset_scanner();
          feed(jarvis, {0xF2,0xF2,0x01,0x03,0x01,0x2C,0x07,0x38,0x7E}); // 30.0
          // Preset-related report (cmd 0x92) between height reports must not
          // publish a false height or stop subsequent height processing.
          feed(jarvis, {0xF2,0xF2,0x92,0x01,0x04,0x97,0x7E});
          assert(jarvis.published.size() == 1);
          feed(jarvis, {0xF2,0xF2,0x01,0x03,0x01,0x36,0x07,0x42,0x7E}); // 31.0
          feed(jarvis, {0xF2,0xF2,0x01,0x03,0x01,0x2C,0x07,0x38,0x7E}); // 30.0
          assert((jarvis.published == std::vector<float>{30,31,30}));
          assert(publisher.events.size() == 1);
          expect("up", "physical_keypad", 1);

          StandingDeskHeightSensor uplift{};
          uplift.set_decoder_variant(DECODER_VARIANT_UPLIFT);
          uplift.setup();
          feed(uplift, {0x81,0xF5,0x01,0x01,0x01,0x44}); // documented capture: 32.4
          feed(uplift, {0x01,0x01,0x01,0x45});
          feed(uplift, {0x01,0x01,0x01,0x44});
          assert(uplift.published.size() == 3);
          assert(std::abs(uplift.get_last_read() - 32.4f) < 0.001f);
          StandingDeskHeightSensor omnidesk{};
          omnidesk.set_decoder_variant(DECODER_VARIANT_OMNIDESK);
          omnidesk.setup();
          feed(omnidesk, {0x01,0x01,0x03,0x20}); // synthesized from decoder: 80.0
          feed(omnidesk, {0x01,0x01,0x03,0x2A});
          feed(omnidesk, {0x01,0x01,0x03,0x20});
          assert((omnidesk.published == std::vector<float>{80,81,80}));

          // Automatic selection still works for every height protocol while
          // keypad events are emitted, even during unsuccessful trial decoders.
          for (int variant = 0; variant < 3; ++variant) {
            StandingDeskHeightSensor automatic{};
            reset_scanner();
            automatic.setup();
            for (int attempt = 0; attempt < 25; ++attempt) {
              if (variant == 0)
                feed(automatic, {0xF2,0xF2,0x01,0x03,0x01,0x2C,0x07,0x38,0x7E});
              else if (variant == 1)
                feed(automatic, {0x01,0x01,0x01,0x44});
              else
                feed(automatic, {0x01,0x01,0x03,0x20});
              now_ms += 100;
              automatic.loop();
            }
            const float expected[] = {30, 32.4f, 80};
            assert(std::abs(automatic.get_last_read() - expected[variant]) < 0.001f);
            assert(!retry_auto(&automatic));
            assert(publisher.events.size() == 1);
            automatic.set_decoder_variant(DECODER_VARIANT_UNKNOWN);
          }

          StandingDeskHeightSensor late{};
          late.setup();
          for (int n = 0; n < 3; ++n) { now_ms += 1001; late.loop(); }
          assert(retry_auto(&late));
          assert(!retry_fixed(&late));
          feed(late, {0xF2,0xF2,0x01,0x03,0x01,0x2C,0x07,0x38,0x7E});
          assert(late.get_last_read() == -1); // Reproduce upstream's discarded bytes.
          assert(retry_auto(&late));
          late.start_decoder_detection(); // Existing action used by the retry.
          feed(late, {0xF2,0xF2,0x01,0x03,0x01,0x2C,0x07,0x38,0x7E});
          assert(late.get_last_read() == 30);
          assert(!retry_auto(&late)); // Never reset a working height decoder.
          feed(late, {0xF2,0xF2,0x01,0x03,0x01,0x36,0x07,0x42,0x7E});
          assert((late.published == std::vector<float>{30,31}));
          // Free upstream decoders (the production class has no destructor).
          for (auto *desk : {&jarvis, &uplift, &omnidesk, &late})
            desk->set_decoder_variant(DECODER_VARIANT_UNKNOWN);
        }
        """, upstream=upstream)


if __name__ == "__main__":
    unittest.main()
