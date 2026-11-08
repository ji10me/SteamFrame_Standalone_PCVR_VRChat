# 検証範囲 / Validation scope

更新: 2026-11-08。**アルファ版です。**

## 確認済み

- 開発用Frameで、Steamの通常経路によるVRChatのVR表示・操作・ワールド入室を確認。
- 同じFrameで、共有依存部品をSteamから削除・再取得し、FEX新規ビルド・EAC初期化・VR起動・入室を確認した過去の試験がある。
- 0.3.4の通常アンインストールと再導入を、配布EXE内と同じpayload・bootstrapで確認。共有依存部品はこの回では再利用。
- 再導入後のHMD姿勢・両コントローラー追跡・視線データ更新・音声対策のCPU割り当てを確認。
- 同じFrameでH.264ハードウェアデコードと音声改善を確認した。0.3.4再導入試験で動画経路を改めて計測したわけではない。
- 80人在室のインスタンスと800MB超と報告されたCOMPLEX7での動作報告がある。別々の報告であり、全アバター同時表示や固定時間の耐久試験ではない。

## 未検証

- **Windows実機での初回ペアリングからGUI操作・管理者認証・導入・削除までの通し操作。**
- **別のFrame・別Steamアカウントでの導入。**
- 実際のAndroid版からPCVR版へ置き換える一連の操作。
- 全ワールド・全アバター、長時間利用、Steam/OSの将来の更新への互換性。

Windows EXEはクロスコンパイルとリソース検査を行ったもので、Windows実機で操作済みとは扱いません。
公開用ソースでPythonの自動テスト257件を実行し、成功しました（外部ソースが必要な2件はskip）。
実際の配布EXEのpayload・ライセンス全文・対応ソースmanifest・配布ZIPの3ファイルとSHA-256を再検証しました。
初回は小人数のワールドで表示・操作・音声を確認してください。
[0.3.4の再導入記録](REINSTALL_0_3_4.md)に試験条件と保管データの扱いを記載しています。

## English

This is an **alpha release**. The embedded backend was exercised over authenticated SSH on the development Frame, including uninstall, reinstall, a fresh FEX build, VR launch, controller tracking, gaze publication, and audio thread affinity.
Shared Steam dependencies were reused for the 0.3.4 reinstall test. An earlier test separately covered downloading those dependencies again.

**The complete native Windows UI, initial pairing and administrator-authentication flow, and installation on another Frame or Steam account remain unverified.**
The public source snapshot passed its 257-test Python run, with two skips for tests requiring external source inputs. The shipped EXE payload, source manifests, licenses, and distribution ZIP were checked separately.
Replacing an actual Android VRChat installation, all-world compatibility, and long-duration reliability are also unverified.
The 80-person-instance and COMPLEX7 reports were separate sessions, not an all-avatars-visible benchmark or a fixed-duration endurance test.
