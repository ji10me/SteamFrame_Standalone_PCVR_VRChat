#!/usr/bin/env python3
"""Transactional, user-only Frame installation. No account data is packaged."""
import argparse
import contextlib
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shlex
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True
KIT = Path(__file__).resolve().parent
sys.path.insert(0, str(KIT / 'support'))
VERSION = 'alpha-dfr-h264-swap-20261109-0.3.5'
FEX_COMMIT = '6effd2384085818345ec32e6abb4181c7135a9a7'
RUNTIME = ('FEX', 'FEXBash', 'FEXGetConfig', 'FEXOfflineCompiler', 'FEXServer', 'FEXServerManager', 'FEXpidof')
DFR_HASHES = {'d3d11.dll': 'a112b719c417c78abe13de88bd6c9be45aac252a9c153ad432740b0a44298db0',
              'dxgi.dll': 'ae23062e195df360abbed15cafba0e2814324c920c2f40fc8b4865538538d89d'}
GST_HASH = '3f6417451f0060f5d573fded651b4fac9c751f0226e45ce6fcb6c958cd6643a6'
INSTALL_TEMPERATURE_LIMIT = 95
BUILD_PAUSE_TEMPERATURE = 92
BUILD_RESUME_TEMPERATURE = 85
MF_HASH = 'e4c80315b6d1bc9ad80c1ec3a19ec0247d7435082d18a0f8938bd00577c398e0'

def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''): h.update(block)
    return h.hexdigest()

def emit(stage, message):
    print(json.dumps({'stage': stage, 'message': message}, ensure_ascii=False), flush=True)

def atomic(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + '.new')
    with temp.open('x', encoding='utf-8') as f:
        os.chmod(temp, 0o600)
        json.dump(value, f, indent=2, ensure_ascii=False)
        f.flush(); os.fsync(f.fileno())
    os.replace(temp, path)

def base(home=None): return Path(home or Path.home()) / '.local/share/frame-pcvr-easy'

def verify_kit(kit=KIT):
    kit = Path(kit)
    manifest = json.loads((kit / 'MANIFEST.json').read_text())
    if manifest.get('format') != 'frame-easy-dfr-h264-v1': raise RuntimeError('パッケージの形式が違います。')
    expected = set(manifest['files']) | {'MANIFEST.json'}
    present = {p.relative_to(kit).as_posix() for p in kit.rglob('*') if p.is_file() or p.is_symlink()}
    if expected != present: raise RuntimeError('パッケージに不足・追加ファイルがあります。')
    for n, h in manifest['files'].items():
        p = kit / n
        if Path(n).is_absolute() or '..' in Path(n).parts or p.is_symlink() or sha(p) != h:
            raise RuntimeError('パッケージのチェックサムが一致しません。')
    if ({n: sha(kit / 'payload/dfr' / n) for n in DFR_HASHES} != DFR_HASHES or
        sha(kit / 'payload/video/libgstvideo4linux2.so') != GST_HASH or
        sha(kit / 'payload/wine/mf.dll') != MF_HASH):
        raise RuntimeError('確認済みDFR・動画部品と異なります。')
    return manifest

def game_active(proc=Path('/proc')):
    for p in proc.glob('[0-9]*'):
        try:
            env = dict(v.split(b'=', 1) for v in (p/'environ').read_bytes().split(b'\0') if b'=' in v)
            if env.get(b'SteamAppId') == b'438100' or env.get(b'SteamGameId') == b'438100': return True
        except OSError: pass
    return False

def options_get():
    from steam_cdp import evaluate
    value = evaluate('new Promise((resolve,reject)=>{let h;const t=setTimeout(()=>{h?.unregister();reject(new Error("VRChat settings timeout"))},5000);h=SteamClient.Apps.RegisterForAppDetails(438100,v=>{if(v.unAppID!==438100)return;clearTimeout(t);setTimeout(()=>h?.unregister(),0);resolve(v.strLaunchOptions)})})')
    if not isinstance(value, str): raise RuntimeError('SteamのVRChat起動設定を読み取れません。')
    return value

def options_set(value):
    from steam_cdp import evaluate
    evaluate('SteamClient.Apps.SetAppLaunchOptions(438100,' + json.dumps(value) + ')')
    deadline = time.monotonic()+5
    while time.monotonic() < deadline:
        if options_get() == value: return
        time.sleep(.2)
    raise RuntimeError('Steam起動設定の保存を確認できません。')

def set_options_checked(old, new, get=None, put=None):
    get=get or options_get;put=put or options_set
    if get() != old: raise RuntimeError('起動設定が別の操作で変更されました。上書きせず停止します。')
    put(new)

