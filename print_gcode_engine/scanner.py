"""Streaming G-code facts, active filament identity and extrusion directions."""
import math
import re
import time
from decimal import Decimal, localcontext
from pathlib import Path
from .arcs import arc_metrics
from .process import ProcessMetrics

D=Decimal
NUMBER=r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)'
FIELDS=re.compile(r'([XYZEIJKRF])\s*('+NUMBER+r')')
LINE_NUMBER=re.compile(r'^N\d+\s*')
COMMAND=re.compile(r'([GMT]\d+(?:\.\d+)?)(?:\s|(?=[A-Z])|$)')
MOTION_CODES=frozenset(('G0','G1','G2','G3'))
SETTINGS=re.compile(r';\s*(filament_type|filament_colour|filament_settings_id|filament_density|filament_diameter|printer_model|printer_settings_id|printer_variant|nozzle_diameter|wall_loops|layer_height|sparse_infill_density|sparse_infill_pattern|nozzle_temperature|nozzle_temperature_initial_layer|bed_temperature|chamber_temperature)\s*[:=]\s*(.*)',re.I)
MASS_HEADER=re.compile(r';\s*(?:total filament (?:used|weight)|filament used)\s*\[g\]\s*[:=]\s*(.+)',re.I)
LAYER_HEADER=re.compile(r';\s*total layer number\s*:\s*(\d+)',re.I)
HEIGHT_HEADER=re.compile(r';\s*max_z_height\s*:\s*('+NUMBER+r')',re.I)
LAYER_MARKER=re.compile(r';\s*(?:LAYER:|LAYER_CHANGE\b|CHANGE_LAYER\b)',re.I)
TIME_HEADER=re.compile(r';\s*time\s*:',re.I)
TIME_VALUE=re.compile(r':\s*(\d+)')
SETPOINT=re.compile(r'[SR]\s*('+NUMBER+r')')
MASS_NUMBER=re.compile(NUMBER)

def plain(value):
    return None if value is None else format(value.normalize() if value else D('0'),'f')

def seconds(value):
    matches=re.findall(r'(\d+(?:\.\d+)?)\s*([dhms])',value.lower())
    return int(sum((D(v)*{'d':86400,'h':3600,'m':60,'s':1}[unit] for v,unit in matches),D(0))) if matches else None

def analyze(path,progress=None,cancelled=None):
    path=Path(path)
    with path.open('rb') as stream: magic=stream.read(4)
    if magic.startswith(b'PK'):
        from .package import analyze_package
        return analyze_package(path,progress,cancelled)
    with localcontext() as ctx:
        ctx.prec=50
        with path.open('rb') as stream: return scan(stream,path.stat().st_size,progress,cancelled)

