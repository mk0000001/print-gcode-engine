"""Optional ahead-of-time build; Python modules remain the portable fallback."""
from setuptools import setup
from Cython.Build import cythonize

setup(name='print-gcode-native',ext_modules=cythonize(
    ['print_gcode_engine/scanner.py','print_gcode_engine/process.py',
     'print_gcode_engine/arcs.py','print_gcode_engine/checkpoint.py','print_gcode_engine/parallel.py'],
    compiler_directives={'language_level':3,'infer_types':True,'annotation_typing':False},
    build_dir='build/cython'))
