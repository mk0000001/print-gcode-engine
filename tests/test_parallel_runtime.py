from pathlib import Path
import tempfile
import unittest

from print_gcode_engine.parallel import analyze_parallel_file, benefits_from_parallel
from print_gcode_engine.analyzer import analyze
from print_gcode_engine.checkpoint import segment_checkpoints


class ParallelRuntimeTests(unittest.TestCase):
    def test_adaptive_gate_keeps_linear_workloads_serial(self):
        self.assertFalse(benefits_from_parallel(b'G1 X1 Y1 E1\n'*200))
        self.assertTrue(benefits_from_parallel(b'G2 X1 Y1 I1 J0 E1\n'*200))
    def test_large_dense_linear_work_uses_native_parallel_only(self):
        sample=b'G1 X1 Y1 E1\n'*200
        self.assertTrue(benefits_from_parallel(sample,size_bytes=256*1024**2,native=True))
        self.assertFalse(benefits_from_parallel(sample,size_bytes=256*1024**2,native=False))
        self.assertFalse(benefits_from_parallel(sample,size_bytes=32*1024**2,native=True))
        self.assertFalse(benefits_from_parallel(sample+b';'+b'x'*100000,size_bytes=256*1024**2,native=True))
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
    def test_storage_blob_names_preserve_parallel_eligibility(self):
        with tempfile.TemporaryDirectory() as directory:
            gcode=Path(directory)/'file.gcode';blob=Path(directory)/'stored.bin'
            self.fixture(gcode);blob.write_bytes(gcode.read_bytes())
            self.assertIsNotNone(segment_checkpoints(blob,2))
            self.assertEqual(segment_checkpoints(blob,2),segment_checkpoints(gcode,2))

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
