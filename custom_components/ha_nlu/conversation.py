"""Conversation agent for the HA NLU integration.

Routes user text through the local intent service (/plan), then either
executes Home Assistant services or reads entity states and replies in Chinese.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import logging
from typing import Any, Literal

import aiohttp

from homeassistant.components import conversation
from homeassistant.components.conversation.chat_log import AssistantContent
from homeassistant.components.conversation.models import (
    ConversationInput,
    ConversationResult,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_URL, DEFAULT_URL

_LOGGER = logging.getLogger(__name__)

MATCH_ALL: Literal["*"] = "*"


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the conversation entity from a config entry."""
    async_add_entities([HaNluConversationEntity(config_entry)])

CONFIRM_TEXT = {
    "climate_control": "好的，空调设置已完成。",
    "media_control": "好的。",
    "light_control": "好的，灯已打开。",
    "switch_control": "好的，已切换开关。",
    "vacuum_control": "好的，扫地机器人已开始工作。",
}


class HaNluConversationEntity(
    conversation.ConversationEntity, conversation.AbstractConversationAgent
):
    """A conversation entity that routes to the local intent service."""

    _attr_has_entity_name = True
    _attr_supported_features = conversation.ConversationEntityFeature.CONTROL

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize the conversation entity."""
        super().__init__()
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}-nlu"
        self._attr_name = "HA 意图服务"

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        """Return a list of supported languages."""
        return MATCH_ALL

    @property
    def _base_url(self) -> str:
        return (self.entry.data.get(CONF_URL) or DEFAULT_URL).rstrip("/")

    async def async_added_to_hass(self) -> None:
        """Register the agent when added."""
        await super().async_added_to_hass()
        conversation.async_set_agent(self.hass, self.entry, self)

    async def async_will_remove_from_hass(self) -> None:
        """Unregister the agent when removed."""
        conversation.async_unset_agent(self.hass, self.entry)
        await super().async_will_remove_from_hass()

    async def _async_call_plan(self, text: str) -> dict | None:
        """Call the local intent service /plan endpoint."""
        try:
            async with (
                aiohttp.ClientSession() as session,
                session.post(
                    f"{self._base_url}/plan",
                    json={"text": text},
                    timeout=aiohttp.ClientTimeout(total=8),
                ) as resp,
            ):
                if resp.status != 200:
                    _LOGGER.warning("intent service returned %s", resp.status)
                    return None
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            _LOGGER.warning("intent service unavailable: %s", err)
            return None

    # ---------------- speech assembly ----------------

    def _state(self, entity_id: str) -> str | None:
        state = self.hass.states.get(entity_id)
        return state.state if state else None

    def _fmt(self, value: str | None, suffix: str = "") -> str:
        """Format a sensor value, rounding floats and stripping trailing zeros."""
        if value is None:
            return "未知"
        try:
            f = float(value)
            txt = f"{f:.1f}"
            if txt.endswith(".0"):
                txt = txt[:-2]
            return f"{txt}{suffix}"
        except (TypeError, ValueError):
            return f"{value}{suffix}"

    async def _async_env_speech(self, plan: Mapping[str, Any], p: Mapping[str, Any]) -> str:
        metric = p.get("metric", "temperature")
        entities: list[str] = list(p.get("entities") or [])
        room = (plan.get("slots") or {}).get("ROOM", "")
        prefix = f"{room}" if room else "家里"

        def val(idx: int) -> str | None:
            if idx < len(entities):
                return self._state(entities[idx])
            return None

        if metric == "humidity":
            return f"{prefix}湿度是 {self._fmt(val(1) or val(0), '%')}"
        if metric == "both":
            return f"{prefix}温度是 {self._fmt(val(0), '度')}，湿度是 {self._fmt(val(1), '%')}"
        if metric == "illuminance":
            return f"{prefix}光照度是 {self._fmt(val(0))} 勒克斯"
        return f"{prefix}当前温度是 {self._fmt(val(0), '度')}"

    def _async_occ_speech(self, p: Mapping[str, Any]) -> str:
        entities: list[str] = list(p.get("entities") or [])
        count_e, anyone_e, person_e = None, None, None
        if len(entities) > 0:
            count_e = entities[0]
        if len(entities) > 1:
            anyone_e = entities[1]
        if len(entities) > 2:
            person_e = entities[2]

        count = self._state(count_e) if count_e else None
        if count is not None and count.replace(".", "").replace("-", "").isdigit():
            return f"现在家里有 {count} 人"

        anyone = self._state(anyone_e) if anyone_e else None
        if anyone in ("on", "home"):
            return "现在家里有人。"
        if anyone in ("off", "not_home"):
            return "现在家里没有人。"

        person = self._state(person_e) if person_e else None
        if person == "home":
            return "现在家里有人。"
        if person == "not_home":
            return "现在家里没有人。"
        return "暂时查询不到家里的人员信息。"

    async def _async_execute(self, p: Mapping[str, Any], context: Context) -> bool:
        """Execute actions from the plan."""
        entity_id = p.get("entity")
        actions: list[Mapping[str, Any]] = p.get("actions") or []
        hass = self.hass

        for action in actions:
            a_type = action.get("type")
            if a_type == "service":
                service = action.get("service", "")
                domain, svc = service.split(".", 1)
                await hass.services.async_call(
                    domain,
                    svc,
                    action.get("data"),
                    target={"entity_id": entity_id} if entity_id else None,
                    context=context,
                    blocking=False,
                )
            elif a_type == "state":
                await self._async_execute_state(action, entity_id, context)
            elif a_type == "button":
                ref = action.get("entity_ref") or ""
                if ref and self.hass.states.get(ref):
                    await hass.services.async_call(
                        "button",
                        "press",
                        {},
                        target={"entity_id": ref},
                        context=context,
                        blocking=False,
                    )
                else:
                    _LOGGER.warning("button entity not available: %s", ref)
            elif a_type == "notify":
                ref = action.get("entity_ref") or ""
                if ref.startswith("notify."):
                    await hass.services.async_call(
                        "notify",
                        ref[len("notify."):],
                        {"message": action.get("text", "")},
                        context=context,
                        blocking=False,
                    )
                else:
                    _LOGGER.warning("notify action without a notify entity ref: %s", action)
        return True

    async def _async_execute_state(
        self, action: Mapping[str, Any], entity_id: str | None, context: Context
    ) -> None:
        """Execute a state-relative action (needs a manual read)."""
        hass = self.hass
        op = action.get("op")

        if op == "set_temp_by_delta":
            state = hass.states.get(entity_id) if entity_id else None
            current = state.attributes.get("current_temperature") if state else None
            if current is None:
                return
            await hass.services.async_call(
                "climate",
                "set_temperature",
                {"temperature": float(current) + float(action["delta"])},
                target={"entity_id": entity_id},
                context=context,
                blocking=False,
            )
        elif op == "volume_relative":
            state = hass.states.get(entity_id) if entity_id else None
            current = state.attributes.get("volume_level") if state else None
            if current is not None:
                level = max(
                    0.0, min(1.0, float(current) + float(action["delta_pct"]) / 100.0)
                )
                await hass.services.async_call(
                    "media_player",
                    "volume_set",
                    {"volume_level": level},
                    target={"entity_id": entity_id},
                    context=context,
                    blocking=False,
                )
        elif op == "volume_set":
            await hass.services.async_call(
                "media_player",
                "volume_set",
                {"volume_level": float(action["volume_level"])},
                target={"entity_id": entity_id},
                context=context,
                blocking=False,
            )

    async def _async_plan_to_speech(
        self, plan: Mapping[str, Any], context: Context
    ) -> str:
        """Turn a /plan response into a Chinese reply, executing when needed."""
        p = plan.get("plan") or {}
        status = p.get("status")
        intent_name = p.get("intent")

        if status == "unknown" or plan.get("confidence", 0) < 0.5:
            return p.get("reply") or "抱歉，这句我没有听懂。"
        if status == "ask":
            return p.get("question") or "请问你指的是哪个房间？"
        if status in ("no_data", "no_entity"):
            return p.get("reply") or "没有找到相关数据。"
        if status == "query":
            if intent_name == "env_query":
                return await self._async_env_speech(plan, p)
            if intent_name == "occupancy_query":
                return self._async_occ_speech(p)
            return "查询成功。"
        if status == "execute":
            await self._async_execute(p, context)
            return CONFIRM_TEXT.get(intent_name, "好的。")
        return "抱歉，这句我没有听懂。"

    # ---------------- entity protocol ----------------

    async def _async_handle_message(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
    ) -> conversation.ConversationResult:
        """Handle a single conversation turn."""
        text = (user_input.text or "").strip()
        plan = await self._async_call_plan(text)
        if plan is None:
            speech = "抱歉，语音识别服务暂时不可用，请稍后再试。"
            continue_conversation = False
        else:
            speech = await self._async_plan_to_speech(plan, user_input.context)
            continue_conversation = (plan.get("plan") or {}).get("status") == "ask"

        chat_log.async_add_assistant_content_without_tools(
            AssistantContent(agent_id=user_input.agent_id, content=speech)
        )
        response = intent.IntentResponse(language=user_input.language)
        response.async_set_speech(speech)
        return ConversationResult(
            conversation_id=None,
            response=response,
            continue_conversation=continue_conversation,
        )