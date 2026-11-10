# 0.3.7-alpha — 更新されたProton Experimentalへの対応

2026-11-11。[日本語README](../README.md) / [English README](../README.en.md)

## 変更

Proton ExperimentalはSteamで頻繁に自動更新されます。
0.3.6までは確認済み版のファイルハッシュと完全一致しないと「Proton Experimentalが確認済み版と異なります」と表示して停止していたため、更新後のFrameでは導入できませんでした。
報告された例では、確認済みのビルドID 26306234に対して、Frame上のビルドIDは26491492でした。

- かんたんインストーラー（`frame_setup.py`）：Protonのハッシュが一致しない場合は警告を表示して続行します。
  専用のコピーで差し替える対象（`proton`、DXVKの`d3d11.dll`・`dxgi.dll`、`files/lib/wine/x86_64-windows/mf.dll`）が存在しない場合は停止します。
- 手動キット（`tools/install_portable.py`）：ProtonのビルドIDが違う場合は`warnings`へ出力します。必要なDXVK設定キーの検査は従来どおり行います。
- FEX-Emuは、ラッパーの書き換えとthunkの作成が確認済み版を前提にしているため、従来どおり版が一致しなければ停止します。
- Steam上の共有Protonは今までどおり変更しません。専用のコピーだけを変更します。

## 確認範囲

確認済みでないProtonの版に、確認済みの版を基にした`mf.dll`とDFR版DXVKを組み合わせます。
DXVKはWineの内部APIに依存しないため、影響は小さいと想定しています。
`mf.dll`はWine内部の変更により、動画の再生や音声の再開で不具合が出る可能性があります。導入後は動画プレイヤーのあるワールドで確認してください。
新しいProtonの版での実機確認は、これから行います。

## English

Proton Experimental is updated frequently by Steam. A Proton build that differs from the tested build is now reported as a warning instead of stopping installation, as long as the files replaced in the private copy exist. FEX-Emu version checks remain strict. The shared Steam Proton is still never modified.
The bundled `mf.dll` and DFR DXVK are combined with an untested Proton build. Check video playback after installation.

## 配布物の検査

自動テストは282件で失敗なし（外部のソース入力が必要な2件はskip）。`verify_release.py`で、EXE内のpayload・固定FEX・公式OpenSSH・配布ZIPが一致することを確認しました。

| 配布物 | bytes | SHA-256 |
|---|---:|---|
| SteamFrame-PCVR-VRChat-Setup-Windows-alpha.zip | 19908260 | `d4ebe8c80c9a4d5309ab0799ae354daffd9e1b6ace041f3d662c2d927ecf4e7b` |
