"""Analytic arc/helical bounds and tangent second moments, constant memory."""
import math

TAU=2*math.pi
PLANES={'G17':('X','Y','Z'),'G18':('Z','X','Y'),'G19':('Y','Z','X')}
CENTER={'X':'I','Y':'J','Z':'K'}

def _sweep(start,end,clockwise):
    delta=(end-start)%TAU
    if clockwise:return delta-TAU if delta>1e-12 else -TAU
    return delta if delta>1e-12 else TAU

def arc_metrics(start,end,fields,clockwise,plane='G17',scale=1,absolute_center=False,offsets=None):
    u,v,w=PLANES[plane];offsets=offsets or {axis:0 for axis in 'XYZ'}
    p={a:float(start[a]) for a in 'XYZ'};q={a:float(end[a]) for a in 'XYZ'}
    params={k:float(value)*float(scale) for k,value in fields.items() if k in 'IJKR'}
    if not all(math.isfinite(x) and abs(x)<1e9 for x in [*p.values(),*q.values(),*params.values()]):
        raise ValueError('ARC_NUMERIC_LIMIT')
    if 'R' in params:
        if any(k in params for k in 'IJK'):raise ValueError('ARC_MIXED_CENTER_FORMAT')
        radius=abs(params['R']);dx=q[u]-p[u];dy=q[v]-p[v];chord=math.hypot(dx,dy)
        if chord<1e-12 or radius==0 or chord>2*radius+1e-6:raise ValueError('ARC_INVALID_RADIUS')
        height=math.sqrt(max(0,radius*radius-chord*chord/4))
        mx=(p[u]+q[u])/2;my=(p[v]+q[v])/2
        candidates=[]
        for side in (1,-1):
            cx=mx-side*dy*height/chord;cy=my+side*dx*height/chord
            a=math.atan2(p[v]-cy,p[u]-cx);b=math.atan2(q[v]-cy,q[u]-cx)
            sweep=_sweep(a,b,clockwise)
            candidates.append((cx,cy,a,sweep))
        selected=[c for c in candidates if (abs(c[3])<=math.pi+1e-9 if params['R']>=0 else abs(c[3])>=math.pi-1e-9)]
        if not selected:raise ValueError('ARC_RADIUS_SELECTION_FAILED')
        cx,cy,a,sweep=selected[0]
    else:
        ku,kv=CENTER[u],CENTER[v]
        if ku not in params and kv not in params:raise ValueError('ARC_MISSING_CENTER')
        if absolute_center:
            if ku not in params or kv not in params:raise ValueError('ARC_ABSOLUTE_CENTER_INCOMPLETE')
            cx=params[ku]+float(offsets[u]);cy=params[kv]+float(offsets[v])
        else:
            cx=p[u]+params.get(ku,0);cy=p[v]+params.get(kv,0)
        radius=math.hypot(p[u]-cx,p[v]-cy);end_radius=math.hypot(q[u]-cx,q[v]-cy)
        if radius<1e-12 or abs(radius-end_radius)>max(.005,radius*.001):raise ValueError('ARC_CENTER_RADIUS_MISMATCH')
        a=math.atan2(p[v]-cy,p[u]-cx);b=math.atan2(q[v]-cy,q[u]-cx);sweep=_sweep(a,b,clockwise)
    angle=abs(sweep);dz=q[w]-p[w];length=math.hypot(radius*angle,dz)
    travel_per_radian=length/angle
    trig=(math.sin(2*(a+sweep))-math.sin(2*a))*(1 if sweep>0 else -1)/4
    moments={u:radius*radius/travel_per_radian*max(0,angle/2-trig),
             v:radius*radius/travel_per_radian*max(0,angle/2+trig),w:dz*dz/length}
    bounds={axis:[min(p[axis],q[axis]),max(p[axis],q[axis])] for axis in 'XYZ'}
    for cardinal in (0,math.pi/2,math.pi,3*math.pi/2):
        distance=(cardinal-a)%TAU if sweep>0 else (a-cardinal)%TAU
        if distance<=angle+1e-9:
            for axis,value in ((u,cx+radius*math.cos(cardinal)),(v,cy+radius*math.sin(cardinal))):
                bounds[axis][0]=min(bounds[axis][0],value);bounds[axis][1]=max(bounds[axis][1],value)
    return {'length':length,'moments':[moments[a] for a in 'XYZ'],'bounds':bounds}
