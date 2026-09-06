"""State publication contract."""

from typing import Protocol

from deskvision.state.scene_state import SceneState


class StatePublisher(Protocol):
    def publish(self, state: SceneState) -> None: ...
