import unittest
import pandas as pd
from scripts.audit_target_records import join_monthly,raw_summary


class AuditTests(unittest.TestCase):
    def test_sign_and_date_concentration(self):
        rows=[{'정상출고량':str(q),'재고마감일':d,'_source':'test','_record_ordinal':i} for i,(q,d) in enumerate([(10,'20251001'),(-3,'20251001'),(5,'20251002'),(5,'20251002')])]
        s=raw_summary(rows)
        self.assertEqual(s['positive_sum'],20)
        self.assertEqual(s['signed_sum'],17)
        self.assertEqual(s['negative_records'],1)
        self.assertEqual(s['largest_date_share'],.5)
        self.assertEqual(s['duplicate_payload_records'],1)

    def test_invalid_is_reported(self):
        s=raw_summary([{'정상출고량':'bad','재고마감일':'20251001','_source':'test'}])
        self.assertEqual(s['invalid_numeric_records'],1)
        self.assertIsNone(s['largest_date_share'])

    def test_target_month_join_and_missing(self):
        p=pd.DataFrame(dict(institution_code=['01'],department=['02'],item_code=['03'],forecast_month=pd.to_datetime(['2025-10-01']),prediction=[7],target_usage=[10]))
        m=pd.DataFrame(dict(institution_code=['01'],department=['02'],item_code=['03'],year_month=pd.to_datetime(['2025-10-01']),model_demand_positive_sum=[10]))
        self.assertEqual(join_monthly(p,m).abs_error.iloc[0],3)
        m.year_month=pd.to_datetime(['2025-09-01'])
        with self.assertRaises(ValueError): join_monthly(p,m)

    def test_duplicate_month_rejected(self):
        p=pd.DataFrame(dict(institution_code=['1'],department=['2'],item_code=['3'],forecast_month=['2025-10'],prediction=[7],target_usage=[10]))
        m=pd.DataFrame(dict(institution_code=['1','1'],department=['2','2'],item_code=['3','3'],year_month=['2025-10','2025-10']))
        with self.assertRaises(pd.errors.MergeError): join_monthly(p,m)
