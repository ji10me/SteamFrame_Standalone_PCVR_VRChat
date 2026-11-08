# H.264動画と音声の修正

FrameのIris向けにGStreamer V4L2の出力処理を修正し、H.264のハードウェアデコードを優先します。
出力形式調整時のbuffer pool生成、drain後の再開、失敗時のエラー伝播と状態解除を対象にしています。
VP9・HEVCの未合格ハードウェア経路は有効にしません。

Wine MFには、再生速度0で開始した音声を1へ戻す際の再開修正を含めます。
対象スレッドと元のCPU割り当てが確認済み構成に一致する場合だけ、音声とJob WorkerのCPU割り当ても調整します。

同じFrameで動画出力と、アバター読み込み中の音声改善を確認しています。
すべての動画・ワールド・長時間利用の安定性を実証したものではありません。
0.3.4再導入時は部品ハッシュを照合しましたが、動画経路の再計測は行っていません。

- [GStreamer修正とライセンス](../patches/gstreamer-iris-drain-restart/README.md)
- [Wine MF修正とライセンス](../patches/wine-mf-audio-rate-resume/README.md)
- [対応ソースと再ビルド](../installer/REBUILD.md)
- [検証範囲](VALIDATION.md)
