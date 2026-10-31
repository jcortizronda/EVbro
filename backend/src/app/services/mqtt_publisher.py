"""The server-wide MQTT client from the environment (`MQTT_*` in `.env`), for the person who runs the server.

It is one `MqttLinkClient` (services/mqtt_hub.py) built from the settings: topics, Home Assistant discovery and
commands back. Managers connect their own brokers from Ajustes → Cuenta (`MqttHub`); this one covers the cars chosen by
`MQTT_PUBLISH_SCOPE` (the oldest manager's by default).
"""
from __future__ import annotations

from app.config import settings
from app.services.mqtt_hub import MqttLinkClient, MqttLinkConfig


class _EnvPublisher(MqttLinkClient):
    def __init__(self) -> None:
        super().__init__(MqttLinkConfig.from_env())

    async def start(self) -> None:                            # the environment decides whether it runs at all
        if not settings.MQTT_ENABLED:
            return
        self.config = MqttLinkConfig.from_env()
        await super().start()


MqttPublisher = _EnvPublisher()
