#!/usr/bin/env python3
"""Inspect the recipient's game edition and acquire only Steam-managed tools."""
import json
from pathlib import Path
import time
import zipfile

TOOLS = {1493710: 'Proton Experimental', 1826330: 'Proton EasyAntiCheat Runtime',
         3127680: 'FEX-Emu', 4183110: 'SteamLinuxRuntime 4'}
APPS = {438100: 'VRChat', **TOOLS}
# Running, validation, allocation, download, staging, commit or stop-in-progress.
ACTIVE_TOOL_UPDATE_FLAGS=256|131072|262144|524288|1048576|2097152|4194304|8388608
ANDROID_NOTICE = ('Android版VRChatを検出しました。PCVR版を導入するには、先にAndroid版のアンインストールが必要です。'
                  '削除するとローカルの設定・データを失う可能性があります。必要なデータは先に保管してください。')


class AndroidPresentError(RuntimeError):
    """The GUI must get explicit removal consent before retrying installation."""


def require_android_removed(info):
    if info['edition'] == 'android':
        raise AndroidPresentError(ANDROID_NOTICE)


def read_manifest(path):
    from framectl import at, read_vdf
    data = at(read_vdf(path), 'AppState')
    if not isinstance(data, dict):
        raise ValueError('Invalid Steam app manifest')
    return data


def steam_libraries(steamapps):
    """Resolve the recipient's declared libraries; never infer arbitrary paths."""
    from framectl import at, read_vdf
    steamapps=Path(steamapps)
    libraries={steamapps.resolve()}
    folders=steamapps/'libraryfolders.vdf'
    if folders.exists():
        data=at(read_vdf(folders),'libraryfolders')
        if not isinstance(data,dict):raise ValueError('Invalid Steam library metadata')
        for entry in data.values():
            name=at(entry,'path') if isinstance(entry,dict) else None
            if isinstance(name,str) and Path(name).is_absolute():
                libraries.add((Path(name)/'steamapps').resolve())
    return sorted(libraries)


def game_edition(steamapps):
    """Read files only. An APK and a Windows executable are never interchangeable."""
    from framectl import at, binary_arch
    steamapps = Path(steamapps)
    try:libraries=steam_libraries(steamapps)
    except (OSError,ValueError):return {'edition':'unknown','reason':'library_metadata'}
    manifests = [p / 'appmanifest_438100.acf' for p in sorted(libraries)
                 if (p / 'appmanifest_438100.acf').exists()]
    if not manifests:
        return {'edition': 'missing'}
    if len(manifests) != 1:
        return {'edition': 'ambiguous', 'reason': 'multiple_libraries', 'library_count': len(manifests)}
    manifest = manifests[0]
    try:
        info = read_manifest(manifest)
        relative = Path(at(info, 'installdir') or '')
        if at(info, 'appid') != '438100' or not relative.parts or relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Invalid VRChat install path')
        common = manifest.parent / 'common'
        game = common / relative
        game.resolve().relative_to(common.resolve())
        flags = int(at(info, 'StateFlags') or 0)
        if flags != 4:
            return {'edition': 'incomplete', 'reason': 'steam_state', 'state_flags': flags}
        executable = game / 'VRChat.exe'
        # Android depots may place the APK below another directory. Never follow
        # a file link out of this game's directory or inspect an unlimited set.
        apks = []
        for candidate in game.rglob('*'):
            if candidate.suffix.lower() != '.apk' or not candidate.is_file():
                continue
            if len(apks) >= 64:
                return {'edition': 'unknown', 'reason': 'too_many_apks', 'state_flags': flags}
            candidate.resolve().relative_to(game.resolve())
            apks.append(candidate)
        if executable.is_file():
            executable.resolve().relative_to(game.resolve())
        windows = executable.is_file() and binary_arch(executable) == 'PE-x86_64'
        android = False
        for apk in apks:
            with zipfile.ZipFile(apk) as archive:
                android |= 'AndroidManifest.xml' in archive.namelist()
        edition = 'mixed' if windows and android else 'windows' if windows else 'android' if android else 'unknown'
        return {'edition': edition, 'steamapps': str(manifest.parent), 'game': str(game),
                'state_flags': flags, 'windows_executable': 'valid' if windows else 'unrecognized' if executable.is_file() else 'missing',
                'apk_count': len(apks), 'reason': 'no_recognized_files' if edition == 'unknown' else None}
    except (OSError, ValueError, zipfile.BadZipFile):
        return {'edition': 'unknown', 'reason': 'unreadable_or_invalid_files'}


