import importlib.util
from pathlib import Path
import unittest
import tempfile
import json
from datetime import datetime
import pyarrow as pa
import pyarrow.parquet as pq

s=importlib.util.spec_from_file_location('linker',Path(__file__).resolve().parents[1]/'scripts/link_ingredient_features.py')
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)


class LinkTests(unittest.TestCase):
    def test_exact_preserves_codes(self):
        d={};m.update(d,('a','001'),['0007','1','2','EA'])
        method,values,candidate=m.resolve(('a','001'),d,{})
        self.assertEqual(method,'exact_institution_item');self.assertEqual(values[0],'0007');self.assertEqual(candidate,[None]*4)

    def test_global_only_candidate(self):
        d={};m.update(d,'001',['0007','1','2','EA'])
        method,values,candidate=m.resolve(('a','001'),{},d)
        self.assertEqual(method,'global_code_candidate');self.assertEqual(values,[None]*4);self.assertEqual(candidate[0],'0007')

    def test_conflict_not_overridden(self):
        d={};m.update(d,('a','001'),['A','','','']);m.update(d,('a','001'),['B','','',''])
        broad={};m.update(broad,'001',['A','','',''])
        self.assertEqual(m.resolve(('a','001'),d,broad)[0],'exact_conflict')

    def test_attribute_conflict_is_missing(self):
        d={};m.update(d,('a','001'),['A','1','2','EA']);m.update(d,('a','001'),['A','1','2','BOX'])
        self.assertIsNone(m.resolve(('a','001'),d,{})[1][3])

    def test_missing_not_zero(self):
        self.assertEqual(m.resolve(('a','001'),{},{}),('unmatched',[None]*4,[None]*4))

    def test_full_join_preserves_rows_and_target(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            source=pa.table({'institution_code':['a','b','c'],'item_code':['001']*3,
                'forecast_month':[datetime(2025,5,1)]*3,'target_usage':[0.,10.,20.]})
            pq.write_table(source,root/'prepared.parquet')
            (root/'drug.DAT').write_text('약품코드|보건기관코드_en|성분코드|용도구분|약종류구분|약품단위1\n001|a|007|1|2|EA\n',encoding='utf-8')
            m.run(root/'prepared.parquet',root/'drug.DAT',root/'out')
            result=pq.read_table(root/'out/prepared_with_ingredients.parquet')
            self.assertTrue(result.select(source.column_names).equals(source))
            self.assertEqual(result['linked_ingredient_code'].to_pylist(),['007',None,None])
            self.assertEqual(result['candidate_ingredient_code'].to_pylist(),[None,'007','007'])
            self.assertFalse(any(result['ingredient_time_verified'].to_pylist()))
            with self.assertRaises(FileExistsError):m.run(root/'prepared.parquet',root/'drug.DAT',root/'out')


if __name__=='__main__':unittest.main()
