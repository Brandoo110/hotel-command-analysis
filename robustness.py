#!/usr/bin/env python3
"""Post-assessment retrospective robustness analysis. Shadow decisions only; no device actions."""
import argparse
import csv
import hashlib
import json
import math
from collections import Counter
from copy import deepcopy
from datetime import timedelta, timezone
from pathlib import Path
from diagnose import load_commands
from replay import RULE_THRESHOLDS, SCENARIOS, decide, fit_rules, split_time

LOCAL_TZ = timezone(timedelta(hours=8))
DEFAULT_COSTS = {'timeout_cost':75.0,'reconnect_cost':12.0,'false_block_penalty':0.0}
FIXED_RULES = {'none':{'kind':'none'}, 'all':{'kind':'all'},
               'global_ge15':{'kind':'age','threshold':15},
               'global_gt15':{'kind':'age_gt','threshold':15},
               'global_ge20':{'kind':'age','threshold':20}}


def number(value):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:
        raise ValueError('Costs and probabilities must be finite nonnegative numbers')
    return float(value)


def normalize_config(raw):
    if not isinstance(raw,dict) or set(raw)-{'rescue_rate','default_costs','category_costs','observe_only_categories'}:
        raise ValueError('Unknown configuration field or non-object configuration')
    q=number(raw.get('rescue_rate',.5))
    if q>1:
        raise ValueError('rescue_rate must be between 0 and 1')
    defaults=raw.get('default_costs',{})
    categories=raw.get('category_costs',{})
    excluded=raw.get('observe_only_categories',[])
    if not isinstance(categories,dict) or not isinstance(excluded,list) or any(not isinstance(k,str) or not k.strip() for k in excluded):
        raise ValueError('Invalid category costs or observe-only list')

    def costs(overrides,base):
        if not isinstance(overrides,dict) or set(overrides)-set(DEFAULT_COSTS):
            raise ValueError('Unknown cost field or non-object cost profile')
        return {**base,**{key:number(value) for key,value in overrides.items()}}

    defaults=costs(defaults,DEFAULT_COSTS)
    resolved={}
    for category,overrides in categories.items():
        if not isinstance(category,str) or not category.strip():
            raise ValueError('Category name must be nonempty')
        resolved[category]=costs(overrides,defaults)
    return dict(rescue_rate=q,default_costs=defaults,category_costs=resolved,
                observe_only_categories=sorted(set(excluded)))


def validate_categories(rows,config):
    known={r['device_category'] for r in rows}
    requested=set(config['category_costs'])|set(config['observe_only_categories'])
    if requested-known:
        raise ValueError('Configuration categories absent from dataset: '+', '.join(sorted(requested-known)))


def estimate(rows,rule,config):
    """Unknown outcomes: all-success lower bound versus all-timeout upper bound.

    Penalty is incremental to reconnect cost. Rescue rate is a scenario, never fitted
    from this CSV. Bounds concern missing labels, not statistical confidence.
    """
    flagged=[r for r in rows if decide(r,rule)]
    eligible=[r for r in flagged if r['device_category'] not in config['observe_only_categories']]
    counts=Counter(r['outcome'] for r in eligible)
    reconnect=penalty=unknown_penalty=potential=unknown_potential=0.0
    for row in eligible:
        costs=config['category_costs'].get(row['device_category'],config['default_costs'])
        reconnect+=costs['reconnect_cost']
        if row['outcome']==0:
            potential+=costs['timeout_cost']
        elif row['outcome']==1:
            penalty+=costs['false_block_penalty']
        else:
            unknown_penalty+=costs['false_block_penalty']
            unknown_potential+=costs['timeout_cost']
    q=config['rescue_rate']
    reference=q*potential-reconnect-penalty
    return dict(commands=len(rows),observed_timeouts=sum(r['outcome']==0 for r in rows),
                flagged=len(flagged),eligible=len(eligible),observe_only=len(flagged)-len(eligible),
                hit_timeout=counts[0],false_block=counts[1],unknown=counts[None],
                reconnect_cost_minutes=reconnect,false_block_cost_minutes=penalty,
                unknown_false_block_cost_minutes=unknown_penalty,timeout_loss_at_risk_minutes=potential,
                unknown_timeout_loss_at_risk_minutes=unknown_potential,
                net_reference_minutes=reference,net_lower_minutes=reference-unknown_penalty,
                net_upper_minutes=reference+q*unknown_potential,
                break_even_q_lower=(reconnect+penalty+unknown_penalty)/potential if potential else None)


