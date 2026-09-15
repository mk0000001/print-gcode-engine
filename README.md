# Print G-code Engine

The modal checkpoint pass defers absolute XYZ and feed conversion until a chunk boundary or a relative-coordinate, origin-reset, or unit-change command needs numeric state. Superseded absolute positions are not repeatedly converted. Decimal50 state precision, relative movement, unit scaling, tool state and retraction debt remain exact; there is no reduced-precision fast path. Checkpoint states are regression-tested against complete prefix scans for2/4/6 partitions.

`scan(..., motion_callback=callback)` optionally streams visual motions as `(layer_number, before_xyz, after_xyz, feature, tool, deposited_mm, arc)`. The callback shares the analyzer's modal coordinates and retraction accounting; copy coordinates if retaining them beyond the call. Arc geometry is included only for observers. Stationary unretraction does not create a model-layer area entry, including tiny residual extrusion before a support feature change.

Optional native build (CPython with a C compiler): install `Cython==3.1.3 setuptools==80.9.0 wheel==0.45.1`, then run `python build_native.py build_ext --inplace -j 2` from this repository. Scanner, process histograms, arc geometry and checkpoint passes compile ahead of time. The `.py` files remain the portable fallback. Decimal arithmetic and analysis rules are preserved; no fast-math flags or reduced-precision coordinates are used. Compiled extensions must be rebuilt for the target Python version and platform.

Adaptive parallel mode: set `GCODE_PARALLEL_WORKERS=4` to opt in for files of at least 8 MiB whose bounded sample contains substantial arc motion. A compiled scanner also enables parallel processing for dense motion files of at least 128 MiB. Smaller linear-heavy files retain the serial path. Sliced 3MF also requires a writable `GCODE_SCRATCH_DIR` with room for its uncompressed G-code; the temporary member is removed afterward. Files without sufficient layer markers fall back to serial scanning. Default is serial. Speed varies by workload; benchmark before enabling. Chunk results are combined with time-weighted histograms, modal checkpoints and ordered metadata. This is not an 80% CPU utilization guarantee.

Progress is time-based (approximately 250 ms from the scanner/checkpoint loop), independent of crossing a byte threshold. Progress consumers should avoid blocking the analyzer; final completion always reports the full byte count.

Parallel and archive progress also expose `phase_bytes_processed`, `phase_total_bytes`, and `eta_final_phase` where needed. Use these for remaining-time estimates rather than the weighted display percentage. Preparation and nonfinal plates must not be presented as the end of the entire job; `eta_context` distinguishes archive members.

Standalone bounded-memory G-code and sliced-3MF analyzer. `print_gcode_engine.analyzer.analyze(path)` returns slicer metadata, active material usage, process metrics, and toolpath orientation. Supports large sequential streams and ZIP member streaming. External G-code remains untrusted for production.

StealthChanger/Orca tool profiles are recognized from `printer_settings_id` and Klipper start comments; sparse T tool IDs are mapped to profile ordinals when only active profiles are listed. This package does not make strength or pricing decisions.
