import importlib.util
from pathlib import Path
import unittest
import numpy as np
import pandas as pd

s=importlib.util.spec_from_file_location('experiment',Path(__file__).resolve().parents[1]/'scripts/ingredient_wape_experiment.py')
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)


class ExperimentTests(unittest.TestCase):
    def test_metric(self):
        self.assertEqual(m.metric([10,10],[8,10])['WAPE'],10)
        self.assertEqual(m.metric([10,10],[8,10])['BIAS'],-10)

    def test_invalid(self):
        with self.assertRaises(ValueError):m.metric([0],[0])
        with self.assertRaises(ValueError):m.metric([1],[np.nan])

    def test_preprocessing_uses_training_only(self):
        f=pd.DataFrame({'code':['new',None],'num':[np.nan,4]})
        b=dict(columns=['code','num'],categories={'code':['known','__MISSING__']},medians={'num':2})
        x=m.transform(f,b)
        self.assertTrue(pd.isna(x.code.iloc[0]))
        self.assertEqual(x.num.tolist(),[2,4])

    def test_no_candidate_features(self):
        self.assertEqual(len(m.EXTRA),4)
        self.assertTrue(all(c.startswith('linked_') for c in m.EXTRA))

    def test_temporal_split(self):
        f=pd.DataFrame({'year_month':pd.to_datetime(['2018-01-01','2025-03-01','2025-04-01','2025-09-01']),
            'forecast_month':pd.to_datetime(['2018-02-01','2025-04-01','2025-05-01','2025-10-01']),
            'historical_training_eligible':[False,True,True,True]})
        tr,va=m.masks(f,'2025-04-01','2025-05-01','2025-06-01')
        self.assertEqual(tr.tolist(),[False,True,False,False])
        self.assertEqual(va.tolist(),[False,False,True,False])


if __name__=='__main__':unittest.main()
