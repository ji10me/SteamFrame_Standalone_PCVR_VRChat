# Steam Frame PCVR for VRChat — 導入キット

**アルファ版です。独自部分はMITライセンスで、組み込み・改変・再配布できます。配布時は元作者の名前を含む著作権表示と、ライセンス全文を残してください。**
導入前に [利用許諾](../LICENSE.md) を確認してください。第三者部品には元のライセンスが適用されます。

**初めて導入する方は [インストール説明書](INSTALL.ja.md) を順番に読んでください。**
準備、Frame側でのコマンド実行、Steamへの設定、初回VR起動、エラー対処を説明しています。
キットの横の `INSTALL.ja.md` も同じ内容です。

Windows版VRChatをFrame内で実行し、Frame標準のSteamVRでVR表示する実験用キット。
別PCからの映像配信は使わない。元のFrameでは45人在室のインスタンスで利用できたが、
全員のアバター同時フル表示や人数上限、別のFrameでの動作は未検証。

## 同梱するもの

導入ソース、独立Vulkanテスト、汎用のメモリ設定、ファイルのSHA-256一覧。
個人のログイン情報、Steam ID、VRChat ID、IP、SSH鍵、ログ、スクリーンショット、
Prefix、ゲーム・アバターデータ、Valve製バイナリは含まない。
本体や外部部品は受け取った本人が取得する。[外部部品](THIRD_PARTY.md)

## 最初に本人のFrameで行うこと

1. SteamでWindows版VRChat (438100)、Proton Experimental (1493710)、
   Proton EasyAntiCheat Runtime (1826330)、FEX (3127680)、
   SteamLinuxRuntime_4 (4183110) をインストールする。
   SteamVRはFrame標準のものを使う。
2. VRChatの互換ツールをProton Experimentalに指定する。
   起動オプションはまだ追加せず、Steamから一度起動し、本人が規約・初期設定を行う。
   標準FEXでEACエラーになっても、PrefixとEasyAntiCheat用ディレクトリの初期作成が必要。
3. **VRChatを完全に終了する。** インストール完了まで起動しない。
4. ARM64 LinuxのFrame上のターミナルで作業する。Mac上ではインストールしない。
   Python 3.9以上、clang/clang++、cmake、ninja、ld.lld、git、patchelf、cp、
   Vulkan開発用ヘッダー・ライブラリが必要。
   キットはOSのパッケージ追加や読み取り専用解除を自動実行しない。

現版は標準Steamライブラリ内に各ツール・VRChat Prefixがある構成を対象とする。
外部ライブラリ・独自互換ツール・別ビルドを検出したら停止する。
既にこのプロジェクトの候補Prefixがある場合も、上書きせず停止する。

## キットの検査と導入

受け取った `.tar.gz` と `.sha256` を同じディレクトリへ保存し、Frame上で実行する。
SHA-256は転送破損の検出用。署名ではないため、信頼できる相手から受け取る。

```sh
sha256sum -c frame-pcvr-kit.tar.gz.sha256
tar -xzf frame-pcvr-kit.tar.gz
cd frame-pcvr-kit
python3 -B tools/install_portable.py --check
```

`--check` はファイル・前提条件の読み取りだけを行う。不足があれば先に解消する。
未検証のSteam/FEX更新は自動で組み合わせない。

```sh
python3 -B tools/install_portable.py --install --accept-alpha-license
```

並列2でFEXをソースビルドするため時間・空き容量が必要。空き容量は最低12GiBを事前確認するが、
Prefixの大きさやビルド環境によって追加容量が必要になる。
続けて本人の停止済みPrefixを本人のFrame内に複製し、私有候補を組み立てる。
CPU実行、Vulkan接続・self-lookup、両眼サイズのGPU画像一致試験が通ってから設定を適用する。
テストはゲームやSteamVRに映像を提出しない。

完了すると本人用の `LAUNCH_OPTIONS.txt` の場所が表示される。
その1行をSteamの **VRChatだけ** のプロパティ→起動オプションに貼り付け、
互換ツールはProton Experimentalを維持して **VRモード** で起動する。
ログインは本人が行う。EAC・ログイン・入室の成功はキットの自動テストでは確認しない。

途中で失敗してもSteam設定は自動変更しない。エラー表示のローカルログで原因を確認する。
再実行で既存候補を上書きしないため、途中作成された候補のある状態での再導入は個別確認が必要。

## 45人在室で使った表示設定

ログイン後、本人のVRChat設定画面で次を指定する。ユーザー別の設定は自動編集しない。

| 項目 | 設定 |
|---|---|
| 最大アバター表示数 | 有効、5人 |
| 距離による非表示 | 有効、4.5m |
| フレンドを常に表示 | ON |
| 手動Show Avatarを優先 | ON |
| アバター展開後容量 | 100MiB |
| アバターダウンロード容量 | 200MiB |

フレンドや手動表示の例外によって5人を超える。45人全員を通常表示する設定ではない。
汎用メモリ設定はpipeline lifetime有効、compiler 2本、VRAM報告2048MiB、
Xalia無効、私有FEX DiskCache無効。RPO/FDM/UBO変換は使わない。
画質・体感や実メモリ使用量はワールドやアバターに依存する。

## 動作確認と戻し方

最初はホームで、両眼の立体表示、頭・両手、メニュー、音声を確認し、
小人数のワールドから試す。大きいモニターのように見える場合はVRモード選択を確認する。
Steam/OS更新後にハッシュ不一致で停止したら、共有ツールへ上書きして回避しない。

標準に戻すにはVRChatを終了し、追加したVRChat起動オプションを取り除く。
導入前に起動オプションがあった場合は本人が保存した元の値へ戻す。
共有FEX・Proton・SteamVRや他ゲームは上書きしない。常駐サービスも追加しない。

**インストール後の** `~/.local/share/frame-pcvr/` には本人の認証済みPrefixや診断ログができる。
このディレクトリを他人へ渡さない。
認証情報や個人データを含まない本キットの独自部分はMITライセンスで再配布できる。
元作者の名前を含む著作権表示と、ライセンス全文を残し、第三者部品には各ライセンスを適用する。
