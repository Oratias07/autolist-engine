"""Channel interface. Adapters (email now, WhatsApp later) implement this protocol."""

from __future__ import annotations

from typing import Protocol

from autolist.core.models import ChannelMessage, HealthStatus, OutboundMessage


class Channel(Protocol):
    def fetch_new(self) -> list[ChannelMessage]: ...

    def send(self, msg: OutboundMessage) -> str: ...

    def health(self) -> HealthStatus: ...
