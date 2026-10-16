"""Synthetic cases test grouping semantics, not business findings."""
import unittest
from diagnose import prepare_commands, summarize, diagnose
from test_audit import command


class DiagnosisTests(unittest.TestCase):
    def test_exact_threshold_and_unknown_accounting(self):
        rows = prepare_commands([
            command(), command(),
            command(cmd_id='2', last_heartbeat_at='2026-08-31T23:46:00Z', is_success='1'),
            command(cmd_id='3', is_success='')])
        groups = diagnose(rows)['heartbeat_age']
        self.assertEqual(len(rows), 3)
        self.assertEqual(groups['<15']['success'], 1)
        self.assertEqual(groups['>=15']['timeout'], 1)
        self.assertEqual(groups['>=15']['unknown'], 1)
        self.assertEqual(groups['>=15']['known_failure_rate'], 1)
        self.assertEqual(groups['>=15']['failure_rate_bounds'], [.5, 1])

    def test_property_group_counts_reconcile(self):
        rows = prepare_commands([command(),command(cmd_id='2',hotel_property='B',is_success='1')])
        result = diagnose(rows)
        self.assertEqual(sum(g['commands'] for g in result['property'].values()), 2)
        self.assertEqual(result['property_heartbeat']['A / >=15']['timeout'], 1)

    def test_invalid_data_uses_audit_contract(self):
        with self.assertRaises(ValueError):
            prepare_commands([command(),command(is_success='1')])

    def test_empty_summary_has_no_fabricated_rate(self):
        self.assertIsNone(summarize([])['known_failure_rate'])
        self.assertIsNone(summarize([])['failure_rate_bounds'])

if __name__ == '__main__':
    unittest.main()
