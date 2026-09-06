// Windows-only SteamVR smoke driver. It deliberately contains no transport,
// JSON, camera, or production state-adapter code.

#include <openvr_driver.h>

#include <cmath>
#include <cstdint>
#include <cstring>
#include <memory>

namespace {

constexpr char kDriverName[] = "deskvisionkeyboard";
constexpr char kSerialNumber[] = "DESKVISION-KEYBOARD-DEMO-001";
constexpr char kModelNumber[] = "DeskVision Keyboard Smoke Model";
constexpr char kRenderModelName[] = "{deskvisionkeyboard}deskvision_keyboard";

// A deterministic visibility pose in HMD-local OpenVR coordinates. OpenVR is
// right-handed: +x right, +y up, and -z forward, with distances in metres.
constexpr double kOffsetRightMetres = 0.0;
constexpr double kOffsetUpMetres = -0.32;
constexpr double kOffsetForwardMetres = -0.62;
constexpr double kKeyboardTiltRadians = -1.1344640137963142;  // -65 degrees.

void Log(const char* message) {
  if (vr::VRDriverLog() != nullptr) {
    vr::VRDriverLog()->Log(message);
  }
}

vr::HmdQuaternion_t QuaternionFromMatrix(const vr::HmdMatrix34_t& matrix) {
  vr::HmdQuaternion_t quaternion{};
  quaternion.w =
      std::sqrt(std::fmax(0.0, 1.0 + matrix.m[0][0] + matrix.m[1][1] +
                                   matrix.m[2][2])) /
      2.0;
  quaternion.x =
      std::copysign(std::sqrt(std::fmax(
                        0.0, 1.0 + matrix.m[0][0] - matrix.m[1][1] -
                                 matrix.m[2][2])) /
                        2.0,
                    matrix.m[2][1] - matrix.m[1][2]);
  quaternion.y =
      std::copysign(std::sqrt(std::fmax(
                        0.0, 1.0 - matrix.m[0][0] + matrix.m[1][1] -
                                 matrix.m[2][2])) /
                        2.0,
                    matrix.m[0][2] - matrix.m[2][0]);
  quaternion.z =
      std::copysign(std::sqrt(std::fmax(
                        0.0, 1.0 - matrix.m[0][0] - matrix.m[1][1] +
                                 matrix.m[2][2])) /
                        2.0,
                    matrix.m[1][0] - matrix.m[0][1]);
  return quaternion;
}

vr::HmdQuaternion_t Multiply(const vr::HmdQuaternion_t& lhs,
                             const vr::HmdQuaternion_t& rhs) {
  return {
      lhs.w * rhs.w - lhs.x * rhs.x - lhs.y * rhs.y - lhs.z * rhs.z,
      lhs.w * rhs.x + lhs.x * rhs.w + lhs.y * rhs.z - lhs.z * rhs.y,
      lhs.w * rhs.y - lhs.x * rhs.z + lhs.y * rhs.w + lhs.z * rhs.x,
      lhs.w * rhs.z + lhs.x * rhs.y - lhs.y * rhs.x + lhs.z * rhs.w,
  };
}

class KeyboardDevice final : public vr::ITrackedDeviceServerDriver {
 public:
  vr::EVRInitError Activate(std::uint32_t object_id) override {
    object_id_ = object_id;
    const vr::PropertyContainerHandle_t properties =
        vr::VRProperties()->TrackedDeviceToPropertyContainer(object_id_);

    vr::VRProperties()->SetStringProperty(properties,
                                          vr::Prop_TrackingSystemName_String,
                                          kDriverName);
    vr::VRProperties()->SetStringProperty(
        properties, vr::Prop_ModelNumber_String, kModelNumber);
    vr::VRProperties()->SetStringProperty(
        properties, vr::Prop_SerialNumber_String, kSerialNumber);
    vr::VRProperties()->SetStringProperty(
        properties, vr::Prop_ManufacturerName_String, "DeskVision");
    vr::VRProperties()->SetStringProperty(
        properties, vr::Prop_RenderModelName_String, kRenderModelName);
    vr::VRProperties()->SetStringProperty(
        properties, vr::Prop_ResourceRoot_String, kDriverName);
    vr::VRProperties()->SetStringProperty(
        properties, vr::Prop_RegisteredDeviceType_String,
        "deskvisionkeyboard/DESKVISION-KEYBOARD-DEMO-001");
    vr::VRProperties()->SetBoolProperty(
        properties, vr::Prop_DeviceIsWireless_Bool, false);
    vr::VRProperties()->SetBoolProperty(
        properties, vr::Prop_DeviceProvidesBatteryStatus_Bool, false);

    Log("DeskVision keyboard tracker activated.");
    return vr::VRInitError_None;
  }

