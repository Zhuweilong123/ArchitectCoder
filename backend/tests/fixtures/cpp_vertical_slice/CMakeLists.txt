cmake_minimum_required(VERSION 3.20)
project(cpp_vertical_slice LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

add_library(service src/service.cpp)
target_include_directories(service PUBLIC src)

enable_testing()
add_executable(service_test tests/service_test.cpp)
target_link_libraries(service_test PRIVATE service)
add_test(NAME service_test COMMAND service_test)
