#!/bin/sh
# Run on ARM64 Linux. Stages a candidate Runtime; never changes Steam/OS files.
set -eu
case "$(uname -s):$(uname -m)" in
  Linux:aarch64|Linux:arm64) ;;
  *) echo 'Build on the Frame (ARM64 Linux), not the Mac or a Mac VM.' >&2; exit 2 ;;
esac
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
for tool in clang clang++ cmake ninja ld.lld git python3; do
  command -v "$tool" >/dev/null || { echo "Missing: $tool (OS packages are not changed automatically)" >&2; exit 2; }
done
JOBS=${FEX_FRAME_JOBS:-4}
case "$JOBS" in ''|*[!0-9]*|0) echo 'FEX_FRAME_JOBS must be a positive integer' >&2; exit 2 ;; esac
BASE=${FEX_FRAME_WORK:-$HOME/.local/share/frame-pcvr/build}
umask 077
mkdir -p "$BASE"
RUN=$(mktemp -d "$BASE/eac-2610.5-XXXXXX")
SRC=$RUN/source
STAGE=$RUN/stage
COMMIT=6effd2384085818345ec32e6abb4181c7135a9a7
TAG=eac-2610.5
printf 'Work directory: %s\n' "$RUN"
# Record the local baseline before any build; stdout remains private in RUN.
python3 "$ROOT/tools/framectl.py" collect > "$RUN/baseline.json"
git clone --depth 1 --branch "$TAG" https://github.com/StonkGuy/FEX.git "$SRC"
test "$(git -C "$SRC" rev-parse HEAD)" = "$COMMIT" || { echo 'Pinned commit mismatch' >&2; exit 2; }
test "$(git -C "$SRC" rev-parse "$TAG^{commit}")" = "$COMMIT" || { echo 'Pinned tag mismatch' >&2; exit 2; }
git -C "$SRC" submodule update --init --recursive --depth 1 -- \
  Source/Common/cpp-optparse External/fmt External/drm-headers External/xxhash \
  External/jemalloc_glibc External/range-v3 External/unordered_dense External/rpmalloc
test -z "$(git -C "$SRC" status --porcelain)" || { echo 'Source/submodule tree is dirty' >&2; exit 2; }
git -C "$SRC" submodule status --recursive > "$RUN/submodules.txt"
# This builds only the execution component for later review. It does NOT establish
# compatibility with Valve's thunks. Never copy this stage over the Steam tool.
cmake -S "$SRC" -B "$RUN/cmake" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++ \
  -DBUILD_STEAM_SUPPORT=ON -DBUILD_TESTING=OFF \
  -DBUILD_THUNKS=OFF -DBUILD_FEXCONFIG=OFF \
  -DENABLE_LTO=OFF -DENABLE_ASSERTIONS=OFF \
  -DTUNE_CPU=native -DCMAKE_CXX_SCAN_FOR_MODULES=OFF \
  -DCMAKE_INSTALL_PREFIX=/usr > "$RUN/configure.log" 2>&1 || {
    cat "$RUN/configure.log" >&2; exit 1;
  }
cmake --build "$RUN/cmake" --target FEX FEXBash FEXGetConfig FEXOfflineCompiler \
  FEXServer FEXServerManager FEXpidof FEXCompatTool -j "$JOBS" > "$RUN/build.log" 2>&1 || {
    tail -n 80 "$RUN/build.log" >&2; exit 1;
  }
DESTDIR="$STAGE" cmake --install "$RUN/cmake" --component Runtime --strip > "$RUN/install.log" 2>&1 || {
  cat "$RUN/install.log" >&2; exit 1;
}
python3 - "$ROOT/tools" "$RUN" "$COMMIT" <<'PY'
import datetime, json, pathlib, platform, sys
sys.path.insert(0, sys.argv[1])
from framectl import sha256, command_version
run = pathlib.Path(sys.argv[2])
stage = run / 'stage'
meta = {
    'commit': sys.argv[3], 'tag': 'eac-2610.5',
    'created_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'machine': platform.machine(), 'kernel': platform.release(),
    'runtime_only': True, 'integration_verified': False,
    'baseline_sha256': sha256(run / 'baseline.json'),
    'cmake_cache_sha256': sha256(run / 'cmake/CMakeCache.txt'),
    'submodules': (run / 'submodules.txt').read_text(),
    'toolchain': {t: command_version(t) for t in ('clang', 'cmake', 'ninja', 'ld.lld', 'git')},
    'artifacts': {str(p.relative_to(stage)): sha256(p) for p in sorted(stage.rglob('*')) if p.is_file()},
}
(stage / 'frame-build.json').write_text(json.dumps(meta, indent=2) + '\n')
PY
python3 "$ROOT/tools/framectl.py" audit-stage "$STAGE" > "$RUN/audit.json" || {
  cat "$RUN/audit.json" >&2; exit 1;
}
printf 'Staged: %s\nAudit: %s\nSteam was not changed. Native thunk/ABI integration still requires review.\n' "$STAGE" "$RUN/audit.json"
