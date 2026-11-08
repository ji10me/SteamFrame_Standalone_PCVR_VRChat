# 開発用の読み取り専用採取

普段の導入にはWindowsアプリを使ってください。
開発用にOS・Steamの構成を確認する場合は、接続先を明示して実行します。

```sh
sh scripts/collect-frame.sh steamos@FRAME_ADDRESS
```

`FRAME_ADDRESS`を本人のFrameに表示されるホスト名またはIPへ置き換えます。
接続先の既定IPはありません。ホスト鍵確認と認証は通常のSSHに任せます。
採取先はGitで除外された`local/runs/`です。採取データは本人が内容を確認して扱い、公開リポジトリへ追加しないでください。
