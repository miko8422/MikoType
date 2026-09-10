#pragma once

// Wire protocol shared by the Windows bridge and portable contract tests.
// It deliberately has no OpenVR, Windows, camera, or Python dependencies.
#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <string>
#include <utility>
#include <vector>

namespace mikotype {
constexpr std::size_t kHeaderBytes = 96;
constexpr std::size_t kMaximumPixelsBytes = 2048U * 2048U * 4U;
constexpr std::uint32_t kFrameFresh = 1U;
constexpr std::uint32_t kEnabled = 2U;
constexpr std::uint32_t kPoseConfirmed = 4U;

struct Frame {
  std::uint32_t width = 0, height = 0, flags = 0;
  std::uint64_t sequence = 0;
  float width_m = 0, height_m = 0;
  float x = 0, y = 0, z = 0, yaw = 0, pitch = 0, roll = 0;
  std::array<std::uint8_t, 32> model_sha256{};
  std::vector<std::uint8_t> rgba;
};

inline std::uint32_t Read32(const std::uint8_t* value) {
  return static_cast<std::uint32_t>(value[0]) |
         (static_cast<std::uint32_t>(value[1]) << 8U) |
         (static_cast<std::uint32_t>(value[2]) << 16U) |
         (static_cast<std::uint32_t>(value[3]) << 24U);
}
inline float ReadFloat(const std::uint8_t* value) {
  const auto bits = Read32(value);
  float result = 0;
  static_assert(sizeof(result) == sizeof(bits), "32-bit IEEE float required");
  std::memcpy(&result, &bits, sizeof(result));
  return result;
}

inline bool DecodeFrame(const std::vector<std::uint8_t>& bytes, Frame& result,
                        std::string& error) {
  if (bytes.size() < kHeaderBytes ||
      std::memcmp(bytes.data(), "MIKOVR01", 8) != 0 ||
      Read32(bytes.data() + 8) != 1) {
    error = "Invalid MikoType frame header/version";
    return false;
  }
  Frame candidate;
  candidate.width = Read32(bytes.data() + 12);
  candidate.height = Read32(bytes.data() + 16);
  candidate.flags = Read32(bytes.data() + 20);
  candidate.sequence = static_cast<std::uint64_t>(Read32(bytes.data() + 24)) |
      (static_cast<std::uint64_t>(Read32(bytes.data() + 28)) << 32U);
  if (candidate.width == 0 || candidate.width > 2048 ||
      candidate.height == 0 || candidate.height > 2048 ||
      (candidate.flags & ~7U) != 0) {
    error = "Frame dimensions/flags exceed protocol limits";
    return false;
  }
  const auto pixel_bytes = static_cast<std::size_t>(candidate.width) *
      candidate.height * 4U;
  if (pixel_bytes > kMaximumPixelsBytes ||
      bytes.size() != kHeaderBytes + pixel_bytes) {
    error = "Frame body length does not match RGBA dimensions";
    return false;
  }
  candidate.width_m = ReadFloat(bytes.data() + 32);
  candidate.height_m = ReadFloat(bytes.data() + 36);
  candidate.x = ReadFloat(bytes.data() + 40);
  candidate.y = ReadFloat(bytes.data() + 44);
  candidate.z = ReadFloat(bytes.data() + 48);
  candidate.yaw = ReadFloat(bytes.data() + 52);
  candidate.pitch = ReadFloat(bytes.data() + 56);
  candidate.roll = ReadFloat(bytes.data() + 60);
  for (const float value : {candidate.width_m, candidate.height_m, candidate.x,
       candidate.y, candidate.z, candidate.yaw, candidate.pitch, candidate.roll}) {
    if (!std::isfinite(value)) {
      error = "Frame contains non-finite dimensions/pose";
      return false;
    }
  }
  if (candidate.width_m < 0.01F || candidate.width_m > 10.0F ||
      candidate.height_m < 0.01F || candidate.height_m > 10.0F ||
      std::abs(candidate.x) > 10 || candidate.y < -2 || candidate.y > 10 ||
      std::abs(candidate.z) > 10 || std::abs(candidate.yaw) > 180 ||
      std::abs(candidate.pitch) > 180 || std::abs(candidate.roll) > 180) {
    error = "Frame dimensions/pose are outside the supported safety envelope";
    return false;
  }
  std::memcpy(candidate.model_sha256.data(), bytes.data() + 64, 32);
  candidate.rgba.assign(bytes.begin() + kHeaderBytes, bytes.end());
  result = std::move(candidate);
  return true;
}

inline bool DecodeSha256(const std::string& text,
                         std::array<std::uint8_t, 32>& result) {
  if (text.size() != 64) return false;
  auto digit = [](char c) -> int {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
  };
  for (std::size_t index = 0; index < result.size(); ++index) {
    const int first = digit(text[index * 2]), second = digit(text[index * 2 + 1]);
    if (first < 0 || second < 0) return false;
    result[index] = static_cast<std::uint8_t>(first * 16 + second);
  }
  return true;
}

using Matrix34 = std::array<float, 12>;

inline std::array<double, 4> QuaternionFromMatrix(const Matrix34& m) {
  // Trace/largest-diagonal branches preserve axis signs near 180 degrees.
  // Per-component sqrt+copysign loses those signs when float32 off-diagonal
  // differences cancel, even though the rotation matrix itself is valid.
  std::array<double, 4> q{1, 0, 0, 0};
  const double trace = static_cast<double>(m[0]) + m[5] + m[10];
  if (trace > 0) {
    const double s = 2 * std::sqrt(trace + 1);
    q = {0.25 * s, (m[9] - m[6]) / s, (m[2] - m[8]) / s, (m[4] - m[1]) / s};
  } else if (m[0] > m[5] && m[0] > m[10]) {
    const double s = 2 * std::sqrt(std::fmax(0.0, 1.0 + m[0] - m[5] - m[10]));
    if (s > 1e-12) q = {(m[9] - m[6]) / s, 0.25 * s, (m[1] + m[4]) / s, (m[2] + m[8]) / s};
  } else if (m[5] > m[10]) {
    const double s = 2 * std::sqrt(std::fmax(0.0, 1.0 + m[5] - m[0] - m[10]));
    if (s > 1e-12) q = {(m[2] - m[8]) / s, (m[1] + m[4]) / s, 0.25 * s, (m[6] + m[9]) / s};
  } else {
    const double s = 2 * std::sqrt(std::fmax(0.0, 1.0 + m[10] - m[0] - m[5]));
    if (s > 1e-12) q = {(m[4] - m[1]) / s, (m[2] + m[8]) / s, (m[6] + m[9]) / s, 0.25 * s};
  }
  const double norm = std::sqrt(q[0]*q[0] + q[1]*q[1] + q[2]*q[2] + q[3]*q[3]);
  for (double& value : q) value /= norm;
  return q;
}

// Column-vector convention: Ry(yaw) * Rx(pitch) * Rz(roll), metres.
inline Matrix34 StandingPose(const Frame& frame) {
  constexpr float radians = 0.01745329251994329577F;
  const float cy = std::cos(frame.yaw * radians), sy = std::sin(frame.yaw * radians);
  const float cp = std::cos(frame.pitch * radians), sp = std::sin(frame.pitch * radians);
  const float cr = std::cos(frame.roll * radians), sr = std::sin(frame.roll * radians);
  return {cy * cr + sy * sp * sr, -cy * sr + sy * sp * cr, sy * cp, frame.x,
          cp * sr, cp * cr, -sp, frame.y,
          -sy * cr + cy * sp * sr, sy * sr + cy * sp * cr, cy * cp, frame.z};
}

inline Matrix34 StandingToRaw(const Matrix34& raw_to_standing,
                              const Matrix34& device_to_standing) {
  // inverse(R,t) * device: rigid transform inverse is (R^T, -R^T t).
  Matrix34 result{};
  for (int row = 0; row < 3; ++row) {
    for (int column = 0; column < 3; ++column) {
      for (int axis = 0; axis < 3; ++axis) {
        result[row * 4 + column] += raw_to_standing[axis * 4 + row] *
            device_to_standing[axis * 4 + column];
      }
    }
    for (int axis = 0; axis < 3; ++axis) {
      result[row * 4 + 3] += raw_to_standing[axis * 4 + row] *
          (device_to_standing[axis * 4 + 3] - raw_to_standing[axis * 4 + 3]);
    }
  }
  return result;
}
}  // namespace mikotype
