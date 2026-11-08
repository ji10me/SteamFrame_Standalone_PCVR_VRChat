# 開発・再配布 / Development and redistribution

独自部分の改変・組み込み・再配布はMITの条件で可能です。
元作者の名前を含む著作権表示と、ライセンス全文を残してください。第三者部品の表示も保持してください。
本人のPrefix・認証情報・ログ・写真・視線校正値をIssue、コミット、配布物へ含めないでください。

Pythonの合成データ・モックによる試験は、リポジトリのルートで実行できます。

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
```

独立したコンパイラーや外部ソースが必要な試験は、環境によりskipされます。
実機試験とWindows実機のGUI試験は別に扱ってください。
対応ソースとコンパイル手順は[再ビルド手順](installer/REBUILD.md)を参照してください。
FEX上流ソースには、このプロジェクトとは別の貢献規約があります。上流の規約を確認してください。

Original project components may be modified, integrated and redistributed under MIT, preserving original-author copyright notices and the full license text. Third-party components retain their own licenses.
Never commit or redistribute personal prefixes, credentials, private logs, photos or gaze calibration.