def scan(raw,total,progress=None,cancelled=None):
    xyz={a:D(0) for a in 'XYZ'}; offset={a:D(0) for a in 'XYZ'}; bounds={a:[None,None] for a in 'XYZ'}
    epos={0:D(0)}; used={}; retract={}; tools=set(); tool=0; last_tool=None; changes=0; ignored=0
    absolute_xyz=True;absolute_e=True;scale=D(1);lines=0;layers=0;header_layers=None;max_z=None
    duration=None;model_time=None;grams=None;mass_values=[];warnings=[];sources={};config={};feature=None
    stealth_start=False
    reported=0;next_check=0;direction=[0.0,0.0,0.0];road_length=0.0;support_road_length=0.0
    bridge_road_length=0.0;overhang_road_length=0.0;brim_road_length=0.0
    first_model_z=None;first_model_bounds={axis:[None,None] for axis in 'XY'}
    plane='G17';absolute_center=False;arc_count=0;arc_excluded=0
    process=ProcessMetrics()
    layer_volume={};section_profile_limited=False
    last_absolute_motion=None
    while True:
        if lines%1024==0 and time.monotonic()>=next_check:
            if cancelled and cancelled():raise RuntimeError('ANALYSIS_CANCELLED')
            next_check=time.monotonic()+1
        binary=raw.readline(1024*1024+1)
        if not binary:break
        if len(binary)>1024*1024:raise ValueError('GCODE_LINE_TOO_LONG')
        if b'\x00' in binary:raise ValueError('BINARY_GCODE_NOT_SUPPORTED')
        lines+=1
        if progress and raw.tell()-reported>=4*1024*1024:
            reported=raw.tell();progress({'bytes_processed':reported,'total_bytes':total,'lines':lines})
        if absolute_xyz and absolute_e and binary==last_absolute_motion:
            continue
        last_absolute_motion=None
        text=binary.decode('utf-8','replace').strip()
        if text.startswith(';'):
            lower=text.lower()
            if 'stealthchanger' in lower and ('print_start' in lower or 'toolchanger' in lower or 'tool change' in lower):stealth_start=True
            if 'model printing time:' in lower:model_time=seconds(lower.split('model printing time:',1)[1].split(';')[0])
            if 'total estimated time:' in lower:
                duration=seconds(lower.split('total estimated time:',1)[1]);sources['duration_seconds']='GCODE_TOTAL_TIME_HEADER'
            elif duration is None and ('estimated printing time' in lower or 'estimated print time' in lower):
                duration=seconds(lower);sources['duration_seconds']='GCODE_HEADER'
            elif duration is None and TIME_HEADER.match(lower):
                match=TIME_VALUE.search(lower)
                if match:duration=int(match[1]);sources['duration_seconds']='GCODE_HEADER'
            match=MASS_HEADER.match(text)
            if match:
                parsed=[D(v) for v in MASS_NUMBER.findall(match[1])]
                if any(v<0 for v in parsed):raise ValueError('INVALID_FILAMENT_MASS')
                if lower.startswith('; total filament'):
                    grams=sum(parsed,D(0))
                else:
                    mass_values=parsed
                    if grams is None:grams=sum(parsed,D(0))
                sources['grams']='GCODE_HEADER'
            match=LAYER_HEADER.match(text)
            if match:header_layers=int(match[1])
            match=HEIGHT_HEADER.match(text)
            if match:max_z=D(match[1])
            if LAYER_MARKER.match(text):layers+=1
            if lower.startswith('; feature:') or lower.startswith(';type:'):feature=lower.split(':',1)[1].strip()
            match=SETTINGS.match(text)
            if match:
                config[match[1].lower()]=match[2]
                if match[1].lower()=='filament_diameter':
                    try:process.diameters=[float(v) for v in re.split(r'[,;]',match[2])]
                    except ValueError:process.diameters=[]
            continue
        command=text.split(';',1)[0].strip().upper()
        if command.startswith('N'):command=LINE_NUMBER.sub('',command)
        # Most slicer lines are whitespace-delimited motion commands.
        # Keep the regex fallback for compact commands, subcodes and checksums.
        if len(command)>2 and command[:2] in MOTION_CODES and command[2].isspace():
            code=command[:2]
        else:
            match=COMMAND.match(command)
            if not match:continue
            code=match[1]
        if code=='G90':absolute_xyz=True
        elif code=='G91':absolute_xyz=False
        elif code=='M82':absolute_e=True
        elif code=='M83':absolute_e=False
        elif code=='G20':scale=D('25.4')
        elif code=='G21':scale=D(1)
        elif code in ('G17','G18','G19'):plane=code
        elif code=='G90.1':absolute_center=True
        elif code=='G91.1':absolute_center=False
        elif code in ('M104','M109','M140','M190','M141','M191'):
            value=SETPOINT.search(command)
            if value:
                component='nozzle' if code in ('M104','M109') else 'bed' if code in ('M140','M190') else 'chamber'
                process.setpoint(component,float(value[1]))
        elif code.startswith('T'):
            key=int(code[1:])
            if key>=255:ignored+=1;continue
            if last_tool is not None and key!=last_tool:changes+=1
            last_tool=key;tool=key;tools.add(key);epos.setdefault(key,D(0))
        elif code=='G92':
            for axis,value in FIELDS.findall(command):
                number=D(value)*scale
                if axis=='E':epos[tool]=number
                elif axis in xyz:offset[axis]=xyz[axis]-number
        elif code in MOTION_CODES:
            fields=dict(FIELDS.findall(command));delta=D(0);deposited=D(0);before=xyz.copy()
            if 'F' in fields:process.feed=float(D(fields['F'])*scale)
            if 'E' in fields:
                value=D(fields['E'])*scale;delta=value-epos[tool] if absolute_e else value
                epos[tool]=value if absolute_e else epos[tool]+value
                if delta<0:retract[tool]=retract.get(tool,D(0))-delta
                if delta>0:
                    recovery=min(retract.get(tool,D(0)),delta);retract[tool]=retract.get(tool,D(0))-recovery
                    deposited=delta-recovery
                    used[tool]=used.get(tool,D(0))+deposited;tools.add(tool)
                    if last_tool is None:last_tool=tool
            for axis in 'XYZ':
                if axis in fields:
                    value=D(fields[axis])*scale;xyz[axis]=value+offset[axis] if absolute_xyz else xyz[axis]+value
            arc=None
            if code in ('G2','G3'):
                try:
                    arc=arc_metrics(before,xyz,fields,code=='G2',plane,scale,absolute_center,offset)
                    arc_count+=1
                except ValueError:
                    arc_excluded+=1
                    if 'ARC_GEOMETRY_INVALID_OR_UNSUPPORTED' not in warnings:warnings.append('ARC_GEOMETRY_INVALID_OR_UNSUPPORTED')
            feature_name=feature or ''
            support_feature=feature_name.startswith('support')
            auxiliary_feature=feature_name in ('brim','skirt')
            if deposited>0 and feature_name not in ('custom','prime tower','wipe tower'):
                if not support_feature and not auxiliary_feature and xyz['Z']>=0:
                    if 0<=tool<len(process.diameters) and process.diameters[tool]>0:
                        level=round(float(xyz['Z']),3)
                        if level in layer_volume or len(layer_volume)<20000:
                            diameter=process.diameters[tool]
                            layer_volume[level]=layer_volume.get(level,0.0)+float(deposited)*math.pi*(diameter/2)**2
                        else:section_profile_limited=True
                for axis in 'XYZ':
                    bounds[axis][0]=xyz[axis] if bounds[axis][0] is None else min(bounds[axis][0],xyz[axis],before[axis])
                    bounds[axis][1]=xyz[axis] if bounds[axis][1] is None else max(bounds[axis][1],xyz[axis],before[axis])
                    if arc:
                        lo,hi=arc['bounds'][axis]
                        bounds[axis][0]=min(bounds[axis][0],D(str(round(lo,9))))
                        bounds[axis][1]=max(bounds[axis][1],D(str(round(hi,9))))
                if not support_feature and not auxiliary_feature and code in ('G0','G1'):
                    vector=[float(xyz[a]-before[a]) for a in 'XYZ'];length=math.sqrt(sum(v*v for v in vector))
                    if length>0:
                        road_length+=length
                        if 'bridge' in feature_name:bridge_road_length+=length
                        if 'overhang' in feature_name:overhang_road_length+=length
                        if first_model_z is None or xyz['Z']<first_model_z:
                            first_model_z=xyz['Z'];first_model_bounds={axis:[None,None] for axis in 'XY'}
                        if xyz['Z']==first_model_z:
                            for axis in 'XY':
                                lo,hi=first_model_bounds[axis]
                                first_model_bounds[axis]=[min(before[axis],xyz[axis]) if lo is None else min(lo,before[axis],xyz[axis]),max(before[axis],xyz[axis]) if hi is None else max(hi,before[axis],xyz[axis])]
                        for i in range(3):direction[i]+=vector[i]*vector[i]/length
                        process.deposit(length,float(deposited),tool)
                elif support_feature and code in ('G0','G1'):
                    vector=[float(xyz[a]-before[a]) for a in 'XYZ'];length=math.sqrt(sum(v*v for v in vector))
                    if length>0:support_road_length+=length
                elif feature_name=='brim' and code in ('G0','G1'):
                    vector=[float(xyz[a]-before[a]) for a in 'XYZ'];length=math.sqrt(sum(v*v for v in vector))
                    if length>0:brim_road_length+=length
                elif not support_feature and not auxiliary_feature and arc:
                    road_length+=arc['length']
                    if 'bridge' in feature_name:bridge_road_length+=arc['length']
                    if 'overhang' in feature_name:overhang_road_length+=arc['length']
                    for i in range(3):direction[i]+=arc['moments'][i]
                    process.deposit(arc['length'],float(deposited),tool)
            if code in ('G0','G1') and absolute_xyz and absolute_e:
                last_absolute_motion=binary
    if progress:progress({'bytes_processed':total,'total_bytes':total,'lines':lines})
    if duration is None and model_time is not None:duration=model_time;sources['duration_seconds']='GCODE_MODEL_TIME_ONLY'
    if duration is None:warnings.append('PRINT_TIME_NOT_AVAILABLE')
    if grams is None:warnings.append('FILAMENT_MASS_NOT_AVAILABLE')
    if section_profile_limited:warnings.append('LAYER_VOLUME_PROFILE_LIMITED_TO_20000_HEIGHTS')
    support_ratio=support_road_length/(road_length+support_road_length) if road_length+support_road_length else None
    difficult_ratio=(support_road_length+bridge_road_length+overhang_road_length)/(road_length+support_road_length) if road_length+support_road_length else None
    first_sizes=[float(hi-lo) for lo,hi in first_model_bounds.values() if lo is not None and hi is not None and hi>lo]
    slenderness=float(max_z)/min(first_sizes) if max_z is not None and len(first_sizes)==2 and min(first_sizes)>0 else None
    risk_tier='HIGH' if (support_ratio is not None and support_ratio>.35) or (difficult_ratio is not None and difficult_ratio>.15) or (slenderness is not None and slenderness>5 and not brim_road_length) else 'MEDIUM' if support_road_length or bridge_road_length or overhang_road_length or (slenderness is not None and slenderness>3) else 'LOW'
    dimensions=[plain(hi-lo) if lo is not None else None for lo,hi in bounds.values()]
    sources['dimensions_mm']='EXTRUSION_TOOLPATH_ENVELOPE_REQUIRES_REVIEW'
    types=[s.strip(' \"').upper() for s in re.split(r'[,;]',config.get('filament_type',''))]
    colors=[s.strip(' \"') for s in re.split(r'[,;]',config.get('filament_colour',''))]
    actual=sorted(key for key,value in used.items() if value>0) or sorted(tools)
    usage=[]
    for ordinal,key in enumerate(actual):
        # Toolchanger profiles often list only installed/used tools, so T4
        # may be the fourth slot after T0/T1/T3 rather than a fifth entry.
        slot=ordinal if len(types)==len(actual) and max(actual)>=len(types) else key
        mass=grams if len(actual)==1 else mass_values[slot] if len(mass_values)==len(types) and slot<len(mass_values) else None
        usage.append({'tool_id':key,'material':types[slot] if slot<len(types) and types[slot] else 'UNKNOWN',
            'grams':plain(mass),'color':colors[slot] if slot<len(colors) else None,'source':'GCODE_ACTIVE_TOOL_AND_HEADER'})
    detected=sorted({u['material'] for u in usage if u['material']!='UNKNOWN'})
    if not detected:warnings.append('ACTIVE_FILAMENT_IDENTITY_NOT_AVAILABLE')
    model_text=' '.join(str(config.get(k,'')) for k in ('printer_model','printer_settings_id','printer_variant')).lower()
    # Slicers differ in which setting key they emit. Keep aliases here so a
    # G-code-only upload still gets a deterministic catalog model whenever the
    # slicer left an identifiable token in its header.
    detected_printer=next((key for key,name in [
        ('VORON_2_4','voron 2.4'),('VORON_2_4','voron2.4'),
        ('STEALTH','stealthchanger'),
        ('H2C','h2c'),('H2D','h2d'),('A1_MINI','a1 mini'),('A1','a1'),
        ('X1E','x1e'),('X1C','x1 carbon'),('P1S','p1s'),('P1P','p1p'),
        ('P2S','p2s'),('Q2','q2'),('Q1','q1'),('X_MAX3','x max 3'),
        ('X_PLUS3','x plus 3'),('K1_MAX','k1 max'),('K1C','k1c'),
        ('K1','k1'),('K2_PLUS','k2 plus'),('K2','k2'),('MK4S','mk4s'),
        ('MK4','mk4'),('MK3S_PLUS','mk3s'),('XL','prusa xl'),('MINI_PLUS','mini+')
    ] if name in model_text),None)
    if detected_printer is None and stealth_start:detected_printer='STEALTH'
    mixed=config.get('filament_is_mixed','').lower()
    active_ids=set(actual) if 'actual' in locals() else set(used)
    mixed_values=[v.strip() for v in re.split(r'[,;]',mixed)]
    is_mixed=any(i<len(mixed_values) and mixed_values[i].lower() in ('true','1') for i in active_ids)
    return {'duration_seconds':duration,'model_duration_seconds':model_time,'grams':plain(grams),
        'filament_mm':[plain(used.get(key,D(0))) for key in actual],'dimensions_mm':dimensions,'toolchanges':changes,
        'tool_ids':actual,'ignored_tool_control_commands':ignored,'layers':header_layers or layers,'observed_layer_markers':layers,
        'max_z_height_mm':plain(max_z),'warnings':warnings,'metric_sources':sources,'trust':'EXTERNAL_UNTRUSTED','lines':lines,
        'configuration':config,'material_usage':usage,'detected_materials':detected,'process_metrics':process.result(),
        'layer_volume_profile':{'basis':'POSITIVE_MODEL_EXTRUSION_VOLUME_BY_DEPOSITION_Z','unit':'mm3',
            'z_quantization_mm':0.001,'incomplete':section_profile_limited,
            'layers':[{'z_mm':z,'volume_mm3':round(volume,6)} for z,volume in sorted(layer_volume.items())]},
        'normal_output_color_count':len({u['color'] for u in usage if u['color']}) or None,
        'printer':detected_printer,'toolchanger_system':'STEALTHCHANGER' if (stealth_start or 'stealthchanger' in model_text) else None,
        'mmu_system':None,
        'multicolor_system':'VORTEK' if detected_printer in ('H2C','H2D') and ('multi_material' in config.get('single_extruder_multi_material','').lower() or config.get('filament_map_mode','').lower().startswith('auto')) else None,
        'full_spectrum_detected':is_mixed,
        'nozzle_diameter_mm':config.get('nozzle_diameter','').split(',')[0].strip(' \"') or None,
        'support_risk':{'support_road_length_mm':round(support_road_length,3),'model_road_length_mm':round(road_length,3),
            'bridge_road_length_mm':round(bridge_road_length,3),'overhang_road_length_mm':round(overhang_road_length,3),'brim_road_length_mm':round(brim_road_length,3),
            'support_ratio':round(support_ratio,6) if support_ratio is not None else None,
            'difficult_road_ratio':round(difficult_ratio,6) if difficult_ratio is not None else None,
            'first_layer_footprint_bbox_mm':first_sizes if len(first_sizes)==2 else None,
            'height_to_minimum_footprint_width':round(slenderness,3) if slenderness is not None else None,
            'tier':risk_tier,'basis':'GCODE_FEATURE_PATH_RATIOS_AND_FIRST_LAYER_FOOTPRINT_BBOX_HEURISTIC','failure_probability':None},
        'orientation':{'build_axis':'Z','road_direction_weights_xyz':[round(v/road_length,6) for v in direction] if road_length else None,
            'sampled_road_length_mm':round(road_length,3),'method':'LINE_AND_ANALYTIC_ARC_TANGENT_SECOND_MOMENT',
            'arc_count':arc_count,'excluded_arc_count':arc_excluded,'arc_excluded':arc_excluded>0}}
