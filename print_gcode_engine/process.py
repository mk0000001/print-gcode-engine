"""Bounded time-weighted histograms of commanded deposition conditions."""
import math

def update_range(current,value):
    if not math.isfinite(value):return
    if current['min'] is None or value<current['min']:current['min']=value
    if current['max'] is None or value>current['max']:current['max']=value
    current['last']=value

def empty_range():return {'min':None,'max':None,'last':None}

class Distribution:
    def __init__(self):
        self.bins=[0.0]*256
        self.maximum=None;self.minimum=None;self.weight=0.0
        self._last_value=None;self._last_index=0
    def add(self,value,weight):
        if value<=0 or weight<=0 or not math.isfinite(value+weight):return
        if value==self._last_value:
            index=self._last_index
        else:
            index=int(math.log2(value)*8)+128
            index=0 if index<0 else 255 if index>255 else index
            self._last_value=value;self._last_index=index
        self.bins[index]+=weight;self.weight+=weight
        if self.maximum is None or value>self.maximum:self.maximum=value
        if self.minimum is None or value<self.minimum:self.minimum=value
    def quantile(self,fraction):
        if not self.weight:return None
        total=0.0
        for index,weight in enumerate(self.bins):
            total+=weight
            if total>=self.weight*fraction:return round(2**((index-128+.5)/8),6)
    def snapshot(self):
        return {'bins':list(self.bins),'minimum':self.minimum,'maximum':self.maximum,'weight':self.weight}
    def merge(self,snapshot):
        bins=snapshot['bins']
        if len(bins)!=len(self.bins) or any(not math.isfinite(v) or v<0 for v in bins):raise ValueError('INVALID_DISTRIBUTION_SNAPSHOT')
        weight=snapshot['weight']
        if not math.isfinite(weight) or weight<0:raise ValueError('INVALID_DISTRIBUTION_SNAPSHOT')
        self.bins=[math.fsum((a,b)) for a,b in zip(self.bins,bins)]
        self.weight=math.fsum((self.weight,weight))
        if snapshot['minimum'] is not None:self.minimum=snapshot['minimum'] if self.minimum is None else min(self.minimum,snapshot['minimum'])
        if snapshot['maximum'] is not None:self.maximum=snapshot['maximum'] if self.maximum is None else max(self.maximum,snapshot['maximum'])
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
    def snapshot(self):
        return {'ranges':{name:getattr(self,name).copy() for name in ('nozzle','bed','chamber','deposition_nozzle')},
            'speeds':self.speeds.snapshot(),'flows':self.flows.snapshot(),'length':self.length,'seconds':self.seconds}
    def merge(self,snapshot):
        """Merge chunks in source order; never average per-chunk quantiles."""
        self.speeds.merge(snapshot['speeds']);self.flows.merge(snapshot['flows'])
        self.length=math.fsum((self.length,snapshot['length']))
        self.seconds=math.fsum((self.seconds,snapshot['seconds']))
        for name,incoming in snapshot['ranges'].items():
            current=getattr(self,name)
            if incoming['min'] is not None:current['min']=incoming['min'] if current['min'] is None else min(current['min'],incoming['min'])
            if incoming['max'] is not None:current['max']=incoming['max'] if current['max'] is None else max(current['max'],incoming['max'])
            if incoming['last'] is not None:current['last']=incoming['last']
