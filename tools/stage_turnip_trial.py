#!/usr/bin/env python3
"""Stage private profiled Turnip autotuning only after an independent stereo image test."""
import argparse
import contextlib
import io
import json
from pathlib import Path
import re

from framectl import FEX_COMMIT, sha256
from stage_vulkan_trial import main as stage_vulkan

NATIVE_DRIVER = Path('/usr/lib/libvulkan_freedreno.so')
TUNING_KEYS = ('TU_AUTOTUNE_ALGO', 'TU_AUTOTUNE_FLAGS', 'TU_DEBUG', 'TU_DEBUG_FILE')


def main(source, image_proof):
    base = Path.home() / '.local/share/frame-pcvr'
    source = Path(source).resolve()
    source.relative_to((base / 'candidates').resolve())
    image_proof = Path(image_proof).resolve()
    image_proof.relative_to((base / 'runs').resolve())
    integration = json.loads((source / 'integration.json').read_text())
    if (integration.get('commit') != FEX_COMMIT or integration.get('state') != 'smoke-passed'
            or integration.get('vulkan_trial') != 'native' or integration.get('multiview_trial') != 'none'):
        raise RuntimeError('Use the validated native Vulkan source without transforming layers')
    proof = json.loads(image_proof.read_text())
    if (proof.get('candidate') != str(source) or proof.get('mode') != 'route'
            or proof.get('probe') != 'image' or proof.get('turnip_autotune_algo') != 'profiled'
            or proof.get('candidate_integration_sha256') != sha256(source / 'integration.json')
            or proof.get('native_driver_sha256') != sha256(NATIVE_DRIVER)):
        raise RuntimeError('Stereo proof is missing or belongs to another source/driver/tuning')
    cases = proof.get('cases', [])
    if {case.get('name') for case in cases} != {'native', 'guest-thunk-0', 'guest-thunk-1'} or len(cases) != 3:
        raise RuntimeError('All independent route controls must pass')
    for case in cases:
        log = case.get('log_tail', '')
        if case.get('returncode') != 0 or case.get('timed_out') is not False or 'Unknown TU_AUTOTUNE_ALGO' in log:
            raise RuntimeError('A probe failed or the driver ignored the tuning')
    guest = next(case for case in cases if case['name'] == 'guest-thunk-1')
    if len(re.findall(r'image=(?:2880x1440x1|1440x1440x2) mismatched_pixels=0 ', guest['log_tail'])) != 2:
        raise RuntimeError('Both stereo image layouts must match')
    manifest = json.loads((source / 'emulator.json').read_text())['emulator_v0']
    for section in ('environment', 'container_environment'):
        if any(key in manifest.get(section, {}) for key in TUNING_KEYS):
            raise RuntimeError('Existing Turnip tuning needs individual review')
    with contextlib.redirect_stdout(io.StringIO()):
        result = stage_vulkan(source, 'native')
    candidate = Path(result['candidate'])
    path = candidate / 'emulator.json'
    manifest = json.loads(path.read_text())
    manifest['emulator_v0']['container_environment']['TU_AUTOTUNE_ALGO'] = 'profiled'
    path.write_text(json.dumps(manifest, indent=2))
    path = candidate / 'integration.json'
    record = json.loads(path.read_text())
    record['extra_hashes']['emulator.json'] = sha256(candidate / 'emulator.json')
    record['turnip_trial'] = {'algorithm': 'profiled', 'image_proof': str(image_proof),
                              'native_driver_sha256': proof['native_driver_sha256'],
                              'render_resolution_changed': False, 'texture_settings_changed': False,
                              'benefit_verified': False, 'display_verified': False,
                              'populated_world_endurance_verified': False,
                              'tradeoff': 'Adaptive sampling can cause more variable frame times during tuning'}
    path.write_text(json.dumps(record, indent=2))
    result['turnip_autotune_algo'] = 'profiled'
    (Path(result['prefix']) / 'turnip-trial.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source')
    parser.add_argument('--image-proof', type=Path, required=True)
    args = parser.parse_args()
    main(args.source, args.image_proof)