def choose_rules(train,config):
    """Select on training labels only; costs and exclusions are explicit external inputs."""
    def score(rule):
        m=estimate(train,rule,config)
        return m['net_lower_minutes'],-m['eligible']

    rules=deepcopy(FIXED_RULES)
    for kind,thresholds in RULE_THRESHOLDS.items():
        rules['best_'+kind]=max([dict(kind=kind,threshold=t) for t in thresholds],key=score)
    candidates=[]
    for threshold in RULE_THRESHOLDS['age']:
        properties=[]
        for hotel in sorted({r['hotel_property'] for r in train}):
            m=estimate([r for r in train if r['hotel_property']==hotel],dict(kind='age',threshold=threshold),config)
            if m['hit_timeout']+m['false_block']>=20 and m['net_lower_minutes']>0:
                properties.append(hotel)
        candidates.append(dict(kind='scoped_age',threshold=threshold,properties=properties))
    rules['best_scoped']=max(candidates,key=score)
    return rules,max(rules,key=lambda name:score(rules[name]))


def local_date(row):
    return row['request_time'].astimezone(LOCAL_TZ).date().isoformat()


def walk_forward(rows,config,min_train_days=3):
    days=sorted({local_date(r) for r in rows})
    if min_train_days<1 or len(days)<=min_train_days:
        raise ValueError('Insufficient observed days for the requested training history')
    folds=[]
    decisions=[]
    for day in days[min_train_days:]:
        train=[r for r in rows if local_date(r)<day]
        test=[r for r in rows if local_date(r)==day]
        rules,selected=choose_rules(train,config)
        policies={**rules,'training_selected':rules[selected]}
        for row in test:
            flagged=decide(row,rules[selected])
            excluded=flagged and row['device_category'] in config['observe_only_categories']
            decisions.append(dict(cmd_id=row['cmd_id'],requested_at=row['requested_at'],
                                  test_date=day,device_category=row['device_category'],
                                  hotel_property=row['hotel_property'],heartbeat_age_minutes=row['heartbeat_age_minutes'],
                                  selected_name=selected,flagged=flagged,eligible=flagged and not excluded,
                                  reason='observe_only_constraint' if excluded else ('rule_candidate' if flagged else 'outside_rule'),
                                  observed_outcome='' if row['outcome'] is None else row['outcome']))
        folds.append(dict(test_date=day,train_commands=len(train),test_commands=len(test),
                          selected_name=selected,rules=policies,
                          metrics={name:estimate(test,rule,config) for name,rule in policies.items()}))
    # Each request appears in exactly one replay fold. Sum costs, never average rates.
    totals={}
    for name in folds[0]['metrics']:
        totals[name]={key:sum(fold['metrics'][name][key] for fold in folds)
                      for key in folds[0]['metrics'][name] if key!='break_even_q_lower'}
        m=totals[name]
        numerator=m['reconnect_cost_minutes']+m['false_block_cost_minutes']+m['unknown_false_block_cost_minutes']
        m['break_even_q_lower']=numerator/m['timeout_loss_at_risk_minutes'] if m['timeout_loss_at_risk_minutes'] else None
        m['negative_days']=sum(fold['metrics'][name]['net_lower_minutes']<0 for fold in folds)
    return dict(min_train_observed_days=min_train_days,test_commands=sum(f['test_commands'] for f in folds),
                folds=folds,totals=totals,decisions=decisions)


