#!/usr/bin/env python3
"""Historical cost scenarios; no network or device actions. Savings are not measured effects."""
import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from audit import parse_time
from diagnose import load_commands

TIMEOUT_COST = 75
RECONNECT_COST = 12
SCENARIOS = [0.0,0.25,0.5,0.75,1.0]


def decide(row, rule):
    kind = rule['kind']
    if kind == 'none':
        return False
    if kind == 'all':
        return True
    if kind == 'age':
        return row['heartbeat_age_minutes'] >= rule['threshold']
    if kind == 'scoped_age':
        return (row['heartbeat_age_minutes'] >= rule['threshold']
                and row['hotel_property'] in rule['properties'])
    if kind == 'signal':
        return row['signal_quality'] <= rule['threshold']
    if kind == 'payload':
        return row['payload_bytes'] >= rule['threshold']
    raise ValueError('Unknown policy kind: '+kind)


def evaluate(rows, rule):
    blocked = [row for row in rows if decide(row,rule)]
    counts = Counter(row['outcome'] for row in blocked)
    failures = sum(row['outcome']==0 for row in rows)
    cost = RECONNECT_COST*len(blocked)
    potential = TIMEOUT_COST*counts[0]
    return dict(commands=len(rows), observed_timeouts=failures, blocked=len(blocked),
                hit_timeout=counts[0], blocked_success=counts[1], blocked_unknown=counts[None],
                missed_timeout=failures-counts[0],
                block_rate=len(blocked)/len(rows) if rows else None,
                timeout_recall=counts[0]/failures if failures else None,
                known_precision=counts[0]/(counts[0]+counts[1]) if counts[0]+counts[1] else None,
                baseline_known_loss_minutes=TIMEOUT_COST*failures,
                reconnect_cost_minutes=cost,
                net_minutes_by_q={str(q):q*potential-cost for q in SCENARIOS},
                break_even_q=cost/potential if potential else None,
                unknown_all_failure_net_minutes_q0_5=.5*TIMEOUT_COST*(counts[0]+counts[None])-cost)


def split_time(rows, cutoff):
    instant=parse_time(cutoff)
    return ([row for row in rows if row['request_time']<instant],
            [row for row in rows if row['request_time']>=instant])


def fit_rules(train):
    """Fit only on the supplied training partition. Selection assumes q=0.5."""
    grids={'age':[5,10,15,20,30,45,60], 'signal':[4,9,14,19,24,30],
           'payload':[40,50,60,70,80,90]}
    rules={'no_block':{'kind':'none'},'block_all':{'kind':'all'}}
    searches=[]

    def score(rule):
        m=evaluate(train,rule)
        return m['net_minutes_by_q']['0.5'], -m['blocked']

    for kind,thresholds in grids.items():
        candidates=[dict(kind=kind,threshold=t) for t in thresholds]
        rules[kind]=max(candidates,key=score)
        searches.extend(dict(rule=rule,train=evaluate(train,rule)) for rule in candidates)
    candidates=[]
    for threshold in grids['age']:
        properties=[]
        for hotel in sorted({row['hotel_property'] for row in train}):
            m=evaluate([row for row in train if row['hotel_property']==hotel],
                       dict(kind='age',threshold=threshold))
            if m['hit_timeout']+m['blocked_success'] >= 20 and m['net_minutes_by_q']['0.5']>0:
                properties.append(hotel)
        rule=dict(kind='scoped_age',threshold=threshold,properties=properties)
        candidates.append(rule)
        searches.append(dict(rule=rule,train=evaluate(train,rule)))
    rules['scoped_age']=max(candidates,key=score)
    selected=max(rules,key=lambda name:score(rules[name]))
    return rules,selected,searches


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=Path('data/device_commands.csv'))
    parser.add_argument('--out',type=Path,default=Path('results'))
    parser.add_argument('--cutoff',default='2026-09-06T00:00:00+08:00')
    args=parser.parse_args()
    rows=load_commands(args.input)
    train,test=split_time(rows,args.cutoff)
    if not train or not test:
        raise ValueError('Training and replay partitions must both contain commands')
    rules,selected,searches=fit_rules(train)
    comparison={name:dict(rule=rule,train=evaluate(train,rule),test=evaluate(test,rule))
                for name,rule in rules.items()}
    result=dict(
        source_sha256=hashlib.sha256(args.input.read_bytes()).hexdigest(),cutoff=args.cutoff,
        train_commands=len(train),test_commands=len(test),
        cost_model=dict(timeout_minutes=TIMEOUT_COST,reconnect_minutes=RECONNECT_COST,
                        selection_q=.5,unknown_outcomes='charge reconnect cost; no assumed benefit'),
        limitations=[
            'q is an assumed reconnect rescue rate, not estimated from the supplied CSV.',
            'Net savings are counterfactual scenarios, not observed production improvements.',
            'Full-data exploration preceded this retrospective time replay; it is not a pristine holdout.',
            'No device IDs: within-device dependence and intervention side effects are not measured.',
            'Costs follow the task per-command assumption; shared incidents may invalidate additive real-world costs.'
        ],
        selected_name=selected,selected_rule=rules[selected],strategies=comparison,training_search=searches)
    args.out.mkdir(parents=True,exist_ok=True)
    (args.out/'replay.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    with (args.out/'strategy_comparison.csv').open('w',encoding='utf-8',newline='') as handle:
        fields=['strategy','split','commands','blocked','hit_timeout','blocked_success','blocked_unknown',
                'missed_timeout','net_minutes_q0_5','net_minutes_q1','break_even_q']
        writer=csv.DictWriter(handle,fieldnames=fields,lineterminator='\n');writer.writeheader()
        for name,parts in comparison.items():
            for split in ['train','test']:
                metrics=parts[split]
                record={key:metrics[key] for key in fields if key in metrics}
                writer.writerow(dict(strategy=name,split=split,**record,
                                     net_minutes_q0_5=metrics['net_minutes_by_q']['0.5'],
                                     net_minutes_q1=metrics['net_minutes_by_q']['1.0']))
    # Outcome is exported for evaluation only; decide() never reads it.
    with (args.out/'decisions.csv').open('w',encoding='utf-8',newline='') as handle:
        fields=['cmd_id','requested_at','hotel_property','heartbeat_age_minutes','split','decision','reason','observed_outcome']
        writer=csv.DictWriter(handle,fieldnames=fields,lineterminator='\n');writer.writeheader()
        for split,partition in [('train',train),('test',test)]:
            for row in partition:
                blocked=decide(row,rules[selected])
                writer.writerow(dict(cmd_id=row['cmd_id'],requested_at=row['requested_at'],
                                     hotel_property=row['hotel_property'],heartbeat_age_minutes=row['heartbeat_age_minutes'],
                                     split=split,decision='reconnect_candidate' if blocked else 'allow',
                                     reason='matches_selected_rule' if blocked else 'outside_selected_rule',
                                     observed_outcome=row['outcome'] if row['outcome'] is not None else ''))
    print(json.dumps(dict(selected=rules[selected],test=comparison[selected]['test']),ensure_ascii=False,indent=2))

if __name__ == '__main__':
    main()
