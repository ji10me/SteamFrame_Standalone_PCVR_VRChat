# 外部部品・ライセンスと配布範囲

本プロジェクト独自部分は、キット直下の `LICENSE` にあるMITライセンスで提供する。
組み込み・改変・再配布時も元作者の名前を含む著作権表示と、ライセンス全文を保持する。第三者部分には各元ライセンスが適用される。

このキットは導入用Python・シェル・Cソースと汎用設定だけを含む。
Steam、SteamVR、SteamLinuxRuntime、Proton、FEX実行ファイル、Vulkanドライバー、
VRChat、Easy Anti-Cheat、Wine Prefix、ゲーム・アバターキャッシュは含まない。

インストール時に、受取人のFrame上で次の部品を用意する。

- Steamで本人がインストールした標準ツール・ゲーム。各製品の利用条件に従う。
- FEX: `https://github.com/StonkGuy/FEX.git` の `eac-2610.5`、
  commit `6effd2384085818345ec32e6abb4181c7135a9a7`。
  このコミットのトップレベルは [MIT License](https://github.com/StonkGuy/FEX/blob/6effd2384085818345ec32e6abb4181c7135a9a7/LICENSE)。
  submodule・取り込まれたコードは各ライセンスに従う。トップレベルのMIT表記だけで全依存部品を同一条件とみなさない。
- Proton: [上流のライセンス案内](https://github.com/ValveSoftware/Proton/blob/proton_11.0/LICENSE) が示すとおり、
  部品ごとに複数のライセンスがある。本人が取得した版の `LICENSE`、`LICENSE.proton`、
  各ディレクトリの `LICENSE` / `COPYING` を確認する。上流の案内へのリンクは、導入済みExperimentalの版を特定するものではない。
- Wine、DXVK、Mesa、コンパイラー、FEXのビルド依存等は、本人が取得した各版に付属するライセンスに従う。
- Steam・SteamVR等の利用には [Steam Subscriber Agreement](https://store.steampowered.com/subscriber_agreement/) と各製品の条件、
  VRChatの利用には [VRChat Terms of Service](https://hello.vrchat.com/legal) 等が別途適用される。
- Vulkan self-lookup adapterは本キットのCソースから本人のFrame用にビルドする。
  本人が取得した標準thunkの私有コピーとリンクする。標準thunk本体は同梱しない。

第三者の権利や再配布許可をこの文書で付与するものではない。
FEXやProtonの取得・ビルドによって、Steam・VRChat等の利用条件への適合が保証されるわけではない。
生成された実行環境には独自部分・第三者部分・本人の認証情報が混在するため、ひとまとめに再配布しない。
第三者部分だけの利用・再配布に関する可否は、その部品のライセンスで判断する。
確認日: 2026-11-03。リンク先の最新条件よりも、実際に取得した版のライセンス・適用される利用規約を確認する。
