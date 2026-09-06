# Production Perception and Mapping

This package contains production perception contracts and selected runtime
implementations. It must never import from `demo/` or `tests/`.

## MediaPipe hand tracker

`MediaPipeHandTracker` consumes an in-process BGR `FramePacket` and returns a
frame-correlated `HandTrackingResult` containing up to two hands, handedness,
21 normalized image landmarks, optional 21 world landmarks, and model-only
latency timestamps.

The tracker uses MediaPipe Tasks VIDEO mode with monotonically increasing frame
timestamps. This preserves MediaPipe's temporal tracking path while keeping the
project's latest-frame-only policy: no input frame queue is added here.

Initialization requires `metrics_acknowledged=True`. According to the current
MediaPipe privacy notice, input images remain on device, while Tasks sends
performance/utilization metrics. The explicit guard prevents a production
caller from silently starting that behavior.

The selected runtime is pinned to `mediapipe==0.10.35` and uses the CPU delegate
because that is the version/backend validated on the current Mac. The model
asset is packaged under `perception/assets/`.

Official references:

- Hand Landmarker API and VIDEO timestamp behavior:
  https://ai.google.dev/edge/api/mediapipe/python/mp/tasks/vision/HandLandmarker
- Options and tracking thresholds:
  https://ai.google.dev/edge/api/mediapipe/python/mp/tasks/vision/HandLandmarkerOptions
- MediaPipe privacy notice:
  https://github.com/google-ai-edge/mediapipe#privacy-notice

```python
from deskvision.perception.mediapipe_hands import MediaPipeHandTracker

tracker = MediaPipeHandTracker(metrics_acknowledged=True)
try:
    result = tracker.track(frame_packet)
    scene_hands = result.scene_hands()
finally:
    tracker.close()
```

The isolated model lab imports this production class through a thin `infer()`
adapter. Therefore the MediaPipe result shown on port 8768 is the production
tracking implementation, not a divergent Demo copy.

## Physical keyboard mapping

`ArucoKeyboardLocator` detects `DICT_4X4_50` anchors on the same raw
`FramePacket` consumed by MediaPipe. It estimates the measured keyboard
reference plane, smooths small motion, gates implausible jumps, and permits only
a bounded occlusion coast. An expired or low-confidence pose is unusable.

`ContactKeyMapper` projects each eligible fingertip into that reference plane
and compares it with the user's five-sample-per-key Contact Map. It returns
ranked likely-contact candidates keyed by stable `physical_key_id` values.

`ProductionMappingPipeline` runs hand tracking, keyboard pose, and key mapping
in one process over one frame object. A stage result is committed only after its
source ID, frame ID, and capture timestamp match. Any mismatch or unusable pose
fails closed; data from an older frame cannot become a current highlight.

`LatestFramePerceptionWorker` consumes only the newest captured frame. There is
no perception queue, and superseded frames are counted rather than processed.
