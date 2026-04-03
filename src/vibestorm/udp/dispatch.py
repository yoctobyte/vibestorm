"""Small dispatch helpers for decoded UDP messages."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from vibestorm.udp.template import MessageDispatch, MessageTemplateIndex, build_template_index, dispatch_message


@dataclass(slots=True)
class MessageDispatcher:
    index: MessageTemplateIndex

    @classmethod
    def from_repo_root(cls, root: Path) -> "MessageDispatcher":
        return cls(index=build_template_index(root / "third_party" / "secondlife" / "message_template.msg"))

    def dispatch(self, payload: bytes) -> MessageDispatch:
        return dispatch_message(payload, self.index)
