# Print G-code Engine

Standalone bounded-memory G-code and sliced-3MF analyzer. `print_gcode_engine.analyzer.analyze(path)` returns slicer metadata, active material usage, process metrics, and toolpath orientation. Supports large sequential streams and ZIP member streaming. External G-code remains untrusted for production.

StealthChanger/Orca tool profiles are recognized from `printer_settings_id` and Klipper start comments; sparse T tool IDs are mapped to profile ordinals when only active profiles are listed. This package does not make strength or pricing decisions.
