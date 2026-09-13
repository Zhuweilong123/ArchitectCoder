# C++ vertical slice fixture

This fixture is intentionally small: CMake emits `compile_commands.json`, the
Clang adapter extracts `sample::Service` and `Service::run`, and CTest verifies
the same behavior. A design projection is included for the later UML/source
consistency rule. The fixture is source-only in the repository; configure it
in a C++ toolchain image or a host with CMake/Clang before running it.
