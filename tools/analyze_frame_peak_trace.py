#!/usr/bin/env python3
"""Analyze the opt-in timing transport, retaining clock and coverage limits."""
import argparse
from bisect import bisect_left
from collections import defaultdict
import json
import math
from pathlib import Path
import struct

HEADER = struct.Struct('<8s6I11Q2I')
EVENT = struct.Struct('<4Q2I3Q')
CAPACITY = 262144
KINDS = {1: 'interop_flush', 2: 'interop_queue_lock', 3: 'interop_queue_held',
         4: 'CS_synchronize', 5: 'submission_timeline_wait', 6: 'submission_drain',
         7: 'device_queue_lock', 8: 'frame_latency', 9: 'DXGI_present',
         10: 'GPU_completion_wait', 11: 'queue_submit', 12: 'submit_thread_queue_lock'}


def parse_transport(raw):
    if len(raw) < HEADER.size:
        raise ValueError('Truncated transport header')
    h = HEADER.unpack_from(raw)
    if (h[0] != b'FPEAK001' or h[1] != 1 or not 0 < h[5] <= CAPACITY
            or h[2] != len(raw) or len(raw) != HEADER.size + h[5] * EVENT.size
            or h[16] != 1_000_000_000 or not 0 < h[17] <= 120_000_000_000):
        raise ValueError('Unsupported or inconsistent transport')
    events, uncommitted = [], 0
    for index in range(min(h[7], h[5])):
        commit, begin, end, reference, cookie, kind, *_ = EVENT.unpack_from(raw, HEADER.size + index * EVENT.size)
        if commit != index + 1:
            uncommitted += 1
            continue
        if (kind not in KINDS or not cookie or begin < h[9] or not begin
                or end < begin or end - h[9] >= h[17]):
            raise ValueError('Invalid committed event; refusing a partial interpretation')
        events.append({'kind': KINDS[kind], 'begin_ns': begin, 'end_ns': end,
                       'duration_ms': (end - begin) / 1e6, 'reference': reference,
                       'thread_cookie': cookie, 'ticket': commit})
    return {'start_ns': h[9], 'stop_ns': h[10], 'enabled': bool(h[3]),
            'stopped': bool(h[4]), 'capacity': h[5], 'tickets': h[7],
            'dropped': h[8], 'overflow': h[7] > h[5], 'uncommitted': uncommitted,
            'events': events}


def distribution(values):
    values = sorted(values)
    return {name: values[max(0, math.ceil(fraction * len(values)) - 1)]
            for name, fraction in [('median', .5), ('p95', .95), ('p99', .99), ('max', 1)]} if values else {}


class ClockAlignment:
    """Bound an affine clock model by every observed request/ack interval."""
    def __init__(self, rows):
        self.rows = []
        for row in rows:
            if row['ack'] != row['request'] or not row['writer_clock_ns']:
                continue
            if row['host_after_ns'] < row['host_before_ns']:
                raise ValueError('Calibration monotonic clock moved backwards')
            if not {'host_before_epoch_ns', 'host_after_epoch_ns'} <= row.keys():
                continue
            if row['host_after_epoch_ns'] < row['host_before_epoch_ns']:
                raise ValueError('Calibration wall clock moved backwards')
            self.rows.append(row)
        self.rows.sort(key=lambda row: row['writer_clock_ns'])
        self.times = [row['writer_clock_ns'] for row in self.rows]
        self.low = max((row['host_before_epoch_ns'] - row['writer_clock_ns'] for row in self.rows), default=None)
        self.high = min((row['host_after_epoch_ns'] - row['writer_clock_ns'] for row in self.rows), default=None)
        self.constant_consistent = self.low is not None and self.low <= self.high
        self.polygon = []
        self.origin = self.times[0] if self.times else 0
        self.base = self.rows[0]['host_before_epoch_ns'] - self.origin if self.rows else 0
        self.model = 'constant_offset'
        self.consistent = self.constant_consistent
        if len(set(self.times)) >= 2:
            self.model = 'bounded_affine'
            points = [((row['writer_clock_ns'] - self.origin) / 1e9,
                       row['host_before_epoch_ns'] - row['writer_clock_ns'] - self.base,
                       row['host_after_epoch_ns'] - row['writer_clock_ns'] - self.base)
                      for row in self.rows]
            # Analyst-selected limit, not a claim about the hardware's accuracy.
            # Rate is ns/s: 1,000 ppm = 1,000,000 ns/s.
            rate_lo, rate_hi = -1_000_000., 1_000_000.
            for i, (x0, lo0, hi0) in enumerate(points):
                for x1, lo1, hi1 in points[i + 1:]:
                    if x1 == x0:
                        if max(lo0, lo1) > min(hi0, hi1):
                            rate_lo, rate_hi = 1., 0.
                    else:
                        rate_lo = max(rate_lo, (lo1 - hi0) / (x1 - x0))
                        rate_hi = min(rate_hi, (hi1 - lo0) / (x1 - x0))
            if rate_lo <= rate_hi:
                lo0, hi0 = points[0][1:]
                polygon = [(rate_lo, lo0), (rate_hi, lo0), (rate_hi, hi0), (rate_lo, hi0)]
                for x, lo, hi in points:
                    polygon = self.clip(polygon, lambda rate, offset: offset + rate*x - lo)
                    polygon = self.clip(polygon, lambda rate, offset: hi - offset - rate*x)
                self.polygon = polygon
            self.consistent = bool(self.polygon)

    @staticmethod
    def clip(polygon, distance):
        if not polygon:
            return []
        out = []
        previous = polygon[-1]
        dp = distance(*previous)
        for current in polygon:
            dc = distance(*current)
            if (dc >= 0) != (dp >= 0):
                fraction = dp / (dp - dc)
                out.append(tuple(a + fraction*(b - a) for a, b in zip(previous, current)))
            if dc >= 0:
                out.append(current)
            previous, dp = current, dc
        return out

    def bounds(self, writer_ns):
        if not self.consistent:
            return None
        if self.model == 'bounded_affine':
            x = (writer_ns - self.origin) / 1e9
            offsets = [offset + rate*x for rate, offset in self.polygon]
            low = writer_ns + self.base + math.floor(min(offsets)) - 2
            high = writer_ns + self.base + math.ceil(max(offsets)) + 2
        else:
            low, high = writer_ns + self.low, writer_ns + self.high
        # Round outward when converting epoch nanoseconds to floating seconds.
        return [math.nextafter(low / 1e9, -math.inf), math.nextafter(high / 1e9, math.inf)]

    def describe(self):
        return {'successful_pairs': len(self.rows), 'consistent_constant_offset': self.constant_consistent,
                'usable': self.consistent, 'model': self.model,
                'rate_error_ppm_bounds': [min(p[0] for p in self.polygon)/1000,
                                          max(p[0] for p in self.polygon)/1000] if self.polygon else None,
                'offset_range_ns': [self.low, self.high],
                'roundtrip_ms': distribution([(row['host_after_ns'] - row['host_before_ns']) / 1e6 for row in self.rows]),
                'limitation': 'Bounds assume a smooth affine relation between calibration points (constant offset with one point); no exact frame attribution is implied.'}

    def locate(self, event):
        result = dict(event)
        result['begin_epoch_bounds'] = self.bounds(event['begin_ns'])
        result['end_epoch_bounds'] = self.bounds(event['end_ns'])
        if self.times:
            middle = (event['begin_ns'] + event['end_ns']) // 2
            i = bisect_left(self.times, middle)
            nearby = self.rows[max(0, i - 1):min(len(self.rows), i + 1)]
            nearest = min(nearby, key=lambda row: abs(row['writer_clock_ns'] - middle))
            result['nearest_calibration_distance_s'] = abs(nearest['writer_clock_ns'] - middle) / 1e9
        return result


