# このパッケージの外部部品

独自のインストーラーは同梱LICENSEのMITライセンスです。配布時は元作者の名前を含む著作権表示と、ライセンス全文を残してください。
第三者部品のライセンスが与える利用・複製等の権利を、この独自条件で制限しません。

- **DXVK/DFR**: 確認済みanalytic rendererの`d3d11.dll`・`dxgi.dll`。
  対応する公開リポジトリのソースsnapshotを埋め込みpayloadへ収録します。
  上流とFrame向け改変の権利表示・Zlib/MIT等の元ライセンスを保持します。
  [Frame DFRソース](https://github.com/ji10me/dxvk-steam-frame-dfr)、
  [参照した上流DFR](https://github.com/ReinaS-64892/dxvk/tree/dfr)。
- **GStreamer V4L2**: LGPL-2.1-or-laterの1.22.0を基にしたIris向け修正版。
  変更を含むソースtree全体、修正パッチ、COPYINGをpayloadへ同梱します。圧縮はXZで、展開後のソースは従来と同一です。
  デバッグ情報を除去した後、読み取り専用の診断用ソースファイル名に限って、ビルドユーザーのホーム接頭辞を同じ長さの汎用パスへ正規化します。
  全実行セクションのバイト列が不変であることを検査し、proof JSONを同梱します。
  元の実機SHA256は`ded2618848cda4156407823a877804d7b5fc00af079eb807b97db1d34305dba6`、
  配布用は`3f6417451f0060f5d573fded651b4fac9c751f0226e45ce6fcb6c958cd6643a6`。
- **Wine Media Foundation**: LGPL-2.1-or-laterの修正版`mf.dll`。
  [ValveSoftware/wineの固定ソース](https://github.com/ValveSoftware/wine/tree/7b3ceaf8644bb5d57140903e12897efac69be16a)、
  MFの再ビルドに必要な実装・ヘッダー・ツール・NLSデータ・import library/static runtimeの対応ソース、適用済みの音声再開修正、変更記録、再ビルドhelper、COPYING.LIBを同梱します。
  Wine全体の不要な実装を収録せず、MFのみをビルドする構成へ整理しています。元コードの著作権表示は保持します。
  元の実機SHA256は`df227462d91152062d3857fa01433b8b02d2991c66f057f19278cdbb0177359c`、
  配布用は`e4c80315b6d1bc9ad80c1ec3a19ec0247d7435082d18a0f8938bd00577c398e0`。
  GStreamerと同様にデバッグ情報と診断用ソースパスだけを正規化し、実行セクションの不変を検査します。
  これらの処理は個人パスを配布しないためのもので、ウイルス対策の検出回避のためではありません。
- **OpenVR**: Valveの公開ヘッダー・元LICENSEを同梱します。Frameの既存native libraryとリンクします。
- **WindowsアプリのC++ランタイム**: LLVM/LLVM-MinGWの静的ランタイムを使います。
  Apache-2.0 with LLVM ExceptionsとMinGW-w64/winpthreads等の元のCOPYINGをpayloadの`licenses/`へ保持します。
- **FEX**: 実行バイナリーをこのパッケージには含めません。
  本人のハッシュ検証済みビルドをFrame内で再利用するか、
  [固定ソース](https://github.com/StonkGuy/FEX/tree/6effd2384085818345ec32e6abb4181c7135a9a7)から本人のFrameで作成します。
  上位MITと各依存部品のライセンスが適用されます。
- **Microsoft Win32-OpenSSH**: PC側の通信に使う`ssh.exe`・`ssh-keygen.exe`・`libcrypto.dll`を
  [公式10.4.0.0p1-Preview](https://github.com/PowerShell/Win32-OpenSSH/releases/tag/10.4.0.0p1-Preview)から収録。
  サービスのインストールやシステムのSSH設定変更はしません。
  公式ZIPのSHA256は`b67ff8becd203502190ec42ac7787c24c2cd3325ed7700485fe7a1c83d2a7957`。
  EXEから専用配置へ展開する`LICENSE.txt`・`NOTICE.txt`が適用され、OpenSSL等の権利表示も保持します。

上記のWine MF修正版を除くProton全体、Steam、SteamVR、SteamLinuxRuntime、OSのドライバー、VRChat、EAC、ゲームPrefix、
認証情報、個人registry、ログ、アバター・動画データは配布物に含めません。
Steam部品は受取人が正規のSteamから取得したものを、その本人のFrame内だけで専用配置へ複製します。
本人によるログインや各製品の利用条件への同意は、このインストーラーでは代行しません。

ソースの取り出し方: アプリの「利用許諾・ソース」ボタンで、内蔵の許諾とソース入りZIPを取り出せます。
`FrameSetup.exe`のRCDATA resource 101は通常のZIPです。
開発用出力の`frame-payload.zip`にも同じ内容があり、manifestで全ファイルのSHA256を照合できます。
配布ZIPにはEXE・独自部分の`LICENSE.txt`・外部部品のライセンス全文`THIRD_PARTY_LICENSES.txt`を置きます。
対応ソースはEXE内にあり、外側のZIPへ重複して収録しません。
