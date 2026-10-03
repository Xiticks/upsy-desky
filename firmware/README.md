# Firmware Configs

This directory contains [ESPHome](https://esphome.io) config files for the Upsy Desky.

It is organized like so:

- `base.yaml`: The base essential configuration, which contains components for reading the desk height, preset buttons, etc.
- `stock.yaml`: Inherits everything from `base.yaml` and adds components which are useful on stock firmware, such as the WiFi hotspot, web server, and Improv Serial
- `debug.yaml`: Inherits everything from `stock.yaml` and adds components which are useful for debugging

## Addons

Major parts of the config are separated into "addons" so they can be easily included or excluded. The following addons are available:

- `presets.yaml`: Adds support for recalling and setting presets on the desk control box
- `runtime-config.yaml`: Adds support for runtime configuration options (you might want to remove this if you are configuring everything via ESPHome yaml)
- `bluetooth-proxy.yaml`: Contains the necessary configuration to use the Upsy Desky as a [Bluetooth Proxy](https://esphome.io/components/bluetooth_proxy.html)
- `stable-ids.yaml`: Contains configuration necessary to keep some entity IDs stable via the HTTP API
- `button-record.yaml`: Full Jarvis/Uplift GPIO scanner; included by `base.yaml` by default
- `button-record-simple.yaml`: Alternative scanner, selectable instead of the full scanner
- `keypad-events.yaml`: Keypad entities and reporting without GPIO or protocol handling; included by the adapter

## Physical keypad detection

`Keypad Button` emits `up`, `down`, `preset_1` through `preset_4`, `memory`, or
`unknown`. Use this native event entity for automations: repeated presses emit
separate events. `Last Keypad Button`, `Last Keypad Source`, and the disabled
`Last Keypad Raw Mask` diagnostic describe the latest action. Sources are
`physical_keypad`, `virtual_control`, and `mixed_input`.

The scanner samples the four active-low keypad wires every 5ms and debounces
complete masks for 30ms. Shared outputs use input-enabled, open-drain mode so
idle releases the wires for the handset. Output latches distinguish firmware
commands from physical presses. A held physical button emits once; a debounced
full release re-arms detection. Partial preset contact releases do not emit
Up/Down events. Firmware Memory followed by a preset emits both commands.

Both scanner addons define their own input observation settings, default button
masks, and debounce variables. Open-drain outputs are configured in `base.yaml`
and `presets.yaml`, independently of scanning. They use the same four-line
encoding and Memory/store mappings for the Jarvis/Uplift keypad configuration,
independently of the selected height decoder. Existing pin and mask
substitutions and Home Assistant entity IDs remain unchanged.

The upstream desk configurations provide the same four preset-control lines for
Fully Jarvis and Uplift v2. Omnidesk has a height decoder but no keypad
configuration. The other desks listed as compatible with Upsy Desky have no
separate button mapping or electrical description in this repository. Height
decoder support does not establish physical keypad support:

| Height variant | Height parsing | Keypad encoding |
| --- | --- | --- |
| `jarvis` | Checksummed `F2 F2` height frames | Defaults match existing Upsy/Jarvis preset wiring; physically tested on Jarvis |
| `uplift` | `01 01` plus two height bytes | Defaults match existing Upsy/Uplift preset wiring; not physically tested |
| `omnidesk` | Uplift framing with extended high height bytes | No verified keypad configuration; physical detection unsupported without electrical/mapping verification |
| `auto` | Upstream decoder selection | Preserves the stock Jarvis/Uplift keypad adapter; does not identify keypad wiring |

For an unverified keypad in a stock/base configuration, disable this GPIO adapter:

```yaml
substitutions:
  upsy_keypad_gpio_enabled: "false"
```

This stops keypad GPIO reads and disables input observation on the shared
outputs. Movement and preset outputs remain open-drain, releasing inactive
lines to the stock board's pull-ups even when the scanner addon is removed.
Custom hardware must provide suitable pull-ups. This option does not establish
that those controls support a new desk.
No physical or virtual keypad events are inferred while the adapter is disabled.

Masks use `button_bit1_pin`, `button_bit2_pin`, `button_bit4_pin`, and
`button_m_pin` in that order. Defaults match the existing preset outputs:
Up=`0x01`, Down=`0x02`, Preset 1=`0x03`, Preset 2=`0x04`, Preset 3=`0x06`,
Preset 4=`0x05`, Memory=`0x08`. Override `upsy_keypad_up_mask`,
`upsy_keypad_down_mask`, `upsy_keypad_preset_1_mask` through
`upsy_keypad_preset_4_mask`, and `upsy_keypad_memory_mask` for other encodings.
Use distinct nonzero masks and verify the raw-mask diagnostic. These settings
change observation labels; custom preset output actions must still match the
handset. Unknown stable patterns emit `unknown`.

Default physical Memorty detection can be overridden with:

```yaml
substitutions:
  upsy_keypad_physical_memory_mask: "0x0A"
```

This changes only physical Memory recognition. Keep `upsy_keypad_memory_mask`
at `0x08` for the existing firmware store commands, which drive Memory alone.
The physical setting defaults to `upsy_keypad_memory_mask` for backward
compatibility. The raw diagnostic remains the actual observed mask, and mixed
sources still emit `unknown` even when their combined mask equals Memory.

Physical and firmware activity on different wires emits `unknown`/`mixed_input`
because combined masks can resemble a preset. Physical activity on a wire
already driven low by firmware is indistinguishable. Presses shorter than the
debounce period are filtered. UART-only handsets require protocol-specific
captures and are not supported by this GPIO scanner.

## Optional simplified scanner comparison

`button-record-simple.yaml` is an opt-in alternative for the same Jarvis/Uplift
wiring. Select it directly in the existing `packages:` block of `base.yaml`:

```yaml
  addon_button_record: !include addons/button-record-simple.yaml
```

Each version contains its settings, input observation modes, and debounce
variables, and includes the `keypad-events.yaml` publisher. The simple version
does not include the full scanner. Select exactly one version;
remove any separate `keypad_simple` package entry from the device configuration.
The simple scanner has no release/re-arm state. It keeps
whole-mask/source debounce, output-latch source detection, mixed-input rejection,
and input observation of open-drain outputs. It omits the normal scanner's
compile-time UART/wake pin and physical Memory-mask assertions, assuming the
verified pinout and mappings. Incorrect overrides are no longer rejected by
those assertions.

The simpler scanner emits each stable nonzero mask/source change. A held button
still emits once, but another stable pattern can emit without a full release.
This also handles virtual Memory followed by a preset without a special case.

| Input sequence | Normal scanner | Simplified scanner |
| --- | --- | --- |
| Press, hold, release, press again | One event per press | Same |
| Firmware Memory then preset | Memory and preset | Same |
| Stable physical Memory `10` then `3`, without release | Memory only | Memory then Preset 1 |
| Stable physical `3` then `1`, without release | Preset 1 only | Preset 1 then Up |
| Stable physical Memory `10` then `2`, without release | Memory only | Memory then Down |
| Stable physical `3` then `4`, without release | Preset 1 only | Preset 1 then Preset 2 |
| Stable mixed firmware/physical input | `unknown` / `mixed_input` | Same |

The intermediate-mask cases are synthetic comparisons, not observed contact
transitions from a Jarvis capture. A mask of `3` does not establish that a handset
actually produces `1` or `2` during a press/release. Hardware testing should check
the emitted events during press, hold, and release, especially for presets and
Memory. Patterns lasting less than the debounce interval remain filtered.

For a Home Assistant ESPHome test, copy this checkout's `firmware` directory to
`/config/esphome/upsy-desky-test`. Choose the
simple include in that directory's `base.yaml` as shown above. In the existing
device configuration, use the local firmware package, keeping the
other device settings (name, WiFi, API encryption, OTA, and height units):

```yaml
packages:
  tj_horner.upsy_desky: !include upsy-desky-test/stock.yaml

substitutions:
  # Keep the existing name/friendly_name substitutions too.
  upsy_keypad_physical_memory_mask: "0x0A"
```

The example uses the physical Memory mask observed on the user's Jarvis; other
handsets should retain their verified setting. Build and install through ESPHome
as usual. To return to the full scanner, change the base include back to
`addons/button-record.yaml` and rebuild. The repository's default base include
continues to select the full scanner.

## Reusing keypad reporting

`keypad-events.yaml` contains the native event, last-button/source entities,
disabled raw-mask diagnostic, and shared publisher. It configures no GPIOs and
reads no UART. Backends report a button, raw mask (or zero when unused), and
source: `physical_keypad`, `virtual_control`, or `mixed_input`.

ESPHome template event types must be declared before compilation. The adapter
supplies the list using `!extend`; the common publisher has no assumed buttons
or presets. A different backend can use the publisher with its own static list:

```yaml
packages:
  keypad_events: !include addons/keypad-events.yaml
event:
  - id: !extend upsy_keypad_button_event
    event_types: [up, down, unknown]
```

This declares reporting only. Physical detection for another keypad needs verified
pinout, voltage/polarity and idle behavior, safe output modes, button encodings,
press/release and store sequences, or labeled UART captures for serial handsets.

## Height reporting

UART bytes remain exclusively handled by the upstream height sensor and its
selected decoder. `get_last_read()` supplies target-height control; the sensor
publishes changed heights every 500ms. Keypad detection does not read UART,
change decoder state, or publish height.

If automatic decoder selection fails before the desk reports a height,
`base.yaml` retries the existing detection action and wake byte every five
seconds while `auto` has no valid height. Explicit variants and working decoders
are unchanged. The boot wake and scanner startup retain their 250ms delays.

## Home Assistant events

Native event entities do not require Home Assistant action permission. Native
API sensor updates normally have a 100ms batching delay; keypad events are sent
immediately. Home Assistant can therefore receive a keypad event before the
source sensor updates. Read `event_type` for the button and do not assume the
source sensor belongs to that event. `api.batch_delay: 0ms` removes normal
batching delay, but separate entity updates are not an atomic payload.

The base package remains usable without `api`, including MQTT configurations.

## Regression checks

Run `python3 -m unittest discover -s tests -v` from the repository root with
Python and `g++`. The tests compile the actual YAML scanner and cover button
masks, held/repeated presses, bounce, partial release, firmware/mixed sources,
alternative masks, clock wraparound, and local event publication.
They also check that a disabled scanner reads no GPIOs and that the normal
scanner rejects keypad input/output pins overlapping height RX and wake TX. The
configuration checks validate the publisher with a different event list and no GPIO definitions,
and validate stock firmware with GPIO detection disabled.

`test_keypad_simple.py` runs the same regressions against the actual alternative
lambda, with explicit expectations for its additional partial-release events.
It checks that omitted configuration assertions no longer reject pin overlap or
invalid physical Memory masks; incorrect mappings can produce incorrect labels.
It also compares direct physical mask changes and virtual-to-physical source
changes against the normal scanner. `tests/keypad-simple.yaml` is the device
fixture for the configuration comparison. With ESPHome installed,
`SimpleKeypadConfigTests` selects each addon directly in a temporary copy of
`base.yaml`. It checks that either selection provides all shared dependencies
and only one scanner, while preserving outputs, UART, height/movement controls,
reporting entities, boot actions, and the height retry interval. CI runs this
check after installing ESPHome.

Set `STANDING_DESK_SOURCE` to a checkout of `tjhorner/esphome-standing-desk` to
also compile its actual height component and decoders against simulated UART,
GPIO, time, and publication. Tests exercise all three height protocols,
automatic selection, interleaved physical presses, Jarvis preset-related reports,
and failed startup detection followed by recovery. Jarvis packets follow the
decoder protocol, Uplift packets come from the
[reverse-engineering guide](https://upsy-desky.tjhorner.dev/docs/advanced/reverse-engineering/),
and Omnidesk packets are synthesized from its decoder. Only Jarvis has been
physically tested; these tests do not validate other handsets' electronics.
