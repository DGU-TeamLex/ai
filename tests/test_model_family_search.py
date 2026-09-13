import importlib.util
from pathlib import Path
import unittest
import subprocess
import sys
import numpy as np
import pandas as pd

spec = importlib.util.spec_from_file_location('search',Path(__file__).parents[1]/'scripts/model_family_search.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class SearchTests(unittest.TestCase):
    def test_bias_and_wape(self):
        self.assertEqual(m.metric([10,10],[12,8])['WAPE'],20)
        self.assertEqual(m.metric([10,10],[12,8])['BIAS_PCT'],0)

    def test_missing_is_not_zero(self):
        frame = pd.DataFrame({'series_segment_id':[1,1,2], 'lag_1':[10,20,30]})
        ctx = m.contexts_from_frame(frame,3)
        np.testing.assert_equal(ctx,[[np.nan,np.nan,10],[np.nan,10,20],[np.nan,np.nan,30]])

    def test_context_excludes_future(self):
        frame = pd.DataFrame({'series_segment_id':[1,1,1], 'lag_1':[1,2,999]})
        before = m.contexts_from_frame(frame,3)[1]
        frame.loc[2,'lag_1']=12345
        np.testing.assert_equal(before,m.contexts_from_frame(frame,3)[1])

    def test_no_partial_score(self):
        with self.assertRaises(ValueError):
            m.metric([1,2],[1,np.nan])

    def test_neural_id_order(self):
        f=m.make_neural_frame([np.array([1.,2.]),np.array([3.])],2)
        self.assertEqual(f.unique_id.tolist(),[0,0,1])
        self.assertEqual(f.y.tolist(),[.5,1,1.5])


    def test_owned_process_termination(self):
        child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])
        try:
            m.stop_owned_tree(child)
            self.assertIsNotNone(child.poll())
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()


if __name__=='__main__':
    unittest.main()
