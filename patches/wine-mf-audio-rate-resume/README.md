# Wine Media Foundationのrate 0→1の音声再開修正

試験用パッチ。**実機の私有Protonへ試験適用済み。実ワールドの無音解消は未確認。**
Wine `dlls/mf/sar.c` の、再生速度0でclockを開始してから1へ戻す経路を修正する。
元コードはclockをRUNNINGへ変更してもaudio clientを開始しないため、
SetRateだけで速度1へ戻すとclient開始とprerollが行われない。

関連する一次報告は[WineHQ bug 60310](https://list.winehq.org/hyperkitty/list/wine-bugs%40list.winehq.org/thread/VZFLMHGOGZ6KW7Y2IGEGPGTFUPW6RYGU/)。
これが今回のVRChatの無音を引き起こした経路かは、まだ確定していない。

## 修正

- rateが0→1へ変わり、clientが存在しclockがRUNNINGの場合、audio clientを開始してprerollする。
- 既にclientが動いている場合の`AUDCLNT_E_NOT_STOPPED`は成功と扱う。
- 開始に失敗した場合は旧rateを保ち、失敗を呼び出し元へ返して明示的な再試行を可能にする。
- 速度不変・速度0への変更・clockがPAUSEDの経路は既存動作を保つ。
- フレームごとの監視、外部からのゲーム再起動やSYNC操作は追加しない。

## 独立試験

`tools/mf_audio_recovery_probe.c` は、ゲーム外のprivate Wine Prefixで
MF audio rendererに合成無音だけを与える。音声の録音やgame hookは行わない。
rate0はmedia typeの設定**前**に送る。type設定後ではprerollが先行し、別の試験になる。

Frameのインストール済みWineを使った試験では、通常再生と約1.8秒の供給停止後の再開は通過し、
rate0開始→rate1だけ音声要求が0のまま再開しなかった。

同じupstream SARソースを組み込んだ独立PEでも比較した。

| 手順 | 元SAR | 修正SAR |
| --- | --- | --- |
| 通常開始 | 成功、initial requests=3 | 成功、initial requests=3 |
| rate0開始→1 | 失敗、resumed requests=0 | 成功、resumed requests=3 |
| 合成データ供給停止→再供給 | 成功、resumed requests=1 | 成功、resumed requests=1 |

probeからrenderer生成関数を直接呼び、未使用のactivation helperはtest-onlyのabort stubでリンクした。
このstubを実ゲーム用部品へ組み込んではならない。
これはMF内部のデータ要求再開の確認であり、実ゲームの実聴・全経路やクラッシュ防止の保証ではない。

`tests/test_mf_audio_resume.py` は元sourceへpatchを適用した実callbackをC fault harnessで検証し、
再開、開始失敗/再試行、既に開始済み、paused、rate不変/0、非対応rateの7件を通過した。
元sourceのSHA256は `719bb8030ee6b5a784cecfc4ec0001bb8bf25acc2cdf07c8b9fc5fc1616ae37a`。

```sh
FRAME_WINE_SAR_SOURCE=/path/to/wine/dlls/mf/sar.c \
  python3 -m unittest discover -s tests -p test_mf_audio_resume.py -v
```

## mf.dll全体での独立統合試験

ValveSoftware/wineの`7b3ceaf8644bb5d57140903e12897efac69be16a`を固定し、
元コードと本パッチ適用コードから、それぞれ正規の`mf.dll`全体をビルドした。
private Protonへ読み込ませ、通常開始・rate0→1・供給停止後の再開・
`MFCreateAudioRendererActivate`による生成を比較した。
各ケースではvolume/stream-volumeのサービス取得と2チャンネルであることも確認した。
元DLLはrate0→1のみsample要求0件で失敗し、修正版は3件を発行して全4ケース通過した。
正規DLLには上記のtest-only activation stubを含まない。

Wine builtin DLLはPrefixのsystem32へコピーするだけでは元runtimeのDLLを使う場合がある。
初回のその構成の結果は候補DLLの試験として採用せず、
private runtimeへ切り替えたうえで、OSのファイルマッピングと候補DLLのhashを確認した。
ゲームが使用するProtonは変更していない。
元DLLとの全ソースcommitの一致や、実ゲームでの全互換性を確認したという意味ではない。

## ライセンスと導入境界

追加パッチはLGPL-2.1-or-laterで提供する。元Wineの著作権表示とライセンスを保持する。
このパッチとWine由来部分へ、独自部分のアルファ版配布制限を追加適用しない。
[COPYING](COPYING)を参照。

修正版で合成H264/AAC動画の読み込み・flush/seekも3周通過した（各60frames/EOS）。
初回の広範なCOM登録は40秒でtimeoutしたため、必要なMF部品だけを登録し直したprivate Prefixで確認した。
この動画試験は修正版のみであり、動画性能の元DLLとの比較や実聴ではない。

停止中に実機の私有Protonへ修正版を反映し、元DLL/metadataをFrame内に保存した。
管理対象hashへmf.dllを追加し、全16部品のhash一致を確認して通常Steam起動を要求した。
ゲームのPrefixのsystem32/mf.dllはこの私有Protonへのsymlinkを保ち、共有の互換ツールは変更しない。
配布EXE/ZIPは未更新。実ワールドの音声を確認してから採用を判断する。
