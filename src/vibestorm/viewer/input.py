"""Input → bus command translation for the viewer.

Pygame events are mapped to typed bus commands (movement, chat). HUD-owned
input (the chat box) is handled separately by ``hud.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from vibestorm.bus import Bus
from vibestorm.bus.commands import AddControlFlags, RemoveControlFlags
from vibestorm.udp.control_flags import AgentControlFlags

if TYPE_CHECKING:
    import pygame

    from vibestorm.viewer.camera import Camera


# Map pygame key constant → control-flag bit. Holding the key down sets the
# bit; releasing clears it. Defined lazily inside ``_key_to_flag`` so this
# module can be imported without pygame at unit-test time.
_KEY_FLAG_TABLE: dict[int, AgentControlFlags] | None = None


def _key_to_flag_table() -> dict[int, AgentControlFlags]:
    global _KEY_FLAG_TABLE
    if _KEY_FLAG_TABLE is None:
        import pygame

        _KEY_FLAG_TABLE = {
            pygame.K_w: AgentControlFlags.AT_POS,
            pygame.K_UP: AgentControlFlags.AT_POS,
            pygame.K_s: AgentControlFlags.AT_NEG,
            pygame.K_DOWN: AgentControlFlags.AT_NEG,
            pygame.K_a: AgentControlFlags.TURN_LEFT,
            pygame.K_LEFT: AgentControlFlags.TURN_LEFT,
            pygame.K_d: AgentControlFlags.TURN_RIGHT,
            pygame.K_RIGHT: AgentControlFlags.TURN_RIGHT,
            pygame.K_q: AgentControlFlags.LEFT_POS,
            pygame.K_e: AgentControlFlags.LEFT_NEG,
            pygame.K_PAGEUP: AgentControlFlags.UP_POS,
            pygame.K_PAGEDOWN: AgentControlFlags.UP_NEG,
            pygame.K_f: AgentControlFlags.FLY,
        }
    return _KEY_FLAG_TABLE


@dataclass(slots=True)
class ViewerIntent:
    """Side effects an input event might want besides bus dispatch."""
    quit_requested: bool = False
    request_center_on_avatar: bool = False
    chat_input_focus: bool = False


def handle_event(event: pygame.event.Event, camera: Camera, bus: Bus) -> ViewerIntent:
    """Dispatch one pygame event. Returns a ViewerIntent for app-level decisions."""
    import pygame

    intent = ViewerIntent()

    if event.type == pygame.QUIT:
        intent.quit_requested = True
        return intent

    if event.type == pygame.KEYDOWN:
        if event.key == pygame.K_c:
            intent.request_center_on_avatar = True
            return intent
        if event.key == pygame.K_RETURN:
            intent.chat_input_focus = True
            return intent
        flag = _key_to_flag_table().get(event.key)
        if flag is not None:
            bus.dispatch(AddControlFlags(int(flag)))
        return intent

    if event.type == pygame.KEYUP:
        flag = _key_to_flag_table().get(event.key)
        if flag is not None:
            bus.dispatch(RemoveControlFlags(int(flag)))
        return intent

    if event.type == pygame.MOUSEWHEEL:
        # +1 wheel up → zoom in. Anchor on mouse position.
        try:
            mx, my = pygame.mouse.get_pos()
        except pygame.error:
            mx, my = camera.screen_size[0] // 2, camera.screen_size[1] // 2
        factor = 1.1 if event.y > 0 else (1.0 / 1.1)
        camera.zoom_at_screen(mx, my, factor)
        return intent

    if event.type == pygame.MOUSEMOTION:
        # Right-button drag → pan.
        if event.buttons[2]:
            dx, dy = event.rel
            camera.pan_screen(dx, dy)
        return intent

    return intent


__all__ = ["ViewerIntent", "handle_event"]
