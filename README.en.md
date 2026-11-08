# Steam Frame PCVR for VRChat

[日本語](README.md) | **English**

An unofficial setup tool for running the Windows version of VRChat in VR on Steam Frame itself.
Install it from a Windows PC; rendering and gameplay run on Frame. A separate rendering PC is not required during play.

**Developed by JITOME** · **Experimental alpha · MIT for original components** · **Current release: 0.3.4-alpha**

[Download](https://github.com/ji10me/SteamFrame_Standalone_PCVR_VRChat/releases/tag/v0.3.4-alpha) · [Detailed installation guide (Japanese)](installer/README.ja.md) · [License](LICENSE.en.md)

## Features

- **Simple installation:** a Windows app guides pairing with Frame and installs the required components after approval.
- **Eye-tracked rendering:** includes Dynamic Foveated Rendering (DFR), which prioritizes image quality around your gaze.
- **Video and audio improvements:** includes H.264 hardware decoding and fixes for media-player audio resumption.
- **Separate installation:** keeps compatibility tools and settings in a dedicated location to reduce changes to the original environment. The same app provides uninstallation.
- **Optional memory fallback:** adds an 8 GiB swap file on internal storage as a fallback when RAM runs low.

## Requirements

| Item | Details |
|---|---|
| Steam Frame | Sign in with your own Steam account and enable Developer Mode. |
| Windows PC | Runs the Windows x64 installer. Python and development tools are not required. |
| Network | Connect the PC and Frame to the same network. Internet access is required for component downloads and VRChat. |
| Free storage | At least 12 GiB for the main setup; 40 GiB or more is recommended with optional swap. Existing user data may require more space. |

## Installation

1. Download the **single distribution ZIP** from [Releases](https://github.com/ji10me/SteamFrame_Standalone_PCVR_VRChat/releases/tag/v0.3.4-alpha), use **Extract All** on Windows, and open `FrameSetup.exe`. The ZIP contains the installer and two license documents.
2. On Frame, enable Developer Mode and open **Settings → Developer → Pair New Host**. Menu wording may vary by language.
3. Run the EXE on Windows, read the license terms, accept them, and click **Install (インストール)**. The installer interface is currently in Japanese.
4. Approve the **`SteamFrame_Standalone_PCVR_VRChat`** request on Frame.
5. Once installation finishes, launch VRChat from Steam on Frame and select VR mode.

You must handle Steam agreements, Steam sign-in, and VRChat sign-in yourself. If you select optional swap, enter Frame's sudo password in the separate authentication window.
If the Android version of VRChat is detected, the installer asks before removing it. Preserve any data you need before agreeing.
Previously created pairing keys may still display the old name.

See the [detailed guide (Japanese)](installer/README.ja.md) for troubleshooting and compatibility-tool requirements.

## Usage and uninstallation

**After launching VRChat, set anti-aliasing to OFF and all other graphics settings to LOW in VRChat's graphics settings.**

- **Launch:** start VRChat in VR mode from Steam on Frame.
- **Optional swap:** activation lasts until Frame restarts. Use the same EXE's **8 GiB swap** button to enable it again after a restart.
- **Uninstall:** close VRChat and use **Uninstall (アンインストール)** in the same EXE. Your private Windows environment (prefix), including sign-in data, is retained separately; Steam-managed game files and shared components remain installed.

## Current status

On the development Frame, the user reported successful play in an instance with 80 people and in COMPLEX7, a world reported to exceed 800 MB. These are separate reports; they do not mean that COMPLEX7 was tested with 80 people. They do not establish support for displaying every avatar simultaneously or for every world.

Version 0.3.4-alpha reduces distribution size without changing the MIT license terms. DFR, video, audio, and swap runtime code is unchanged from 0.3.0.
On the same Frame, the exact EXE payload and bootstrap completed [uninstallation, reinstallation, and a subsequent VR launch (Japanese)](docs/REINSTALL_0_3_4.md) over authenticated SSH.
A complete first-time Windows pairing, installation, administrator authentication, and uninstallation flow, as well as operation on another Frame, remains unverified.

[Latest changes and validation (Japanese)](docs/RELEASE_0_3_4.md) · [Validation and limitations](docs/VALIDATION.md)

## License and copyright notices

Original project components are available under the **[MIT License](LICENSE)**. Integration into other programs, modification, redistribution, and commercial use are permitted.

When distributing copies or substantial portions of the original components, **preserve the copyright notices, including the original authors' names, and the full license text.**
For this project's original components, this means `Copyright (c) 2026 JITOME` and the full MIT license text.
A source LICENSE file or accompanying license notices or documentation for a binary distribution can carry these notices. No additional author-name display in the application UI or social-media posts is required.

For third-party components, preserve their original copyright and license notices and follow the applicable license conditions. See the [license guide](LICENSE.en.md) and [third-party notices (Japanese)](installer/THIRD_PARTY.md).

The software is provided as is without warranty. To the extent permitted by law, the authors accept no responsibility for equipment damage, overheating, data loss, repair, compensation, or other damages arising from installation, use, modification, or removal. See the [no-warranty explanation](LICENSE.en.md#no-warranty) and [MIT text](LICENSE).

## Documentation and acknowledgments

| Resource | Contents |
|---|---|
| [Design (Japanese)](docs/ARCHITECTURE.md) | Architecture and development approach |
| [Windows setup (Japanese)](docs/EASY_INSTALLER.md) | Distribution, installation, and removal details |
| [Hardware video decoding (Japanese)](docs/HARDWARE_VIDEO_DECODE.md) | Iris H.264 fixes and validation |
| [Public DFR repository](https://github.com/ji10me/dxvk-steam-frame-dfr) | DFR sources and documentation in English and Japanese |
| [Third-party notices (Japanese)](installer/THIRD_PARTY.md) | Component sources, licenses, and corresponding source code |
| [Development history (Japanese)](docs/VALIDATION.md) | Investigation and device-validation records |

This project builds on FEX, Proton/Wine, DXVK, GStreamer, OpenVR, and other projects. Thanks to their developers and everyone who helped with testing and shared findings. Third-party copyright and attribution notices are preserved.

Developed and maintained by [JITOME](https://github.com/ji10me).
