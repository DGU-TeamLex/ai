import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from scripts.wait_then_tail_experiment import ready


class QueueTests(unittest.TestCase):
    def test_states(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)
            def state(stage):
                (p/'status.json').write_text(json.dumps({'stage':stage}))
            state('running')
            self.assertFalse(ready(p))
            state('failed')
            with self.assertRaises(RuntimeError): ready(p)
            state('completed')
            with self.assertRaises(RuntimeError): ready(p)
            (p/'test_results.json').write_text('{}')
            with patch('scripts.wait_then_tail_experiment.psutil.process_iter',return_value=[]), patch('scripts.wait_then_tail_experiment.psutil.virtual_memory',return_value=SimpleNamespace(available=8*1024**3)):
                self.assertTrue(ready(p))
            process=SimpleNamespace(info={'cmdline':['python','feature_expansion_experiment.py']},pid=-1)
            with patch('scripts.wait_then_tail_experiment.psutil.process_iter',return_value=[process]):
                self.assertFalse(ready(p))
