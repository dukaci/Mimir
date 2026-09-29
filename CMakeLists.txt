cmake_minimum_required(VERSION 3.24)
project(mimir VERSION 2.0.0 LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE Release)
endif()
set(CMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASE ON)
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_SOURCE_DIR}/bin)      # settings.json etc. live next to bin/

# ---------------------------------------------------------------- dependencies
# Pinned source archives, built with the app: nothing to install at run time.
include(FetchContent)
function(fetch name url sha256)
  FetchContent_Declare(${name} URL ${url} URL_HASH SHA256=${sha256} DOWNLOAD_EXTRACT_TIMESTAMP TRUE)
endfunction()
fetch(glfw https://github.com/glfw/glfw/archive/refs/tags/3.4.tar.gz
      c038d34200234d071fae9345bc455e4a8f2f544ab60150765d7704e08f3dac01)
fetch(imgui https://github.com/ocornut/imgui/archive/refs/tags/v1.92.9b.tar.gz
      21d8a0a565e85dce943e375db00812c2f3f0ab21f3f0f7964e364a63422d7f99)
fetch(implot https://github.com/epezent/implot/archive/09e2ba71766e25d88053a2173936c9d1043bae42.tar.gz
      17dd3b860237cb95c0d1c0c6accaf92ad4ea4be61add84eca85e3dc077963e12)
fetch(stb https://github.com/nothings/stb/archive/2c980bb59875b0d32144a71867fbdebb2f77cd20.tar.gz
      9a955b1b49a4410088a2e0ee2a9c057c3c907d0c1d75454144cb980aca0ba515)

foreach(opt GLFW_BUILD_DOCS GLFW_BUILD_TESTS GLFW_BUILD_EXAMPLES GLFW_INSTALL GLFW_BUILD_WAYLAND)
  set(${opt} OFF CACHE BOOL "" FORCE)
endforeach()
FetchContent_MakeAvailable(glfw imgui implot stb)
find_package(OpenGL REQUIRED)
find_package(Threads REQUIRED)

add_library(gui STATIC
  ${imgui_SOURCE_DIR}/imgui.cpp ${imgui_SOURCE_DIR}/imgui_draw.cpp ${imgui_SOURCE_DIR}/imgui_tables.cpp
  ${imgui_SOURCE_DIR}/imgui_widgets.cpp ${imgui_SOURCE_DIR}/backends/imgui_impl_glfw.cpp
  ${imgui_SOURCE_DIR}/backends/imgui_impl_opengl3.cpp ${implot_SOURCE_DIR}/implot.cpp
  ${implot_SOURCE_DIR}/implot_items.cpp)
target_include_directories(gui SYSTEM PUBLIC ${imgui_SOURCE_DIR} ${imgui_SOURCE_DIR}/backends ${implot_SOURCE_DIR}
                                              ${stb_SOURCE_DIR})
target_compile_definitions(gui PUBLIC IMGUI_DISABLE_OBSOLETE_FUNCTIONS)
target_link_libraries(gui PUBLIC glfw OpenGL::GL)

# ---------------------------------------------------------------- app
if(MSVC)
  set(WARNINGS /W4 /permissive- /wd4100)
else()
  set(WARNINGS -Wall -Wextra -Wno-unused-parameter)
endif()

add_library(core STATIC src/history.cpp src/settings.cpp src/log.cpp src/sampler.cpp src/gpu.cpp)
target_compile_options(core PRIVATE ${WARNINGS})
target_link_libraries(core PUBLIC Threads::Threads)
if(WIN32)
  target_sources(core PRIVATE src/sys_windows.cpp src/net_windows.cpp)
  target_compile_definitions(core PUBLIC UNICODE _UNICODE NOMINMAX _CRT_SECURE_NO_WARNINGS)
  target_link_libraries(core PUBLIC pdh iphlpapi advapi32 dwmapi shell32 ntdll)
else()
  target_sources(core PRIVATE src/sys_linux.cpp src/net_linux.cpp)
  target_link_libraries(core PUBLIC ${CMAKE_DL_LIBS})
endif()

add_executable(mimir WIN32 src/main.cpp src/ui.cpp src/theme.cpp)
target_compile_options(mimir PRIVATE ${WARNINGS})
target_compile_definitions(mimir PRIVATE MIMIR_VERSION="${PROJECT_VERSION}")
target_link_libraries(mimir PRIVATE core gui)
if(WIN32)
  target_sources(mimir PRIVATE src/mimir.rc)
  if(MSVC)
    target_link_options(mimir PRIVATE /ENTRY:mainCRTStartup)      # main() without a console window
  endif()
endif()

add_executable(mimir_tests tests/tests.cpp)
target_compile_options(mimir_tests PRIVATE ${WARNINGS})
target_include_directories(mimir_tests PRIVATE src)
target_link_libraries(mimir_tests PRIVATE core)
