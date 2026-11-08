# Steam Frame PCVR for VRChat 0.3.4-alpha — Discord向け軽量配布

2026-11-08。[リリース](https://github.com/ji10me/SteamFrame_Standalone_PCVR_VRChat/releases/tag/v0.3.4-alpha)。
[日本語README](../README.md) / [English README](../README.en.md) / [導入・削除](../installer/README.ja.md)。

## 配布ファイル

Releasesへの添付は **配布ZIP1つ**。Windowsで「すべて展開」して使う。
中身は`FrameSetup.exe`・`LICENSE.txt`・`THIRD_PARTY_LICENSES.txt`の3ファイル。
独自部分のMIT全文と元作者名、外部部品のライセンス原文を保持する。
対応ソースとビルド手順はEXE内に残し、アプリの「利用許諾・ソース」で取り出せる。
大きなZIPや個別の英訳・チェックサム等をReleasesへ重複添付しない。checksumは本文に記載する。

| 配布物 | bytes | SHA-256 |
|---|---:|---|
| SteamFrame-PCVR-VRChat-Setup-Windows-alpha.zip | 18,763,178 | `77a29c02800a6e51384ad4bd27d2ec8192bfc3b7066edf6446e7e42e441df9dc` |
| ZIP内のFrameSetup.exe | 22,567,424 | `304549d5364a8c46fe88802819ef38f110426be195dc0dcebb9cd5b7aaaea9be` |

旧ZIP131,961,208 bytesから18,763,178 bytes、約85.8%削減した。
ZIPは20,000,000 bytes未満。EXE単体は20MBを超えるため、DiscordではZIPを添付する。
元の約52KBのソースキットとは異なり、Windows GUI・通信ツール・修正版実行部品・対応ソースを含む。

## 減らしたものと残したもの

- Wine: 全体archive約54MiBを、mf.dllの再ビルドに必要な約6.3MiBの対応ソースへ整理。
  MF実装・ヘッダー・ホスト用ツール・NLSデータ・import library/static runtimeの入力を含む。
  元の固定コミットと各ファイルhash、変更記録を保持。MF以外のWine全体をビルドするsnapshotではない。
- GStreamer: ソースtree全体を保持し、gzipからXZへ変更。展開後のtarが同一であることを検証。
- DXVK/DFR: 対応ソースを引き続き同梱。
- 配布ZIP: EXE内の対応ソースを、外側のZIPへ二重に収録する構成を廃止。
- ライセンス: MIT本文、第三者の元のライセンス全文・著作権表示を維持。ソースの提供を省略しない。

## 検証

最終のWine MF対応ソースarchiveを新しいディレクトリへ展開し、同梱するrebuild helperを使い、
Mac上のLLVM-MinGW SDKでWindows x86-64のmf.dllをクリーンビルドできた。
この検証用DLLを配布物や実機へ適用せず、配布するMF/H.264/DXVKの実行バイナリーは旧版とbyte一致を確認した。
起動・session・swapの処理も同一。

その後、同じFrameで旧専用環境を通常アンインストールし、このEXE内と同一のpayload・bootstrapから再導入した。
両処理の正常終了、FEX新規ビルド、専用runtimeの16/16ハッシュ一致、共有FEX・Protonの保持、
写真2枚の保管、既存8GiBスワップの再利用、SteamからのVR起動・両コントローラー追跡・視線更新・音声対策のCPU割り当てを確認した。
詳細と確認範囲は[0.3.4の再インストール記録](REINSTALL_0_3_4.md)を参照。

既存インストーラー関連96件、配布キット関連10件、新しい配布構成・ソース保持の5件、計111件が合格。
EXE内payload、対応ソースのmanifest・元コミット、全ライセンス、公式OpenSSH、ZIPの内容・サイズ・hashを検証した。
個人Prefix・認証情報・写真・実機ログを配布対象へ追加していない。独自部分のMITと第三者部品の各ライセンスを維持する。
Windows実機での初回ペアリングから管理者認証・導入・削除までの通し操作と、別Frameでの動作は引き続き未検証。
