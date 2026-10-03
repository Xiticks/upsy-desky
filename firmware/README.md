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
- `button-record-simple.yaml`: Observes the stock Jarvis/Uplift keypad GPIOs and decodes button presses
- `keypad-events.yaml`: Provides the native keypad event and latest-action entities; included by the scanner

## Keypad events

`base.yaml` includes the Jarvis/Uplift GPIO scanner. `Keypad Button` is a native
ESPHome event entity with types `up`, `down`, `preset_1` through `preset_4`,
`memory`, and `unknown`. Use it for Home Assistant automations so repeated
presses of the same button still trigger. ESPHome event entities require
Home Assistant Core 2024.5 or newer and do not require HA action permission.

The scanner samples the four active-low handset lines every 5ms and debounces
the complete mask and its source for 30ms. Each stable nonzero mask/source
change emits once; a held, unchanged button does not repeat. Memory followed by
a preset can emit both without a full release. Stable partial-contact changes
can also emit another button, and presses shorter than debounce are filtered.

Physical Memory defaults to `0x0A` (Memory plus bit 2). For a handset using
Memory alone, override it in the consuming ESPHome configuration:

```yaml
substitutions:
  upsy_keypad_physical_memory_mask: "0x08"
```

This changes only physical recognition. Firmware store commands continue to
use `0x08`. `Last Keypad Button`, `Last Keypad Source`, and the disabled-by-default
`Last Keypad Raw Mask` diagnostic describe the latest action. Sources are
`physical_keypad`, `virtual_control`, or `mixed_input`; observable mixed input
always emits `unknown`. Physical activity on a wire already pulled low by
firmware cannot be distinguished. Source and mask are separate entity updates,
not an atomic event payload; do not use the last-source sensor to reliably
filter individual events by source.

Scanning adds input observation to the existing open-drain outputs. It does
not read UART or change height parsing. Height-decoder support alone does not
establish compatibility with other handset wiring. For an unverified keypad,
disable observation and inferred events without changing output modes:

```yaml
substitutions:
  upsy_keypad_gpio_enabled: "false"
```
