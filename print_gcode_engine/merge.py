"""Ordered merge of checkpoint-initialized scan chunks. Experimental."""
from copy import deepcopy
from decimal import Decimal, localcontext
from math import fsum
import re

from .process import ProcessMetrics
from .scanner import plain


def merge_chunks(chunks):
    if not chunks:raise ValueError('CHUNKS_REQUIRED')
    with localcontext() as context:
        context.prec=50
        return _merge(chunks)


def _merge(chunks):
    states=[row['_scan_state'] for row in chunks]
    result=deepcopy(chunks[-1]);result.pop('_scan_state',None)
    config=result['configuration']
    def latest(key):return next((row[key] for row in reversed(chunks) if row.get(key) is not None),None)
    explicit_times=[row for row in chunks if row.get('duration_seconds') is not None and row['metric_sources'].get('duration_seconds')!='GCODE_MODEL_TIME_ONLY']
    result['duration_seconds']=(explicit_times[-1]['duration_seconds'] if explicit_times else latest('duration_seconds'))
    result['model_duration_seconds']=latest('model_duration_seconds')
    totals=[row for row in chunks if row['_scan_state']['total_mass_seen']]
    result['grams']=totals[-1]['grams'] if totals else next((row['grams'] for row in chunks if row['grams'] is not None),None)
    result['metric_sources']={key:value for row in chunks for key,value in row['metric_sources'].items()}
    if explicit_times:result['metric_sources']['duration_seconds']=explicit_times[-1]['metric_sources']['duration_seconds']
    result['max_z_height_mm']=latest('max_z_height_mm')
    for key in ('lines','toolchanges','ignored_tool_control_commands','observed_layer_markers'):
        result[key]=sum(row[key] for row in chunks)
    header_layers=next((state['header_layers'] for state in reversed(states) if state['header_layers'] is not None),None)
    result['layers']=header_layers or result['observed_layer_markers']
    dimensions=[]
    for axis in 'XYZ':
        valid=[state['deposition_bounds'][axis] for state in states if state['deposition_bounds'][axis][0] is not None]
        dimensions.append(plain(max(b[1] for b in valid)-min(b[0] for b in valid)) if valid else None)
    result['dimensions_mm']=dimensions
    used={};seen=set()
    for state in states:
        seen.update(state['seen_tools'])
        for tool,value in state['used'].items():used[tool]=used.get(tool,Decimal(0))+value
    active=sorted(tool for tool,value in used.items() if value>0) or sorted(seen)
    result['tool_ids']=active;result['filament_mm']=[plain(used.get(tool,Decimal(0))) for tool in active]
    types=[s.strip(' \"').upper() for s in re.split(r'[,;]',config.get('filament_type',''))]
    colors=[s.strip(' \"') for s in re.split(r'[,;]',config.get('filament_colour',''))]
    mass_values=next((state['mass_values'] for state in reversed(states) if state['mass_values']),[])
    usage=[]
    for ordinal,tool in enumerate(active):
        slot=ordinal if len(types)==len(active) and max(active)>=len(types) else tool
        mass=result['grams'] if len(active)==1 else plain(mass_values[slot]) if len(mass_values)==len(types) and slot<len(mass_values) else None
        usage.append({'tool_id':tool,'material':types[slot] if slot<len(types) and types[slot] else 'UNKNOWN',
            'grams':mass,'color':colors[slot] if slot<len(colors) else None,'source':'GCODE_ACTIVE_TOOL_AND_HEADER'})
    result['material_usage']=usage
    result['detected_materials']=sorted({row['material'] for row in usage if row['material']!='UNKNOWN'})
    result['normal_output_color_count']=len({row['color'] for row in usage if row['color']}) or None
    mixed=[v.strip().lower() for v in re.split(r'[,;]',config.get('filament_is_mixed',''))]
    result['full_spectrum_detected']=any(tool<len(mixed) and mixed[tool] in ('true','1') for tool in active)
    process=ProcessMetrics()
    for state in states:process.merge(state['process_snapshot'])
    result['process_metrics']=process.result()
    volumes={}
    for state in states:
        for z,volume in state['layer_volume'].items():volumes.setdefault(z,[]).append(volume)
    if len(volumes)>20000 or any(row['layer_volume_profile']['incomplete'] for row in chunks):raise ValueError('PARALLEL_PROFILE_LIMIT')
    result['layer_volume_profile']['layers']=[{'z_mm':z,'volume_mm3':round(fsum(v),6)} for z,v in sorted(volumes.items())]
    road=fsum(state['road_length_mm'] for state in states)
    moments=[fsum(state['road_direction_moments_xyz'][i] for state in states) for i in range(3)]
    orientation=result['orientation']
    orientation['road_direction_weights_xyz']=[round(v/road,6) for v in moments] if road else None
    orientation['sampled_road_length_mm']=round(road,3)
    for key in ('arc_count','excluded_arc_count'):orientation[key]=sum(row['orientation'][key] for row in chunks)
    orientation['arc_excluded']=orientation['excluded_arc_count']>0
    support,bridge,overhang,brim=[fsum(state['risk_lengths'][i] for state in states) for i in range(4)]
    first_z=min((state['first_model_z'] for state in states if state['first_model_z'] is not None),default=None)
    first_states=[state for state in states if first_z is not None and state['first_model_z']==first_z]
    sizes=[]
    for axis in 'XY':
        valid=[state['first_model_bounds'][axis] for state in first_states if state['first_model_bounds'][axis][0] is not None]
        if valid:
            width=max(b[1] for b in valid)-min(b[0] for b in valid)
            if width>0:sizes.append(float(width))
    slender=float(result['max_z_height_mm'])/min(sizes) if result['max_z_height_mm'] is not None and len(sizes)==2 else None
    ratio=support/(road+support) if road+support else None
    difficult=(support+bridge+overhang)/(road+support) if road+support else None
    tier='HIGH' if (ratio is not None and ratio>.35) or (difficult is not None and difficult>.15) or (slender is not None and slender>5 and not brim) else 'MEDIUM' if support or bridge or overhang or (slender is not None and slender>3) else 'LOW'
    result['support_risk'].update({'support_road_length_mm':round(support,3),'model_road_length_mm':round(road,3),
        'bridge_road_length_mm':round(bridge,3),'overhang_road_length_mm':round(overhang,3),'brim_road_length_mm':round(brim,3),
        'support_ratio':round(ratio,6) if ratio is not None else None,'difficult_road_ratio':round(difficult,6) if difficult is not None else None,
        'first_layer_footprint_bbox_mm':sizes if len(sizes)==2 else None,'height_to_minimum_footprint_width':round(slender,3) if slender is not None else None,'tier':tier})
    warnings=list(dict.fromkeys(warning for row in chunks for warning in row['warnings']))
    for code,present in [('PRINT_TIME_NOT_AVAILABLE',result['duration_seconds'] is not None),('FILAMENT_MASS_NOT_AVAILABLE',result['grams'] is not None),('ACTIVE_FILAMENT_IDENTITY_NOT_AVAILABLE',bool(result['detected_materials']))]:
        if present:warnings=[warning for warning in warnings if warning!=code]
    result['warnings']=warnings
    return result
