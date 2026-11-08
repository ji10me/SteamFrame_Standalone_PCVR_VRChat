# Frame Iris の動画出力・drain再開を修正する試験パッチ

GStreamer gst-plugins-good **1.22.0** の V4L2 デコーダーを対象とする。
Steam Frame上のH.264ハードウェア再生で、出力pool未生成によるクラッシュと、
再生終了後のflush・seekで読み出しが停止する問題に対処する。
独立Wine試験に加え、修正版のVRChatでもハードウェア出力を確認した。
フルパッチは現在の専用インストーラーへ採用済み。
下記の追加修正候補は別管理で、実ゲームの受け入れは別途必要。

## 変更

最新のフルパッチは **`frame-iris-compat.patch`**。
出力形式設定がcolorimetry不一致などで失敗した際に、ドライバーの実形式を再取得し、
未生成のcapture poolを作ってから解像度変更通知を設定する。
代替pool生成はIrisのdriver/card一致と`FRAME_V4L2_IRIS_POOL=1`の両方を条件とする。
caps・形式・poolの検証も追加し、失敗時はエラーへ戻す。

診断用の`FRAME_V4L2_IRIS_TRACE=1`は段階・寸法・最初32出力のメタデータだけを記録する。
動画URL・画素・音声を出力しない。
試験したMMAP固定はフルパッチにopt-inとして残るが、現採用試験では有効にしない。
形式設定失敗を強制する独立試験用の変更は、このフルパッチに含めない。

### drain後の再開

`VIDIOC_DECODER_CMD / V4L2_DEC_CMD_STOP` の完了を専用の状態として記録する。
次のflushで、キューのSTREAMOFF/STREAMONより先に`V4L2_DEC_CMD_START`を発行する。
この再開命令は、次の全条件が揃う場合だけ送る。

- drainを完了したデコーダーである。
- QUERYCAPのdriverが`iris_driver`、cardが`iris_decoder`と一致する。
- ゲスト環境で`FRAME_V4L2_IRIS_RESTART=1`を明示している。

`/sys/class/video4linux/video22/name`の`qcom-iris-decoder`とQUERYCAPのcard名は異なる。
`output_flow`だけでdrain完了を判定しない。再開命令の失敗はGStreamerのエラーとして返す。
他のV4L2機器やエンコーダーの再開処理は変更しない。

### 2026-11-07の追加修正候補

`lifecycle-errors.patch` は、フルパッチ適用後のソースへ重ねる追加パッチ。
2026-11-07、本人の適用指示を受け、専用releaseの実機プラグインへ適用した。
通常Steamからの新しいVRChatで、修正版のinodeと一致するライブラリの読み込みを確認した。
既配布EXE/ZIPやインストーラー用素材はまだ更新していない。

- drainがfinish/flushの失敗を無視して成功を返す経路を修正する。
  出力タスクの通常終了で使われるEOS/FLUSHINGは従来どおり許容する。
- IrisのSTART命令に失敗した際もqueueのunlock状態を解除し、
  エラーとdrain済みの状態を保持する。失敗したままpoolをSTREAMONせず、後の明示flushで再試行できる。
- 出力形式が無い場合、`gboolean`のset_formatから負のflow値を返して
  true扱いになる不具合を修正し、FALSEを返す。

元の1.22.0からフルパッチと追加パッチを適用した実関数をCへ取り込み、
再開失敗・後の再試行・finishエラー・形式交渉エラー・通常drain・他driverを試験する。
`tests/test_iris_lifecycle.py`は修正前6件中4件失敗、修正後6件合格。
実機の今回の無音がこの経路で発生した証拠はなく、無音解消を保証する修正ではない。

```sh
FRAME_GST_SOURCE_ARCHIVE=/path/to/gst-plugins-good-1.22.0.tar.xz \
  python3 -m unittest discover -s tests -p test_iris_lifecycle.py -v
```

この追加修正も下記と同じLGPL-2.1-or-laterで提供する。

## ビルド時の注意

元の1.22.0ソースのルートで`patch -p1 < /path/to/frame-iris-compat.patch`を適用する。
元ソースに対する5ファイルの`patch --dry-run -p1`を確認済み。
`restart-after-drain.patch`は初期のEOS修正だけを残した履歴で、フルパッチと重ねて適用しない。
元ソースの配布元は[公式1.22.0アーカイブ](https://gstreamer.freedesktop.org/src/gst-plugins-good/gst-plugins-good-1.22.0.tar.xz)。
SHA256は`582e617271e7f314d1a2211e3e3856ae2e4303c8c0d6114e9c4a5ea5719294b0`。

ProtonのGStreamer 1.22と組み合わせる場合は、**GStreamer 1.22の開発ヘッダー**で構築する。
Frameの1.24用ヘッダーを使うとプラグインの要求バージョンが1.24になり、読み込まれない。
x86版にはx86用glibcヘッダーも必要。FrameのARM用ヘッダーを流用しない。
試験では私有ディレクトリへ構築し、OS・共有Protonへインストールしていない。

H.264のみを優先し、未合格のVP9/HEVCハードウェアは有効にしない。

```text
GST_PLUGIN_FEATURE_RANK=v4l2h264dec:512,v4l2vp9dec:0,v4l2h265dec:0
FRAME_V4L2_IRIS_RESTART=1
FRAME_V4L2_IRIS_POOL=1
# 診断時のみ
FRAME_V4L2_IRIS_TRACE=1
```

プラグインの探索パスは私有領域へ限定する。生成registryの保存場所は実機で確認し、
試験パスを含むゲームPrefix内の生成cacheは復帰時に原本を残して退避する。
registryディレクトリを環境変数で指定するだけでは、ゲーム側で反映されない場合がある。
この設定例だけを通常のSteam起動オプションへ追加しても、FEXゲストへ届く保証はない。
試験結果・適用範囲は[動画デコード調査](../../docs/HARDWARE_VIDEO_DECODE.md)を参照。

## ライセンス

追加部分は **LGPL-2.1-or-later** とする。このディレクトリのパッチとCOPYINGに、
プロジェクト独自部分のアルファ版配布制限を追加適用しない。
元ファイルの著作権表示とLGPL-2.0-or-laterの表示は保持する。
元のデコーダー実装にはCollabora Ltd.とNicolas Dufresne氏の表示がある。
全文は[COPYING](COPYING)。第三者へのバイナリ提供時には、対応するソース・変更・ライセンスの提供条件も確認する。
