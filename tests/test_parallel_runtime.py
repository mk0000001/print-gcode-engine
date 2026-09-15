from pathlib import Path
import tempfile
import unittest

from print_gcode_engine.parallel import analyze_parallel_file
from print_gcode_engine.analyzer import analyze


class ParallelRuntimeTests(unittest.TestCase):
    def fixture(self,path):
        lines=['; filament_type = PLA','M83','G90']
        for i in range(80):lines.extend((';LAYER_CHANGE',f'G1 X{i+1} Y0 Z0 E1 F60'))
        path.write_text('\n'.join(lines)+'\n')

    def test_runtime_merge_and_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'layers.gcode';self.fixture(path)
            updates=[]
            result=analyze_parallel_file(path,updates.append,workers=2)
            execution=result.pop('analysis_execution')
            self.assertEqual(execution['workers'],2)
            self.assertEqual(result,analyze(path))
            self.assertEqual(updates[-1]['bytes_processed'],path.stat().st_size)

    def test_cancel_before_work(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'layers.gcode';self.fixture(path)
            with self.assertRaisesRegex(RuntimeError,'ANALYSIS_CANCELLED'):
                analyze_parallel_file(path,cancelled=lambda:True,workers=2)

    def test_cancel_after_checkpoint_stops_children(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'layers.gcode';self.fixture(path)
            calls=0
            def cancelled():
                nonlocal calls
                calls+=1
                return calls>1
            with self.assertRaisesRegex(RuntimeError,'ANALYSIS_CANCELLED'):
                analyze_parallel_file(path,cancelled=cancelled,workers=2)


if __name__=='__main__':unittest.main()
