cmake_minimum_required(VERSION 3.21)
project(mikotype_steamvr LANGUAGES CXX)

if(NOT WIN32 OR NOT CMAKE_SIZEOF_VOID_P EQUAL 8)
  message(FATAL_ERROR "MikoType SteamVR integration requires a Windows x64 toolchain.")
endif()
set(OPENVR_SDK_ROOT "" CACHE PATH "Absolute path to official Valve OpenVR SDK")
set(MIKOTYPE_ASSET_ROOT "" CACHE PATH "Absolute path to extracted WebUI OpenVR assets")
foreach(root IN ITEMS OPENVR_SDK_ROOT MIKOTYPE_ASSET_ROOT)
  if(NOT IS_ABSOLUTE "${${root}}")
    message(FATAL_ERROR "${root} must be an absolute path.")
  endif()
endforeach()
foreach(input IN ITEMS
    "${OPENVR_SDK_ROOT}/headers/openvr.h"
    "${OPENVR_SDK_ROOT}/headers/openvr_driver.h"
    "${OPENVR_SDK_ROOT}/lib/win64/openvr_api.lib"
    "${OPENVR_SDK_ROOT}/bin/win64/openvr_api.dll"
    "${OPENVR_SDK_ROOT}/LICENSE"
    "${MIKOTYPE_ASSET_ROOT}/mikotype_keyboard.json"
    "${MIKOTYPE_ASSET_ROOT}/export_manifest.json")
  if(NOT EXISTS "${input}")
    message(FATAL_ERROR "Required build input is missing: ${input}")
  endif()
endforeach()

add_library(driver_mikotypekeyboard SHARED driver/src/driver.cpp)
add_executable(mikotype_steamvr_bridge bridge/src/main.cpp)
foreach(target IN ITEMS driver_mikotypekeyboard mikotype_steamvr_bridge)
  target_compile_features(${target} PRIVATE cxx_std_17)
  target_compile_definitions(${target} PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX UNICODE _UNICODE)
  target_include_directories(${target} PRIVATE "${OPENVR_SDK_ROOT}/headers" "${CMAKE_CURRENT_SOURCE_DIR}/common")
  if(MSVC)
    target_compile_options(${target} PRIVATE /W4 /permissive- /EHsc /utf-8)
  endif()
endforeach()
# The server driver gets its interface context from vrserver through the
# header-only driver helpers; it must not depend on the client openvr_api.dll.
target_link_libraries(mikotype_steamvr_bridge PRIVATE winhttp "${OPENVR_SDK_ROOT}/lib/win64/openvr_api.lib")
set_target_properties(driver_mikotypekeyboard PROPERTIES PREFIX "")

install(TARGETS driver_mikotypekeyboard RUNTIME DESTINATION "mikotypekeyboard/bin/win64")
install(DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/driver/mikotypekeyboard/" DESTINATION "mikotypekeyboard")
install(DIRECTORY "${MIKOTYPE_ASSET_ROOT}/" DESTINATION "mikotypekeyboard/resources/rendermodels/mikotype_keyboard"
    FILES_MATCHING PATTERN "*.json" PATTERN "*.obj" PATTERN "*.mtl" PATTERN "*.png")
install(TARGETS mikotype_steamvr_bridge RUNTIME DESTINATION "bridge")
install(FILES "${OPENVR_SDK_ROOT}/bin/win64/openvr_api.dll" DESTINATION "bridge")
install(FILES "${OPENVR_SDK_ROOT}/LICENSE" DESTINATION "." RENAME "OPENVR-LICENSE.txt")
install(FILES "${MIKOTYPE_ASSET_ROOT}/export_manifest.json" DESTINATION ".")
