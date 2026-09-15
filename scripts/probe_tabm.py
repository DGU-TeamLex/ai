"""CPU compatibility probe only; synthetic tensors are NOT model performance data."""
import json
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'outputs/tabm_runtime'))
import torch
from tabm import TabM

def run():
    torch.set_num_threads(2)
    torch.manual_seed(42)
    model=TabM.make(n_num_features=74,d_out=1,k=4,n_blocks=2,d_block=128,dropout=.1)
    inputs=torch.randn(512,74)
    start=time.perf_counter()
    prediction=model(inputs)
    if tuple(prediction.shape)!=(512,4,1):raise ValueError('Unexpected prediction shape')
    # Average member losses, not loss of the averaged prediction (official training guidance).
    loss=prediction.abs().mean()
    loss.backward()
    if not all(p.grad is None or torch.isfinite(p.grad).all() for p in model.parameters()):raise ValueError('Nonfinite gradient')
    result=dict(device='cpu',threads=torch.get_num_threads(),shape=list(prediction.shape),
        elapsed_seconds=time.perf_counter()-start,torch_version=torch.__version__,
        synthetic_probe_only=True,real_data_training_started=False)
    print(json.dumps(result,indent=2))
    return result

if __name__=='__main__':run()