def edition_error(info):
    edition = info['edition']
    if edition == 'windows':
        return None
    if edition == 'android':
        return ANDROID_NOTICE
    if edition == 'missing':
        return 'VRChatが未導入です。インストール時にSteamからWindows版を取得します。'
    if edition == 'incomplete':
        return ('VRChatの取得・更新が未完了です（StateFlags=' + str(info.get('state_flags', '?')) + '）。'
                'FrameのSteamのダウンロード画面で完了させ、VRChatを終了してから再実行してください。既存データは変更していません。')
    if edition == 'mixed':
        return 'VRChatのWindows版とAndroid版のファイルが混在しています。SteamでVRChatのファイル整合性を確認してください。推測で削除せず停止しました。'
    if edition == 'ambiguous':
        return '複数のSteamライブラリにVRChatがあります。Steamで使用する配置を確認してください。既存データは変更していません。'
    if info.get('reason') == 'library_metadata':
        return 'Steamライブラリの情報を読み取れません。Steamのダウンロード完了を確認してFrameを再起動し、再実行してください。既存データは変更していません。'
    detail = ('（StateFlags=' + str(info.get('state_flags', '?')) +
              ' / Windows実行ファイル=' + {'valid': '確認済み', 'missing': 'なし', 'unrecognized': '形式不明'}.get(info.get('windows_executable'), '未確認') +
              ' / APK数=' + str(info.get('apk_count', '?')) + '）')
    return ('VRChatの版を確認できません' + detail + '。'
            'FrameのSteamでVRChatのファイル整合性とダウンロード完了を確認してください。既存データを変更せず停止しました。')


def tool_status(steamapps, appid):
    if appid not in TOOLS:
        raise ValueError('Not an installer dependency')
    try:
        from framectl import at
        libraries=steam_libraries(steamapps)
        manifests=[p/('appmanifest_'+str(appid)+'.acf') for p in libraries
                   if (p/('appmanifest_'+str(appid)+'.acf')).is_file()]
        result={'complete':False,'manifest_count':len(manifests),'reason':'missing'}
        if not manifests:return result
        if len(manifests)!=1:return dict(result,reason='multiple_libraries')
        manifest=manifests[0]
        info = read_manifest(manifest)
        relative = Path(at(info, 'installdir') or '')
        flags=int(at(info,'StateFlags') or 0)
        result.update(state_flags=flags,paused=bool(flags&512),active_update=bool(flags&ACTIVE_TOOL_UPDATE_FLAGS))
        if at(info, 'appid') != str(appid):return dict(result,reason='invalid_manifest')
        if not relative.parts or relative.is_absolute() or '..' in relative.parts:
            return dict(result,reason='invalid_path')
        target = manifest.parent / 'common' / relative
        target.resolve().relative_to((manifest.parent / 'common').resolve())
        result.update(target_present=target.is_dir(),path=str(target))
        if not target.is_dir():return dict(result,reason='files_missing')
        if appid==1493710 and not all((target/n).is_file() for n in ['proton','version']):
            return dict(result,reason='files_missing')
        # FullyInstalled (4) can coexist with UpdateRequired (2) or AppRunning
        # (64). These do not mean files are absent. Never reuse active updates,
        # paused/incomplete downloads, corruption, uninstall or unknown flags.
        if not flags&4 or flags&~(4|2|64):return dict(result,reason='steam_state')
        return dict(result,complete=True,reason='installed')
    except (OSError, ValueError):
        return {'complete':False,'reason':'unreadable_metadata','manifest_count':0}


def tool_complete(steamapps,appid):
    return tool_status(steamapps,appid)['complete']


def tool_diagnostic(info):
    """Shareable numeric state, excluding install paths and account fields."""
    return ('StateFlags='+str(info.get('state_flags','?'))+
            ' / manifest数='+str(info.get('manifest_count','?'))+
            ' / 配置='+('あり' if info.get('target_present') else '未確認')+
            ' / 検査='+str(info.get('reason','unknown')))


def wizard(action, appid, rpc=None):
    if action not in ('start', 'read', 'continue', 'resume', 'remove') or appid not in APPS or (action == 'remove' and appid != 438100):
        raise ValueError('Unexpected dependency operation')
    if rpc is None:
        from steam_cdp import evaluate
        rpc = evaluate
    source = (Path(__file__).parent / 'steam_install.js').read_text()
    return rpc('(' + source + ')(' + json.dumps({'action': action, 'appid': appid}) + ')')


