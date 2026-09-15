from io import BytesIO
import unittest

from print_gcode_engine.process import ProcessMetrics
from print_gcode_engine.scanner import scan


class MergeTests(unittest.TestCase):
    def test_time_weighted_quantiles_and_last_setpoint(self):
        a=b'; filament_diameter = 1.75\nM83\nM104 S220\nG1 X100 E10 F600\n'
        b=b'M104 S240\nG1 X110 E1 F6000\n'
        first=scan(BytesIO(a),len(a),include_internal=True)
        second=scan(BytesIO(b),len(b),initial_state=first['_scan_state'],include_internal=True)
        expected=scan(BytesIO(a+b),len(a+b))['process_metrics']
        merged=ProcessMetrics()
        merged.merge(first['_scan_state']['process_snapshot'])
        merged.merge(second['_scan_state']['process_snapshot'])
        self.assertEqual(merged.result(),expected)


if __name__=='__main__':unittest.main()
