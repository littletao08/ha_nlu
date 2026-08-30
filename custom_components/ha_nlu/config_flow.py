"""Config flow for the HA NLU integration."""

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant

from .const import DEFAULT_URL, DOMAIN


async def _validate_url(hass: HomeAssistant, url: str) -> None:
    """Quick sanity check that the intent service is reachable."""
    import aiohttp

    try:
        async with (
            aiohttp.ClientSession() as session,
            session.get(
                f"{url.rstrip('/')}/health", timeout=aiohttp.ClientTimeout(total=5)
            ) as resp,
        ):
            resp.raise_for_status()
    except (aiohttp.ClientError, Exception) as err:
        raise vol.Invalid(f"目标服务不可用：{err}") from err


class HaNluConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Handle a flow initiated by the user."""
        errors: dict[str, str] = {}

        if user_input is not None:
            url = user_input[CONF_URL].strip().rstrip("/")
            if not url.startswith(("http://", "https://")):
                errors[CONF_URL] = "请输入 http(s):// 开头的地址"
            else:
                try:
                    await _validate_url(self.hass, url)
                except vol.Invalid as err:
                    errors[CONF_URL] = str(err)

            if not errors:
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="HA 意图服务", data={CONF_URL: url}
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_URL, default=DEFAULT_URL): str}),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Allow reconfiguration of the endpoint."""
        errors: dict[str, str] = {}

        if user_input is not None:
            url = user_input[CONF_URL].strip().rstrip("/")
            if not url.startswith(("http://", "https://")):
                errors[CONF_URL] = "请输入 http(s):// 开头的地址"
            else:
                try:
                    await _validate_url(self.hass, url)
                except vol.Invalid as err:
                    errors[CONF_URL] = str(err)

            if not errors:
                return self.async_update_reentry_and_finish(data=user_input)

        current = (
            self.reauth_entry.data if self.reauth_entry else self._get_current_entry.data
        )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {vol.Required(CONF_URL, default=current.get(CONF_URL, DEFAULT_URL)): str}
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Handle reauth."""
        return await self.async_step_reconfigure(user_input)