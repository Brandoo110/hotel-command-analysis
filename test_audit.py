"""Small synthetic fixtures validate code only, never business conclusions."""
import unittest
from audit import audit_records


def command(**changes):
    row = dict(cmd_id='1', requested_at='2026-09-01T08:00:00+08:00',
               last_heartbeat_at='2026-08-31T23:45:00Z', hotel_property='A',
               device_category='switch', signal_quality='10', payload_bytes='50', is_success='0')
    row.update(changes)
    return row


class AuditTests(unittest.TestCase):
    def test_timezone_duplicates_unknown_and_denominator(self):
        result = audit_records([command(), command(), command(cmd_id='2', is_success=''),
                                command(cmd_id='3', is_success='1')])
        self.assertEqual(result['raw_rows'], 4)
        self.assertEqual(result['duplicate_rows_removed'], 1)
        self.assertEqual(result['unique_commands'], 3)
        self.assertEqual(result['heartbeat_age_minutes'], {'min':15, 'max':15})
        self.assertEqual(result['outcomes'], {'success':1, 'timeout':1, 'unknown':1})
        self.assertEqual(result['known_failure_rate'], .5)
        self.assertEqual(result['failure_rate_bounds'], [1/3, 2/3])

    def test_conflicting_duplicates_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            audit_records([command(), command(is_success='1')])

    def test_invalid_data_not_silently_counted(self):
        for changes in [dict(is_success='2'), dict(last_heartbeat_at='2026-09-01T00:01:00Z'),
                        dict(requested_at='2026-09-01T08:00:00'), dict(signal_quality='nan'),
                        dict(cmd_id=''), dict(payload_bytes='-1')]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                audit_records([command(**changes)])

    def test_empty_input_and_unknown_only(self):
        self.assertIsNone(audit_records([])['known_failure_rate'])
        result = audit_records([command(is_success='')])
        self.assertIsNone(result['known_failure_rate'])
        self.assertEqual(result['failure_rate_bounds'], [0, 1])

if __name__ == '__main__':
    unittest.main()
