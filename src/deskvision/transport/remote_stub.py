"""Remote SceneState transport is outside V0.1."""


class RemoteStatePublisher:
    def publish(self, state: object) -> None:
        del state
        raise NotImplementedError("Remote transport is reserved for V0.6")
