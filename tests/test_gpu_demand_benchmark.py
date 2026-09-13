import importlib.util
from pathlib import Path
import unittest
import pandas as pd

spec = importlib.util.spec_from_file_location('gpu_benchmark', Path(__file__).parents[1]/'scripts/gpu_demand_benchmark.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class BenchmarkTests(unittest.TestCase):
    def test_metrics(self):
        m = module.metrics([10, 10], [8, 10])
        self.assertEqual(m['WAPE'], 10)
        self.assertEqual(m['BIAS_PCT'], -10)

    def test_zero_denominator(self):
        with self.assertRaises(ValueError):
            module.metrics([0], [1])

    def test_splits_follow_forecast_month(self):
        df = pd.DataFrame(dict(year_month=pd.to_datetime(['2018-01-01','2025-06-01','2025-07-01','2025-09-01']),
            forecast_month=pd.to_datetime(['2018-02-01','2025-07-01','2025-08-01','2025-10-01']),
            historical_training_eligible=[False,False,False,False]))
        train, valid, test = module.split_masks(df)
        self.assertEqual(train.tolist(), [False,True,False,False])
        self.assertEqual(valid.tolist(), [False,False,True,False])
        self.assertEqual(test.tolist(), [False,False,False,True])
        self.assertFalse((train & valid | train & test | valid & test).any())


if __name__ == '__main__':
    unittest.main()