def sensitivity(test,rules,config):
    """Freeze rules first, then change assumptions; never re-fit on replay outcomes."""
    result=[]
    for q in SCENARIOS:
        for extra in [0,2,5,15]:
            scenario=deepcopy(config)
            scenario['rescue_rate']=q
            scenario['default_costs']['false_block_penalty']+=extra
            for costs in scenario['category_costs'].values():
                costs['false_block_penalty']+=extra
            for name,rule in rules.items():
                result.append(dict(strategy=name,rescue_rate=q,extra_false_block_penalty=extra,
                                   **estimate(test,rule,scenario)))
    return result


def write_csv(path,records):
    with path.open('w',encoding='utf-8',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=list(records[0]),lineterminator='\n')
        writer.writeheader();writer.writerows(records)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=Path('data/device_commands.csv'))
    parser.add_argument('--out',type=Path,default=Path('results/robustness'))
    parser.add_argument('--cost-config',type=Path)
    parser.add_argument('--min-train-days',type=int,default=3)
    parser.add_argument('--cutoff',default='2026-09-06T00:00:00+08:00')
    args=parser.parse_args()
    config=normalize_config(json.loads(args.cost_config.read_text()) if args.cost_config else {})
    rows=load_commands(args.input)
    validate_categories(rows,config)
    rolling=walk_forward(rows,config,args.min_train_days)
    decisions=rolling.pop('decisions')
    train,test=split_time(rows,args.cutoff)
    if not train or not test:
        raise ValueError('Reference cutoff must produce nonempty training and replay partitions')
    # Keep the original baseline policy for comparison, even under alternative cost assumptions.
    old_rules,old_selected,_=fit_rules(train)
    frozen={name:rule for name,rule in FIXED_RULES.items() if name.startswith('global_')}
    frozen['original_training_selected']=old_rules[old_selected]
    scenarios=sensitivity(test,frozen,config)
    cohorts=[]
    for dimension in ['hotel_property','device_category']:
        for group in sorted({row[dimension] for row in test}):
            subset=[row for row in test if row[dimension]==group]
            for name,rule in frozen.items():
                cohorts.append(dict(dimension=dimension,group=group,strategy=name,**estimate(subset,rule,config)))
    result=dict(source_sha256=hashlib.sha256(args.input.read_bytes()).hexdigest(),config=config,
                reference_cutoff=args.cutoff,reference_rules=frozen,
                reference_metrics={name:estimate(test,rule,config) for name,rule in frozen.items()},
                rolling=rolling,limitations=[
                    'Post-assessment retrospective analysis of the same seven-day dataset, not new independent evidence.',
                    'Earlier-day outcomes are assumed available; the CSV has no outcome arrival times.',
                    'Rescue rate and incremental false-block costs are assumptions, not measured interventions.',
                    'Missing-label bounds are not confidence intervals; no device IDs exist for clustered inference.',
                    'Observe-only constraints change hypothetical eligibility, not real device behavior.'
                ])
    args.out.mkdir(parents=True,exist_ok=True)
    (args.out/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    write_csv(args.out/'daily.csv',[dict(test_date=f['test_date'],strategy=name,selected_name=f['selected_name'],**metrics)
                                  for f in rolling['folds'] for name,metrics in f['metrics'].items()])
    write_csv(args.out/'decisions.csv',decisions)
    write_csv(args.out/'sensitivity.csv',scenarios)
    write_csv(args.out/'cohorts.csv',cohorts)
    print(json.dumps(dict(test_commands=rolling['test_commands'],totals={k:dict(net=v['net_lower_minutes'],negative_days=v['negative_days'],eligible=v['eligible'],hit_timeout=v['hit_timeout']) for k,v in rolling['totals'].items()}),ensure_ascii=False,indent=2))

if __name__=='__main__':
    main()
