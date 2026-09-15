from io import BytesIO
import unittest
from unittest.mock import patch
from print_gcode_engine.scanner import scan


class CountingStream(BytesIO):
    def __init__(self,data):super().__init__(data);self.tell_calls=0
    def tell(self):self.tell_calls+=1;return super().tell()


class ProgressCadenceTests(unittest.TestCase):
    def test_sub_four_megabyte_work_reports_by_time_without_per_line_tell(self):
        data=b'M83\n'+b'G1 X1 E.01 F600\n'*8192
        stream=CountingStream(data);updates=[];ticks=iter(i*.3 for i in range(100))
        with patch('print_gcode_engine.scanner.time.monotonic',side_effect=lambda:next(ticks)):
            result=scan(stream,len(data),updates.append)
        intermediate=[row for row in updates if 0<row['bytes_processed']<len(data)]
        self.assertGreater(len(intermediate),1)
        self.assertLess(stream.tell_calls,100)
        self.assertEqual(updates[-1]['bytes_processed'],len(data))
        self.assertEqual(updates[-1]['lines'],result['lines'])


if __name__=='__main__':unittest.main()
