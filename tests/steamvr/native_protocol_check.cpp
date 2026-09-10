#include "protocol.h"
#include <fstream>
#include <iostream>
#include <iterator>

int main(int argc, char** argv) {
  if (argc != 2) return 10;
  // Include mixed-axis half-turns that cause sqrt+copysign conversion to lose
  // quaternion signs with float32 matrices.
  for (int yaw = -180; yaw <= 180; yaw += 45) {
    for (int pitch = -180; pitch <= 180; pitch += 45) {
      for (int roll = -180; roll <= 180; roll += 45) {
        mikotype::Frame pose;
        pose.yaw = static_cast<float>(yaw); pose.pitch = static_cast<float>(pitch);
        pose.roll = static_cast<float>(roll);
        const auto matrix = mikotype::StandingPose(pose);
        const auto q = mikotype::QuaternionFromMatrix(matrix);
        const double w = q[0], x = q[1], y = q[2], z = q[3];
        const double reconstructed[9] = {
          1 - 2*y*y - 2*z*z, 2*x*y - 2*z*w, 2*x*z + 2*y*w,
          2*x*y + 2*z*w, 1 - 2*x*x - 2*z*z, 2*y*z - 2*x*w,
          2*x*z - 2*y*w, 2*y*z + 2*x*w, 1 - 2*x*x - 2*y*y};
        for (int row = 0; row < 3; ++row) for (int col = 0; col < 3; ++col) {
          if (std::abs(reconstructed[row*3 + col] - matrix[row*4 + col]) > 1e-5) return 11;
        }
      }
    }
  }
  std::ifstream input(argv[1], std::ios::binary);
  std::vector<std::uint8_t> bytes((std::istreambuf_iterator<char>(input)), {});
  mikotype::Frame frame;
  std::string error;
  if (!mikotype::DecodeFrame(bytes, frame, error)) { std::cout << error; return 2; }
  std::array<std::uint8_t, 32> expected{};
  if (!mikotype::DecodeSha256(std::string(64, 'a'), expected) || expected != frame.model_sha256) return 3;
  if (mikotype::DecodeSha256(std::string(64, 'g'), expected) || mikotype::DecodeSha256("abc", expected)) return 4;
  const auto standing = mikotype::StandingPose(frame);
  // Raw-to-standing is a 90 degree yaw plus a known translation. Validate
  // inverse translation/rotation, not just a matrix pass-through.
  const mikotype::Matrix34 origin{0, 0, 1, 1, 0, 1, 0, 2, -1, 0, 0, 3};
  const auto raw = mikotype::StandingToRaw(origin, standing);
  for (float value : raw) std::cout << value << " ";
  std::cout << frame.sequence << " " << frame.rgba.size();
  return 0;
}
