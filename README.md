# 鶴ヶ峰 安全・気象情報サイネージ

鶴ヶ峰周辺の公開気象情報を取得し、注意条件に応じて8種類の安全画像を全画面表示するGitHub Pages向けサイトです。

## 導入
1. このフォルダの中身を新しいPublicリポジトリへアップロードします。
2. Settings > Pages > Build and deployment で `Deploy from a branch`、`main`、`/(root)` を選びます。
3. Actions > Update safety weather data > Run workflow を実行します。
4. `https://アカウント名.github.io/リポジトリ名/` をサイネージへ登録します。

## 画像と条件
- dry-warning.png: 乾燥注意報
- rain-probability.png: 降水確率60%以上
- low-temperature.png: 最低気温5℃以下
- heavy-rain.png: 大雨情報または現在降水量10mm/h以上
- landslide.png: 土砂災害情報
- sunset.png: 日没30分前から日没10分後
- strong-wind.png: 風速10m/s以上
- thunder.png: 雷注意報

数値基準は `config.js` で変更できます。注意情報は毎時00分から10分、警報級情報は即時表示します。複数条件は20秒ごとに切り替えます。

## 重要
本表示は補助情報です。現場責任者の指示、会社の安全基準、自治体・気象庁の公式情報を優先してください。気象庁側のデータ形式変更時には `scripts/update_data.py` の調整が必要になる場合があります。
