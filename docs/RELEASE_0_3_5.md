# 0.3.5-alpha — 導入失敗の診断とFEX作成時の温度待ち

2026-11-09。[日本語README](../README.md) / [English README](../README.en.md)

## 変更点

- VRChatの版を確認できない場合に、取得・更新未完了、Windows/Android混在、複数ライブラリ、ライブラリ情報の読み取り失敗を区別する。
- `StateFlags`、Windows実行ファイルの有無・形式、APK数を表示する。所有者ID、認証情報、個人パス、manifest原文は出力しない。
- ゲームフォルダー内の下位ディレクトリ・大文字拡張子のAPKも検出する。ゲーム外へのファイルリンクは開かず、調べるAPKは最大64個。
- FEX作成の並列数を2から1へ変更し、作業時間の上限を1時間から2時間へ変更。
- **インストール時だけ95℃を停止上限**にする。FEX作成は92℃で自分のビルドプロセス群だけを一時停止し、85℃以下で自動再開する。温度調整は合計15分まで待ち、作業時間には数えない。
- 温度・空きRAM・空き容量・時間のどの条件で停止したかを、実測値と対処方法とともに表示する。RAM・容量の制限を無効にしない。
- 未完了の導入が残っている場合、同じEXEのアンインストールで片付けてから再実行するよう案内する。

## 既存構成への影響

DFR・H.264・Wine MFの実行バイナリー、ゲーム起動・視線連携・音声用CPU割り当て・補助スワップの処理は変更しない。
共有Steam部品、ゲームファイル、アカウント、電力モード・GPUクロック・プレイ中の温度制御を変更しない。
FEX上流ソースも変更しない。ビルドを制御する本プロジェクトのラッパーを修正した。

0.3.4で導入途中に止まった場合は、新しいEXEでアンインストールしてから再導入する。
正常動作中の利用者は、この導入処理の変更のために入れ直す必要はない。

## 診断の限界

報告された旧版の画面だけでは、VRChatの判定失敗の詳細も、温度・RAM・容量・時間のどれが上限だったかも確定できない。
今回の修正で原因を区別して表示し、APK配置差とビルド中の温度上昇に対処する。
すべての報告環境で導入できたことを実証したものではない。

合成状態・モックを用いたテストで、APK配置、ファイルリンク拒否、個別の停止条件、温度待ち・再開・終了処理を確認した。
Windows実機の初回ペアリングから導入・削除までの操作と、別Frameへの導入は引き続き未検証。
詳しい前提は[検証範囲](VALIDATION.md)を参照。

## English

This alpha improves edition detection and installation diagnostics, and uses a single-job FEX build with a two-hour active-work limit.
During installation only, the hard stop is 95°C. The build's own process group pauses at 92°C and resumes at or below 85°C, with a total cooling-wait budget of 15 minutes.
Memory and disk guards remain enabled. Runtime DFR, H.264/MF, gaze, audio affinity and swap behavior are unchanged.
The original screenshots do not establish which limit was reached; the new messages report the specific reason and numeric readings.
Recipient-device acceptance and the full native Windows UI/pairing flow still require validation.

## 配布物の検査

Windows向けEXEをクロスコンパイルし、実際の埋め込みpayloadと修正後のソースの一致、95℃・92℃・85℃の設定、FEX並列数1を確認した。
自動テスト270件を実行して失敗なし（外部ソース入力が必要な2件はskip）。合成の親子プロセス群でも一時停止・再開後の完了を確認した。
配布ZIPはEXE・ライセンス2ファイルの3ファイル。全ライセンス・対応ソース・manifest・公式OpenSSHとの一致、個人情報パターンの再帰検査を確認した。
これらはWindows実機の操作や、報告者のFrameで導入できたことの実証とは区別する。

| 配布物 | bytes | SHA-256 |
|---|---:|---|
| SteamFrame-PCVR-VRChat-Setup-Windows-alpha.zip | 18767329 | `f08e31e55eb8f16253b15bcdacaaa54b914ba5e2b6b0250a8091d01150e1c436` |
| ZIP内のFrameSetup.exe | 22569984 | `5742ac345552802dcf3c57ab1bdc4d4bcb9d6d7ebdf079b5ce495c5c2f57a70f` |
