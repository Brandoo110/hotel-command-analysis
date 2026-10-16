"""Synthetic verification fixtures only; no invented data enters business reports."""
import unittest
from copy import deepcopy
from diagnose import prepare_commands
from replay import decide
from robustness import normalize_config, estimate, choose_rules, walk_forward, validate_categories
from test_audit import command


class RobustnessTests(unittest.TestCase):
    def test_strict_age_boundary(self):
        self.assertFalse(decide({'heartbeat_age_minutes':15},{'kind':'age_gt','threshold':15}))
        self.assertTrue(decide({'heartbeat_age_minutes':16},{'kind':'age_gt','threshold':15}))

    def test_unknown_bounds_include_possible_false_block_penalty(self):
        rows=prepare_commands([command(),command(cmd_id='2',is_success='1'),command(cmd_id='3',is_success='')])
        config=normalize_config({'default_costs':{'false_block_penalty':5}})
        result=estimate(rows,{'kind':'all'},config)
        self.assertEqual(result['net_reference_minutes'],-3.5)
        self.assertEqual(result['net_lower_minutes'],-8.5)
        self.assertEqual(result['net_upper_minutes'],34)
        self.assertAlmostEqual(result['break_even_q_lower'],46/75)

    def test_category_costs_and_observe_only_constraint(self):
        rows=prepare_commands([command(cmd_id='1',device_category='lock',is_success='1'),
                               command(cmd_id='2',device_category='thermostat')])
        config=normalize_config({'category_costs':{'lock':{'false_block_penalty':100}},
                                 'observe_only_categories':['lock']})
        result=estimate(rows,{'kind':'all'},config)
        self.assertEqual(result['flagged'],2)
        self.assertEqual(result['eligible'],1)
        self.assertEqual(result['observe_only'],1)
        self.assertEqual(result['net_lower_minutes'],25.5)

    def test_invalid_config_rejected(self):
        for config in [{'rescue_rate':float('nan')},{'rescue_rate':1.1},
                       {'default_costs':{'reconnect_cost':-1}}, {'oops':True},
                       {'category_costs':{'lock':{'unknown_cost':1}}},
                       {'observe_only_categories':'lock'}, {'rescue_rate':True}]:
            with self.subTest(config=config),self.assertRaises(ValueError):
                normalize_config(config)

    def test_no_evidence_prefers_no_block(self):
        rows=prepare_commands([command(is_success='')])
        rules,selected=choose_rules(rows,normalize_config({}))
        self.assertEqual(selected,'none')
        self.assertEqual(rules[selected],{'kind':'none'})

    def test_future_outcomes_do_not_change_first_fold_rule(self):
        raw=[]
        for day in range(1,6):
            raw.append(command(cmd_id=str(day),requested_at=f'2026-09-{day:02d}T08:00:00+08:00',
                               last_heartbeat_at=f'2026-09-{day:02d}T00:00:00Z',is_success='1'))
        original=walk_forward(prepare_commands(raw),normalize_config({}),min_train_days=3)
        changed=deepcopy(raw)
        changed[3]['is_success']='0'
        revised=walk_forward(prepare_commands(changed),normalize_config({}),min_train_days=3)
        self.assertEqual(original['folds'][0]['rules'],revised['folds'][0]['rules'])
        self.assertEqual(original['folds'][0]['selected_name'],revised['folds'][0]['selected_name'])
        self.assertEqual(original['folds'][0]['test_date'],'2026-09-04')
        self.assertEqual(original['test_commands'],2)
        self.assertEqual(len({r['cmd_id'] for r in original['decisions']}),2)

    def test_observe_only_category_typo_is_rejected(self):
        rows=prepare_commands([command()])
        with self.assertRaises(ValueError):
            validate_categories(rows,normalize_config({'observe_only_categories':['not-in-data']}))

    def test_minimum_history_is_enforced(self):
        with self.assertRaises(ValueError):
            walk_forward(prepare_commands([command()]),normalize_config({}),min_train_days=3)

if __name__=='__main__':
    unittest.main()
