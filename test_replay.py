"""Synthetic cases test policy and accounting, not real-world reconnect efficacy."""
import unittest
from replay import decide, evaluate, fit_rules, split_time
from diagnose import prepare_commands
from test_audit import command


class ReplayTests(unittest.TestCase):
    def test_cost_includes_success_and_unknown_blocks(self):
        rows=prepare_commands([command(),command(cmd_id='2',is_success='1'),command(cmd_id='3',is_success='')])
        m=evaluate(rows,{'kind':'age','threshold':15})
        self.assertEqual((m['blocked'],m['hit_timeout'],m['blocked_success'],m['blocked_unknown']),(3,1,1,1))
        self.assertEqual(m['net_minutes_by_q']['0.5'],1.5)
        self.assertEqual(m['net_minutes_by_q']['1.0'],39)
        self.assertEqual(m['break_even_q'],.48)

    def test_policy_uses_pre_request_features_only(self):
        row={'heartbeat_age_minutes':20,'hotel_property':'A'}
        self.assertTrue(decide(row,{'kind':'age','threshold':20}))
        self.assertFalse(decide(row,{'kind':'age','threshold':21}))
        self.assertFalse(decide(row,{'kind':'scoped_age','threshold':20,'properties':['B']}))

    def test_time_split_boundary_and_equivalent_offsets(self):
        rows=prepare_commands([
            command(cmd_id='1',requested_at='2026-09-05T23:59:00+08:00',last_heartbeat_at='2026-09-05T15:58:00Z'),
            command(cmd_id='2',requested_at='2026-09-05T16:00:00Z',last_heartbeat_at='2026-09-05T15:59:00Z')])
        train,test=split_time(rows,'2026-09-06T00:00:00+08:00')
        self.assertEqual([r['cmd_id'] for r in train],['1'])
        self.assertEqual([r['cmd_id'] for r in test],['2'])

    def test_no_failures_prefers_no_intervention(self):
        rows=prepare_commands([command(is_success='1')])
        rules,selected,_=fit_rules(rows)
        self.assertEqual(selected,'no_block')
        self.assertEqual(evaluate(rows,rules[selected])['net_minutes_by_q']['0.5'],0)
        self.assertIsNone(evaluate(rows,{'kind':'all'})['break_even_q'])

    def test_unknowns_cannot_manufacture_training_benefit(self):
        rows=prepare_commands([command(is_success='')])
        _,selected,_=fit_rules(rows)
        self.assertEqual(selected,'no_block')

if __name__ == '__main__':
    unittest.main()
