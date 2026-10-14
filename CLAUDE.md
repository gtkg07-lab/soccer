# サッカー情報アプリ

プレミアリーグ・ラ・リーガ・リーグ・アン・チャンピオンズリーグ・UEFAネーションズリーグの試合結果・日程・順位表・スタメン・スタッツ・選手一覧を見られるスマホ向けWebアプリ。

## 構成
- `index.html` / `app.js` / `style.css`：画面（ビルド不要の素のHTML/JS）
- `scripts/fetch.py`：ESPN の公開データ（非公式）から `data/` に JSON を保存する（Python 標準ライブラリのみ）
- `data/`：自動生成。手で編集しない
- `.github/workflows/update.yml`：15分ごとに更新、毎日0時（日本時間）に選手一覧も更新して GitHub Pages に公開

## 決まりごと
- 説明は日本語で
- 有料のデータサービス（API-Football など）への切り替えは、必ず事前に説明して承諾を得る
- APIキーなどの秘密情報はファイルに書かない（GitHub Secrets を使う）
