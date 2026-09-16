import tempfile,unittest
from pathlib import Path
from print_gcode_engine.analyzer import analyze
from print_gcode_engine.parallel import analyze_parallel_file


def build(path,trailing=True,layers=40):
    lines=['G90','M83']
    if not trailing:lines.insert(0,'; filament_diameter = 1.75')
    for index in range(layers):
        lines+=[';LAYER_CHANGE','; FEATURE: Outer wall',f'G1 X{index%10} Y1 Z{(index+1)*.2} E.5','G1 Y20 E1.5']
    if trailing:lines+=['; filament_diameter = 1.75','; printer_model = Q1']
    path.write_text('\n'.join(lines)+'\n')


class TrailingConfig(unittest.TestCase):
    def test_layer_profile_survives_config_declared_after_extrusion(self):
        with tempfile.TemporaryDirectory() as directory:
            late=Path(directory)/'late.gcode';early=Path(directory)/'early.gcode'
            build(late,trailing=True);build(early,trailing=False)
            late_layers=analyze(late)['layer_volume_profile']['layers']
            early_layers=analyze(early)['layer_volume_profile']['layers']
            self.assertEqual(len(late_layers),40)
            self.assertEqual([row['volume_mm3'] for row in late_layers],[row['volume_mm3'] for row in early_layers])

    def test_parallel_path_merges_deferred_volume_identically(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'late.gcode';build(path,trailing=True,layers=80)
            serial=analyze(path)['layer_volume_profile']['layers']
            parallel=analyze_parallel_file(path,workers=2)['layer_volume_profile']['layers']
            self.assertEqual(len(parallel),80)
            self.assertEqual(parallel,serial)


if __name__=='__main__':unittest.main()
