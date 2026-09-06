// Standalone OpenVR companion used only to prove a transparent texture can be
// attached to the keyboard tracker's coordinate frame. It is intentionally not
// linked into vrserver and does not consume the production SceneState yet.

#include <openvr.h>

#include <array>
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <iostream>
#include <optional>
#include <stdexcept>
#include <string>
#include <thread>

#if defined(_WIN32)
#include <windows.h>
#endif

namespace {

constexpr char kKeyboardSerial[] = "DESKVISION-KEYBOARD-DEMO-001";
constexpr char kOverlayKey[] = "deskvision.keyboard.static-highlight";
constexpr char kOverlayName[] = "DeskVision Keyboard Static Highlight";
constexpr char kDefaultImageName[] = "keyboard_highlight_test.png";
// The generated PNG uses the same 512:224 aspect as the validated
// 348.5175 x 152.3025 mm keyboard bounds.
constexpr float kOverlayWidthMetres = 0.3485175F;
constexpr float kOverlayHeightAboveDeviceMetres = 0.018F;
constexpr int kDeviceWaitSeconds = 15;

std::filesystem::path ExecutableDirectory() {
#if defined(_WIN32)
  std::array<wchar_t, 32768> buffer{};
  const DWORD length = GetModuleFileNameW(
      nullptr, buffer.data(), static_cast<DWORD>(buffer.size()));
  if (length == 0 || length >= buffer.size()) {
    throw std::runtime_error("Cannot resolve the overlay executable path.");
  }
  return std::filesystem::path(buffer.data()).parent_path();
#else
  return std::filesystem::current_path();
#endif
}

std::string TrackedString(vr::IVRSystem* system,
                          vr::TrackedDeviceIndex_t device,
                          vr::ETrackedDeviceProperty property) {
  std::array<char, vr::k_unMaxPropertyStringSize> value{};
  vr::ETrackedPropertyError error = vr::TrackedProp_Success;
  system->GetStringTrackedDeviceProperty(
      device, property, value.data(), static_cast<std::uint32_t>(value.size()),
      &error);
  if (error != vr::TrackedProp_Success) {
    return {};
  }
  return value.data();
}

std::optional<vr::TrackedDeviceIndex_t> FindKeyboard(vr::IVRSystem* system) {
  for (vr::TrackedDeviceIndex_t device = 0;
       device < vr::k_unMaxTrackedDeviceCount; ++device) {
    if (!system->IsTrackedDeviceConnected(device)) {
      continue;
    }
    if (system->GetTrackedDeviceClass(device) !=
        vr::TrackedDeviceClass_GenericTracker) {
      continue;
    }
    if (TrackedString(system, device, vr::Prop_SerialNumber_String) ==
        kKeyboardSerial) {
      return device;
    }
  }
  return std::nullopt;
}

std::optional<vr::TrackedDeviceIndex_t> WaitForKeyboard(
    vr::IVRSystem* system) {
  const auto deadline = std::chrono::steady_clock::now() +
                        std::chrono::seconds(kDeviceWaitSeconds);
  do {
    if (const auto device = FindKeyboard(system); device.has_value()) {
      return device;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(250));
  } while (std::chrono::steady_clock::now() < deadline);
  return std::nullopt;
}

bool OverlayCallSucceeded(vr::EVROverlayError error, const char* operation) {
  if (error == vr::VROverlayError_None) {
    return true;
  }
  std::cerr << operation << " failed: "
            << vr::VROverlay()->GetOverlayErrorNameFromEnum(error) << '\n';
  return false;
}

class OpenVrSession final {
 public:
  OpenVrSession() = default;
  OpenVrSession(const OpenVrSession&) = delete;
  OpenVrSession& operator=(const OpenVrSession&) = delete;

  ~OpenVrSession() {
    if (overlay_handle_ != vr::k_ulOverlayHandleInvalid &&
        vr::VROverlay() != nullptr) {
      vr::VROverlay()->HideOverlay(overlay_handle_);
      vr::VROverlay()->DestroyOverlay(overlay_handle_);
    }
    if (system_ != nullptr) {
      vr::VR_Shutdown();
    }
  }

