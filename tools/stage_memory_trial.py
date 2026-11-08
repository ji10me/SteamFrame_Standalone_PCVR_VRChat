#!/usr/bin/env python3
"""Stage isolated VRChat memory experiments with explicit quality tradeoffs."""
import argparse
import contextlib
import io
import json
import re
from pathlib import Path

from framectl import sha256
from stage_vulkan_trial import main as stage_vulkan

VRAM_REPORT_PROFILES = {'dxgi-report-4096': 4096, 'dxgi-report-2048': 2048}
PROFILES = ('pipeline-lifetime', 'no-xalia', 'compiler-threads-2', 'no-disk-cache', 'no-profile-stats', *VRAM_REPORT_PROFILES)


def main(source, observation, profile='pipeline-lifetime'):
    if profile not in PROFILES:
        raise RuntimeError('Unknown memory profile')
    report_target = VRAM_REPORT_PROFILES.get(profile)
    base = Path.home() / '.local/share/frame-pcvr'
    source = Path(source).resolve()
    observation = Path(observation).resolve()
    observation.relative_to((base / 'runs').resolve())
    baseline = json.loads(observation.read_text())
    if baseline.get('appid') != '438100' or baseline.get('candidate') != str(source):
        raise RuntimeError('Baseline must identify this VRChat candidate')
    if profile == 'pipeline-lifetime' and not baseline.get('pipeline_lifetime_supported'):
        raise RuntimeError('Installed DXVK has not been checked for pipeline lifetime support')
    # Preserve the complete effective configuration, including Steam overrides.
    config = baseline.get('DXVK_CONFIG', '')
    config_toggle = {'no-disk-cache': ('DiskCache', 'FEX_DISKCACHE'),
                     'no-profile-stats': ('ProfileStats', 'FEX_PROFILESTATS')}.get(profile)
    if config_toggle:
        option, override = config_toggle
        template = json.loads((source / 'ConfigTemplate.json').read_text())
        active_config = json.loads((Path(baseline['prefix']) / 'fex-emu/Config.json').read_text())
        manifest = json.loads((source / 'emulator.json').read_text())['emulator_v0']
        if template.get('Config', {}).get(option) != '1' or active_config.get('Config', {}).get(option) != '1':
            raise RuntimeError('Both the source template and prefix must have ' + option + ' enabled')
        for section in ('environment', 'container_environment'):
            if override in manifest.get(section, {}):
                raise RuntimeError('An existing ' + option + ' environment override needs individual review')
        if baseline.get(override) is not None:
            raise RuntimeError('The measured launch has a ' + option + ' environment override')
    if profile == 'pipeline-lifetime' and 'trackPipelineLifetime' in config:
        raise RuntimeError('An existing lifetime override needs individual review')
    if profile == 'compiler-threads-2':
        if not baseline.get('compiler_threads_supported'):
            raise RuntimeError('Installed DXVK has not been checked for compiler thread support')
        if re.search(r'(?:^|[;\n])\s*dxvk\.numCompilerThreads\s*=', config):
            raise RuntimeError('An existing compiler thread override needs individual review')
    if report_target is not None:
        if not baseline.get('dxgi_memory_reporting_supported'):
            raise RuntimeError('Installed DXGI has not been checked for memory reporting support')
        reported = baseline.get('reported_device_memory_MiB')
        if type(reported) is not int or reported <= report_target:
            raise RuntimeError(f'A measured VRChat VRAM report above {report_target} MiB is required')
        if re.search(r'(?:^|[;\n])\s*dxgi\.maxDeviceMemory\s*=', config):
            raise RuntimeError('An existing reported VRAM override needs individual review')
    with contextlib.redirect_stdout(io.StringIO()):
        result = stage_vulkan(source, 'native', source_prefix=baseline['prefix'])
    candidate = Path(result['candidate'])
    manifest_path = candidate / 'emulator.json'
    manifest = json.loads(manifest_path.read_text())
    env = manifest['emulator_v0']['container_environment']
    env['DXVK_CONFIG'] = config
    if profile == 'pipeline-lifetime':
        env['DXVK_CONFIG'] = config.rstrip('; ') + ('; ' if config.strip() else '') + 'dxvk.trackPipelineLifetime = True'
    elif profile == 'no-xalia':
        env['PROTON_USE_XALIA'] = '0'
    elif profile == 'compiler-threads-2':
        env['DXVK_CONFIG'] = config.rstrip('; ') + ('; ' if config.strip() else '') + 'dxvk.numCompilerThreads = 2'
    elif config_toggle:
        # The compat wrapper recreates Config.json from this private template.
        # Change both so the selection survives that regeneration.
        template_path = candidate / 'ConfigTemplate.json'
        template = json.loads(template_path.read_text())
        template['Config'][option] = '0'
        template_path.write_text(json.dumps(template, indent=2))
        config_path = Path(result['prefix']) / 'fex-emu/Config.json'
        active_config = json.loads(config_path.read_text())
        active_config['Config'][option] = '0'
        config_path.write_text(json.dumps(active_config, indent=2))
    elif report_target is not None:
        env['DXVK_CONFIG'] = config.rstrip('; ') + ('; ' if config.strip() else '') + f'dxgi.maxDeviceMemory = {report_target}'
    else:
        raise RuntimeError('Unknown memory profile')
    # Info logging records the selected DXVK options, without Proton tracing.
    env['DXVK_LOG_LEVEL'] = 'info'
    manifest_path.write_text(json.dumps(manifest, indent=2))
    integration_path = candidate / 'integration.json'
    integration = json.loads(integration_path.read_text())
    integration['extra_hashes']['emulator.json'] = sha256(manifest_path)
    if config_toggle:
        integration['extra_hashes']['ConfigTemplate.json'] = sha256(candidate / 'ConfigTemplate.json')
    integration['memory_trial'] = {
        'profile': profile, 'baseline': str(observation),
        'render_quality_changed': False, 'benefit_verified': False,
    }
    if profile == 'compiler-threads-2':
        integration['memory_trial'].update(
            target='transient compilation memory, not a steady-state memory cap',
            tradeoff='shader compilation can take longer or cause more stutter',
            display_verified=False,
        )
    if profile == 'no-disk-cache':
        integration['memory_trial'].update(
            target='per-process disk-cache index allocations; in-memory JIT execution remains enabled',
            tradeoff='startup and first-time code compilation may take longer',
            cpu_compatibility_options_changed=False,
            shared_cache_deleted=False,
            display_verified=False,
            game_memory_reduction_verified=False,
        )
    if profile == 'no-profile-stats':
        integration['memory_trial'].update(
            target='FEX diagnostic counter collection only; no CPU compatibility or image-quality option changed',
            tradeoff='FEX shared diagnostic counters are unavailable for this private launch',
            cpu_compatibility_options_changed=False,
            disk_cache_setting_changed=False,
            display_verified=False,
            game_memory_reduction_verified=False,
            game_cpu_reduction_verified=False,
        )
    if report_target is not None:
        memory = integration['memory_trial']
        del memory['render_quality_changed']
        memory.update(
            render_quality_may_change=True,
            resolution_msaa_settings_changed=False,
            reported_device_memory_MiB=report_target,
            measured_baseline_device_memory_MiB=baseline['reported_device_memory_MiB'],
            target='application mipmap streaming decisions via the DXGI adapter description',
            hard_allocation_cap=False,
            application_streaming_budget_verified=False,
            texture_residency_reduction_verified=False,
            tradeoff='resident texture mip levels may become less detailed; application-dependent',
            display_verified=False,
        )
    integration_path.write_text(json.dumps(integration, indent=2))
    result.update(memory_profile=profile, baseline=str(observation))
    (Path(result['prefix']) / 'memory-trial.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source')
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--profile', choices=PROFILES, default='pipeline-lifetime')
    args = parser.parse_args()
    main(args.source, args.baseline, args.profile)
