"""Reject incomplete or clock-inconsistent evidence instead of inventing causality."""
import copy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('peak_analysis', ROOT/'tools/analyze_frame_peak_trace.py')
analysis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analysis)


def transport(tickets=3, dropped=0, uncommitted=False):
    capacity = 3
    header = analysis.HEADER.pack(b'FPEAK001', 1, 128 + capacity*64, 0, 1, capacity, 0,
                                  tickets, dropped, 1_000_000_000, 5_000_000_000,
                                  1, 1, 1, 1, 1, 1_000_000_000, 120_000_000_000, 0, 0)
    records = b''.join(analysis.EVENT.pack(0 if uncommitted and i == 2 else i+1,
                       start, end, 1, 1, kind, 0, 0, 0) for i, (start, end, kind) in enumerate([
                           (1_100_000_000, 1_101_000_000, 1),
                           (3_100_000_000, 3_160_000_000, 4),
                           (4_100_000_000, 4_101_000_000, 2)]))
    return analysis.parse_transport(header + records)


def report():
    return {'calibration': [{'request': 1, 'ack': 1, 'host_before_ns': 9_999_000_000,
                            'host_after_ns': 10_001_000_000, 'writer_clock_ns': 2_000_000_000,
                            'host_before_epoch_ns': 101_999_000_000, 'host_after_epoch_ns': 102_001_000_000}],
            'record_start_request': 100, 'record_start_ack': 100.01,
            'native_recording': {'samples': [{'timestamp(ms)': 3500, 'interval_s': 1,
                                             'peakCpu(ms)': 60, 'peakGpu(ms)': 25}]}}


class PeakTraceAnalysisTest(unittest.TestCase):
    def test_long_wait_correlates_with_native_interval(self):
        result = analysis.analyze(report(), transport())
        peak = result['native_over50ms'][0]
        self.assertTrue(peak['complete_trace_coverage_under_clock_model'])
        self.assertEqual(peak['largest_nearby_spans'][0]['kind'], 'CS_synchronize')
        self.assertEqual(peak['largest_nearby_spans'][0]['duration_ms'], 60)

    def test_clock_drift_is_not_silently_corrected(self):
        data = report()
        second = copy.deepcopy(data['calibration'][0])
        second.update(request=2, ack=2, host_before_epoch_ns=102_999_000_000,
                      host_after_epoch_ns=103_001_000_000)
        data['calibration'].append(second)
        result = analysis.analyze(data, transport())
        self.assertFalse(result['clock_alignment']['consistent_constant_offset'])
        self.assertFalse(result['native_over50ms'][0]['complete_trace_coverage_under_clock_model'])
        self.assertEqual(result['native_over50ms'][0]['largest_nearby_spans'], [])

    def test_gradual_clock_rate_difference_is_bounded_by_all_pairs(self):
        data = report()
        rows = []
        for i in range(4):
            writer = 2_000_000_000 + i*10_000_000_000
            expected_epoch = writer + 100_000_000_000 + i*200_000
            rows.append({'request': i+1, 'ack': i+1, 'writer_clock_ns': writer,
                         'host_before_ns': writer+8_000_000_000-100_000,
                         'host_after_ns': writer+8_000_000_000+100_000,
                         'host_before_epoch_ns': expected_epoch-100_000,
                         'host_after_epoch_ns': expected_epoch+100_000})
        clock = analysis.ClockAlignment(rows)
        self.assertTrue(clock.consistent)
        self.assertFalse(clock.constant_consistent)
        self.assertEqual(clock.describe()['model'], 'bounded_affine')
        lo, hi = clock.bounds(17_000_000_000)
        expected = 117.0003
        self.assertLessEqual(lo, expected)
        self.assertGreaterEqual(hi, expected)
        self.assertLess(hi-lo, .001)
        rate_lo, rate_hi = clock.describe()['rate_error_ppm_bounds']
        self.assertLessEqual(rate_lo, 20)
        self.assertGreaterEqual(rate_hi, 20)

    def test_clock_step_is_not_reinterpreted_as_smooth_drift(self):
        rows = []
        for i in range(3):
            writer = 2_000_000_000+i*10_000_000_000
            epoch = writer+100_000_000_000+(200_000_000 if i==2 else 0)
            rows.append({'request':i+1,'ack':i+1,'writer_clock_ns':writer,
                         'host_before_ns':writer+8_000_000_000-100_000,
                         'host_after_ns':writer+8_000_000_000+100_000,
                         'host_before_epoch_ns':epoch-100_000,
                         'host_after_epoch_ns':epoch+100_000})
        clock=analysis.ClockAlignment(rows)
        self.assertFalse(clock.consistent)
        self.assertIsNone(clock.bounds(17_000_000_000))

    def test_overflow_and_missing_publication_invalidate_full_coverage(self):
        for args in [dict(tickets=4, dropped=1), dict(uncommitted=True), dict(dropped=1)]:
            with self.subTest(args=args):
                result = analysis.analyze(report(), transport(**args))
                self.assertFalse(result['native_over50ms'][0]['complete_trace_coverage_under_clock_model'])

    def test_peak_outside_capture_is_not_treated_as_negative_evidence(self):
        data = report()
        data['native_recording']['samples'][0]['timestamp(ms)'] = 8000
        result = analysis.analyze(data, transport())
        peak = result['native_over50ms'][0]
        self.assertFalse(peak['complete_trace_coverage_under_clock_model'])
        self.assertEqual(peak['largest_nearby_spans'], [])

    def test_unarmed_data_has_no_performance_result(self):
        with self.assertRaisesRegex(ValueError, 'No committed'):
            analysis.analyze(report(), transport(tickets=0))

    def test_invalid_transport_and_backwards_clock_rejected(self):
        with self.assertRaises(ValueError):
            analysis.parse_transport(b'FPEAK001')
        data = report()
        data['calibration'][0]['host_after_ns'] = 1
        with self.assertRaises(ValueError):
            analysis.analyze(data, transport())


if __name__ == '__main__':
    unittest.main()
