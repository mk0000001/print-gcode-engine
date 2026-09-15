"""Modal checkpoints for layer-aligned parallel scans, with lazy absolute coordinates."""
from decimal import Decimal, localcontext
from pathlib import Path
import time
import re

from .scanner import COMMAND, FIELDS, LINE_NUMBER, MOTION_CODES, SETPOINT, SETTINGS, LAYER_MARKER


def segment_checkpoints(path, workers=4, *, cancelled=None, progress=None):
    with localcontext() as context:
        context.prec=50
        return _segment_checkpoints(path,workers,cancelled,progress)


def _segment_checkpoints(path, workers=4, cancelled=None, progress=None):
    """Return (start, end, initial_state) for plain layer-marked G-code.

    The pass tracks modal state only; expensive geometry, arc tangents, and
    process histograms remain in scan(). Unsupported inputs return None.
    """
    path=Path(path)
    if workers<2 or workers>8:raise ValueError('INVALID_WORKER_COUNT')
    size=path.stat().st_size
    # Storage uses opaque .bin names; analyze() already dispatches by content.
    if size<1024:return None
    # Avoid a full extra modal pass for unlayered files. A very large first
    # layer may conservatively choose serial analysis with this bounded probe.
    with path.open('rb') as stream:head=stream.read(min(size,4*1024*1024))
    markers=re.finditer(rb'(?mi)^;\s*(?:LAYER:|LAYER_CHANGE\b|CHANGE_LAYER\b)',head)
    if next(markers,None) is None or next(markers,None) is None:return None
    del head
    zero=Decimal(0)
    xyz={axis:zero for axis in 'XYZ'};offset={axis:zero for axis in 'XYZ'}
    epos={0:zero};retract={};tool=0;last_tool=None
    absolute_xyz=True;absolute_e=True;scale=Decimal(1)
    plane='G17';absolute_center=False;config={};feature=None;stealth_start=False
    feed=0.0;diameters=[];setpoints={'nozzle':None,'bed':None,'chamber':None}
    pending_xyz={};pending_feed=None
    cuts=[0];states=[]
    lines=0;next_check=0;layer_number=0

    def flush_pending():
        nonlocal feed,pending_feed
        # Absolute coordinates are overwritten by subsequent moves. Resolve only
        # at boundaries or before a command that needs the current numeric state.
        for axis,value in pending_xyz.items():xyz[axis]=Decimal(value)*scale+offset[axis]
        pending_xyz.clear()
        if pending_feed is not None:
            feed=float(Decimal(pending_feed)*scale);pending_feed=None

    def snapshot():
        flush_pending()
        return {'xyz':xyz.copy(),'offset':offset.copy(),'epos':epos.copy(),
            'retract':retract.copy(),'tool':tool,'last_tool':last_tool,
            'absolute_xyz':absolute_xyz,'absolute_e':absolute_e,'scale':scale,
            'plane':plane,'absolute_center':absolute_center,'config':config.copy(),
            'feature':feature,'stealth_start':stealth_start,'feed':feed,
            'diameters':list(diameters),'setpoints':setpoints.copy(),'layer_number':layer_number}

    states.append(snapshot())
    next_position=0
    with path.open('rb') as stream:
        while True:
            if lines%4096==0 and time.monotonic()>=next_check:
                next_check=time.monotonic()+.25
                if cancelled and cancelled():raise RuntimeError('ANALYSIS_CANCELLED')
                if progress:progress({'bytes_processed':stream.tell(),'total_bytes':size,'lines':lines})
            position=next_position
            binary=stream.readline(1024*1024+1)
            if not binary:break
            next_position+=len(binary)
            lines+=1
            if len(binary)>1024*1024:raise ValueError('GCODE_LINE_TOO_LONG')
            if b'\x00' in binary:raise ValueError('BINARY_GCODE_NOT_SUPPORTED')
            text=binary.decode('utf-8','replace').strip()
            if text.startswith(';'):
                if len(cuts)<workers and position>=size*len(cuts)/workers and LAYER_MARKER.match(text):
                    cuts.append(position);states.append(snapshot())
                    # The final chunk needs its starting modal state only.
                    # Its body (including validation) is handled by the worker.
                    if len(cuts)==workers:break
                if LAYER_MARKER.match(text):layer_number+=1
                lower=text.lower()
                if 'stealthchanger' in lower and ('print_start' in lower or 'toolchanger' in lower or 'tool change' in lower):stealth_start=True
                if lower.startswith('; feature:') or lower.startswith(';type:'):feature=lower.split(':',1)[1].strip()
                match=SETTINGS.match(text)
                if match:
                    config[match[1].lower()]=match[2]
                    if match[1].lower()=='filament_diameter':
                        try:diameters=[float(v) for v in match[2].replace(';',',').split(',')]
                        except ValueError:diameters=[]
                continue
            command=text.split(';',1)[0].strip().upper()
            if command.startswith('N'):command=LINE_NUMBER.sub('',command)
            match=COMMAND.match(command)
            if not match:continue
            code=match[1]
            if code=='G90':absolute_xyz=True
            elif code=='G91':flush_pending();absolute_xyz=False
            elif code=='M82':absolute_e=True
            elif code=='M83':absolute_e=False
            elif code=='G20':flush_pending();scale=Decimal('25.4')
            elif code=='G21':flush_pending();scale=Decimal(1)
            elif code in ('G17','G18','G19'):plane=code
            elif code=='G90.1':absolute_center=True
            elif code=='G91.1':absolute_center=False
            elif code in ('M104','M109','M140','M190','M141','M191'):
                value=SETPOINT.search(command)
                if value:
                    component='nozzle' if code in ('M104','M109') else 'bed' if code in ('M140','M190') else 'chamber'
                    setpoints[component]=float(value[1])
            elif code.startswith('T'):
                key=int(code[1:])
                if key>=255:continue
                last_tool=key;tool=key;epos.setdefault(key,zero)
            elif code=='G92':
                flush_pending()
                for axis,value in FIELDS.findall(command):
                    number=Decimal(value)*scale
                    if axis=='E':epos[tool]=number
                    elif axis in xyz:offset[axis]=xyz[axis]-number
            elif code in MOTION_CODES:
                fields=dict(FIELDS.findall(command))
                if 'F' in fields:pending_feed=fields['F']
                if 'E' in fields:
                    value=Decimal(fields['E'])*scale
                    delta=value-epos[tool] if absolute_e else value
                    epos[tool]=value if absolute_e else epos[tool]+value
                    if delta<0:retract[tool]=retract.get(tool,zero)-delta
                    elif delta>0:
                        debt=retract.get(tool,zero)
                        retract[tool]=debt-min(debt,delta) if debt else debt
                        if last_tool is None:last_tool=tool
                for axis in 'XYZ':
                    if axis in fields:
                        if absolute_xyz:pending_xyz[axis]=fields[axis]
                        else:xyz[axis]+=Decimal(fields[axis])*scale
    if len(cuts)<2:return None
    cuts.append(size)
    return [(cuts[i],cuts[i+1],states[i]) for i in range(len(states))]
