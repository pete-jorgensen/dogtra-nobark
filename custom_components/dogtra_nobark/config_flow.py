"""Config flow: the collar advertises as DOGTRA_<tail>, which is the only identifier.

Its advertisement carries no manufacturer data and only the generic 0x181A service UUID
(which many unrelated devices also advertise), so the local name prefix is the match --
in ``manifest.json`` and re-checked here.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS

from .const import DOMAIN, LOCAL_NAME_PREFIX


def _is_collar(info: BluetoothServiceInfoBleak) -> bool:
    return (info.name or "").startswith(LOCAL_NAME_PREFIX)


def _title(info: BluetoothServiceInfoBleak) -> str:
    return f"Dogtra collar ({info.name or info.address})"


class DogtraNobarkConfigFlow(ConfigFlow, domain=DOMAIN):
    """Discovery and manual setup."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered: BluetoothServiceInfoBleak | None = None
        self._discovered_devices: dict[str, BluetoothServiceInfoBleak] = {}

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """A collar turned up on its own."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        if not _is_collar(discovery_info):
            return self.async_abort(reason="not_supported")
        self._discovered = discovery_info
        self.context["title_placeholders"] = {"name": _title(discovery_info)}
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm adding a discovered collar."""
        assert self._discovered is not None
        if user_input is not None:
            return self.async_create_entry(
                title=_title(self._discovered),
                data={CONF_ADDRESS: self._discovered.address},
            )
        self._set_confirm_only()
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={"name": _title(self._discovered)},
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick from the collars Home Assistant can currently hear."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=_title(self._discovered_devices[address]),
                data={CONF_ADDRESS: address},
            )

        current = self._async_current_ids()
        self._discovered_devices = {
            info.address: info
            for info in async_discovered_service_info(self.hass, connectable=True)
            if info.address not in current and _is_collar(info)
        }
        if not self._discovered_devices:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(
                        {a: _title(i) for a, i in self._discovered_devices.items()}
                    )
                }
            ),
        )
