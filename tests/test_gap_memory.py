import importlib.util
from pathlib import Path
import unittest
import pandas as pd
s=importlib.util.spec_from_file_location('gap',Path(__file__).resolve().parents[1]/'scripts/build_gap_memory.py')
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class Tests(unittest.TestCase):
    def sample(self):
        f=pd.DataFrame({c:['x']*4 for c in m.ITEM+m.META})
        f['year_month']=pd.to_datetime(['2025-01-01','2025-02-01','2025-04-01','2025-05-01'])
        f['model_demand_positive_sum']=[10,20,30,40];return f
    def test_past_only(self):
        f=self.sample();r=m.features(f)
        self.assertTrue(pd.isna(r.prior_segment_last.iloc[0]))
        self.assertEqual(r.prior_segment_last.iloc[2],20)
        self.assertEqual(r.prior_segment_mean.iloc[2],15)
        self.assertEqual(r.prior_segment_age_months.iloc[2],2)
        f.loc[3,'model_demand_positive_sum']=999
        pd.testing.assert_series_equal(r.iloc[2],m.features(f).iloc[2])
    def test_metadata_change_blocks(self):
        f=self.sample();f.loc[2:,'standard_item_unit_code']='other'
        self.assertTrue(m.features(f).prior_segment_last.isna().all())
    def test_old_gap_blocks(self):
        f=self.sample();f.loc[2:,'year_month']=pd.to_datetime(['2026-04-01','2026-05-01'])
        self.assertTrue(m.features(f).prior_segment_last.isna().all())
    def test_unknown_blocks(self):
        f=self.sample();f.standard_item_unit_code='UNKNOWN'
        self.assertTrue(m.features(f).prior_segment_last.isna().all())
if __name__=='__main__':unittest.main()