def remove_android(steamapps, emit, *, confirmed=False, inspect=None, operation=None,
                   active=None, clock=time.monotonic, sleep=time.sleep, timeout=180):
    """Only an explicitly confirmed Steam-managed Android VRChat may be removed."""
    inspect = inspect or game_edition
    operation = operation or wizard
    active = active or (lambda: False)
    if not confirmed:
        raise AndroidPresentError(ANDROID_NOTICE)
    info = inspect(steamapps)
    if info['edition'] != 'android':
        raise RuntimeError('VRChatの版が変わりました。削除せず、最初からやり直してください。')
    if Path(info['steamapps']).resolve() != Path(steamapps).resolve():
        raise RuntimeError('別SteamライブラリのVRChatは削除せず停止します。')
    if active():
        raise RuntimeError('VRChatを完全に終了してから再実行してください。')
    emit('remove-android', '確認を受け付けました。SteamでAndroid版VRChatをアンインストールしています。')
    operation('remove', 438100)
    deadline = clock() + timeout
    while True:
        current = inspect(steamapps)['edition']
        if current == 'missing':
            emit('remove-android', 'Android版のアンインストールを確認しました。PCVR版の導入へ進みます。')
            return
        if current not in ('android', 'incomplete') or active():
            raise RuntimeError('VRChatの状態が変わりました。削除要求を繰り返さず停止します。')
        if clock() >= deadline:
            raise RuntimeError('Android版の削除完了を確認できません。Steamの状態を確認してから再実行してください。')
        sleep(1)


def ensure_game(steamapps, emit, *, edition='windows', inspect=None, operation=None,
                clock=time.monotonic, sleep=time.sleep, timeout=3600):
    if edition not in ('windows', 'android'):
        raise ValueError('Unexpected VRChat edition')
    inspect = inspect or game_edition
    operation = operation or wizard
    if inspect(steamapps)['edition'] == edition:
        return
    operation('start', 438100)
    deadline = clock() + timeout
    next_notice = 0
    resumed = False
    while inspect(steamapps)['edition'] != edition:
        if clock() >= deadline:
            raise RuntimeError('VRChatの版の切り替えが完了しません。Steamのダウンロード状況を確認してください。')
        status = operation('read', 438100)
        phase = status.get('phase')
        if phase in ('foreign', 'failed', 'canceled', 'unknown'):
            raise RuntimeError('VRChatの取得が中断されました。Steamのダウンロード状況を確認してください。')
        if phase == 'config':
            operation('continue', 438100)
        elif phase == 'idle' and not resumed:
            operation('resume', 438100)
            resumed = True
        if clock() >= next_notice:
            emit('download-game', 'SteamでVRChatを取得しています。規約や確認画面が出たらFrameで操作してください。')
            next_notice = clock() + 20
        sleep(1)


def ensure_tools(steamapps, emit, *, complete=None, operation=None,
                 clock=time.monotonic, sleep=time.sleep, timeout=3600):
    """Resume after the recipient handles a Steam EULA. Never accept one here."""
    inspect=tool_status if complete is None else None
    complete = complete or tool_complete
    operation = operation or wizard
    for appid, label in TOOLS.items():
        if complete(steamapps, appid):
            emit('download-tools',label+'は導入済みです。既存の配置を使います。')
            continue
        if inspect:
            info=inspect(steamapps,appid)
            if info['reason'] in ('multiple_libraries','invalid_manifest','invalid_path','unreadable_metadata'):
                raise RuntimeError(label+'の導入情報を確認できません（'+tool_diagnostic(info)+'）。Steamの配置を確認してください。既存部品は変更しません。')
            emit('download-tools',label+'の検査: '+tool_diagnostic(info))
        emit('download-tools', label + 'をSteamから取得します。')
        operation('start', appid)
        deadline = clock() + timeout
        next_notice = 0;resumed=False;idle_since=None
        while not complete(steamapps, appid):
            if clock() >= deadline:
                raise RuntimeError(label + 'の導入待ち時間が終了しました。Steamのダウンロード状況を確認してください。')
            status = operation('read', appid)
            phase = status.get('phase')
            if phase in ('foreign', 'failed', 'canceled', 'unknown'):
                raise RuntimeError(label + 'の導入を中断しました。別のSteam操作やエラーを確認してください。')
            if phase == 'config':
                operation('continue', appid)
            info=inspect(steamapps,appid) if inspect else None
            if phase in ('idle','complete') and not resumed and not (info and (info.get('active_update') or info['complete'])):
                operation('resume',appid);resumed=True
            # A completed/idle wizard is not proof that a dependency exists.
            # Avoid an hour of identical messages when no files were installed.
            if phase in ('idle','complete') and info and not info.get('active_update'):
                idle_since=clock() if idle_since is None else idle_since
                if clock()-idle_since>=120:
                    raise RuntimeError(label+'の取得が進んでいません（'+tool_diagnostic(info)+'）。FrameのSteamでダウンロードを再開し、完了を確認してください。')
            else:idle_since=None
            if clock() >= next_notice:
                message = ('Frameに表示されたSteamの規約・確認画面を確認してください。同意後は自動で続行します。'
                           if phase == 'confirmation' else
                           label + 'のダウンロード・導入を待っています。PCを閉じず接続を維持してください。'+(' '+tool_diagnostic(info) if info else ''))
                emit('download-tools', message)
                next_notice = clock() + 20
            sleep(1)
        emit('download-tools', label + 'のSteam導入を確認しました。')
