# 全国インラインスケート大会収集（初期実装）

- `collect-inline-events.yml` は毎日10:25 JSTに巡回し、未承認候補をGitHub Issuesに登録します。
- HTML抽出は保守的な試作です。PDF、複数日開催、名称・日付の分離、ページネーション、各種目の網羅には未対応です。候補には誤抽出が含まれ得ます。
- **Issueに approved ラベルを付けても公開されません。** 安全のため、主催者情報を確認して `events/approved.json` に明示的に追記したものだけ公開します。
- `approved.json` は配列。各要素に `title`, `date_start`, `date_end`, `category`, `status:"approved"`, `official_url` を指定してください。日付は YYYY-MM-DD。
- `build-inline-events-site.yml` は公開用静的ファイルをActions artifactに出力します。**GitHub Pagesへの公開はまだ設定していません**。公開する場合はGitHub Pagesの有効化と専用デプロイWorkflowが必要です。
- LINEのリッチメニューはまだ変更していません。既存の天気通知ワークフローにも触れていません。