def analyze(report, transport):
    events = transport['events']
    if not events or not transport['start_ns']:
        raise ValueError('No committed measurement events; startup waiting is not a performance result')
    alignment = ClockAlignment(report.get('calibration', []))
    by_kind = defaultdict(list)
    for event in events:
        by_kind[event['kind']].append(event)
    metrics = {}
    for kind, rows in by_kind.items():
        metrics[kind] = {'count': len(rows), 'duration_ms': distribution([row['duration_ms'] for row in rows]),
                         'over20ms': sum(row['duration_ms'] > 20 for row in rows),
                         'largest': [alignment.locate(row) for row in sorted(rows, key=lambda row: row['duration_ms'], reverse=True)[:3]]}
    capture_start = alignment.bounds(transport['start_ns'])
    terminal = transport['stop_ns'] or max(row['end_ns'] for row in events)
    capture_end = alignment.bounds(terminal)
    loss_free = not (transport['overflow'] or transport['uncommitted'] or transport['dropped'])
    located = [alignment.locate(row) for row in events] if alignment.consistent else []
    peaks = []
    for sample in report.get('native_recording', {}).get('samples', []):
        if sample['peakCpu(ms)'] <= 50:
            continue
        lo = report['record_start_request'] + sample['timestamp(ms)'] / 1000 - sample['interval_s']
        hi = report['record_start_ack'] + sample['timestamp(ms)'] / 1000
        if hi < lo:
            raise ValueError('Invalid native recording interval')
        covered = bool(capture_start and capture_end and loss_free and capture_start[1] <= lo and capture_end[0] >= hi)
        nearby = [row for row in located if row['begin_epoch_bounds'][0] <= hi and row['end_epoch_bounds'][1] >= lo]
        peaks.append({'native': sample, 'approximate_epoch_window': [lo, hi],
                      'complete_trace_coverage_under_clock_model': covered,
                      'largest_nearby_spans': sorted(nearby, key=lambda row: row['duration_ms'], reverse=True)[:12]})
    return {'events': len(events), 'tickets': transport['tickets'], 'dropped': transport['dropped'],
            'overflow': transport['overflow'], 'uncommitted': transport['uncommitted'],
            'clock_alignment': alignment.describe(), 'capture_start_epoch_bounds': capture_start,
            'capture_end_epoch_bounds': capture_end, 'metrics': metrics,
            'native': report.get('native_recording', {}).get('settled_dashboard_closed'),
            'native_over50ms': peaks, 'collector_cpu_seconds': report.get('collector_cpu_seconds'),
            'notes': ['Overlapping/nested spans must not be added into CPU execution time.',
                      'GPU completion is queued-work waiting, not exclusive GPU execution.',
                      'Thread cookies, submission references and OpenVR frame IDs are distinct.',
                      'Missing or inconsistent calibration suppresses native/event time matching.',
                      'Dropped spans can include stop-boundary events; their cause cannot be recovered from this header.',
                      'No long recorded wait is not proof that an unobserved path caused no delay.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--result', type=Path, required=True)
    parser.add_argument('--events', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output.resolve() in {args.result.resolve(), args.events.resolve()}:
        parser.error('Use a new output file; preserve the source evidence')
    if args.events.stat().st_size > HEADER.size + CAPACITY * EVENT.size:
        parser.error('Transport exceeds its bounded capacity')
    result = analyze(json.loads(args.result.read_text()), parse_transport(args.events.read_bytes()))
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'events': result['events'], 'clock_alignment': result['clock_alignment'],
                      'native_peak_windows': len(result['native_over50ms']), 'output': str(args.output)}))


if __name__ == '__main__':
    main()
