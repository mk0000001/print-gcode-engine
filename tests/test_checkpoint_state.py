from decimal import localcontext
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from print_gcode_engine.checkpoint import segment_checkpoints
from print_gcode_engine.scanner import scan


class CheckpointStates(unittest.TestCase):
    def test_every_checkpoint_matches_full_scanner_with_modal_changes(self):
        rows=['; filament_diameter = 1.75,1.75','G21','G90','M83']
        for layer in range(100):
            rows.extend([';LAYER_CHANGE',f'G1 X{layer}.12345678901234567890123456789012345678901234567890123456789 Y2 Z{layer*.2} F1200 E1',
                         'G1 X50 Y4','G92 X10 Y-3','G91','G1 X.5 Y-.25 E-.8','G20','G1 Y.25 F60 E.01','G21',
                         'G90','G1 X40 Y10 E.6','M82','G92 E0','G1 X41 E-.4','G1 X42 E.1','G1 X43 E.4',
                         'T1','G92 E2','G1 Y11 E2.3','T0','M83','G18','G90.1','G17','G91.1','M104 S220','M140 S60',
                         '; FEATURE: Outer wall','G1 X44 E1','G1 Y12 F1800'])
        data=('\n'.join(rows)+'\n').encode()
        with tempfile.TemporaryDirectory() as directory,localcontext() as ctx:
            ctx.prec=50;path=Path(directory)/'fixture.bin';path.write_bytes(data)
            for workers in (2,4,6):
                for start,end,state in segment_checkpoints(path,workers)[1:]:
                    expected=scan(BytesIO(data[:start]),start,include_internal=True)['_scan_state']
                    for key,value in state.items():self.assertEqual(value,expected[key],(workers,start,key))


if __name__=='__main__':unittest.main()
