#!/usr/bin/env python3
"""Audit the supplied command CSV: python3 audit.py [--input PATH] [--output PATH]."""
import argparse
import csv
import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

FIELDS = {'cmd_id', 'requested_at', 'last_heartbeat_at', 'hotel_property',
          'device_category', 'signal_quality', 'payload_bytes', 'is_success'}


def parse_time(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Timestamp must specify timezone')
    return parsed.astimezone(timezone.utc)


def audit_records(records):
    unique = {}
    ages = []
    requests = []
    for row in records:
        if set(row) != FIELDS or any(value is None for value in row.values()):
            raise ValueError('CSV schema mismatch or incomplete row')
        if not row['cmd_id']:
            raise ValueError('Missing cmd_id')
        if row['cmd_id'] in unique:
            if row != unique[row['cmd_id']]:
                raise ValueError('Conflicting duplicate cmd_id: '+row['cmd_id'])
            continue
        if row['is_success'] not in ('0', '1', ''):
            raise ValueError('Invalid outcome: '+row['cmd_id'])
        requested = parse_time(row['requested_at'])
        age = (requested - parse_time(row['last_heartbeat_at'])).total_seconds()/60
        if age < 0:
            raise ValueError('Heartbeat is later than request: '+row['cmd_id'])
        if not math.isfinite(float(row['signal_quality'])) or int(row['payload_bytes']) < 0:
            raise ValueError('Invalid numeric value: '+row['cmd_id'])
        unique[row['cmd_id']] = row
        ages.append(age)
        requests.append(requested)
    counts = Counter(row['is_success'] for row in unique.values())
    n = len(unique)
    known = counts['0']+counts['1']
    return dict(raw_rows=len(records), unique_commands=n,
                duplicate_rows_removed=len(records)-n,
                outcomes=dict(success=counts['1'], timeout=counts['0'], unknown=counts['']),
                known_outcomes=known,
                known_failure_rate=counts['0']/known if known else None,
                failure_rate_bounds=[counts['0']/n, (counts['0']+counts[''])/n] if n else None,
                heartbeat_age_minutes=dict(min=min(ages), max=max(ages)) if ages else None,
                request_range_utc=[min(requests).isoformat(),max(requests).isoformat()] if requests else None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=Path('data/device_commands.csv'))
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    with args.input.open(encoding='utf-8-sig', newline='') as handle:
        result = audit_records(list(csv.DictReader(handle)))
    result['source_sha256'] = hashlib.sha256(args.input.read_bytes()).hexdigest()
    output = json.dumps(result, ensure_ascii=False, indent=2)+'\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding='utf-8')
    print(output, end='')

if __name__ == '__main__':
    main()