  void Deactivate() override {
    object_id_ = vr::k_unTrackedDeviceIndexInvalid;
  }

  void EnterStandby() override {}

  void* GetComponent(const char*) override { return nullptr; }

  void DebugRequest(const char*, char* response,
                    std::uint32_t response_capacity) override {
    if (response != nullptr && response_capacity > 0) {
      response[0] = '\0';
    }
  }

  vr::DriverPose_t GetPose() override {
    vr::DriverPose_t pose{};
    pose.deviceIsConnected = true;
    pose.poseIsValid = false;
    pose.result = vr::TrackingResult_Uninitialized;
    pose.qWorldFromDriverRotation.w = 1.0;
    pose.qDriverFromHeadRotation.w = 1.0;
    pose.qRotation.w = 1.0;

    vr::TrackedDevicePose_t hmd_pose{};
    vr::VRServerDriverHost()->GetRawTrackedDevicePoses(
        0.0F, &hmd_pose, vr::k_unTrackedDeviceIndex_Hmd + 1);
    if (!hmd_pose.bDeviceIsConnected || !hmd_pose.bPoseIsValid) {
      return pose;
    }

    const vr::HmdMatrix34_t& hmd = hmd_pose.mDeviceToAbsoluteTracking;
    const double local_offset[3] = {kOffsetRightMetres, kOffsetUpMetres,
                                    kOffsetForwardMetres};
    for (int row = 0; row < 3; ++row) {
      pose.vecPosition[row] =
          hmd.m[row][3] + hmd.m[row][0] * local_offset[0] +
          hmd.m[row][1] * local_offset[1] +
          hmd.m[row][2] * local_offset[2];
    }

    const double half_tilt = kKeyboardTiltRadians / 2.0;
    const vr::HmdQuaternion_t local_tilt = {
        std::cos(half_tilt), std::sin(half_tilt), 0.0, 0.0};
    pose.qRotation = Multiply(QuaternionFromMatrix(hmd), local_tilt);
    pose.poseIsValid = true;
    pose.result = vr::TrackingResult_Running_OK;
    return pose;
  }

  void PublishPose() {
    if (object_id_ == vr::k_unTrackedDeviceIndexInvalid) {
      return;
    }
    vr::VRServerDriverHost()->TrackedDevicePoseUpdated(
        object_id_, GetPose(), sizeof(vr::DriverPose_t));
  }

 private:
  vr::TrackedDeviceIndex_t object_id_ = vr::k_unTrackedDeviceIndexInvalid;
};

class DriverProvider final : public vr::IServerTrackedDeviceProvider {
 public:
  vr::EVRInitError Init(vr::IVRDriverContext* context) override {
    VR_INIT_SERVER_DRIVER_CONTEXT(context);
    Log("DeskVision keyboard smoke driver initializing.");

    keyboard_ = std::make_unique<KeyboardDevice>();
    if (!vr::VRServerDriverHost()->TrackedDeviceAdded(
            kSerialNumber, vr::TrackedDeviceClass_GenericTracker,
            keyboard_.get())) {
      Log("DeskVision keyboard tracker registration failed.");
      keyboard_.reset();
      VR_CLEANUP_SERVER_DRIVER_CONTEXT();
      return vr::VRInitError_Driver_Failed;
    }
    return vr::VRInitError_None;
  }

  void Cleanup() override {
    keyboard_.reset();
    VR_CLEANUP_SERVER_DRIVER_CONTEXT();
  }

  const char* const* GetInterfaceVersions() override {
    return vr::k_InterfaceVersions;
  }

  void RunFrame() override {
    if (keyboard_ != nullptr) {
      keyboard_->PublishPose();
    }
  }

  bool ShouldBlockStandbyMode() override { return false; }
  void EnterStandby() override {}
  void LeaveStandby() override {}

 private:
  std::unique_ptr<KeyboardDevice> keyboard_;
};

DriverProvider g_driver_provider;

}  // namespace

#if defined(_WIN32)
#define DESKVISION_DRIVER_EXPORT extern "C" __declspec(dllexport)
#elif defined(__GNUC__) || defined(__APPLE__)
#define DESKVISION_DRIVER_EXPORT \
  extern "C" __attribute__((visibility("default")))
#else
#error Unsupported compiler for the OpenVR driver entry point.
#endif

DESKVISION_DRIVER_EXPORT void* HmdDriverFactory(const char* interface_name,
                                                int* return_code) {
  if (interface_name != nullptr &&
      std::strcmp(interface_name,
                  vr::IServerTrackedDeviceProvider_Version) == 0) {
    if (return_code != nullptr) {
      *return_code = vr::VRInitError_None;
    }
    return &g_driver_provider;
  }

  if (return_code != nullptr) {
    *return_code = vr::VRInitError_Init_InterfaceNotFound;
  }
  return nullptr;
}
