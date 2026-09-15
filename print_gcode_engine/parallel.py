"""Bounded-file multiprocessing with modal checkpoints and ordered merging."""
from concurrent.futures import ProcessPoolExecutor, wait
from decimal import localcontext
from pathlib import Path
from queue import Empty
import multiprocessing
import re

from .checkpoint import segment_checkpoints
from .merge import merge_chunks
from .scanner import scan


def benefits_from_parallel(sample):
    """Bounded workload heuristic from VM benchmarks; linear files stay serial."""
    commands=re.finditer(rb'(?m)^\s*(?:N\d+\s*)?G([0123])(?=\s|[XYZIJKREF])',sample)
    total=arcs=0
    for command in commands:
        total+=1
        if command[1] in (b'2',b'3'):arcs+=1
    return total>=100 and arcs/total>=.08


class BoundedReader:
    def __init__(self,stream,start,end):
        self.stream=stream;self.start=start;self.end=end;stream.seek(start)
    def tell(self):return self.stream.tell()-self.start
    def readline(self,limit=-1):
        remaining=self.end-self.stream.tell()
        if remaining<=0:return b''
        return self.stream.readline(min(remaining,limit) if limit>=0 else remaining)


def _piece(path,start,end,state,index,event,updates):
    def progress(value):updates.put((index,value['bytes_processed'],value['lines']))
    with localcontext() as context:
        context.prec=50
        with Path(path).open('rb') as stream:
            return scan(BoundedReader(stream,start,end),end-start,progress,event.is_set,initial_state=state,include_internal=True)


def analyze_parallel_file(path,progress=None,cancelled=None,workers=4):
    path=Path(path);size=path.stat().st_size
    def checkpoint_progress(value):
        if progress:progress({'bytes_processed':int(value['bytes_processed']*.25),'total_bytes':size,'lines':value['lines'],'phase':'CHECKPOINT'})
    segments=segment_checkpoints(path,workers,cancelled=cancelled,progress=checkpoint_progress)
    if not segments:
        with localcontext() as context:
            context.prec=50
            with path.open('rb') as stream:return scan(stream,size,progress,cancelled)
    context=multiprocessing.get_context('spawn')
    with context.Manager() as manager:
        event=manager.Event();updates=manager.Queue()
        with ProcessPoolExecutor(max_workers=len(segments),mp_context=context) as pool:
            futures=[pool.submit(_piece,str(path),a,b,state,index,event,updates) for index,(a,b,state) in enumerate(segments)]
            counts={}
            try:
                while True:
                    if cancelled and cancelled():raise RuntimeError('ANALYSIS_CANCELLED')
                    done,pending=wait(futures,timeout=.25)
                    while True:
                        try:
                            index,processed,lines=updates.get_nowait();counts[index]=(processed,lines)
                        except Empty:break
                    if progress:
                        processed=sum(row[0] for row in counts.values())
                        progress({'bytes_processed':min(size,int(size*.25+processed*.75)),
                                  'total_bytes':size,'lines':sum(row[1] for row in counts.values()),'phase':'PARALLEL','workers':len(segments)})
                    for future in done:
                        if future.exception():raise future.exception()
                    if not pending:break
                result=merge_chunks([future.result() for future in futures])
            except BaseException:
                event.set()
                for future in futures:future.cancel()
                raise
    result['analysis_execution']={'mode':'MULTIPROCESS','workers':len(segments)}
    if progress:progress({'bytes_processed':size,'total_bytes':size,'lines':result['lines'],'phase':'COMPLETE','workers':len(segments)})
    return result
