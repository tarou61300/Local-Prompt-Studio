# llama.cpp runtimes

Both runtimes are isolated inside this project and come from the official
`ggml-org/llama.cpp` GitHub Release `b9637`.

- CPU: `runtime/cpu`
- Vulkan x64: `runtime/vulkan`
- Version pin: `LLAMA_CPP_VERSION_PIN.txt`
- Commit pin: `LLAMA_CPP_COMMIT_PIN.txt`
- Variant SHA256 pins: `LLAMA_CPP_CPU_SHA256_PIN.txt` and
  `LLAMA_CPP_VULKAN_SHA256_PIN.txt`

Use `scripts/fetch_llama_cpp.ps1 -Variant cpu` or `-Variant vulkan`. The script
queries official release metadata, matches the exact asset identity, validates
the official digest and archive size, checks extraction paths, and replaces only
the selected project-local runtime variant.

CUDA, HIP, and SYCL runtimes are not supported in this development stage.
