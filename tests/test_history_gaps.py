import importlib.util
from pathlib import Path
import unittest
import pandas as pd
s=importlib.util.spec_from_file_location('gap',Path(__file__).resolve().parents[1]/'scripts/audit_history_gaps.py')
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class Tests(unittest.TestCase):
    def test_previous_segment_distinct_from_new_item(self):
        f=pd.DataFrame(dict(history_months=[2,2,6],series_observation_count=[2,10,10]))
        short,prior=m.classify(f)
        self.assertEqual(short.tolist(),[True,True,False])
        self.assertEqual(prior.tolist(),[False,True,False])
if __name__=='__main__':unittest.main()
