cmake_minimum_required(VERSION 3.21)

project(deskvisionkeyboard_driver LANGUAGES CXX)

if(NOT WIN32)
  message(FATAL_ERROR "The DeskVision SteamVR driver can only be built for Windows.")
endif()

set(OPENVR_SDK_ROOT "" CACHE PATH "Absolute path to the Valve OpenVR SDK root")
if(NOT IS_ABSOLUTE "${OPENVR_SDK_ROOT}")
  message(FATAL_ERROR "OPENVR_SDK_ROOT must be an absolute path to the Valve OpenVR SDK.")
endif()

set(OPENVR_DRIVER_HEADER "${OPENVR_SDK_ROOT}/headers/openvr_driver.h")
set(OPENVR_STATIC_LIBRARY "${OPENVR_SDK_ROOT}/lib/win64/openvr_api.lib")
if(NOT EXISTS "${OPENVR_DRIVER_HEADER}")
  message(FATAL_ERROR "Missing OpenVR driver header: ${OPENVR_DRIVER_HEADER}")
endif()
if(NOT EXISTS "${OPENVR_STATIC_LIBRARY}")
  message(FATAL_ERROR "Missing OpenVR win64 static library: ${OPENVR_STATIC_LIBRARY}")
endif()

add_library(driver_deskvisionkeyboard SHARED src/driver.cpp)
target_compile_features(driver_deskvisionkeyboard PRIVATE cxx_std_17)
target_include_directories(
  driver_deskvisionkeyboard
  PRIVATE "${OPENVR_SDK_ROOT}/headers"
)
target_link_libraries(driver_deskvisionkeyboard PRIVATE "${OPENVR_STATIC_LIBRARY}")
target_compile_definitions(
  driver_deskvisionkeyboard
  PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX
)

if(MSVC)
  target_compile_options(driver_deskvisionkeyboard PRIVATE /W4 /permissive- /EHsc)
endif()

set_target_properties(
  driver_deskvisionkeyboard
  PROPERTIES
    PREFIX ""
    OUTPUT_NAME "driver_deskvisionkeyboard"
)

install(
  TARGETS driver_deskvisionkeyboard
  RUNTIME DESTINATION "deskvisionkeyboard/bin/win64"
)
install(
  DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/deskvisionkeyboard/"
  DESTINATION "deskvisionkeyboard"
)
