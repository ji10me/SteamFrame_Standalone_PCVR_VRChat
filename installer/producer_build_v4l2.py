"""Build only a private experimental V4L2 plugin, never install into the OS."""
from pathlib import Path
import concurrent.futures,json,os,shlex,subprocess
os.nice(15)
os.sched_setaffinity(0,{0,1,2})
b=Path.home()/'.local/share/frame-pcvr';q=b/'runs/quality-tuning/video-hw-study'
headers=q/'x86-headers/usr/include'
assert (headers/'x86_64-linux-gnu/sys/stat.h').exists()
pool_test=os.environ.get('BUILD_IRIS_POOL_TEST')=='1'
pool=os.environ.get('BUILD_IRIS_POOL')=='1'
guard=os.environ.get('BUILD_IRIS_GUARD')=='1'
mmap=os.environ.get('BUILD_IRIS_MMAP')=='1'
trace=os.environ.get('BUILD_IRIS_TRACE')=='1'
source_name='gst-plugins-good-pool-test-1.22.0' if pool_test else 'gst-plugins-good-pool-1.22.0' if pool else 'gst-plugins-good-guard-1.22.0' if guard else 'gst-plugins-good-mmap-1.22.0' if mmap else 'gst-plugins-good-trace-1.22.0' if trace else 'gst-plugins-good-1.22.0'
root=Path('/usr/share/guestos/fex-mesa');lib=root/'usr/lib';proton=b/'proton/dfr-in5qrkky/files/lib/x86_64-linux-gnu'
build=q/'build-v4l2-native';out=q/('build-v4l2-x86-pool-test' if pool_test else 'build-v4l2-x86-pool' if pool else 'build-v4l2-x86-guard' if guard else 'build-v4l2-x86-mmap' if mmap else 'build-v4l2-x86-trace' if trace else 'build-v4l2-x86-122');out.mkdir(mode=0o700,exist_ok=True)
config=(build/'config.h').read_text().replace('#undef HAVE_CPU_X86_64','#define HAVE_CPU_X86_64 1')
if not (out/'config.h').exists() or (out/'config.h').read_text()!=config:(out/'config.h').write_text(config)
header_mtime=max(p.stat().st_mtime for p in (q/source_name/'sys/v4l2').glob('*.h'))
resource=subprocess.check_output(['clang','-print-resource-dir'],text=True).strip()
entries=json.loads((build/'compile_commands.json').read_text())
commands=[]
for entry in entries:
    args=[x.replace('../gst-plugins-good-1.22.0','../'+source_name) for x in shlex.split(entry['command'])[1:]]
    # Reuse Meson's detected headers/options, but a separate target, config,
    # object path and x86 sysroot. No ARM object is linked into this plugin.
    for flag in ('-MD',):
        if flag in args:args.remove(flag)
    for flag in ('-MQ','-MF','-o'):
        i=args.index(flag);del args[i:i+2]
    args=[x for x in args if not x.startswith('-fdiagnostics-color') and x!='-I/usr/include/gstreamer-1.0']
    args[:0]=['-I'+str(headers/'gstreamer-1.0'),'-I'+str(q/'x86-headers/usr/lib/x86_64-linux-gnu/gstreamer-1.0/include')]
    target=out/(Path(entry['file']).stem+'.o')
    command=['clang','--target=x86_64-linux-gnu','--sysroot='+str(root),'-ffreestanding','-nostdinc','-isystem',resource+'/include','-isystem',str(headers/'x86_64-linux-gnu'),'-isystem',str(headers),'-isystem','/usr/include','-I'+str(out),*args,'-o',str(target)]
    commands.append((command,target))
def run(pair):
    temperatures=[int(p.read_text()) for p in Path('/sys/class/thermal').glob('thermal_zone*/temp')]
    if max(temperatures)>=85000:raise RuntimeError('temperature guard')
    if pair[1].exists() and pair[1].stat().st_mtime>max((build/pair[0][-3]).stat().st_mtime,(out/'config.h').stat().st_mtime,header_mtime):return 0,'cached '+pair[1].name
    result=subprocess.run(pair[0],cwd=build,capture_output=True,text=True)
    return result.returncode,result.stdout+result.stderr
with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
    results=list(executor.map(run,commands))
(q/'build-v4l2-x86.log').write_text('\n'.join(x[1] for x in results))
failed=[x for x in results if x[0]]
if failed:print(json.dumps({'failed':len(failed),'first_error':failed[0][1][-2500:]}));raise SystemExit(1)
plugins=q/('plugins-pool-test' if pool_test else 'plugins-pool' if pool else 'plugins-guard' if guard else 'plugins-mmap' if mmap else 'plugins-trace' if trace else 'plugins-restart');plugins.mkdir(mode=0o700,exist_ok=True)
libraries=['libgstvideo-1.0.so.0','libgstallocators-1.0.so.0','libgstbase-1.0.so.0','libgstreamer-1.0.so.0','libgobject-2.0.so.0','libglib-2.0.so.0','libc.so.6','libm.so.6']
cmd=['clang','--target=x86_64-linux-gnu','--sysroot='+str(root),'-fuse-ld=lld','-shared','-nostdlib','-Wl,-z,defs','-Wl,--allow-shlib-undefined',*[str(x[1]) for x in commands],'-L'+str(proton),'-L'+str(q/'rtlib'),'-L'+str(lib),*['-l:'+x for x in libraries],'-o',str(plugins/'libgstvideo4linux2.so')]
r=subprocess.run(cmd,capture_output=True,text=True);(q/'build-v4l2-link.log').write_text(r.stdout+r.stderr)
print(json.dumps({'objects':len(commands),'link_status':r.returncode,'log':(r.stdout+r.stderr)[-2500:]}));raise SystemExit(r.returncode)
