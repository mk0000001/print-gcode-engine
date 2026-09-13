"""Bounded time-weighted histograms of commanded deposition conditions."""
import math

def update_range(current,value):
    if not math.isfinite(value):return
    current['min']=value if current['min'] is None else min(current['min'],value)
    current['max']=value if current['max'] is None else max(current['max'],value)
    current['last']=value

def empty_range():return {'min':None,'max':None,'last':None}

class Distribution:
    def __init__(self):
        self.bins=[0.0]*256
        self.maximum=None;self.minimum=None;self.weight=0.0
    def add(self,value,weight):
        if value<=0 or weight<=0 or not math.isfinite(value+weight):return
        index=min(255,max(0,int(math.log2(value)*8)+128))
        self.bins[index]+=weight;self.weight+=weight
        self.maximum=value if self.maximum is None else max(self.maximum,value)
        self.minimum=value if self.minimum is None else min(self.minimum,value)
    def quantile(self,fraction):
        if not self.weight:return None
        total=0.0
        for index,weight in enumerate(self.bins):
            total+=weight
            if total>=self.weight*fraction:return round(2**((index-128+.5)/8),6)
    def result(self):
        return {'min':self.minimum,'max':self.maximum,'p50_approx':self.quantile(.5),'p95_approx':self.quantile(.95),
            'quantile_method':'TIME_WEIGHTED_LOG2_HISTOGRAM_8_BINS_PER_OCTAVE'}

class ProcessMetrics:
    def __init__(self):
        self.nozzle=empty_range();self.bed=empty_range();self.chamber=empty_range()
        self.deposition_nozzle=empty_range();self.speeds=Distribution();self.flows=Distribution()
        self.length=0.0;self.seconds=0.0;self.feed=0.0;self.diameters=[]
    def setpoint(self,component,value):update_range(getattr(self,component),value)
    def deposit(self,length,extruded_mm,tool):
        if length<=0 or self.feed<=0 or extruded_mm<=0:return
        speed=self.feed/60;duration=length/speed
        self.length+=length;self.seconds+=duration;self.speeds.add(speed,duration)
        if self.nozzle['last'] is not None:update_range(self.deposition_nozzle,self.nozzle['last'])
        if 0<=tool<len(self.diameters):
            diameter=self.diameters[tool]
            if diameter>0:self.flows.add(extruded_mm*math.pi*(diameter/2)**2/duration,duration)
    def result(self):
        return {'nozzle_setpoint_c':self.nozzle,'bed_setpoint_c':self.bed,'chamber_setpoint_c':self.chamber,
            'deposition_nozzle_setpoint_c':self.deposition_nozzle,'measured_nozzle_temperature_c':None,
            'commanded_speed_mm_s':self.speeds.result(),'volumetric_flow_mm3_s':self.flows.result(),
            'deposition_length_mm':self.length,'nominal_deposition_seconds':self.seconds,
            'speed_basis':'COMMANDED_FEEDRATE_NOT_MEASURED','duration_basis':'PATH_LENGTH_OVER_FEEDRATE_EXCLUDES_ACCELERATION',
            'material_volume_basis':'POSITIVE_EXTRUSION_AFTER_RETRACTION_RECOVERY_WITH_CONFIGURED_DIAMETER'}
