import importlib.util
from pathlib import Path
import unittest
import numpy as np
import pandas as pd

s=importlib.util.spec_from_file_location('experiment',Path(__file__).resolve().parents[1]/'scripts/gap_wape_experiment.py')
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)

class Tests(unittest.TestCase):
    def test_metric(self):
        self.assertEqual(m.metric([10,10],[8,10])['WAPE'],10)
        self.assertEqual(m.metric([10,10],[8,10])['BIAS'],-10)

    def test_invalid(self):
        with self.assertRaises(ValueError):m.metric([1],[np.nan])

    def test_missing_history_not_imputed(self):
        c=m.EXTRA[0]
        f=pd.DataFrame({c:[np.nan,12.],'other':[np.nan,4.]})
        b=dict(columns=list(f),categories={},medians={c:20.,'other':2.})
        x=m.transform(f,b)
        self.assertTrue(pd.isna(x[c].iloc[0]))
        self.assertEqual(x.other.tolist(),[2.,4.])

    def test_fixed_features(self):
        self.assertEqual(m.EXTRA,['prior_segment_last','prior_segment_mean','prior_segment_count','prior_segment_age_months'])

    def test_temporal_split(self):
        f=pd.DataFrame({'year_month':pd.to_datetime(['2018-01-01','2025-03-01','2025-04-01']),
          'forecast_month':pd.to_datetime(['2018-02-01','2025-04-01','2025-05-01']),
          'historical_training_eligible':[False,True,True]})
        tr,va=m.masks(f,'2025-04-01','2025-05-01','2025-06-01')
        self.assertEqual(tr.tolist(),[False,True,False])
        self.assertEqual(va.tolist(),[False,False,True])

if __name__=='__main__':unittest.main()
