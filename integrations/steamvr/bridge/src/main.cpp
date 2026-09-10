#include <windows.h>
#include <winhttp.h>
#include <openvr.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <deque>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <memory>
#include <mutex>
#include <optional>
#include <regex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
#include "pose_channel.h"

namespace {
constexpr char kSerial[] = "MIKOTYPE-KEYBOARD-001";
using Clock = std::chrono::steady_clock;
std::atomic<bool> g_running{true};

BOOL WINAPI ConsoleSignal(DWORD event) {
  if (event == CTRL_C_EVENT || event == CTRL_BREAK_EVENT || event == CTRL_CLOSE_EVENT) {
    g_running = false;
    return TRUE;
  }
  return FALSE;
}

std::string JsonString(const std::string& text) {
  std::string result = "\"";
  for (const auto value : text) {
    const auto c = static_cast<unsigned char>(value);
    if (c == '\\' || c == '"') { result += '\\'; result += value; }
    else if (c == '\n') result += "\\n";
    else if (c == '\r') result += "\\r";
    else if (c == '\t') result += "\\t";
    else if (c >= 32 && c < 127) result += value;
    else result += '?';
  }
  return result + "\"";
}

class HttpHandle {
 public:
  explicit HttpHandle(HINTERNET value = nullptr) : value_(value) {}
  ~HttpHandle() { if (value_) WinHttpCloseHandle(value_); }
  HttpHandle(const HttpHandle&) = delete;
  HttpHandle& operator=(const HttpHandle&) = delete;
  HINTERNET get() const { return value_; }
 private:
  HINTERNET value_;
};

struct Options {
  INTERNET_PORT port = 0;
  std::wstring token;
  std::array<std::uint8_t, 32> model_sha256{};
};

Options ParseOptions(int argc, wchar_t** argv) {
  std::wstring url, token_file, model_sha;
  for (int index = 1; index < argc; ++index) {
    const std::wstring argument(argv[index]);
    if (index + 1 >= argc) throw std::runtime_error("Every option needs a value");
    if (argument == L"--url") url = argv[++index];
    else if (argument == L"--token-file") token_file = argv[++index];
    else if (argument == L"--model-sha256") model_sha = argv[++index];
    else throw std::runtime_error("Unknown option (only --url, --token-file, --model-sha256)");
  }
  std::wsmatch match;
  if (!std::regex_match(url, match, std::wregex(LR"(^http://127\.0\.0\.1:([0-9]{1,5})/?$)"))) {
    throw std::runtime_error("--url must be exactly http://127.0.0.1:PORT (no remote host/path)");
  }
  const auto port = std::stoul(match[1]);
  if (port == 0 || port > 65535) throw std::runtime_error("Invalid local service port");
  Options options;
  options.port = static_cast<INTERNET_PORT>(port);
  if (token_file.empty()) throw std::runtime_error("--token-file is required; download it in the SteamVR WebUI");
  std::ifstream stream(std::filesystem::path(token_file), std::ios::binary);
  if (!stream) throw std::runtime_error("Cannot read the explicitly selected token file");
  std::string token;
  char value = 0;
  while (stream.get(value)) {
    if (token.size() >= 256) throw std::runtime_error("Token file is too large");
    token += value;
  }
  while (!token.empty() && (token.back() == '\r' || token.back() == '\n')) token.pop_back();
  if (token.size() < 16 || !std::all_of(token.begin(), token.end(), [](unsigned char c) {
      return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
             (c >= '0' && c <= '9') || c == '-' || c == '_';
    })) throw std::runtime_error("Token file has an invalid format; download a fresh plain-text token");
  options.token.assign(token.begin(), token.end());
  const std::string sha(model_sha.begin(), model_sha.end());
  if (!mikotype::DecodeSha256(sha, options.model_sha256)) {
    throw std::runtime_error("--model-sha256 must match the installed asset manifest (64 hex characters)");
  }
  return options;
}

struct TimedFrame { mikotype::Frame frame; Clock::time_point received; };

class Transport {
 public:
  explicit Transport(Options options) : options_(std::move(options)) {
    worker_ = std::thread([this]() { Work(); });
  }
  ~Transport() { Stop(); }
  void Stop() { running_ = false; if (worker_.joinable()) worker_.join(); }
  void Event(const std::string& level, const std::string& event,
             const std::string& message, const std::string& details = "{}") {
    // Only internal, bounded messages enter this queue. Never print URL headers,
    // paths, camera frames, environment variables, or the bridge token.
    std::cout << "[" << level << "] " << event << ": " << message << std::endl;
    const auto payload = "{\"source\":\"windows-bridge\",\"level\":" + JsonString(level) +
      ",\"event\":" + JsonString(event) + ",\"message\":" + JsonString(message) +
      ",\"details\":" + details + "}";
    if (payload.size() > 4096) return;
    std::lock_guard<std::mutex> lock(mutex_);
    if (events_.size() == 64) events_.pop_front();
    // Init failures can end Run before the first successful frame fetch. Keep
    // terminal/error events first so the bounded shutdown flush delivers the
    // useful failure, not merely an older startup message.
    if (level == "error" || event == "bridge_stopped") events_.push_front(payload);
    else events_.push_back(payload);
  }
  std::shared_ptr<const TimedFrame> Latest() {
    std::lock_guard<std::mutex> lock(mutex_);
    return latest_;
  }

