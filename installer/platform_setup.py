#!/usr/bin/env python3
"""Journal per-title compatibility settings; retain legacy Android restore support."""
import json
from pathlib import Path
import time

PROTON = 'proton_experimental'


def compat_get(rpc=None):
    if rpc is None:
        from steam_cdp import evaluate
        rpc = evaluate
    value = rpc('new Promise((resolve,reject)=>{let h;const t=setTimeout(()=>{h?.unregister();reject(Error("Compatibility settings timeout"))},5000);h=SteamClient.Apps.RegisterForAppDetails(438100,v=>{if(v.unAppID!==438100)return;clearTimeout(t);setTimeout(()=>h?.unregister(),0);resolve({effective:v.strCompatToolName,priority:v.nCompatToolPriority})})})')
    if not isinstance(value, dict) or not isinstance(value.get('effective'), str) or type(value.get('priority')) is not int:
        raise RuntimeError('SteamのVRChat互換設定を確認できません。')
    # 250 is the title-specific user override. Preserve unfamiliar priorities.
    if value['priority'] == 75:
        raise RuntimeError('特殊なVRChat互換設定を検出しました。既存設定を保持して停止します。')
    value['tool'] = value['effective'] if value['priority'] == 250 else ''
    return value


def compat_set(expected, name, get=None, put=None):
    get = get or compat_get
    if get() != expected:
        raise RuntimeError('VRChatの互換設定が別の操作で変更されました。上書きせず停止します。')
    if put is None:
        from steam_cdp import evaluate
        put = lambda n: evaluate('SteamClient.Apps.SpecifyCompatTool(438100,' + json.dumps(n) + ')')
    put(name)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        value = get()
        if value['tool'] == name:
            return value
        time.sleep(.2)
    raise RuntimeError('VRChat互換設定の保存を確認できません。')


def backup_path(record, steamapps):
    path = Path(record['backup'])
    parent = Path(steamapps) / 'compatdata'
    if parent.is_symlink() or path.is_symlink() or path.resolve().parent != parent.resolve() or not path.name.startswith('.frame-easy-original-'):
        raise RuntimeError('元データの保管場所が専用領域と一致しません。')
    return path


def prepare(steamapps, journal, save, copy, work, emit, *, deps=None, get=None, put=None, active=None):
    if deps is None:
        if __package__:
            from . import dependencies as deps
        else:
            import dependencies as deps
    get = get or compat_get
    active = active or (lambda: False)
    if active():
        raise RuntimeError('VRChatを完全に終了してください。')
    edition = deps.game_edition(steamapps)
    if __package__:
        from .dependencies import require_android_removed
    else:
        from dependencies import require_android_removed
    require_android_removed(edition)
    if edition['edition'] not in ('windows', 'missing'):
        raise RuntimeError(deps.edition_error(edition))
    if edition.get('steamapps') and Path(edition['steamapps']).resolve() != Path(steamapps).resolve():
        raise RuntimeError('別SteamライブラリのVRChatは移動せず停止します。')
    previous = get()
    record = {'original_edition': edition['edition'], 'previous': previous, 'target': PROTON,
              'change_requested': False, 'data_move_pending': False, 'data_moved': False,
              'data_restored': False, 'restored': False}
    journal['platform'] = record
    save()
    if previous['tool'] != PROTON:
        if active():
            raise RuntimeError('VRChatが起動されたため切り替えを停止しました。')
        record['change_requested'] = True
        save()
        record['selected'] = compat_set(previous, PROTON, get, put)
        save()
    if edition['edition'] != 'windows':
        emit('download-game', 'SteamからWindows版VRChatを取得します。規約の確認が出た場合はFrameで操作してください。')
        deps.ensure_game(steamapps, emit)
    current = deps.game_edition(steamapps)
    if current['edition'] != 'windows':
        raise RuntimeError('Windows版VRChatの取得を確認できません。Steamのダウンロード状況を確認してください。')


def restore(steamapps, journal, save, emit, *, deps=None, get=None, put=None, active=None):
    record = journal.get('platform')
    if not record or record.get('restored'):
        return
    if deps is None:
        if __package__:
            from . import dependencies as deps
        else:
            import dependencies as deps
    get = get or compat_get
    active = active or (lambda: False)
    if active():
        raise RuntimeError('VRChatを終了してから元の版へ戻してください。')
    previous = record['previous']
    current = get()
    if current['tool'] not in (PROTON, previous['tool']):
        raise RuntimeError('VRChatの互換ツールが変更されています。本人の選択を保持して停止します。')
    if record.get('backup'):
        backup = backup_path(record, steamapps)
        data = backup / 'android-data'
        original = Path(steamapps) / 'compatdata/438100'
        if data.exists():
            if data.is_symlink() or original.is_symlink():
                raise RuntimeError('Androidデータの復帰先がsymlinkです。保管したまま停止します。')
            # Retain a newly created Windows prefix, rather than overwriting either user's data.
            if original.exists():
                original.rename(backup / ('windows-data-' + str(time.time_ns())))
            data.rename(original)
            record['data_restored'] = True
            save()
        elif (record.get('data_moved') or record.get('data_move_pending')) and not record.get('data_restored'):
            identity = [original.stat().st_dev, original.stat().st_ino] if original.is_dir() and not original.is_symlink() else None
            if identity != record.get('data_identity'):
                raise RuntimeError('Androidデータの移動記録を確認できません。保管場所を確認してください。')
            record['data_restored'] = True
            save()
    if record.get('change_requested') and current['tool'] != previous['tool']:
        record['restore_requested'] = True
        save()
        compat_set(current, previous['tool'], get, put)
    if record['original_edition'] == 'android' and record.get('change_requested'):
        emit('restore-android', '元の互換設定へ戻しました。SteamがAndroid版を再取得するのを待っています。')
        deps.ensure_game(steamapps, emit, edition='android')
    record['restored'] = True
    save()
