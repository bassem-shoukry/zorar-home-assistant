# Zorar for Home Assistant

Control your [Zorar](https://zorarapp.com) smart power strip from Home Assistant: one switch per
AC outlet, grouped by strip. Home Assistant talks to the Zorar cloud, the same way the Zorar app
does, and checks for changes every 30 seconds.

## Install

[![Open your Home Assistant instance and add this repository to HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=bassem-shoukry&repository=zorar-home-assistant&category=integration)

1. Click the button above, then **Download** in HACS.
2. Restart Home Assistant.

No HACS? Copy `custom_components/zorar` into your Home Assistant `config/custom_components/`
folder and restart.

## Set up

[![Open your Home Assistant instance and start setting up Zorar.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=zorar)

1. Click the button above (or **Settings → Devices & services → Add integration → Zorar**).
2. Choose **Pair with the Zorar app**. Home Assistant shows a code.
3. In the Zorar app open **Account → Home Assistant**, enter the code and tap **Connect**.

Home Assistant finishes on its own. Each strip appears as a device with four outlet switches.

## Good to know

- Only the four AC outlets are switches. The USB ports are always on.
- There is no "all outlets" switch, and no power or energy sensors yet (the strip's units for
  those readings are not verified).
- A strip that is offline shows its outlets as unavailable.
- To disconnect, open **Account → Home Assistant** in the Zorar app and tap **Revoke**.

## Support

Open an [issue](https://github.com/bassem-shoukry/zorar-home-assistant/issues).
