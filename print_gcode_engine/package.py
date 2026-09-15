import json
import re
import os
import shutil
import tempfile
from pathlib import Path
import xml.etree.ElementTree as ET
from decimal import Decimal, localcontext
from zipfile import ZipFile
from .scanner import scan, plain, NATIVE_SCANNER

def analyze_package(path,progress=None,cancelled=None):
    with ZipFile(path) as archive, localcontext() as ctx:
        ctx.prec=50
        entries=archive.infolist(); names={e.filename for e in entries}
        if len(entries)>10000 or len(names)!=len(entries):raise ValueError('ARCHIVE_MEMBER_LIMIT_OR_DUPLICATE')
        gcodes=[e for e in entries if e.filename.lower().endswith('.gcode')]
        if not gcodes:raise ValueError('MODEL_REQUIRES_SLICING_NO_EMBEDDED_GCODE')
        if sum(e.file_size for e in gcodes)>2*1024**3:raise ValueError('UNCOMPRESSED_GCODE_TOO_LARGE')
        def read(name):
            if name not in names:return None
            info=archive.getinfo(name)
            if info.file_size>2*1024**2:raise ValueError('ARCHIVE_METADATA_TOO_LARGE')
            return archive.read(info)
        raw_settings=read('Metadata/project_settings.config'); settings=json.loads(raw_settings) if raw_settings else {}
        xml=read('Metadata/slice_info.config')
        if xml and (b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper()):raise ValueError('UNSAFE_XML_METADATA')
        root=ET.fromstring(xml) if xml else None
        results=[]
        for info in gcodes:
            workers=max(1,min(8,int(os.getenv('GCODE_PARALLEL_WORKERS','1'))))
            scratch=os.getenv('GCODE_SCRATCH_DIR')
            if workers>1 and scratch and info.file_size>=8*1024*1024:
                from .parallel import benefits_from_parallel
                with archive.open(info) as stream:sample=stream.read(4*1024*1024)
                if not benefits_from_parallel(sample,size_bytes=info.file_size,native=NATIVE_SCANNER):scratch=None
            if scratch and workers>1 and info.file_size>=8*1024*1024:
                Path(scratch).mkdir(parents=True,exist_ok=True)
                if shutil.disk_usage(scratch).free<info.file_size+64*1024*1024:scratch=None
            else:scratch=None
            if scratch:
                from .parallel import analyze_parallel_file
                with tempfile.TemporaryDirectory(prefix='gcode-',dir=scratch) as directory:
                    extracted=Path(directory)/'plate.gcode';written=0
                    with archive.open(info) as stream,extracted.open('wb') as output:
                        while True:
                            if cancelled and cancelled():raise RuntimeError('ANALYSIS_CANCELLED')
                            block=stream.read(1024*1024)
                            if not block:break
                            output.write(block);written+=len(block)
                            if progress:progress({'bytes_processed':int(written*.1),'total_bytes':info.file_size,'lines':0,'phase':'EXTRACT'})
                    def mapped(value):
                        if progress:progress({**value,'bytes_processed':int(info.file_size*.1+value['bytes_processed']*.9)})
                    result=analyze_parallel_file(extracted,mapped,cancelled,workers)
            else:
                with archive.open(info) as stream:result=scan(stream,info.file_size,progress,cancelled)
            match=re.search(r'plate_(\d+)\.gcode$',info.filename,re.I);plate_id=match[1] if match else None
            result.update({'plate_id':plate_id,'archive_member':info.filename,'uncompressed_bytes':info.file_size})
            plate=None
            if root is not None:
                for candidate in root.findall('plate'):
                    meta={m.get('key'):m.get('value') for m in candidate.findall('metadata')}
                    if meta.get('index')==plate_id:plate=candidate;break
            if plate is not None:
                meta={m.get('key'):m.get('value') for m in plate.findall('metadata')}
                if meta.get('prediction'):
                    duration=int(meta['prediction'])
                    if result['duration_seconds'] is not None and result['duration_seconds']!=duration:result['warnings'].append('SLICER_TIME_METADATA_CONFLICT')
                    result['duration_seconds']=duration;result['metric_sources']['duration_seconds']='3MF_SLICE_INFO_ACTIVE_PLATE'
                usage=[]
                for f in plate.findall('filament'):
                    mass=Decimal(f.get('used_g','0'))
                    if not mass.is_finite() or mass<0:raise ValueError('INVALID_FILAMENT_MASS')
                    if mass==0:continue
                    usage.append({'tool_id':int(f.get('id','1'))-1,'material':f.get('type','UNKNOWN').upper(),'grams':plain(mass),
                        'color':f.get('color'),'source':'3MF_SLICE_INFO_ACTIVE_PLATE','used_for_object':f.get('used_for_object')=='true',
                        'used_for_support':f.get('used_for_support')=='true','sku_profile':None})
                if usage:
                    result['material_usage']=usage;result['grams']=plain(sum((Decimal(u['grams']) for u in usage),Decimal(0)))
                    result['detected_materials']=sorted({u['material'] for u in usage});result['tool_ids']=[u['tool_id'] for u in usage]
                    result['normal_output_color_count']=len({u['color'] for u in usage if u['used_for_object'] and u['color']}) or 1
                    result['metric_sources']['grams']='3MF_SLICE_INFO_USED_FILAMENT_SUM'
            if settings:
                model=' '.join(str(settings.get(k,'')) for k in ('printer_model','printer_settings_id','printer_variant'))
                lower=model.lower()
                for key,name in [('VORON_2_4','voron 2.4'),('H2C','h2c'),('H2D','h2d'),('A1_MINI','a1 mini'),('A1','a1'),('X1E','x1e'),('X1C','x1 carbon'),('P1S','p1s'),('P1P','p1p'),('P2S','p2s'),('Q2','q2'),('Q1','q1'),('X_MAX3','x max 3'),('X_PLUS3','x plus 3'),('K1_MAX','k1 max'),('K1C','k1c'),('K1','k1'),('K2_PLUS','k2 plus'),('K2','k2'),('MK4S','mk4s'),('MK4','mk4'),('MK3S_PLUS','mk3s'),('XL','prusa xl'),('MINI_PLUS','mini+')]:
                    if name in lower: result['printer']=key;break
                nozzles=settings.get('nozzle_diameter',[])
                if isinstance(nozzles,list) and nozzles:result['nozzle_diameter_mm']=str(nozzles[0])
                profiles=settings.get('filament_settings_id',[])
                for u in result['material_usage']:
                    if isinstance(profiles,list) and u['tool_id']<len(profiles):u['sku_profile']=profiles[u['tool_id']]
                for key in ('layer_height','wall_loops','sparse_infill_density','sparse_infill_pattern','nozzle_temperature','nozzle_temperature_initial_layer','bed_temperature','chamber_temperature','filament_density','filament_is_mixed','filament_mixed_components','filament_map_mode','single_extruder_multi_material','physical_extruder_map','extruder_type','extruder_variant_list','has_filament_switcher'):
                    if key in settings and key not in result['configuration']:result['configuration'][key]=settings[key]
                mixed=settings.get('filament_is_mixed',[])
                active_ids={int(u['tool_id']) for u in result.get('material_usage',[])}
                result['full_spectrum_detected']=bool(any(i<len(mixed) and str(mixed[i]).lower() in ('true','1') for i in active_ids))
                result['multicolor_system']='VORTEK' if result.get('printer') in ('H2C','H2D') and (str(settings.get('single_extruder_multi_material','')) in ('1','true') or 'auto' in str(settings.get('filament_map_mode','')).lower()) else None
            bbox_raw=read(f'Metadata/plate_{plate_id}.json') if plate_id else None
            if bbox_raw:
                bbox=json.loads(bbox_raw,parse_float=Decimal).get('bbox_all')
                if bbox and len(bbox)==4:
                    result['dimensions_mm']=[plain(Decimal(bbox[2])-Decimal(bbox[0])),plain(Decimal(bbox[3])-Decimal(bbox[1])),result['max_z_height_mm'] or result['dimensions_mm'][2]]
                    result['metric_sources']['dimensions_mm']='3MF_ACTIVE_PLATE_BBOX_AND_GCODE_MAX_Z'
            if result['detected_materials']:result['warnings']=[w for w in result['warnings'] if w!='ACTIVE_FILAMENT_IDENTITY_NOT_AVAILABLE']
            results.append(result)
        if len(results)==1:return {**results[0],'container':'SLICED_3MF','plate_count':1}
        return {'container':'SLICED_3MF','plate_count':len(results),'plates':results,'requires_plate_selection':True,
            'duration_seconds':None,'grams':None,'dimensions_mm':[None,None,None],'detected_materials':[],'material_usage':[],
            'warnings':['MULTIPLE_PLATES_SELECT_ONE_FOR_CALCULATION'],'trust':'EXTERNAL_UNTRUSTED'}