 private:
  std::vector<std::uint8_t> Request(HINTERNET connection, const wchar_t* path,
                                   const std::string* body, std::size_t limit) {
    const auto began = Clock::now();
    HttpHandle request(WinHttpOpenRequest(connection, body ? L"POST" : L"GET", path,
        nullptr, WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES, 0));
    if (!request.get()) throw std::runtime_error("WinHTTP request creation failed");
    DWORD redirect = WINHTTP_OPTION_REDIRECT_POLICY_NEVER;
    if (!WinHttpSetOption(request.get(), WINHTTP_OPTION_REDIRECT_POLICY,
                         &redirect, sizeof(redirect))) {
      throw std::runtime_error("Could not disable HTTP redirects");
    }
    const auto headers = L"X-MikoType-SteamVR-Token: " + options_.token +
                         L"\r\nContent-Type: application/json\r\n";
    const DWORD body_size = body ? static_cast<DWORD>(body->size()) : 0;
    if (!WinHttpSendRequest(request.get(), headers.c_str(), static_cast<DWORD>(-1),
          body ? const_cast<char*>(body->data()) : WINHTTP_NO_REQUEST_DATA,
          body_size, body_size, 0) || !WinHttpReceiveResponse(request.get(), nullptr)) {
      throw std::runtime_error("Local HTTP request failed or timed out");
    }
    DWORD status = 0, size = sizeof(status);
    if (!WinHttpQueryHeaders(request.get(), WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
          WINHTTP_HEADER_NAME_BY_INDEX, &status, &size, WINHTTP_NO_HEADER_INDEX)) {
      throw std::runtime_error("Local HTTP response has no status");
    }
    if (status != 200 && !(body && status == 202)) {
      throw std::runtime_error("Local HTTP status " + std::to_string(status) +
        (status == 401 || status == 403 ? " (download a new token after service restart)" : ""));
    }
    DWORD content_length = 0; size = sizeof(content_length);
    if (WinHttpQueryHeaders(request.get(), WINHTTP_QUERY_CONTENT_LENGTH | WINHTTP_QUERY_FLAG_NUMBER,
          WINHTTP_HEADER_NAME_BY_INDEX, &content_length, &size, WINHTTP_NO_HEADER_INDEX) &&
        content_length > limit) throw std::runtime_error("Local response exceeds the size limit");
    std::vector<std::uint8_t> bytes;
    std::array<std::uint8_t, 16384> chunk{};
    for (;;) {
      if (Clock::now() - began > std::chrono::milliseconds(1500)) {
        throw std::runtime_error("Local HTTP response exceeded its total time budget");
      }
      DWORD read = 0;
      if (!WinHttpReadData(request.get(), chunk.data(), static_cast<DWORD>(chunk.size()), &read)) {
        throw std::runtime_error("Local HTTP body read failed or timed out");
      }
      if (read == 0) break;
      if (read > limit - bytes.size()) throw std::runtime_error("Local response exceeds the size limit");
      bytes.insert(bytes.end(), chunk.begin(), chunk.begin() + read);
    }
    return bytes;
  }
  void FlushOne(HINTERNET connection) {
    std::string event;
    {
      std::lock_guard<std::mutex> lock(mutex_);
      if (events_.empty()) return;
      event = std::move(events_.front()); events_.pop_front();
    }
    try { Request(connection, L"/api/steamvr/events", &event, 16384); }
    catch (...) {
      // Reporting failure must not recursively generate logs or block rendering.
      // Keep a bounded recent event for delivery when the local service returns.
      std::lock_guard<std::mutex> lock(mutex_);
      if (events_.size() < 64) events_.push_front(std::move(event));
    }
  }
  void Work() {
    HttpHandle session(WinHttpOpen(L"MikoType-SteamVR/0.1", WINHTTP_ACCESS_TYPE_NO_PROXY,
        WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0));
    if (!session.get() || !WinHttpSetTimeouts(session.get(), 500, 500, 500, 500)) {
      Event("error", "transport_init_failed", "Unable to initialize bounded proxy-free local transport");
      return;
    }
    HttpHandle connection(WinHttpConnect(session.get(), L"127.0.0.1", options_.port, 0));
    if (!connection.get()) {
      Event("error", "transport_init_failed", "Unable to open the local MikoType connection");
      return;
    }
    std::string last_error;
    bool connected = false;
    while (running_) {
      const auto began = Clock::now();
      try {
        auto bytes = Request(connection.get(), L"/api/steamvr/frame.bin", nullptr,
                             mikotype::kHeaderBytes + mikotype::kMaximumPixelsBytes);
        auto next = std::make_shared<TimedFrame>();
        std::string error;
        if (!mikotype::DecodeFrame(bytes, next->frame, error)) throw std::runtime_error(error);
        next->received = Clock::now();
        { std::lock_guard<std::mutex> lock(mutex_); latest_ = std::move(next); }
        if (!connected) Event("info", "service_connected", "Connected to the local frame endpoint");
        connected = true; last_error.clear();
      } catch (const std::exception& error) {
        if (last_error != error.what()) Event("warning", "service_unavailable", error.what());
        connected = false; last_error = error.what();
      }
      FlushOne(connection.get());
      std::this_thread::sleep_until(began + (connected ? std::chrono::milliseconds(33) :
                                                       std::chrono::milliseconds(250)));
    }
    // One best-effort final event only; shutdown is not an unbounded log drain.
    FlushOne(connection.get());
  }
  Options options_;
  std::atomic<bool> running_{true};
  std::thread worker_;
  std::mutex mutex_;
  std::deque<std::string> events_;
  std::shared_ptr<const TimedFrame> latest_;
};

vr::TrackedDeviceIndex_t FindKeyboard(vr::IVRSystem& system) {
  for (vr::TrackedDeviceIndex_t index = 0; index < vr::k_unMaxTrackedDeviceCount; ++index) {
    // Search registered devices even while pose is unconfirmed/disconnected.
    if (system.GetTrackedDeviceClass(index) != vr::TrackedDeviceClass_GenericTracker) continue;
    char serial[128]{};
    vr::ETrackedPropertyError error = vr::TrackedProp_Success;
    system.GetStringTrackedDeviceProperty(index, vr::Prop_SerialNumber_String,
                                          serial, sizeof(serial), &error);
    if (error == vr::TrackedProp_Success && std::string(serial) == kSerial) return index;
  }
  return vr::k_unTrackedDeviceIndexInvalid;
}

std::string SceneProcessName(std::uint32_t pid) {
  if (pid == 0) return "";
  HANDLE process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
  if (!process) return "";
  wchar_t path[32768]{};
  DWORD length = static_cast<DWORD>(std::size(path));
  const bool ok = QueryFullProcessImageNameW(process, 0, path, &length) != FALSE;
  CloseHandle(process);
  if (!ok) return "";
  auto basename = std::filesystem::path(std::wstring(path, length)).filename().wstring();
  std::string result;
  for (const auto c : basename) {
    // Only the basename is reported; never leak an account/install directory.
    result += c >= L'A' && c <= L'Z' ? static_cast<char>(c - L'A' + 'a') :
              c >= 32 && c < 127 ? static_cast<char>(c) : '?';
  }
  return result.substr(0, 128);
}

struct OpenVrSession {
  bool initialized = false;
  vr::VROverlayHandle_t overlay = vr::k_ulOverlayHandleInvalid;
  ~OpenVrSession() {
    if (initialized) {
      if (overlay != vr::k_ulOverlayHandleInvalid && vr::VROverlay()) {
        vr::VROverlay()->HideOverlay(overlay);
        vr::VROverlay()->DestroyOverlay(overlay);
      }
      vr::VR_Shutdown();
    }
  }
};

int Run(const Options& options) {
  HANDLE exclusive = CreateMutexW(nullptr, TRUE, mikotype::kBridgeMutex);
  if (exclusive == nullptr || GetLastError() == ERROR_ALREADY_EXISTS) {
    if (exclusive) CloseHandle(exclusive);
    std::cerr << "Another MikoType bridge is active in this Windows session; close it first.\n";
    return 2;
  }
  struct ExclusiveGuard { HANDLE handle; ~ExclusiveGuard() { ReleaseMutex(handle); CloseHandle(handle); } } guard{exclusive};
  SetConsoleCtrlHandler(ConsoleSignal, TRUE);
  Transport transport(options);
  transport.Event("info", "bridge_started", "Windows hybrid bridge started; waiting for SteamVR and manual alignment");
  mikotype::PoseChannel channel(true);
  if (!channel.Open()) {
    transport.Event("error", "pose_channel_failed", "Could not create same-session pose channel");
    return 3;
  }
  channel.Publish({}, false);
  OpenVrSession session;
  vr::EVRInitError error = vr::VRInitError_None;
  vr::IVRSystem* system = vr::VR_Init(&error, vr::VRApplication_Overlay);
  if (error != vr::VRInitError_None || system == nullptr) {
    transport.Event("error", "openvr_init_failed", vr::VR_GetVRInitErrorAsEnglishDescription(error),
                    "{\"code\":" + std::to_string(error) + "}");
    return 4;
  }
  session.initialized = true;
  transport.Event("info", "openvr_ready", "OpenVR overlay session initialized (this does not prove Home visibility)");
  vr::IVROverlay* overlay = vr::VROverlay();
  if (overlay == nullptr) {
    transport.Event("error", "overlay_unavailable", "SteamVR did not expose IVROverlay"); return 5;
  }
  const auto created = overlay->CreateOverlay("mikotype.keyboard.live", "MikoType Keyboard Live", &session.overlay);
  if (created != vr::VROverlayError_None) {
    transport.Event("error", "overlay_create_failed", overlay->GetOverlayErrorNameFromEnum(created)); return 6;
  }
  overlay->SetOverlayFlag(session.overlay, vr::VROverlayFlags_NoBackside, true);
  overlay->SetOverlayAlpha(session.overlay, 1.0F);

  vr::TrackedDeviceIndex_t tracker = vr::k_unTrackedDeviceIndexInvalid;
  auto last_search = Clock::now() - std::chrono::seconds(2);
  auto last_heartbeat = Clock::now() - std::chrono::seconds(2);
  bool visible = false, last_pose_valid = false;
  std::string gate_reason;
  std::uint64_t uploaded_sequence = 0;
  bool have_uploaded = false;
  std::shared_ptr<const TimedFrame> last_uploaded;
  std::string last_overlay_error;
  while (g_running) {
    const auto now = Clock::now();
    vr::VREvent_t event{};
    while (overlay->PollNextOverlayEvent(session.overlay, &event, sizeof(event))) {
      if (event.eventType == vr::VREvent_Quit) { system->AcknowledgeQuit_Exiting(); g_running = false; }
    }
    if (now - last_search >= std::chrono::seconds(1)) {
      const auto found = FindKeyboard(*system);
      if (found != tracker || last_search.time_since_epoch().count() == 0) {
        transport.Event(found != vr::k_unTrackedDeviceIndexInvalid ? "info" : "warning",
                        found != vr::k_unTrackedDeviceIndexInvalid ? "tracker_found" : "tracker_missing",
                        found != vr::k_unTrackedDeviceIndexInvalid ? "Registered MikoType GenericTracker found" :
                          "MikoType driver is absent; build/install it and enable the SteamVR add-on");
        have_uploaded = false;
      }
      tracker = found; last_search = now;
    }
    const auto latest = transport.Latest();
    const bool recent = latest && now - latest->received <= std::chrono::milliseconds(1500);
    const bool matching = recent && latest->frame.model_sha256 == options.model_sha256;
    const bool enabled = matching && (latest->frame.flags & mikotype::kEnabled) != 0;
    const bool confirmed = enabled && (latest->frame.flags & mikotype::kPoseConfirmed) != 0;
    const bool fresh_content = confirmed && (latest->frame.flags & mikotype::kFrameFresh) != 0;
    std::string reason = !recent ? "service_stale" : !matching ? "model_mismatch" :
      !enabled ? "disabled" : !confirmed ? "alignment_required" : !fresh_content ? "tracking_stale" :
      tracker == vr::k_unTrackedDeviceIndexInvalid ? "tracker_missing" : "ready";
    if (reason != gate_reason) {
      const std::string message = reason == "model_mismatch" ?
          "Installed model differs from the active layout; re-export/rebuild assets and restart SteamVR" :
          reason == "alignment_required" ? "Confirm keyboard standing-space pose in the WebUI before rendering" :
          reason == "tracking_stale" ? "Camera/marker tracking is unavailable; dynamic overlay hidden" :
          reason == "service_stale" ? "No recent service frame; dynamic overlay and tracker pose hidden" :
          reason == "tracker_missing" ? "Driver not found; install and enable the MikoType SteamVR add-on" :
          reason == "disabled" ? "SteamVR output is disabled in the WebUI" : "Live overlay gates passed";
      transport.Event(reason == "ready" || reason == "disabled" ? "info" : "warning", reason, message);
      gate_reason = reason;
    }
    mikotype::Matrix34 raw_pose{};
    if (confirmed) {
      const auto standing_origin = system->GetRawZeroPoseToStandingAbsoluteTrackingPose();
      mikotype::Matrix34 origin{};
      std::memcpy(origin.data(), standing_origin.m, sizeof(standing_origin.m));
      raw_pose = mikotype::StandingToRaw(origin, mikotype::StandingPose(latest->frame));
    }
    // Stale camera clears fingertips but may retain a confirmed stationary 3D
    // keyboard. Service disconnect, model mismatch, disable, or unlock clears both.
    channel.Publish(raw_pose, confirmed);
    if (last_pose_valid != confirmed) {
      transport.Event("info", confirmed ? "pose_published" : "pose_invalidated",
          confirmed ? "Confirmed standing-space pose published to driver" : "Driver pose invalidated");
      last_pose_valid = confirmed;
    }
    const bool want_visible = fresh_content && tracker != vr::k_unTrackedDeviceIndexInvalid;
    bool rendered = want_visible;
    if (want_visible && (!have_uploaded || latest != last_uploaded || latest->frame.sequence != uploaded_sequence)) {
      const auto& frame = latest->frame;
      vr::HmdMatrix34_t transform{};
      transform.m[0][0] = transform.m[1][1] = transform.m[2][2] = 1;
      transform.m[2][3] = 0.018F;
      vr::EVROverlayError operation = overlay->SetOverlayTransformTrackedDeviceRelative(session.overlay, tracker, &transform);
      if (operation == vr::VROverlayError_None) operation = overlay->SetOverlayWidthInMeters(session.overlay, frame.width_m);
      // Raw image aspect ratio need not equal the physical keyboard ratio.
      if (operation == vr::VROverlayError_None) operation = overlay->SetOverlayTexelAspect(session.overlay,
          (frame.width_m / frame.height_m) / (static_cast<float>(frame.width) / frame.height));
      if (operation == vr::VROverlayError_None) operation = overlay->SetOverlayRaw(session.overlay,
          const_cast<std::uint8_t*>(frame.rgba.data()), frame.width, frame.height, 4);
      if (operation == vr::VROverlayError_None) operation = overlay->ShowOverlay(session.overlay);
      if (operation != vr::VROverlayError_None) {
        const std::string name = overlay->GetOverlayErrorNameFromEnum(operation);
        if (name != last_overlay_error) transport.Event("error", "overlay_update_failed", name);
        last_overlay_error = name; rendered = false; have_uploaded = false;
      } else {
        last_overlay_error.clear(); uploaded_sequence = frame.sequence;
        have_uploaded = true; last_uploaded = latest;
      }
    }
    if (!rendered) { overlay->HideOverlay(session.overlay); have_uploaded = false; }
    if (visible != rendered) {
      transport.Event("info", rendered ? "overlay_shown" : "overlay_hidden",
          rendered ? "OpenVR accepted the live texture (verify visibility in your headset)" :
                     "Live texture hidden by freshness/alignment/render gates");
      visible = rendered;
    }
    if (now - last_heartbeat >= std::chrono::seconds(2)) {
      const auto scene_pid = vr::VRApplications() ? vr::VRApplications()->GetCurrentSceneProcessId() : 0;
      const auto scene_name = SceneProcessName(scene_pid);
      transport.Event("info", "bridge_heartbeat", "Windows SteamVR bridge heartbeat",
          "{\"overlay_visible\":" + std::string(visible ? "true" : "false") +
          ",\"tracker_found\":" + (tracker != vr::k_unTrackedDeviceIndexInvalid ? "true" : "false") +
          ",\"pose_confirmed\":" + (confirmed ? "true" : "false") +
          ",\"scene_process_id\":" + std::to_string(scene_pid) +
          ",\"scene_process\":" + JsonString(scene_name) +
          ",\"home_active\":" + (scene_name == "steamtours.exe" ? "true" : "false") +
          ",\"frame_sequence\":" + std::to_string(latest ? latest->frame.sequence : 0) +
          ",\"gate\":" + JsonString(reason) + "}");
      last_heartbeat = now;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(11));
  }
  channel.Publish({}, false);
  overlay->HideOverlay(session.overlay);
  transport.Event("info", "bridge_stopped", "Bridge stopped; dynamic overlay hidden and driver pose invalidated");
  return 0;
}
}  // namespace

int wmain(int argc, wchar_t** argv) {
  try { return Run(ParseOptions(argc, argv)); }
  catch (const std::exception& error) {
    // Option values/token contents are intentionally never echoed.
    std::cerr << "MikoType bridge: " << error.what() << "\n";
    std::cerr << "Usage: mikotype_steamvr_bridge --url http://127.0.0.1:PORT "
                 "--token-file PATH --model-sha256 HEX\n";
    return 1;
  }
}
