"""Wait for the prior suite; do not restart failed jobs or overlap training."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import psutil


def ready(prior):
    state = json.loads((prior/'status.json').read_text(encoding='utf-8'))
    if state['stage'] in ('failed', 'paused'):
        raise RuntimeError(f'Prior experiment requires review: {state}')
    if state['stage'] != 'completed':
        return False
    if not (prior/'test_results.json').exists():
        raise RuntimeError('Completed status without final results')
    for process in psutil.process_iter(['cmdline']):
        try:
            command = ' '.join(process.info['cmdline'] or [])
            if 'feature_expansion_experiment.py' in command and process.pid != os.getpid():
                return False
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False
    return psutil.virtual_memory().available >= 4*1024**3


if __name__ == '__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--prior',type=Path,required=True)
    p.add_argument('--source',required=True)
    p.add_argument('--reference',required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    # Exclusive queue marker prevents duplicate launches for this output.
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.with_suffix('.queue.lock').open('x') as marker:
        marker.write(str(os.getpid()))
    began=time.monotonic()
    print('Waiting for prior suite to complete; timeout 12 hours',flush=True)
    while not ready(args.prior):
        if time.monotonic()-began > 43200:
            raise RuntimeError('Queue wait timed out; no training launched')
        time.sleep(30)
    env=dict(os.environ,OMP_NUM_THREADS='2',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1')
    command=[sys.executable,'-u',str(Path(__file__).with_name('feature_expansion_experiment.py')),
             'suite','--source',args.source,'--reference',args.reference,'--output',str(args.output)]
    print('Prior suite completed; starting tail-feature experiment',flush=True)
    subprocess.run(command,check=True,env=env)
