# Print G-code Engine

Adaptive parallel mode: set `GCODE_PARALLEL_WORKERS=4` to opt in for files of at least 8 MiB whose bounded sample contains substantial arc motion. Linear-heavy files stay serial because VM tests found multiprocessing slower for them. Sliced 3MF also requires a writable `GCODE_SCRATCH_DIR` with room for its uncompressed G-code; the temporary member is removed afterward. Files without sufficient layer markers fall back to serial scanning. Default is serial. Speed varies by workload; benchmark before enabling. Chunk results are combined with time-weighted histograms, modal checkpoints and ordered metadata. This is not an 80% CPU utilization guarantee.

Standalone bounded-memory G-code and sliced-3MF analyzer. `print_gcode_engine.analyzer.analyze(path)` returns slicer metadata, active material usage, process metrics, and toolpath orientation. Supports large sequential streams and ZIP member streaming. External G-code remains untrusted for production.

StealthChanger/Orca tool profiles are recognized from `printer_settings_id` and Klipper start comments; sparse T tool IDs are mapped to profile ordinals when only active profiles are listed. This package does not make strength or pricing decisions.
