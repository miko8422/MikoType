cmake_minimum_required(VERSION 3.21)

project(deskvision_keyboard_overlay LANGUAGES CXX)

if(NOT WIN32)
  message(FATAL_ERROR "The DeskVision OpenVR overlay can only be built for Windows.")
endif()

set(OPENVR_SDK_ROOT "" CACHE PATH "Absolute path to the Valve OpenVR SDK root")
if(NOT IS_ABSOLUTE "${OPENVR_SDK_ROOT}")
  message(FATAL_ERROR "OPENVR_SDK_ROOT must be an absolute path to the Valve OpenVR SDK.")
endif()

set(OPENVR_APPLICATION_HEADER "${OPENVR_SDK_ROOT}/headers/openvr.h")
set(OPENVR_IMPORT_LIBRARY "${OPENVR_SDK_ROOT}/lib/win64/openvr_api.lib")
set(OPENVR_RUNTIME_LIBRARY "${OPENVR_SDK_ROOT}/bin/win64/openvr_api.dll")
foreach(required_file IN ITEMS
        "${OPENVR_APPLICATION_HEADER}"
        "${OPENVR_IMPORT_LIBRARY}"
        "${OPENVR_RUNTIME_LIBRARY}")
  if(NOT EXISTS "${required_file}")
    message(FATAL_ERROR "Missing required OpenVR SDK file: ${required_file}")
  endif()
endforeach()

add_executable(deskvision_keyboard_overlay src/main.cpp)
target_compile_features(deskvision_keyboard_overlay PRIVATE cxx_std_17)
target_include_directories(
  deskvision_keyboard_overlay
  PRIVATE "${OPENVR_SDK_ROOT}/headers"
)
target_link_libraries(
  deskvision_keyboard_overlay
  PRIVATE "${OPENVR_IMPORT_LIBRARY}"
)
target_compile_definitions(
  deskvision_keyboard_overlay
  PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX
)

if(MSVC)
  target_compile_options(deskvision_keyboard_overlay PRIVATE /W4 /permissive- /EHsc)
endif()

add_custom_command(
  TARGET deskvision_keyboard_overlay POST_BUILD
  COMMAND "${CMAKE_COMMAND}" -E copy_if_different
          "${OPENVR_RUNTIME_LIBRARY}"
          "$<TARGET_FILE_DIR:deskvision_keyboard_overlay>/openvr_api.dll"
  VERBATIM
)

install(TARGETS deskvision_keyboard_overlay RUNTIME DESTINATION "overlay")
install(FILES "${OPENVR_RUNTIME_LIBRARY}" DESTINATION "overlay")
install(
  FILES "${CMAKE_CURRENT_SOURCE_DIR}/assets/keyboard_highlight_test.png"
  DESTINATION "overlay"
)
