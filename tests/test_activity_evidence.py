import importlib.util
from pathlib import Path
import unittest

s=importlib.util.spec_from_file_location('intake',Path(__file__).resolve().parents[1]/'scripts/validate_activity_evidence.py')
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)


class IntakeTests(unittest.TestCase):
    def row(self):
        return dict(institution_code='anon',department='d',available_at='2025-07-31T12:00:00+09:00',
            evidence_reference='test_fixture',period_start='2025-07-01',period_end='2025-07-30',
            activity_type='vaccination',record_type='actual',count=0,coverage_status='complete')

    def check(self,row):
        return m.validate_row(row,'activity',m.timestamp('2025-07-31T23:59:59+09:00'),{'anon'})

    def test_zero_valid_missing_invalid(self):
        r=self.row();self.assertEqual(self.check(r),[])
        r['count']=None;self.assertIn('count_must_be_nonnegative_integer',self.check(r))

    def test_late_release(self):
        r=self.row();r['available_at']='2025-08-01T00:00:00+09:00'
        self.assertIn('unavailable_at_forecast_origin',self.check(r))

    def test_partial_is_not_zero(self):
        r=self.row();r['coverage_status']='partial'
        self.assertIn('incomplete_coverage_not_zero',self.check(r))

    def test_no_personal_columns(self):
        r=self.row();r['patient_name']='test'
        self.assertIn('unexpected_fields_remove_personal_data',self.check(r))

    def test_unknown_institution(self):
        r=self.row();r['institution_code']='unknown'
        self.assertIn('unknown_institution',self.check(r))

    def test_timezone_and_future_actual(self):
        r=self.row();r['available_at']='2025-07-31T12:00:00'
        self.assertIn('invalid_date_or_timestamp',self.check(r))
        r=self.row();r['period_end']='2025-08-31'
        self.assertIn('actual_before_period_end',self.check(r))

    def test_planned_future_period_allowed(self):
        r=self.row();r.update(record_type='planned',period_start='2025-08-01',period_end='2025-08-31')
        self.assertEqual(self.check(r),[])

    def test_automatic_approval_not_event_confirmation(self):
        r=dict(institution_code='anon',department='d',available_at='2025-07-31T12:00:00+09:00',
            evidence_reference='test_fixture',event_date='2025-07-30',from_item_code='a',to_item_code='b',
            quantity=1,unit_code='EA',confirmation_role='automated_pipeline')
        self.assertIn('human_evidence_required',m.validate_row(r,'substitution',m.timestamp('2025-08-01T00:00:00Z'),{'anon'}))


if __name__=='__main__':unittest.main()
