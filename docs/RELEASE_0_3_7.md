# 0.3.7-alpha — Proton Experimental 11への対応

2026-11-11。[日本語README](../README.md) / [English README](../README.en.md)

## 概要

報告された2つの問題を直した版です。

1. Proton Experimentalの自動更新後に、インストーラーが「確認済み版と異なります」で停止する。
2. 導入後のVRChatが、2段階認証の直後やワールド入室時に「待機中」のまま進まなくなる。

2は、VRChat（Unity 6）のガベージコレクターが出すエラーダイアログがVRに表示されず、ボタンを押せないまま止まっていたことが原因でした。
Wineのスレッド同期をサーバー側の方式に切り替えると、開発用Frameでの試験では一度も発生しませんでした。

## 1. 版確認で導入が止まる問題

Proton ExperimentalはSteamで頻繁に自動更新されます。
0.3.6までは、確認済み版のファイルハッシュと完全一致しないと停止していました。
2026-11-10にpublicブランチが26306234から26491492（`experimental-11.0-20261105c`）へ更新され、更新後のFrameでは導入できなくなりました。

- 確認済み版を、実機で確認したビルドID 26491492へ更新しました。
- かんたんインストーラー（`frame_setup.py`）は、今後Protonが更新されてハッシュが一致しない場合も、停止せずに警告を表示して続行します。
  専用コピーで差し替える対象（`proton`、DXVKの`d3d11.dll`・`dxgi.dll`、`files/lib/wine/x86_64-windows/mf.dll`）が存在しない場合は停止します。
- 手動キット（`tools/install_portable.py`）は、ProtonのビルドIDが違う場合に`warnings`へ出力します。必要なDXVK設定キーは従来どおり検査します。
- FEX-Emuは、ラッパーの書き換えとthunkの作成が確認済み版を前提にしているため、従来どおり版が一致しなければ停止します。

開発用Frame（Protonは報告と同じ26491492）で、旧0.3.6の事前検査は報告と同じエラーで停止し、0.3.7は導入まで完了しました。

## 2. 「待機中」のまま止まる問題

### 症状

ログイン直後（EOSのログイン処理中）や、ワールド入室中の「Warming shaders for world mirrors」で、VRChatがフレームを出さなくなります。
SteamVRはグリッド表示になり、VRChatのログもそれ以上進みません。

### 調べたこと

- VRChatのスレッドの状態を`/proc`で確認しました。ほぼすべてのスレッドがWineの同期処理で待っていて、CPUをほとんど使っていませんでした。
- `PROTON_LOG`を有効にすると、`GameAssembly.dll`内のクリティカルセクションを、1本のスレッドが持ち続けていました。ほかの11〜22本は、`RtlpWaitForCriticalSection ... blocked by <tid>`の状態でした。
- ロックを持っているスレッドは、止まる直前に`msimg32.dll`を読み込んでいました。直後に、ダイアログ（ウィンドウクラス`#32770`）が作られていました。
- 同じWine環境で、ウィンドウの一覧と文字を読む小さなプログラムを動かしました。VRChatへのデバッガー接続はしていません。表示されていたのは次のダイアログでした。

  ```
  Fatal error in GC
  SuspendThread loop failed   [OK]
  ```

これはUnityのBoehm GCが、スレッドを一時停止してレジスタを読む処理（`SuspendThread`→`GetThreadContext`）の失敗を繰り返したときに出すエラーです。
VRChatの不具合報告（[Fatal Error in GC (SuspendThread Loop failed)](https://feedback.vrchat.com/bug-reports/p/fatal-error-in-gc-suspendthread-loop-failed)）では、Windowsネイティブ環境やほかのProtonでも報告されていて、VRChatは今後のリリースで修正予定としています。
ダイアログがVRに表示されないため、OKを押せずに止まって見えていました。

### 切り分け

| 構成 | 結果 |
|---|---|
| Proton 26491492・ntsync（Protonの既定） | 2回中1回停止（ワールド入室時） |
| Proton 26491492・fsync | 4回中3回停止（2段階認証の直後、ワールド入室時） |
| Proton bleeding-edge 26577837・fsync | 1回中1回停止（2段階認証の直後、GCダイアログを確認） |
| Proton 26491492・サーバー側同期（fsync・esync・ntsyncを無効） | 4回すべて成功（2段階認証ありを含む） |

- Steam標準のFEX・Protonでは、EAC経由の起動（`start_protected_game.exe`）から進まず、比較できませんでした。
- VRの外で、同じ一時停止の処理を繰り返す試験用プログラムを専用FEX・Proton上で実行しました。対象は、計算・Sleep・イベント・パイプ・ソケット・条件変数・Vulkanのネイティブ待ち・作成直後と終了途中のスレッドです。合計約16万回、`GetThreadContext`は一度も失敗しませんでした。
  単純なFEXやVulkan thunkの問題ではなく、VRChatの中の特定のタイミングで起きるものと考えています。
- 不具合報告でも、fsync・esyncの無効化で改善したという例がありました。

### 対策

VRChatの起動時に`PROTON_NO_FSYNC=1`・`PROTON_NO_ESYNC=1`・`PROTON_NO_NTSYNC=1`を設定し、Wineのサーバー側同期を使います。
かんたんインストーラーは`launch.py`で、手動キットは専用FEXの`container_environment`で設定します。Steam上の共有Protonは変更しません。

### 制限

- 試行回数は少なく、発生しないことを保証するものではありません。原因はVRChat側にあり、VRChatの修正版が出れば、この設定は不要になる見込みです。
- サーバー側同期はfsyncより処理が重くなる場合があります。開発用Frameでは操作できる状態でしたが、混雑したインスタンスでの性能は未測定です。
- 止まった場合は、VRChatを終了して起動し直してください。
- 別のFrameでの導入と長時間の安定性は、引き続き未検証です。

## English

**Installer version check.** Proton Experimental auto-updated to build 26491492 on 2026-11-10, and the exact hash check stopped installation. The tested build is now 26491492. A newer, differing Proton build only produces a warning, as long as the files replaced in the private copy exist. FEX-Emu checks remain strict.

**Freeze at login or world entry.** VRChat showed Unity's "Fatal error in GC: SuspendThread loop failed" dialog while holding a lock, so every other thread waited. The dialog is invisible in VR, so the game appeared to freeze. VRChat has acknowledged this bug, and it also occurs on native Windows. On the development Frame, fsync and ntsync froze in 4 of 6 launches. Wineserver-side synchronization (`PROTON_NO_FSYNC=1`, `PROTON_NO_ESYNC=1`, `PROTON_NO_NTSYNC=1`) passed all 4 launches, including one with 2FA. VRChat is now launched with these variables. The sample is small, and performance in crowded instances is not yet measured.

## 配布物の検査

自動テストは282件で失敗なし（外部のソース入力が必要な2件はskip）。`verify_release.py`で、EXE内のpayload・固定FEX・公式OpenSSH・配布ZIPが一致すること、個人の実行時ファイルが含まれないことを確認しました。

| 配布物 | bytes | SHA-256 |
|---|---:|---|
| SteamFrame-PCVR-VRChat-Setup-Windows-alpha.zip | 19908331 | `64192af3781fd09b6b2a025ad9e220926f4f6a2ede6bb7cbb56c23ba9678f0dd` |
