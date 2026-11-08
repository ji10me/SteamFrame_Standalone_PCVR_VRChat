# 構成 / Architecture

Windowsのセットアップアプリは、ペアリング承認後にFrameへ導入本体を転送します。
プレイ中の描画・ゲーム処理はFrame内で実行します。

| 部品 | 役割 |
|---|---|
| Steam / Proton / FEX | Steamの通常起動から、Frame内でWindows x86-64版VRChatを実行 |
| 専用配置 | 本人の共有互換ツールを変更せず、専用のruntimeとPrefixを使用 |
| DXVK / DFR | OpenVR Background APIから得た視線を、共有ファイル経由で描画へ反映 |
| GStreamer / Wine MF | H.264のIris向け出力処理と、動画音声の再開を修正 |
| session supervisor | 対象ゲームの寿命を確認し、視線連携と確認済みCPU割り当てを管理 |
| 任意の8GiBスワップ | 圧縮スワップを優先したメモリー不足時の補助退避領域 |

Steam・VRChat・EAC・Proton全体・本人のPrefixは配布しません。
共有部品は本人のSteamから取得し、正規のログインと規約同意を前提にします。
独自部分はMIT、第三者部分は各元ライセンスです。
[外部部品](../installer/THIRD_PARTY.md)と[再ビルド手順](../installer/REBUILD.md)を参照してください。

For validation limits, see [Validation scope](VALIDATION.md).
