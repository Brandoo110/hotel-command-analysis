#!/usr/bin/env python3
"""Group command failures without conflating missing receipts and timeouts."""
import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from audit import audit_records, parse_time


def prepare_commands(records):
    audit_records(records)  # Fail on malformed data or conflicting duplicate IDs.
    unique = {row['cmd_id']: row for row in records}
    rows = []
    for source in unique.values():
        row = source.copy()
        row['request_time'] = parse_time(row['requested_at'])
        row['heartbeat_age_minutes'] = (row['request_time']-parse_time(row['last_heartbeat_at'])).total_seconds()/60
        row['outcome'] = int(row['is_success']) if row['is_success'] else None
        row['signal_quality'] = float(row['signal_quality'])
        row['payload_bytes'] = int(row['payload_bytes'])
        rows.append(row)
    return sorted(rows, key=lambda row:(row['request_time'],row['cmd_id']))


def load_commands(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as handle:
        return prepare_commands(list(csv.DictReader(handle)))


def summarize(rows):
    counts = Counter(row['outcome'] for row in rows)
    known = counts[0]+counts[1]
    n = len(rows)
    return dict(commands=n, success=counts[1], timeout=counts[0], unknown=counts[None],
                known_outcomes=known, known_failure_rate=counts[0]/known if known else None,
                failure_rate_bounds=[counts[0]/n,(counts[0]+counts[None])/n] if n else None)


def grouped(rows, key):
    groups = defaultdict(list)
    for row in rows:
        groups[key(row)].append(row)
    return {name:summarize(group) for name,group in sorted(groups.items())}


def age_group(row):
    return '<15' if row['heartbeat_age_minutes'] < 15 else '>=15'


def diagnose(rows):
    return dict(
        overall={'all':summarize(rows)},
        heartbeat_age=grouped(rows,age_group),
        property=grouped(rows,lambda r:r['hotel_property']),
        property_heartbeat=grouped(rows,lambda r:r['hotel_property']+' / '+age_group(r)),
        device_category=grouped(rows,lambda r:r['device_category']),
        signal_quality=grouped(rows,lambda r:f"[{int(r['signal_quality']//5)*5},{int(r['signal_quality']//5)*5+5})"),
        payload_bytes=grouped(rows,lambda r:f"[{r['payload_bytes']//20*20},{r['payload_bytes']//20*20+20})"),
        heartbeat_signal=grouped(rows,lambda r:age_group(r)+(' / signal<10' if r['signal_quality']<10 else ' / signal>=10')),
        heartbeat_payload=grouped(rows,lambda r:age_group(r)+(' / payload<60' if r['payload_bytes']<60 else ' / payload>=60')),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=Path('data/device_commands.csv'))
    parser.add_argument('--out',type=Path,default=Path('results'))
    args = parser.parse_args()
    groups = diagnose(load_commands(args.input))
    result = dict(source_sha256=hashlib.sha256(args.input.read_bytes()).hexdigest(),
                  rate_denominator='known outcomes only; missing receipts are unknown',groups=groups)
    args.out.mkdir(parents=True,exist_ok=True)
    (args.out/'diagnosis.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    with (args.out/'cohorts.csv').open('w',encoding='utf-8',newline='') as handle:
        fields=['dimension','group','commands','success','timeout','unknown','known_outcomes','known_failure_rate','failure_rate_lower','failure_rate_upper']
        writer=csv.DictWriter(handle,fieldnames=fields,lineterminator='\n')
        writer.writeheader()
        for dimension,cohorts in groups.items():
            for name,metrics in cohorts.items():
                record=metrics.copy()
                bounds=record.pop('failure_rate_bounds') or [None,None]
                writer.writerow(dict(dimension=dimension,group=name,**record,
                                     failure_rate_lower=bounds[0],failure_rate_upper=bounds[1]))
    print(json.dumps(groups['heartbeat_age'],ensure_ascii=False,indent=2))

if __name__ == '__main__':
    main()
