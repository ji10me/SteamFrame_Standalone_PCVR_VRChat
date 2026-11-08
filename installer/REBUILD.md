# 対応ソースと再ビルド

配布ZIPを展開し、EXEの「利用許諾・ソース」で`frame-payload-with-sources.zip`を取り出すと、実際の修正を含むGStreamer source tree、
DXVK source snapshot、元ライセンス、OpenVR/gaze headersを参照できます。
FEXは固定コミットのソースから作成したARM64 Runtimeを同梱します。FEXソース全体はEXE内には含めません。
下記「FEXの再ビルド」から元ソースと依存部品を取得できます。上流ソース自体は変更していません。

## GStreamerの修正版

`payload/sources/gstreamer-frame-source.tar.xz`は1.22.0の修正済みsource全体です。
`source-build/config.h`と`compile_commands.json`は、元のビルドで使用した設定と35 compilation unitsの計画です。
開発者の絶対パスは`@FRAME_VIDEO_WORK@`へ正規化しています。
`rebuild_gstreamer.py`はその計画からx86-64用のコンパイル・リンクを再構成します。
コンパイラー・依存ライブラリーの差によってbinary hashが同じになることは保証しません。

Frameでclang/lld、GStreamer/GLibの開発環境、本人のProton Experimentalを用意し、
Debian bookwormのx86-64開発ヘッダーを専用の`headers`ディレクトリへ展開します。
OSのヘッダーを置換する必要はありません。
元の試験で使ったpackageには次が含まれます。

- `gstreamer1.0-plugins-good_1.22.0-5+deb12u4_amd64.deb`（元pluginの比較用。修正版sourceは別途同梱）
- `libgstreamer1.0-dev_1.22.0-2+deb12u1_amd64.deb`
- `libgstreamer-plugins-base1.0-dev_1.22.0-3+deb12u6_amd64.deb`
- `libc6-dev_2.36-9+deb12u14_amd64.deb`

```sh
python3 rebuild_gstreamer.py \
  --source /path/to/gst-plugins-good-1.22.0-frame \
  --headers /path/to/unpacked-x86-development-packages \
  --proton /path/to/your/Proton-Experimental \
  --plan /path/to/source-build \
  --work /path/to/new-build-directory \
  --runtime-libs /path/to/your/SteamLinuxRuntime4/x86_64-library-directory \
  --output /path/to/new/libgstvideo4linux2.so
```

生成物は自動インストールしません。ヘッダー・リンク対象を揃えた上で、独立した動画試験を先に行ってください。
元の修正版をビルドしたスクリプトも`producer-build-v4l2.py`に収録しています。
その既定配置は開発時のディレクトリ構成であり、上記再ビルドhelperは引数で別の配置を指定できます。
独立の試験pluginを差し替える場合も、配布する修正版の対応ソースと変更箇所を保持してください。

## DXVK / Windowsアプリ

DXVK snapshot内の`frame/docs/BUILD_AND_USE.en.md`が、対応するsourceとLLVM-MinGWビルドの説明です。
Windowsアプリのsourceは本リポジトリの`installer/windows/`と`installer/build_windows.py`にあります。
利用者向け配布ZIPの中身はEXEとライセンス2ファイルです。対応ソースはEXEから取り出せます。
開発用リポジトリの`installer/build_windows.py`で、固定SDK、公式OpenSSH archive、検証済み部品を入力にしてビルドします。
SteamやProtonなど、受取人のSteamから使う部品のソース案内・ライセンスは、その本人が取得した各配布物に従ってください。

## Wine MFの音声再開修正

`payload/sources/wine-mf-source.tar.xz`はValveSoftware/wineの
`7b3ceaf8644bb5d57140903e12897efac69be16a`からMFの再ビルドに必要な一式を収録した対応ソースです。
MF実装、ヘッダー、ホスト用ツール、NLSデータ、import libraryとstatic runtimeの入力、LGPL本文を含みます。
MF修正は適用済みです。`FRAME_MF_SOURCE.json`に元ソースの固定hash・収録ファイルhash・変更を、
`SOURCE_SCOPE.patch`にビルド対象の整理とMF修正を記録します。MF以外のWine全体を構築するsnapshotではありません。

元の公式archiveのSHA256は`12e7f09e3987ffdf47d22bd1a10b62900bc6587c5eadcb0208b78e3519f029c3`。
`installer/compact_sources.py`が元archiveと生成済みconfigure/config.h.inから対応ソースを構成するコードです。
圧縮前後のGStreamer source tarは同一です。

LLVM-MinGW、ホスト用clang、make、bison 3以降、flexを用意し、tar.xzを新しいディレクトリへ展開します。
この対応ソースには生成済みconfigureを同梱しています。再生成が必要なら`--regenerate-configure`を付け、autoconf/automakeも用意してください。

```sh
python3 rebuild_wine_mf.py \
  --source /path/to/wine-mf-source \
  --toolchain /path/to/llvm-mingw-sdk \
  --work /path/to/new-wine-build \
  --output /path/to/new-mf.dll
```

`--patch`は任意です。同じ同梱パッチを指定した場合は二重適用しません。
これはビルドのみで、ゲームや共有Protonには適用しません。異なるホスト/compilerでは同じbinary hashになるとは限りません。
配布する実行用mf.dllは既存の確認済み版を維持し、Macで再ビルドした検証用DLLへ置き換えません。

## 配布時の診断パス正規化

`sanitize_assets.py`が対応する配布処理です。
LLVMの`--strip-debug`でdebug情報を除去した後、ELFの`.rodata` / PEの`.rdata`内の
`.c`/`.h`で終わる診断用ソースファイル名だけ、同じ長さのホーム接頭辞へ置換します。
文字列の長さ・後半の位置は保ち、コード内や別用途の文字列で見つかった場合は拒否します。
全実行セクションのバイト列が不変であることを検査します。
元/配布hashと置換数は各`strip-proof.json`に記録します。
GStreamerはdebug除去段階では全SHF_ALLOC sectionが不変であることも確認します。
この正規化は診断表示用データだけの変更で、視線・描画・動画・音声処理の変更ではありません。

## FEXの再ビルド

元ソースは`https://github.com/StonkGuy/FEX`の`6effd2384085818345ec32e6abb4181c7135a9a7`（`eac-2610.5`）です。
同梱の`payload/fex/runtime.tar.xz`を展開すると、`PREBUILT.json`に部品hashとsubmoduleのコミットを確認できます。
元のFEXとFEXCore、依存部品のライセンスを変更せず保持してください。ソース取得時は上流の貢献規約も確認してください。

本リポジトリの`scripts/build-fex-stage.sh`、またはEXE内ソースZIPの`support/scripts/build-fex-stage.sh`がビルド用ラッパーです。
ARM64 Linux上でclang・cmake・ninja・lld・git・Pythonを用意し、ソースとsubmoduleの固定コミット一致・変更なしを検査して作成します。
コンパイラーとOSの違いにより同じbinary hashになるとは限りません。再ビルドしたものを共有Steam部品へ上書きしないでください。
通常の配布EXEではFEX本体の再ビルドは行いません。小さな接続用部品の作成とVulkanの検査は引き続きFrameで行います。
