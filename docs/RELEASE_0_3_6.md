# 0.3.6-alpha — ビルド済みFEXとProton検出の改善

2026-11-09。[日本語README](../README.md) / [English README](../README.en.md)

## ビルド済みFEX

FEX `6effd2384085818345ec32e6abb4181c7135a9a7` の変更なしソースからFrameで作成したARM64 Runtimeを同梱する。
FEX・FEXBash・FEXGetConfig・FEXOfflineCompiler・FEXServer・FEXServerManager・FEXpidof・FEXCompatToolと、テンプレート・版情報・元ライセンスを含む。
通常の配布EXEではFEX本体をコンパイルせず、アーカイブと各部品のhash、固定コミット、ARM64 ELF形式、構成ファイルを検査して専用配置へ展開する。

上位MIT・FEXCore・依存部品とDRMヘッダーの権利表示、GPL-2.0とLinux-syscall-noteを含む18ファイルを保持する。
FEXソース全体は同梱せず、[固定ソース](https://github.com/StonkGuy/FEX/tree/6effd2384085818345ec32e6abb4181c7135a9a7)とsubmodule・再ビルド手順を案内する。
Steam/Proton全体・EAC・VRChat・本人のPrefixは同梱しない。OSの共有FEX・Protonを上書きしない。

FEXの長いビルドは不要になるが、小さな接続部品の作成とVulkanの検査にはFrameのclang等を引き続き使う。
SteamOSの共有libc・libstdc++を使うため、任意のARM64端末へそのまま使えるとは扱わない。
Frame上でnative helperと合成x86-64計算プログラムの正常終了を確認した。
検査は専用の作業フォルダーで行い、ゲームや認証済みPrefix、現在の導入を変更していない。

## 導入済みProtonの検出

- 既定ライブラリだけでなく、Steamが宣言しているライブラリ全体から依存部品を探す。Proton/FEXの実際の配置を専用コピーの入力に使う。
- `StateFlags`が完全一致で4であることを要求していた。FullyInstalledとUpdateRequired/AppRunningが併存する場合にも、必要なファイルの存在を検査して導入済みと判定する。
- ダウンロード・更新・検証・破損・削除・不明な状態は導入済みとして流用しない。確認済み版とのハッシュ検査も維持する。
- 一時停止された必要部品の取得は、対象AppIDだけに限定して一度再開を試みる。Steamの規約に自動同意しない。
- 取得画面が完了・待機になっても配置が現れず、実際の更新作業も確認できない場合は、2分でStateFlagsなどの診断と対処を表示する。実行中のバックグラウンド取得にはこの2分条件を適用しない。
- [インストール不具合の報告フォーム](https://github.com/ji10me/SteamFrame_Standalone_PCVR_VRChat/issues/new?template=installation.yml)を追加した。

報告された画面にはmanifestやStateFlagsがないため、その利用者の原因を断定したものではない。
再現できた判定不備と待機処理を直した段階であり、相手のFrameでの再試行結果は別途確認する。

## 容量と確認範囲

FEXの実行部品とライセンスは約1.3MBのXZに収まった。
既存Wine/GStreamerの対応ソースを、展開後のtarを変えず強いXZ圧縮へ変更し、ライセンスやソースを削らず容量を抑えた。
配布ZIPはEXEとライセンス2ファイルのまま。FEXの原文ライセンスも外側のライセンス一覧に保持する。

通常のゲーム起動・DFR・視線連携・H.264/MF・音声割り当て・補助スワップの実行構成は変更しない。
Windows実機での初回ペアリングから導入・削除までの通し操作と、別Frameでの導入は引き続き未検証。

## English

This alpha bundles the pristine, pinned ARM64 FEX runtime and its original dependency notices. Normal installation no longer builds FEX itself; small integration components and Vulkan checks still run on Frame.
Native loading and a synthetic x86-64 calculation were tested in an isolated directory on the development Frame, without using an authenticated prefix or changing the installed runtime.

Installed tools are found across Steam's declared libraries. Fully-installed tools with pending-update/running flags are recognized, while active updates, corruption and unknown states remain rejected. Existing compatibility hash checks remain in place.
The installer can resume a paused dependency once and reports an idle/completed wizard with no installation after a bounded wait, without timing out active background downloads under that short limit.

The screenshot does not prove the affected recipient's exact cause. Recipient-device acceptance and the full native Windows UI/pairing flow remain unverified.

## 配布物の検査

自動テスト282件を実行して失敗なし（外部ソース入力が必要な2件はskip）。
実際のEXE内payload・固定FEX・元ライセンス・Wine/GStreamer対応ソース・公式OpenSSH・配布ZIPの一致を検査した。
個人情報パターンの再帰検査も実施した。FEXの原文Cephes LICENSEには、上流が公開した許諾メールのヘッダーが含まれるため、その全文を保持している。
これは開発用Frameや利用者の通信記録ではない。[固定ソースの原文](https://github.com/StonkGuy/FEX/blob/6effd2384085818345ec32e6abb4181c7135a9a7/External/cephes/LICENSE)と一致を確認した。

| 配布物 | bytes | SHA-256 |
|---|---:|---|
| SteamFrame-PCVR-VRChat-Setup-Windows-alpha.zip | 19908306 | `652b42bfc269501aa52d07cb01017caf9fb8eeefca6aa2ae23bcdcb64d5d50c0` |
| ZIP内のFrameSetup.exe | 23709696 | `f0edef3cd5eeff74f49baa6a2bf49112c5c1a121bb5cf452e50e29c95b8d4dc3` |