def sensors():
    values = [int(p.read_text())/1000 for p in Path('/sys/class/thermal').glob('thermal_zone*/temp')]
    if not values or not all(-20 < v < 150 for v in values): raise RuntimeError('温度センサーを確認できません。')
    mem = next(int(l.split()[1])*1024 for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:'))
    return max(values), mem

def resource_error(temp, mem, free, elapsed, timeout, label, *, startup=False):
    """Only numeric installation diagnostics; no account data or raw logs."""
    ram_limit = (2 if startup else 1)*1024**3
    reason = ('温度上限'+str(INSTALL_TEMPERATURE_LIMIT)+'℃' if temp >= INSTALL_TEMPERATURE_LIMIT else
              '空きRAM不足（必要量'+str(ram_limit//1024**3)+'GiB）' if mem < ram_limit else
              '空き容量不足（必要量512MiB）' if free < 512*1024**2 else
              '作業時間の上限' if elapsed > timeout else None)
    if reason is None:return None
    advice = ('Frameを通気のよい場所で休ませてから再実行してください。' if temp >= INSTALL_TEMPERATURE_LIMIT else
              'VRChatや他のアプリを終了してから再実行してください。' if mem < ram_limit else
              'Frameのストレージ空き容量を増やしてから再実行してください。' if free < 512*1024**2 else
              '通信状態とFrame内の導入ログを確認してください。')
    return (label+'を停止: '+reason+'。温度='+format(temp,'.1f')+'℃、空きRAM='+format(mem/1024**3,'.2f')+
            'GiB、空き容量='+format(free/1024**3,'.2f')+'GiB、作業時間='+str(int(elapsed))+'秒。'+advice+
            ' 共有Steam部品と起動設定は上書きしません。')

def run(command, folder, label, env=None, timeout=3600, cool_build=False):
    """Keep compiler output on the recipient, bound process group and resources."""
    temp, mem = sensors()
    error = resource_error(temp,mem,shutil.disk_usage(folder).free,0,timeout,label,startup=True)
    if error:raise RuntimeError(error)
    # This supervises only our own build process group, never Steam or the game.
    cooling_total=0.0
    if cool_build and temp>=BUILD_PAUSE_TEMPERATURE:
        emit('cooling','FEX作成前の温度調整を待っています。85℃以下で開始します。電力モード・クロックは変更しません。')
        while temp>BUILD_RESUME_TEMPERATURE:
            if cooling_total>=900:raise RuntimeError('FEXの温度調整が15分以内に完了しません。Frameを通気のよい場所で休ませて再実行してください。')
            time.sleep(1);cooling_total+=1
            temp,mem=sensors()
            error=resource_error(temp,mem,shutil.disk_usage(folder).free,0,timeout,label,startup=True)
            if error:raise RuntimeError(error)
    before_build_cooling=cooling_total
    with (folder/(label+'.log')).open('x') as log:
        child = subprocess.Popen(list(map(str, command)), stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                 env=env, start_new_session=True)
        started = time.monotonic();notice=started+20;paused_at=None
        try:
            while child.poll() is None:
                temp, mem = sensors()
                now=time.monotonic()
                paused_seconds=now-paused_at if paused_at is not None else 0
                elapsed=now-started-(cooling_total-before_build_cooling+paused_seconds)
                error=resource_error(temp,mem,shutil.disk_usage(folder).free,max(0,elapsed),timeout,label)
                if error:raise RuntimeError(error)
                if cool_build:
                    if paused_at is None and temp>=BUILD_PAUSE_TEMPERATURE:
                        with contextlib.suppress(ProcessLookupError):os.killpg(child.pid,signal.SIGSTOP)
                        paused_at=now
                        emit('cooling','温度'+format(temp,'.1f')+'℃のためFEX作成だけを一時停止しました。85℃以下で自動再開します。')
                    elif paused_at is not None and temp<=BUILD_RESUME_TEMPERATURE:
                        with contextlib.suppress(ProcessLookupError):os.killpg(child.pid,signal.SIGCONT)
                        cooling_total+=now-paused_at;paused_at=None
                        emit('build-fex','温度'+format(temp,'.1f')+'℃を確認し、FEX作成を再開しました。')
                    if cooling_total+(now-paused_at if paused_at is not None else 0)>=900:
                        raise RuntimeError('FEX作成中の温度調整が合計15分に達しました。Frameを通気のよい場所で休ませて再実行してください。')
                if now>=notice:
                    message=('温度調整を待っています。' if paused_at is not None else '専用環境の作成を続けています。')
                    emit('working',message+' 温度='+format(temp,'.1f')+'℃、空きRAM='+format(mem/1024**3,'.2f')+'GiB。接続を維持してください。')
                    notice=now+20
                time.sleep(.2)
            if child.returncode: raise RuntimeError(label+'に失敗しました。Frame内の導入ログを確認してください。')
        finally:
            if child.poll() is None:
                with contextlib.suppress(ProcessLookupError):os.killpg(child.pid, signal.SIGTERM)
                if paused_at is not None:
                    with contextlib.suppress(ProcessLookupError):os.killpg(child.pid, signal.SIGCONT)
                try: child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    with contextlib.suppress(ProcessLookupError):os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=5)

def paths(home=None):
    home = Path(home or Path.home()); steam = home/'.local/share/Steam/steamapps'
    return {'home':home, 'base':base(home), 'steam':steam, 'standard':steam/'common/FEX-Emu',
            'proton':steam/'common/Proton - Experimental', 'prefix':steam/'compatdata/438100'}

def dependency_module():
    if __package__:
        from . import dependencies
    else:
        import dependencies
    return dependencies

def platform_module():
    if __package__:
        from . import platform_setup
    else:
        import platform_setup
    return platform_setup

def swap_module():
    if __package__:
        from . import swap_setup
    else:
        import swap_setup
    return swap_setup

def install_swap_helper(root):
    source=KIT/'swap_setup.py';target=Path(root)/'swap_admin.py'
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not target.is_file() or target.stat().st_uid!=os.getuid() or sha(target)!=sha(source):
            raise RuntimeError('補助スワップhelperが変更されています。保持して停止します。')
        return
    with target.open('xb') as stream:
        os.fchmod(stream.fileno(),0o600);stream.write(source.read_bytes())

def prepare_existing_game(p, uninstall_android=False):
    deps=dependency_module();edition=deps.game_edition(p['steam'])
    if edition.get('steamapps') and Path(edition['steamapps']).resolve()!=p['steam'].resolve():
        raise RuntimeError('別SteamライブラリのVRChatは変更せず停止します。')
    if edition['edition']=='android':
        if not uninstall_android:deps.require_android_removed(edition)
        deps.remove_android(p['steam'],emit,confirmed=True,active=game_active)
    elif edition['edition'] not in ('windows','missing'):
        raise RuntimeError(deps.edition_error(edition))

def prepare_dependencies(journal,save,release,p):
    if platform.system() != 'Linux' or platform.machine() not in ('aarch64','arm64') or os.geteuid() == 0:
        raise RuntimeError('Steam Frameの通常ユーザーで実行してください。')
    if game_active():raise RuntimeError('VRChatを完全に終了してから再実行してください。')
    deps=dependency_module()
    deps.require_android_removed(deps.game_edition(p['steam']))
    deps.ensure_tools(p['steam'],emit)
    platform_module().prepare(p['steam'],journal,save,copy_tree,release/'work',emit,deps=deps,active=game_active)

def preflight():
    p = paths(); errors = []
    if platform.system() != 'Linux' or platform.machine() not in ('aarch64','arm64') or os.geteuid() == 0:
        errors.append('Steam Frameの通常ユーザーで実行してください。')
    if game_active(): errors.append('VRChatを完全に終了してから再実行してください。')
    for tool in ('python3','clang','clang++','git','cmake','ninja','ld.lld','patchelf','cp'):
        if not shutil.which(tool): errors.append('必要な開発ツールがありません: '+tool)
    for n,label in [('438100','VRChat（Windows版）'),('1493710','Proton Experimental'),('1826330','Proton EasyAntiCheat Runtime'),('3127680','FEX-Emu'),('4183110','SteamLinuxRuntime 4')]:
        if not (p['steam']/('appmanifest_'+n+'.acf')).is_file(): errors.append('Steamで先にインストールしてください: '+label)
    try:
        deps=dependency_module();edition=deps.game_edition(p['steam']);error=deps.edition_error(edition)
        if error:errors.append(error)
        elif Path(edition['steamapps']).resolve()!=p['steam'].resolve():errors.append('別SteamライブラリのVRChatは、この版では移動せず停止します。')
    except (ImportError,OSError,ValueError,KeyError,TypeError):errors.append('VRChatの版を確認できません。既存データを変更せず停止します。')
    for n in ['fex-compat-tool','emulator.json','ConfigTemplate.json','usr/share/fex-emu/ThunksDB.json']:
        if not (p['standard']/n).is_file(): errors.append('FEXの標準配置を確認できません: '+n)
    if not Path('/opt/steamvr/bin/linuxarm64/libopenvr_api.so').exists(): errors.append('FrameのARM64 SteamVRがありません。')
    if not Path('/usr/share/guestos/fex-mesa/usr/lib/libvulkan.so.1').exists(): errors.append('Frameのx86ゲスト環境がありません。')
    if (KIT/'compatibility.json').is_file():
        tested=json.loads((KIT/'compatibility.json').read_text())
        for group,directory in [('standard_fex',p['standard']),('standard_proton',p['proton'])]:
            for n,h in tested[group].items():
                try:match=sha(directory/n)==h
                except OSError:match=False
                if not match:
                    errors.append('SteamのFEX/Protonが確認済み版と異なります。互換性確認前に上書きしないため停止します。');break
    if shutil.disk_usage(p['home']).free < 12*1024**3: errors.append('専用ProtonとPrefixの作成に空き容量12GiB以上が必要です。')
    try: options_get()
    except Exception: errors.append('Steamを起動し、開発者モードを有効にしてください。Steamのローカル設定APIへ接続できません。')
    return {'ready':not errors,'errors':errors,'read_only':True}

def copy_tree(source, target, work, label):
    if target.exists() or target.is_symlink(): raise RuntimeError('新しい専用配置が必要です。')
    target.parent.mkdir(parents=True, exist_ok=True)
    run(['cp','-a','--reflink=auto',str(source)+'/.',target],work,label,timeout=600)

def audited_local_candidate(home, require_dfr=False):
    # Reuse only this recipient's own, hash-verified build. Nothing is exported.
    root=home/'.local/share/frame-pcvr/candidates'
    for record in sorted(root.glob('*/integration.json')):
        try:
            j=json.loads(record.read_text());c=record.parent
            if j.get('state')!='smoke-passed' or j.get('commit')!=FEX_COMMIT or not j.get('standard_unchanged'): continue
            if set(j.get('runtime_hashes',{}))!=set(RUNTIME): continue
            if not all(sha(c/'usr/bin'/n)==h for n,h in j['runtime_hashes'].items()):continue
            if not all(sha(home/'.local/share/Steam/steamapps/common/FEX-Emu'/n)==h for n,h in j['standard_hashes'].items()):continue
            if not all(sha(c/n)==h for n,h in j.get('extra_hashes',{}).items()):continue
            if require_dfr:
                private=j.get('private_proton',{});hashes=private.get('private_hashes',{})
                if not all(hashes.get('files/lib/wine/dxvk/x86_64-windows/'+n)==h for n,h in DFR_HASHES.items()):continue
                proton=Path(private['path'])
                proton.resolve().relative_to((home/'.local/share/frame-pcvr/proton').resolve())
                if not all(sha(proton/n)==h for n,h in hashes.items()):continue
            return c
        except (OSError,ValueError,KeyError):continue
    return None

def prepare_runtime(release, p):
    runtime=release/'runtime'; standard=p['standard']; work=release/'work'
    runtime.mkdir(mode=0o700)
    for n in ('emulator.json','fex-compat-tool','unshare.py','toolmanifest.vdf'):
        shutil.copy2(standard/n,runtime/n)
    for n in ('usr/lib','usr/share'):copy_tree(standard/n,runtime/n,work,'copy-'+n.replace('/','-'))
    stage=audited_local_candidate(p['home'])
    if stage is None:
        emit('build-fex','初回用のFEXを作成しています。時間がかかる場合があります。')
        env=dict(os.environ,FEX_FRAME_WORK=str(release/'build-fex'),FEX_FRAME_JOBS='1',PYTHONDONTWRITEBYTECODE='1')
        run(['sh',KIT/'support/scripts/build-fex-stage.sh'],work,'build-fex',env,timeout=7200,cool_build=True)
        stages=list((release/'build-fex').glob('*/stage'))
        if len(stages)!=1:raise RuntimeError('FEXのビルド結果を特定できません。')
        stage=stages[0]
    (runtime/'usr/bin').mkdir()
    for n in RUNTIME:shutil.copy2(stage/'usr/bin'/n,runtime/'usr/bin'/n)
    for n in ('FEXCompatTool','ConfigTemplate.json','VERSIONS.txt'):shutil.copy2(stage/n,runtime/n)
    template=runtime/'ConfigTemplate.json';value=json.loads(template.read_text())
    value['Config'].update(DiskCache='0',ProfileStats='0');template.write_text(json.dumps(value,indent=2)+'\n')
    wrapper=runtime/'fex-compat-tool';text=wrapper.read_text();needle="    subprocess.call(['/usr/bin/printenv'])"
    if text.count(needle)!=1:raise RuntimeError('FEXラッパーが確認済み版と異なります。')
    wrapper.write_text(text.replace(needle,'    # Whole-environment logging disabled for this private installation.'));wrapper.chmod(0o700)
    guest=runtime/'usr/share/fex-emu/GuestThunks';dependency=guest/'libframe_vulkan_thunk_base.so'
    (guest/'libvulkan-guest.so').resolve().relative_to(runtime.resolve())
    shutil.copy2(guest/'libvulkan-guest.so',dependency)
    run(['patchelf','--set-soname',dependency.name,dependency],work,'thunk-soname',timeout=30)
    sysroot='/usr/share/guestos/fex-mesa'
    run(['clang','--target=x86_64-linux-gnu','--sysroot='+sysroot,'-fuse-ld=lld','-shared','-fPIC','-nostdlib',
         KIT/'support/vulkan_lookup_adapter.c','-L'+str(guest),'-Wl,--no-as-needed','-l:'+dependency.name,'-L'+sysroot+'/usr/lib','-lc','-Wl,-z,defs','-Wl,-rpath,'+str(guest),'-Wl,-soname,libvulkan.so.1','-o',guest/'libvulkan-guest.so'],work,'build-thunk',timeout=60)
    dbpath=runtime/'usr/share/fex-emu/ThunksDB.json';db=json.loads(dbpath.read_text())
    folder=Path(sysroot+'/usr/lib')
    versioned=(folder/'libvulkan.so.1').resolve().name
    overlays=[str(folder/'libvulkan.so.1'),str((folder/'libvulkan.so.1').resolve())]
    for directory in ('/usr/lib','/run/gfx/main/usr/lib'):
        overlays.extend([directory+'/libvulkan.so.1',directory+'/'+versioned])
    for path in overlays:
        if path not in db['DB']['Vulkan']['Overlay']:db['DB']['Vulkan']['Overlay'].append(path)
    dbpath.write_text(json.dumps(db,indent=2)+'\n')
    return runtime

def prepare_prefix(release, runtime, p):
    prefix=p['base']/'p'; source=p['prefix']
    local=audited_local_candidate(p['home'],require_dfr=True)
    if local:
        metadata=json.loads((local/'integration.json').read_text())
        hashes=metadata.get('private_proton',{}).get('private_hashes',{})
        if all(hashes.get('files/lib/wine/dxvk/x86_64-windows/'+n)==h for n,h in DFR_HASHES.items()):
            candidate=Path(metadata['compat_data'])
            candidate.resolve().relative_to((p['home']/'.local/share/frame-pcvr/c').resolve())
            if (candidate/'pfx/user.reg').is_file():source=candidate
    # Only copy this recipient's own stopped prefix, never a packaged account.
    if (source/'pfx/user.reg').is_file():
        copy_tree(source,prefix,release/'work','copy-prefix')
    else:
        # Proton initializes a new Windows prefix on the first normal Steam launch.
        # Do not initialize or copy Android data into the private Windows prefix.
        prefix.mkdir(mode=0o700)
    (prefix/'pfx/drive_c/users/steamuser/AppData/Roaming/EasyAntiCheat').mkdir(parents=True,exist_ok=True)
    # Retire only copied generated GStreamer caches; the source stays untouched.
    caches=list((prefix/'gstreamer-1.0').glob('registry.*.bin'))
    if caches:
        saved=prefix/'frame-easy-original-media-cache';saved.mkdir(mode=0o700)
        for cache in caches:cache.rename(saved/cache.name)
    cfg=prefix/'fex-emu';cfg.mkdir(exist_ok=True)
    config=json.loads((runtime/'ConfigTemplate.json').read_text().replace('@FEX_COMPAT_TOOL@',str(runtime)).replace('@FEX_ROOTFS_PATH@','/usr/share/guestos/fex-mesa'))
    config['Config'].update(DiskCache='0',ProfileStats='0')
    (cfg/'Config.json').write_text(json.dumps(config))
    shutil.copy2(runtime/'usr/share/fex-emu/ThunksDB.json',cfg/'ThunksDB.json')
    (cfg/'setup-app.json').write_text(json.dumps({'Config':{},'ThunksDB':{'Vulkan':0}}))
    values=['GST_PLUGIN_PATH_1_0='+str(release/'video'),'GST_PLUGIN_FEATURE_RANK=v4l2h264dec:512,v4l2vp9dec:0,v4l2h265dec:0',
            'FRAME_V4L2_IRIS_RESTART=1','FRAME_V4L2_IRIS_POOL=1']
    (cfg/'video-app.json').write_text('{"Config":{'+','.join('"Env":'+json.dumps(v) for v in values)+'},"ThunksDB":{"Vulkan":1}}\n')
    (prefix/'arm64-icd.json').write_text(json.dumps({'file_format_version':'1.0.1','ICD':{'library_path':'/usr/lib/pressure-vessel/overrides/lib/aarch64-linux-gnu/vulkan/libvulkan_freedreno.so','library_arch':'64','api_version':'1.4.362'}}))
    emu=json.loads((runtime/'emulator.json').read_text());e=emu['emulator_v0']
    e['environment'].update(FEX_APP_CONFIG=str(cfg/'setup-app.json'))
    e['main_argv']='./usr/bin/FEX'
    e['container_environment'].update(FEX_APP_CONFIG=str(cfg/'video-app.json'),VK_DRIVER_FILES=str(prefix/'arm64-icd.json'),VK_INSTANCE_LAYERS='',FDM_DEBUG='',RPO_DEBUG='',
        DXVK_CONFIG='dxvk.trackPipelineLifetime = True; dxvk.numCompilerThreads = 2; dxgi.maxDeviceMemory = 2048',PROTON_USE_XALIA='0',
        DXVK_GAZE_VRS='1',DXVK_GAZE_FMT='97',DXVK_GAZE_COARSE='5',DXVK_GAZE_FULL_AREA='70',DXVK_GAZE_MID_AREA='85',DXVK_GAZE_DEBUG='0',DXVK_GAZE_DOT='0',DXVK_GAZE_FLIP_Y='0',
        DXVK_GAZE_FILE='Z:'+str(p['base']/'gaze/frame-gaze-v1.bin').replace('/',chr(92)))
    (runtime/'emulator.json').write_text(json.dumps(emu,indent=2))
    return prefix

def prepare_proton(release,p):
    proton=release/'proton';copy_tree(p['proton'],proton,release/'work','copy-proton')
    for n in DFR_HASHES:
        dest=proton/'files/lib/wine/dxvk/x86_64-windows'/n
        dest.resolve().relative_to(proton.resolve())
        dest.unlink();shutil.copy2(KIT/'payload/dfr'/n,dest)
    dest=proton/'files/lib/wine/x86_64-windows/mf.dll'
    dest.resolve().relative_to(proton.resolve())
    dest.unlink();shutil.copy2(KIT/'payload/wine/mf.dll',dest)
    (release/'video').mkdir();shutil.copy2(KIT/'payload/video/libgstvideo4linux2.so',release/'video/libgstvideo4linux2.so')
    return proton

def rebase_prefix_links(prefix,proton,p):
    parents=[p['proton']]
    local=audited_local_candidate(p['home'],require_dfr=True)
    if local:
        record=json.loads((local/'integration.json').read_text()).get('private_proton')
        if record:parents.append(Path(record['path']))
    for directory,subdirs,files in os.walk(prefix,followlinks=False):
        for name in subdirs+files:
            item=Path(directory)/name
            if not item.is_symlink():continue
            target=Path(os.readlink(item))
            if not target.is_absolute():continue
            for parent in parents:
                try:relative=target.relative_to(parent)
                except ValueError:continue
                replacement=proton/relative
                replacement.resolve().relative_to(proton.resolve())
                if not replacement.exists():raise RuntimeError('専用Protonへ移せない参照があります。既存設定を維持して停止します。')
                item.unlink();item.symlink_to(replacement);break
    system=prefix/'pfx/drive_c/windows/system32'
    system.resolve().relative_to(prefix.resolve())
    system.mkdir(parents=True,exist_ok=True)
    for name in DFR_HASHES:
        target=system/name
        if target.exists() or target.is_symlink():target.unlink()
        shutil.copy2(proton/'files/lib/wine/dxvk/x86_64-windows'/name,target)
    # Wine builtin loading uses the private runtime; retain a matching Prefix link.
    target=system/'mf.dll'
    if target.exists() or target.is_symlink():target.unlink()
    target.symlink_to(proton/'files/lib/wine/x86_64-windows/mf.dll')

def prepare_bridge(release):
    run(['clang++','-std=c++17','-O2','-I'+str(KIT/'support/gaze'),KIT/'support/gaze/frame_gaze_bridge.cpp',
         '-L/opt/steamvr/bin/linuxarm64','-Wl,-rpath,/opt/steamvr/bin/linuxarm64','-lopenvr_api','-o',release/'gaze-bridge'],release/'work','build-gaze',timeout=60)
    run(['clang++','-std=c++17','-O2','-I'+str(KIT/'support/gaze'),KIT/'support/openvr_stats.cpp',
         '-L/opt/steamvr/bin/linuxarm64','-Wl,-rpath,/opt/steamvr/bin/linuxarm64','-lopenvr_api','-o',release/'openvr-stats'],release/'work','build-stats',timeout=60)

def import_local_calibration(p):
    destination=p['base']/'calibration.json'
    source=p['home']/'.local/share/frame-pcvr/runs/quality-tuning/foveation-capability-study/gaze-calibration.json'
    if destination.exists() or not source.is_file():return
    value=json.loads(source.read_text())
    if value.get('format')!='frame-dfr-calibration-v1' or value.get('flip_y') is not True:return
    values=[value.get(k) for k in ['gain_x','gain_y','offset_x','offset_y']]
    if not all(isinstance(v,(int,float)) and math.isfinite(v) for v in values):return
    if not all(.5<=v<=1.5 for v in values[:2]) or not all(abs(v)<=.1 for v in values[2:]):return
    atomic(destination,{'values':values,'origin':'this_recipient_existing_calibration'})

def probe_vulkan(release,runtime,prefix):
    root=Path('/usr/share/guestos/fex-mesa');lib=root/'usr/lib'
    resource=subprocess.check_output(['clang','-print-resource-dir'],text=True).strip()
    env={k:v for k,v in os.environ.items() if not k.startswith(('FEX_','VK_','DXVK_','LD_','SteamApp','SteamGame'))}
    env.update(FEX_PORTABLE='1',FEX_ROOTFS=str(root),FEX_APP_CONFIG_LOCATION=str(prefix/'fex-emu')+'/',FEX_APP_DATA_LOCATION=str(prefix)+'/',
        FEX_APP_CONFIG=str(prefix/'fex-emu/probe-app.json'),FEX_SERVERSOCKETPATH='frame-easy-probe',VK_DRIVER_FILES=str(prefix/'probe-icd.json'),VK_LOADER_LAYERS_DISABLE='~implicit~')
    (prefix/'fex-emu/probe-app.json').write_text(json.dumps({'Config':{},'ThunksDB':{'Vulkan':1}}))
    (prefix/'probe-icd.json').write_text(json.dumps({'file_format_version':'1.0.1','ICD':{'library_path':'/usr/lib/libvulkan_freedreno.so','library_arch':'64','api_version':'1.4.362'}}))
    for name in ('route','image'):
        executable=release/('probe-'+name)
        run(['clang','--target=x86_64-linux-gnu','--sysroot='+str(root),'-fuse-ld=lld','-ffreestanding','-nostdinc',
             '-isystem',resource+'/include','-isystem','/usr/include','-fPIE','-pie','-nostdlib',lib/'Scrt1.o',lib/'crti.o',
             KIT/('support/vulkan_'+name+'_probe.c'),'-L'+str(lib),'-lc',lib/'crtn.o','-Wl,--dynamic-linker=/lib64/ld-linux-x86-64.so.2','-o',executable],release/'work','build-'+name,timeout=60)
        run([runtime/'usr/bin/FEX',executable],release/'work','probe-'+name,env,timeout=30)
        log=(release/'work'/('probe-'+name+'.log')).read_text(errors='replace')
        if name=='route' and not all(x in log for x in ['instance_self_lookup=present','device_self_lookup=present']):raise RuntimeError('Vulkan接続を確認できません。')
        if name=='image' and log.count('mismatched_pixels=0')<2:raise RuntimeError('左右の画像検査に失敗しました。')

def installed_options(release):
    return 'python3 -B '+shlex.quote(str(release/'launch.py'))+' -- %command%'

def install(accept=False, uninstall_android=False):
    if not accept:raise RuntimeError('MITライセンスと無保証の説明を確認してください。')
    manifest=verify_kit()
    if platform.system()!='Linux' or platform.machine() not in ('aarch64','arm64') or os.geteuid()==0:raise RuntimeError('Steam Frameの通常ユーザーで実行してください。')
    if game_active():raise RuntimeError('VRChatを完全に終了してから再実行してください。')
    p=paths();root=p['base']
    if (root/'installation.json').exists():
        previous=json.loads((root/'installation.json').read_text())
        if previous.get('phase')!='installed':
            raise RuntimeError('前回の導入が未完了です。同じEXEの「アンインストール」で未完了の専用配置を片付けてから、再度インストールしてください。Steam管理のVRChat本体・共有互換ツールは残します。')
        raise RuntimeError('導入済みです。再導入する場合は先にアンインストールしてください。')
    prepare_existing_game(p,uninstall_android)
    root.mkdir(mode=0o700,parents=True,exist_ok=True)
    old=options_get();releases=root/'releases';releases.mkdir(exist_ok=True)
    release=Path(tempfile.mkdtemp(prefix='a-',dir=releases));(release/'work').mkdir()
    journal={'format':'frame-easy-install-v1','release':str(release),'prefix':str(root/'p'),'phase':'preparing','old_options':old,'new_options':installed_options(release),
             'license_sha256':sha(KIT/'LICENSE.md'),'alpha_terms_acknowledged':True,'version':VERSION,
             'swap_helper_sha256':sha(KIT/'swap_setup.py')}
    atomic(root/'installation.json',journal)
    changed=False
    try:
        prepare_dependencies(journal,lambda:atomic(root/'installation.json',journal),release,p)
        check=preflight()
        if not check['ready']:raise RuntimeError('\n'.join(check['errors']))
        emit('prepare','専用の実行環境を作成しています。既存のSteam部品は上書きしません。')
        runtime=prepare_runtime(release,p);prefix=prepare_prefix(release,runtime,p);proton=prepare_proton(release,p);prepare_bridge(release)
        rebase_prefix_links(prefix,proton,p)
        for n in ('launch.py','session.py'):shutil.copy2(KIT/n,release/n)
        install_swap_helper(root)
        config={'runtime':str(runtime),'prefix':str(prefix),'proton':str(proton),'standard':str(p['standard']),'standard_proton':str(p['proton']),
                'hashes':{str(f.relative_to(release)):sha(f) for f in [runtime/'emulator.json',runtime/'ConfigTemplate.json',runtime/'FEXCompatTool',*[runtime/'usr/bin'/n for n in RUNTIME],
                          *[proton/'files/lib/wine/dxvk/x86_64-windows'/n for n in DFR_HASHES],proton/'files/lib/wine/x86_64-windows/mf.dll',release/'video/libgstvideo4linux2.so',release/'gaze-bridge',release/'openvr-stats']}}
        atomic(release/'runtime.json',config)
        emit('verify','FEXとVulkanを検査しています。')
        env=dict(os.environ,FEX_PORTABLE='1',FEX_ROOTFS='/usr/share/guestos/fex-mesa',FEX_APP_CONFIG_LOCATION=str(prefix/'fex-emu')+'/',FEX_APP_CONFIG=str(prefix/'fex-emu/setup-app.json'),FEX_APP_DATA_LOCATION=str(prefix)+'/',FEX_SERVERSOCKETPATH='frame-easy-probe')
        run([runtime/'usr/bin/FEX','/usr/bin/true'],release/'work','fex-smoke',env,timeout=30)
        probe_vulkan(release,runtime,prefix)
        import_local_calibration(p)
        if game_active():raise RuntimeError('導入中にVRChatが起動されました。起動設定は変更しません。')
        emit('activate','VRChat専用の起動設定を保存しています。')
        journal['phase']='activating';atomic(root/'installation.json',journal)
        set_options_checked(old,journal['new_options']);changed=True
        journal['phase']='installed';atomic(root/'installation.json',journal)
        emit('installed','インストールが完了しました。SteamでProton Experimental・VRモードを選び、VRChatを起動してください。')
    except BaseException:
        if changed or options_get()==journal['new_options']:
            set_options_checked(journal['new_options'],old)
        if journal.get('platform'):
            try:platform_module().restore(p['steam'],journal,lambda:atomic(root/'installation.json',journal),emit,active=game_active)
            except Exception as error:
                journal['platform_restore_error']=str(error)
                emit('restore-pending','元の版への復旧が未完了です。アンインストールで再試行できます。')
        journal['phase']='failed';atomic(root/'installation.json',journal)
        raise

def uninstall(root=None,get=None,put=None,active=None):
    get=get or options_get;put=put or options_set;active=active or game_active
    root=Path(root or base())
    if root.is_symlink():raise RuntimeError('導入先がsymlinkです。削除せず停止します。')
    state=root/'installation.json'
    if not state.exists():emit('uninstalled','このパッケージは導入されていません。');return
    if active():raise RuntimeError('VRChatを完全に終了してからアンインストールしてください。')
    j=json.loads(state.read_text());release=Path(j['release'])
    if j.get('format')!='frame-easy-install-v1':raise RuntimeError('未対応の導入記録です。保持して停止します。')
    if (root/'releases').is_symlink():raise RuntimeError('専用配置がsymlinkです。')
    releases=(root/'releases').resolve()
    if release.is_symlink() or release.resolve().parent!=releases or not release.name.startswith('a-'):
        raise RuntimeError('削除対象が専用配置ではありません。')
    prefix=Path(j.get('prefix',str(root/'p')))
    if prefix!=root/'p' or prefix.is_symlink():raise RuntimeError('個人データの保管先が一致しません。')
    if (root/'retained').is_symlink():raise RuntimeError('個人データの保管先がsymlinkです。')
    current=get()
    if current not in (j['new_options'],j['old_options']):
        raise RuntimeError('起動設定が導入後に変更されています。保持して停止します。')
    swap_module().cleanup(root)
    if current==j['new_options']:set_options_checked(current,j['old_options'],get,put)
    j['phase']='uninstalling';atomic(state,j)
    if j.get('platform'):
        platform_module().restore(paths()['steam'],j,lambda:atomic(state,j),emit,active=active)
    # Preserve only this recipient's account/settings prefix, remove our software.
    retained=root/'retained'
    if retained.is_symlink():raise RuntimeError('個人データの保管先がsymlinkです。')
    retained.mkdir(mode=0o700,exist_ok=True)
    if prefix.exists():prefix.rename(retained/('prefix-'+str(time.time_ns())))
    if release.exists():shutil.rmtree(release)
    helper=root/'swap_admin.py'
    if helper.is_file() and not helper.is_symlink() and sha(helper) in (j.get('swap_helper_sha256'),sha(KIT/'swap_setup.py')):helper.unlink()
    state.rename(retained/('installation-'+str(time.time_ns())+'.json'))
    emit('uninstalled','元の起動設定へ戻しました。ログイン情報・設定を守るため、専用データはFrame内に保管しました。')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['check','install','uninstall'])
    parser.add_argument('--accept-alpha-license',action='store_true')
    parser.add_argument('--uninstall-android-first',action='store_true',help='Android版の削除とローカルデータ消失の可能性に明示的に同意する')
    args=parser.parse_args()
    if args.uninstall_android_first and args.action!='install':parser.error('--uninstall-android-first requires install')
    os.umask(0o077)
    try:
        if args.action=='check':print(json.dumps(preflight(),ensure_ascii=False));return
        if args.action=='install' and not args.accept_alpha_license:raise RuntimeError('MITライセンスと無保証の説明を確認してください。')
        root=base();root.mkdir(parents=True,mode=0o700,exist_ok=True)
        with (root/'installer.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            if args.action=='install':install(args.accept_alpha_license,args.uninstall_android_first)
            else:uninstall()
    except Exception as e:
        if isinstance(e,swap_module().AuthenticationRequired):
            emit('swap-auth-required',str(e));raise SystemExit(43)
        if isinstance(e,dependency_module().AndroidPresentError):
            emit('android-present',str(e));raise SystemExit(42)
        emit('error',str(e));raise SystemExit(2)

if __name__=='__main__':main()
