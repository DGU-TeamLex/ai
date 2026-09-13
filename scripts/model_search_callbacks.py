"""Training progress callback, importable when loading saved neural models."""
import json
from pathlib import Path
import time
from pytorch_lightning.callbacks import Callback


class SearchProgress(Callback):
    def __init__(self, output, name):
        self.output = str(output)
        self.name = name

    def on_train_batch_end(self, trainer, pl_module, outputs, batch, batch_idx):
        if trainer.global_step % 100:
            return
        value = {'model':self.name, 'stage':'training', 'step':trainer.global_step,
                 'max_steps':trainer.max_steps, 'timestamp':time.time()}
        target = Path(self.output)/f'{self.name}_status.json'
        tmp = target.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(value,indent=2),encoding='utf-8')
        tmp.replace(target)
        print(json.dumps(value),flush=True)
