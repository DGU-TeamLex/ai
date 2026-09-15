import importlib.util
from pathlib import Path
import unittest
import pandas as pd
s=importlib.util.spec_from_file_location('audit',Path(__file__).resolve().parents[1]/'scripts/audit_regime_errors.py')
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)

class Tests(unittest.TestCase):
    def frame(self):
        return pd.DataFrame(dict(lag_1=[0,10,10,10,10],target_usage=[5,0,30,4,10],rolling_mean_3=[1]*5,rolling_std_3=[1]*5,history_months=[6]*5,baseline=[5]*5))
    def test_hindsight_states(self):
        self.assertEqual(m.states(self.frame(),True).tolist(),['restart','stopped','surge','drop','other'])
    def test_origin_does_not_use_target(self):
        f=self.frame();before=m.states(f);f.target_usage=999
        self.assertEqual(before.tolist(),m.states(f).tolist())
    def test_share_sums(self):
        r=m.audit(self.frame())
        for axis in ['origin_state','realized_change_DIAGNOSTIC_ONLY','past_scale']:
            self.assertAlmostEqual(sum(v['error_share_pct'] for v in r[axis].values()),100)
    def test_zero_target(self):
        self.assertIsNone(m.metrics([0],[10])['WAPE'])

if __name__=='__main__':unittest.main()
