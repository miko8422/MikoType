#pragma once

#include <windows.h>
#include <cmath>
#include <cstdint>
#include <cstring>
#include "protocol.h"

namespace mikotype {
// Local namespace + default Windows DACL restrict this to the current logon
// session/user. No global namespace, sockets, or external driver dependencies.
constexpr wchar_t kPoseMapping[] = L"Local\\MikoType.KeyboardPose.v1";
constexpr wchar_t kPoseMutex[] = L"Local\\MikoType.KeyboardPoseLock.v1";
constexpr wchar_t kBridgeMutex[] = L"Local\\MikoType.KeyboardBridge.v1";
constexpr std::uint32_t kPoseMagic = 0x4d4b5031;
struct PoseRecord {
  std::uint32_t magic = kPoseMagic;
  std::uint32_t version = 1;
  std::uint64_t updated_tick_ms = 0;
  std::uint32_t valid = 0;
  std::uint32_t reserved = 0;
  float matrix[12]{};
};
static_assert(sizeof(PoseRecord) == 72, "Pose record layout changed");

class PoseChannel {
 public:
  explicit PoseChannel(bool writer) : writer_(writer) {}
  ~PoseChannel() { Close(); }
  PoseChannel(const PoseChannel&) = delete;
  PoseChannel& operator=(const PoseChannel&) = delete;

  bool Open() {
    if (data_ != nullptr) return true;
    if (writer_) {
      mutex_ = CreateMutexW(nullptr, FALSE, kPoseMutex);
      mapping_ = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr,
          PAGE_READWRITE, 0, sizeof(PoseRecord), kPoseMapping);
    } else {
      mutex_ = OpenMutexW(SYNCHRONIZE | MUTEX_MODIFY_STATE, FALSE, kPoseMutex);
      mapping_ = OpenFileMappingW(FILE_MAP_READ, FALSE, kPoseMapping);
    }
    if (mutex_ == nullptr || mapping_ == nullptr) { Close(); return false; }
    data_ = MapViewOfFile(mapping_, writer_ ? FILE_MAP_WRITE : FILE_MAP_READ,
                         0, 0, sizeof(PoseRecord));
    if (data_ == nullptr) { Close(); return false; }
    return true;
  }

  bool Publish(const Matrix34& matrix, bool valid) {
    if (!writer_ || !Open()) return false;
    // Never wait on another process. Retain the previous record briefly if the
    // reader owns the mutex; driver freshness expires independently.
    const DWORD result = WaitForSingleObject(mutex_, 0);
    if (result != WAIT_OBJECT_0 && result != WAIT_ABANDONED) return false;
    PoseRecord record;
    record.updated_tick_ms = GetTickCount64();
    record.valid = valid ? 1U : 0U;
    std::memcpy(record.matrix, matrix.data(), sizeof(record.matrix));
    std::memcpy(data_, &record, sizeof(record));
    ReleaseMutex(mutex_);
    return true;
  }

  bool Read(PoseRecord& record) {
    if (writer_ || !Open()) return false;
    const DWORD result = WaitForSingleObject(mutex_, 0);
    if (result == WAIT_ABANDONED) { ReleaseMutex(mutex_); return false; }
    if (result != WAIT_OBJECT_0) return false;
    std::memcpy(&record, data_, sizeof(record));
    ReleaseMutex(mutex_);
    return IsFresh(record);
  }

  static bool IsFresh(const PoseRecord& record) {
    const auto now = GetTickCount64();
    if (record.magic != kPoseMagic || record.version != 1 || record.valid > 1 ||
        record.updated_tick_ms > now || now - record.updated_tick_ms > 1500) return false;
    for (float value : record.matrix) if (!std::isfinite(value)) return false;
    return true;
  }

 private:
  void Close() {
    if (data_ != nullptr) UnmapViewOfFile(data_);
    if (mapping_ != nullptr) CloseHandle(mapping_);
    if (mutex_ != nullptr) CloseHandle(mutex_);
    data_ = nullptr; mapping_ = nullptr; mutex_ = nullptr;
  }
  bool writer_;
  HANDLE mapping_ = nullptr, mutex_ = nullptr;
  void* data_ = nullptr;
};
}  // namespace mikotype
