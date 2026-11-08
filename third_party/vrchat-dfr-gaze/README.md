GAZ2 v1 shared-memory protocol header from https://github.com/ReinaS-64892/vrchat-dfr-linux

Pinned revision: e410c937d44f818c4de5806a7576f40381cec75f

Original path: shared/gaze_shm.h

SHA256: 39f197f49cf2d02cb9c183ebfec9de9d2823fe3e5300c84bd7725d23fde925b3

Header is unmodified and retains its MIT license; see LICENSE.

`dxvk-tiler-secondary.patch` is a local follow-up applied after that revision's
`patches/dxvk-gaze-vrs.patch`, on DXVK
`0cfe0898289fb11e3d8d0e1c62cd344b825b7d57`. It preserves the tiler secondary
command buffer path, prepares VRS uploads in the primary before recording
draws, sets dynamic state in the drawing command buffer, and records debug
clears inside the secondary. Devices without layered shading-rate attachments
use a single-layer view and leave two-layer eye passes at full rate.
The patch contains changes to zlib/libpng-licensed DXVK (see LICENSE-DXVK)
and the upstream DFR code (see LICENSE); retain their original licenses with
binaries and source distributions. This is an altered experimental version.

`gaze_map_upstream.h` is the unmodified map header embedded in that same
revision's DXVK patch. SHA256:
`262330c327d8fa92adfc23a21d5986c9846f092e3a778eb3fdb1a752f72060a1`.
It is retained as the compatibility oracle for
`dxvk-gaze-map-row-spans.patch`, a local optimization that binary-searches
the original discrete circle boundaries and fills row spans. It preserves
the original distance function, comparison rules, gaze, radii, rates and
padding. The byte-equivalence test covers stereo layouts, edge positions,
random requests, exact radius boundaries and exceptional values.
This map header and its optimization are DXVK patch material; retain
LICENSE-DXVK. The separate GAZ2 protocol header retains its MIT notice.

`dxvk-frame-map-kernel.patch` is an alternative to the row-span patch. Apply
it to the pinned original DFR patch (plus the Frame tiler fix), then copy
`tools/frame_foveation_map.h` into `src/dxvk/`. It replaces the CPU map function
with a call to the project's separately testable analytic-boundary kernel.
The adapter still contains upstream request/worker code: retain LICENSE-DXVK
and the original notices. The project header is covered by the root
LICENSE.md. Reviewing the previous implementation preceded this work; this
is not a clean-room or complete removal-of-upstream-dependencies claim.
The new kernel has passed native CPU compatibility checks, not live VRChat
acceptance. Do not apply both alternative map patches in sequence.

`dxvk-sync-diagnostics.patch` and `frame_sync_diagnostics.h` are optional
diagnostic additions to DXVK, covered as DXVK patch material by LICENSE-DXVK.
Copy the header into `src/d3d11` when applying that patch. They retain the
rendering and synchronization calls and collect only elapsed interop flush,
queue-acquisition and queue-hold counters through a private 256-byte file.
`DXVK_FRAME_SYNC_FILE` must name a pre-existing valid FSYNC001 file. Collection
is disabled initially and stops after a 30-second window; it is experimental
instrumentation, not an FPS optimization. It is excluded from the alpha kit.
Queue hold time does not establish how long a submission worker was blocked.
