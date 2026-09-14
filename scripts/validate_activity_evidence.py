"""Read-only intake validation. Passing syntax is not clinical confirmation."""
import argparse
from datetime import date, datetime
import json
import math
from pathlib import Path

COMMON={'institution_code','department','available_at','evidence_reference'}
ACTIVITY=COMMON|{'period_start','period_end','activity_type','record_type','count','coverage_status'}
SUBSTITUTION=COMMON|{'event_date','from_item_code','to_item_code','quantity','unit_code','confirmation_role'}


def timestamp(value):
    t=datetime.fromisoformat(value.replace('Z','+00:00'))
    if t.tzinfo is None:
        raise ValueError('timezone_required')
    return t


def number(value,integer=False):
    return (isinstance(value,(int,float)) and not isinstance(value,bool)
            and math.isfinite(value) and value>=0 and (not integer or int(value)==value))


def validate_row(row,kind,as_of,known):
    fields=ACTIVITY if kind=='activity' else SUBSTITUTION
    if not isinstance(row,dict):
        return ['object_required']
    errors=[]
    if fields-set(row): errors.append('required_fields_missing')
    if set(row)-fields: errors.append('unexpected_fields_remove_personal_data')
    for field in fields-({'count'} if kind=='activity' else {'quantity'}):
        if not isinstance(row.get(field),str) or not row[field].strip():
            errors.append('blank_or_nontext:'+field)
    if errors: return errors
    if known is None: errors.append('institution_link_not_verified')
    elif row['institution_code'] not in known: errors.append('unknown_institution')
    try:
        available=timestamp(row['available_at'])
        if available>as_of: errors.append('unavailable_at_forecast_origin')
        if kind=='activity':
            start,end=date.fromisoformat(row['period_start']),date.fromisoformat(row['period_end'])
            if start>end: errors.append('reversed_period')
            if row['record_type'] not in {'actual','planned'}: errors.append('invalid_record_type')
            if row['coverage_status']!='complete': errors.append('incomplete_coverage_not_zero')
            if not number(row['count'],True): errors.append('count_must_be_nonnegative_integer')
            if row['record_type']=='actual' and end>available.date(): errors.append('actual_before_period_end')
        else:
            event=date.fromisoformat(row['event_date'])
            if event>available.date(): errors.append('event_after_available_at')
            if row['from_item_code']==row['to_item_code']: errors.append('same_item_not_substitution')
            if not number(row['quantity']): errors.append('quantity_must_be_nonnegative')
            if row['confirmation_role'] not in {'institution_staff','qualified_reviewer'}:
                errors.append('human_evidence_required')
    except (ValueError,TypeError,OverflowError):
        errors.append('invalid_date_or_timestamp')
    return errors


def validate_file(path,kind,as_of,known):
    if path is None:
        return {'status':'missing','rows':0,'valid_rows':0,'errors':{}}
    counts={};total=valid=0;seen=set()
    with path.open(encoding='utf-8-sig') as handle:
        for line in handle:
            if not line.strip(): continue
            total+=1
            try:
                row=json.loads(line)
                issues=validate_row(row,kind,as_of,known)
                if not issues:
                    keys=['institution_code','department','available_at']+(
                        ['period_start','period_end','activity_type','record_type'] if kind=='activity' else
                        ['event_date','from_item_code','to_item_code','evidence_reference'])
                    key=tuple(row[k] for k in keys)
                    if key in seen: issues.append('duplicate_key')
                    seen.add(key)
            except json.JSONDecodeError:
                issues=['invalid_json']
            if not issues: valid+=1
            for issue in issues: counts[issue]=counts.get(issue,0)+1
    return {'status':'valid_structure' if total>0 and not counts else 'blocked',
            'rows':total,'valid_rows':valid,'errors':counts}


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--activity',type=Path)
    p.add_argument('--substitution',type=Path)
    p.add_argument('--institution-codes',type=Path,help='JSON list of authorized existing anonymous institution codes')
    p.add_argument('--as-of',required=True,help='Historical forecast timestamp with timezone')
    a=p.parse_args();origin=timestamp(a.as_of)
    known=None
    if a.institution_codes:
        codes=json.loads(a.institution_codes.read_text(encoding='utf-8-sig'))
        if not isinstance(codes,list) or not all(isinstance(c,str) and c.strip() for c in codes):
            raise ValueError('Expected nonempty string codes in JSON list')
        known=set(codes)
    results={kind:validate_file(getattr(a,kind),kind,origin,known) for kind in ['activity','substitution']}
    print(json.dumps({'as_of':origin.isoformat(),'results':results,
        'clinical_confirmation':'not_established_by_this_validator',
        'training_started':False},ensure_ascii=False,indent=2))
    return 0 if all(r['status']=='valid_structure' for r in results.values()) else 2


if __name__=='__main__':raise SystemExit(main())
