"""Attach undated ingredient evidence without inventing activity or temporal validity."""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

FIELDS=['성분코드','용도구분','약종류구분','약품단위1']
NAMES=['ingredient_code','usage_division','drug_kind','drug_unit']


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def write(path,obj):
    path.write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def update(records,key,values):
    if key not in records:records[key]=[set() for _ in FIELDS]
    for choices,value in zip(records[key],values):
        if value:choices.add(value)


def resolve(key,exact,global_records):
    local=exact.get(key)
    if local and len(local[0])>1:
        return 'exact_conflict',[None]*4,[None]*4
    if local and len(local[0])==1:
        values=[next(iter(s)) if len(s)==1 else None for s in local]
        return 'exact_institution_item',values,[None]*4
    broad=global_records.get(key[1])
    if broad and len(broad[0])==1 and all(len(s)<=1 for s in broad):
        return 'global_code_candidate',[None]*4,[next(iter(s)) if s else None for s in broad]
    return ('exact_missing_ingredient' if local else 'unmatched'),[None]*4,[None]*4


def run(prepared,drug,out):
    out.mkdir(parents=True,exist_ok=False)
    write(out/'status.json',{'stage':'collect_keys'})
    try:
        source=pq.ParquetFile(prepared)
        keys=set()
        for batch in source.iter_batches(batch_size=100000,columns=['institution_code','item_code']):
            keys.update(zip(batch.column(0).to_pylist(),batch.column(1).to_pylist()))
        wanted_drugs={k[1] for k in keys}
        exact={};broad={};rows=missing=0
        write(out/'status.json',{'stage':'scan_drug','source_pairs':len(keys)})
        with drug.open(encoding='utf-8-sig',newline='') as handle:
            reader=csv.DictReader(handle,delimiter='|')
            required=FIELDS+['약품코드','보건기관코드_en']
            if not set(required).issubset(reader.fieldnames or []):raise ValueError('Missing source columns')
            for row in reader:
                rows+=1
                if None in row or any(v is None for v in row.values()):raise ValueError('Malformed source row')
                key=(row['보건기관코드_en'].strip(),row['약품코드'].strip())
                values=[row[c].strip() for c in FIELDS]
                missing+=not bool(values[0])
                if key in keys:update(exact,key,values)
                if key[1] in wanted_drugs:update(broad,key[1],values)
        resolved={k:resolve(k,exact,broad) for k in keys}
        del exact,broad,keys
        counts=Counter();by_year={};written=0
        write(out/'status.json',{'stage':'attach'})
        writer=None
        try:
            for batch in source.iter_batches(batch_size=100000):
                table=pa.Table.from_batches([batch])
                pairs=zip(table['institution_code'].to_pylist(),table['item_code'].to_pylist())
                matches=[resolved[k] for k in pairs]
                methods=[m[0] for m in matches]
                counts.update(methods)
                years=table['forecast_month'].to_pylist()
                for year,method in zip(years,methods):
                    by_year.setdefault(str(year.year),Counter())[method]+=1
                table=table.append_column('ingredient_match_method',pa.array(methods,type=pa.string()))
                for i,name in enumerate(NAMES):
                    table=table.append_column('linked_'+name,pa.array([m[1][i] for m in matches],type=pa.string()))
                    table=table.append_column('candidate_'+name,pa.array([m[2][i] for m in matches],type=pa.string()))
                table=table.append_column('ingredient_time_verified',pa.array([False]*len(matches)))
                if writer is None:writer=pq.ParquetWriter(out/'prepared_with_ingredients.parquet',table.schema,compression='snappy')
                writer.write_table(table);written+=len(matches)
        finally:
            if writer:writer.close()
        result=pq.ParquetFile(out/'prepared_with_ingredients.parquet')
        if written!=source.metadata.num_rows or result.metadata.num_rows!=written:raise ValueError('Row count changed')
        # Compare every original value, including target/key columns, in bounded batches.
        for before,after in zip(source.iter_batches(batch_size=100000),result.iter_batches(batch_size=100000,columns=source.schema_arrow.names)):
            if not before.equals(after,check_metadata=False):raise ValueError('Original values changed')
        manifest={'prepared_source':str(prepared.resolve()),'prepared_sha256':digest(prepared),'drug_source':str(drug.resolve()),
            'drug_sha256':digest(drug),'drug_rows':rows,'drug_missing_ingredient_rows':missing,
            'output_rows':written,'unique_source_pairs':len(resolved),'pair_match_counts':dict(Counter(m[0] for m in resolved.values())),
            'row_match_counts':dict(counts),'row_match_counts_by_forecast_year':{k:dict(v) for k,v in by_year.items()},
            'original_columns_verified_equal':True,'temporal_status':'undated_snapshot_not_historically_verified',
            'clinical_substitution_verified':False,'clinical_activity_status':'no_verified_institution_activity_link',
            'vaccination_status':'no_verified_institution_month_link','training_started':False,
            'candidate_policy':'Global drug-code-only matches stored separately; not promoted into exact linked columns',
            'limitations':['Ingredient and attribute equality is not clinical interchangeability',
                'No collection/effective date in source; retrospective modeling would be exploratory',
                'Metadata may already inform existing normalization; incremental model value untested']}
        write(out/'manifest.json',manifest)
        write(out/'status.json',{'stage':'completed','rows':written})
        print(json.dumps(manifest,ensure_ascii=False,indent=2))
    except Exception as e:
        write(out/'status.json',{'stage':'failed','error':repr(e)})
        raise


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--prepared',type=Path,required=True);p.add_argument('--drug',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();run(a.prepared,a.drug,a.output)