  vr::IVRSystem* system_ = nullptr;
  vr::VROverlayHandle_t overlay_handle_ = vr::k_ulOverlayHandleInvalid;
};

int ParseDurationSeconds(int argc, char** argv) {
  if (argc < 3) {
    return 0;
  }
  try {
    const int value = std::stoi(argv[2]);
    if (value < 1) {
      throw std::invalid_argument("duration must be positive");
    }
    return value;
  } catch (const std::exception&) {
    throw std::runtime_error(
        "The optional second argument must be a positive duration in seconds.");
  }
}

}  // namespace

int main(int argc, char** argv) {
  try {
    const std::filesystem::path image_path =
        argc >= 2 ? std::filesystem::absolute(argv[1])
                  : ExecutableDirectory() / kDefaultImageName;
    const int duration_seconds = ParseDurationSeconds(argc, argv);
    if (!std::filesystem::is_regular_file(image_path)) {
      std::cerr << "Highlight PNG does not exist: " << image_path << '\n'
                << "Pass its path as the first argument or place "
                << kDefaultImageName << " beside the executable.\n";
      return 2;
    }

    OpenVrSession session;
    vr::EVRInitError init_error = vr::VRInitError_None;
    session.system_ = vr::VR_Init(&init_error, vr::VRApplication_Overlay);
    if (init_error != vr::VRInitError_None || session.system_ == nullptr) {
      std::cerr << "OpenVR overlay initialization failed: "
                << vr::VR_GetVRInitErrorAsEnglishDescription(init_error)
                << '\n';
      return 3;
    }

    const auto keyboard = WaitForKeyboard(session.system_);
    if (!keyboard.has_value()) {
      std::cerr << "SteamVR did not expose GenericTracker serial "
                << kKeyboardSerial << " within " << kDeviceWaitSeconds
                << " seconds. Install the driver, restart SteamVR, and enable "
                   "the add-on before retrying.\n";
      return 4;
    }

    if (!OverlayCallSucceeded(
            vr::VROverlay()->CreateOverlay(kOverlayKey, kOverlayName,
                                           &session.overlay_handle_),
            "CreateOverlay")) {
      return 5;
    }

    const std::string image_utf8 = image_path.u8string();
    if (!OverlayCallSucceeded(
            vr::VROverlay()->SetOverlayFromFile(session.overlay_handle_,
                                                image_utf8.c_str()),
            "SetOverlayFromFile") ||
        !OverlayCallSucceeded(
            vr::VROverlay()->SetOverlayWidthInMeters(session.overlay_handle_,
                                                     kOverlayWidthMetres),
            "SetOverlayWidthInMeters") ||
        !OverlayCallSucceeded(
            vr::VROverlay()->SetOverlayAlpha(session.overlay_handle_, 0.90F),
            "SetOverlayAlpha") ||
        !OverlayCallSucceeded(
            vr::VROverlay()->SetOverlayFlag(session.overlay_handle_,
                                            vr::VROverlayFlags_NoBackside,
                                            true),
            "SetOverlayFlag")) {
      return 6;
    }

    const vr::HmdMatrix34_t device_to_overlay = {{
        {1.0F, 0.0F, 0.0F, 0.0F},
        {0.0F, 1.0F, 0.0F, 0.0F},
        {0.0F, 0.0F, 1.0F, kOverlayHeightAboveDeviceMetres},
    }};
    if (!OverlayCallSucceeded(
            vr::VROverlay()->SetOverlayTransformTrackedDeviceRelative(
                session.overlay_handle_, *keyboard, &device_to_overlay),
            "SetOverlayTransformTrackedDeviceRelative") ||
        !OverlayCallSucceeded(
            vr::VROverlay()->ShowOverlay(session.overlay_handle_),
            "ShowOverlay")) {
      return 7;
    }

    std::cout << "BOUND device_index=" << *keyboard
              << " serial=" << kKeyboardSerial
              << " image=" << image_path << '\n';

    const auto deadline = duration_seconds > 0
                              ? std::chrono::steady_clock::now() +
                                    std::chrono::seconds(duration_seconds)
                              : std::chrono::steady_clock::time_point::max();
    bool running = true;
    while (running && std::chrono::steady_clock::now() < deadline) {
      vr::VREvent_t event{};
      while (session.system_->PollNextEvent(&event, sizeof(event))) {
        if (event.eventType == vr::VREvent_Quit) {
          session.system_->AcknowledgeQuit_Exiting();
          running = false;
          break;
        }
      }
      std::this_thread::sleep_for(std::chrono::milliseconds(20));
    }
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "DeskVision overlay failed: " << error.what() << '\n';
    return 8;
  }
}
