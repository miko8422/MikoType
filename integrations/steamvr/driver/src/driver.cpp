// The vrserver driver performs no network, camera, inference, or file logging.
#include <openvr_driver.h>
#include <cmath>
#include <cstring>
#include <memory>
#include "pose_channel.h"

namespace {
constexpr char kSerial[] = "MIKOTYPE-KEYBOARD-001";
void Log(const char* message) { if (vr::VRDriverLog()) vr::VRDriverLog()->Log(message); }

class Keyboard final : public vr::ITrackedDeviceServerDriver {
 public:
  vr::EVRInitError Activate(std::uint32_t object_id) override {
    index_ = object_id;
    const auto p = vr::VRProperties()->TrackedDeviceToPropertyContainer(index_);
    auto text = [&](vr::ETrackedDeviceProperty key, const char* value) {
      vr::VRProperties()->SetStringProperty(p, key, value);
    };
    text(vr::Prop_TrackingSystemName_String, "mikotypekeyboard");
    text(vr::Prop_ModelNumber_String, "MikoType Adaptive Keyboard");
    text(vr::Prop_SerialNumber_String, kSerial);
    text(vr::Prop_ManufacturerName_String, "MikoType");
    text(vr::Prop_RenderModelName_String, "{mikotypekeyboard}mikotype_keyboard");
    text(vr::Prop_ResourceRoot_String, "mikotypekeyboard");
    text(vr::Prop_RegisteredDeviceType_String, "mikotypekeyboard/MIKOTYPE-KEYBOARD-001");
    vr::VRProperties()->SetBoolProperty(p, vr::Prop_DeviceIsWireless_Bool, false);
    vr::VRProperties()->SetBoolProperty(p, vr::Prop_DeviceProvidesBatteryStatus_Bool, false);
    Log("MikoType: keyboard activated; waiting for confirmed bridge pose.");
    return vr::VRInitError_None;
  }
  void Deactivate() override { index_ = vr::k_unTrackedDeviceIndexInvalid; }
  void EnterStandby() override {}
  void* GetComponent(const char*) override { return nullptr; }
  void DebugRequest(const char*, char* response, std::uint32_t capacity) override {
    if (response != nullptr && capacity > 0) response[0] = '\0';
  }
  vr::DriverPose_t GetPose() override {
    vr::DriverPose_t pose{};
    pose.qWorldFromDriverRotation.w = 1;
    pose.qDriverFromHeadRotation.w = 1;
    pose.qRotation.w = 1;
    pose.result = vr::TrackingResult_Uninitialized;
    mikotype::PoseRecord next;
    // Failed nonblocking reads use the last complete record, not partial data.
    if (channel_.Read(next)) cached_ = next;
    pose.deviceIsConnected = mikotype::PoseChannel::IsFresh(cached_);
    if (!pose.deviceIsConnected || cached_.valid != 1) return pose;
    mikotype::Matrix34 rotation{};
    std::memcpy(rotation.data(), cached_.matrix, sizeof(cached_.matrix));
    const auto q = mikotype::QuaternionFromMatrix(rotation);
    pose.qRotation = {q[0], q[1], q[2], q[3]};
    for (int row = 0; row < 3; ++row) pose.vecPosition[row] = cached_.matrix[row * 4 + 3];
    pose.poseIsValid = true;
    pose.result = vr::TrackingResult_Running_OK;
    return pose;
  }
  void Publish() {
    if (index_ == vr::k_unTrackedDeviceIndexInvalid) return;
    const auto pose = GetPose();
    if (pose.poseIsValid != was_valid_) {
      Log(pose.poseIsValid ? "MikoType: confirmed world-space pose is valid." :
                            "MikoType: pose unavailable/stale; hiding keyboard.");
      was_valid_ = pose.poseIsValid;
    }
    vr::VRServerDriverHost()->TrackedDevicePoseUpdated(index_, pose, sizeof(pose));
  }
 private:
  mikotype::PoseChannel channel_{false};
  mikotype::PoseRecord cached_{};
  vr::TrackedDeviceIndex_t index_ = vr::k_unTrackedDeviceIndexInvalid;
  bool was_valid_ = false;
};

class Provider final : public vr::IServerTrackedDeviceProvider {
 public:
  vr::EVRInitError Init(vr::IVRDriverContext* context) override {
    VR_INIT_SERVER_DRIVER_CONTEXT(context);
    Log("MikoType: production hybrid driver starting (no HMD-follow pose).");
    keyboard_ = std::make_unique<Keyboard>();
    if (!vr::VRServerDriverHost()->TrackedDeviceAdded(
          kSerial, vr::TrackedDeviceClass_GenericTracker, keyboard_.get())) {
      Log("MikoType: GenericTracker registration failed.");
      keyboard_.reset();
      VR_CLEANUP_SERVER_DRIVER_CONTEXT();
      return vr::VRInitError_Driver_Failed;
    }
    return vr::VRInitError_None;
  }
  void Cleanup() override { keyboard_.reset(); VR_CLEANUP_SERVER_DRIVER_CONTEXT(); }
  const char* const* GetInterfaceVersions() override { return vr::k_InterfaceVersions; }
  void RunFrame() override { if (keyboard_) keyboard_->Publish(); }
  bool ShouldBlockStandbyMode() override { return false; }
  void EnterStandby() override {}
  void LeaveStandby() override {}
 private:
  std::unique_ptr<Keyboard> keyboard_;
};
Provider provider;
}  // namespace

extern "C" __declspec(dllexport) void* HmdDriverFactory(const char* name, int* error) {
  if (name && std::strcmp(name, vr::IServerTrackedDeviceProvider_Version) == 0) {
    if (error) *error = vr::VRInitError_None;
    return &provider;
  }
  if (error) *error = vr::VRInitError_Init_InterfaceNotFound;
  return nullptr;
}
