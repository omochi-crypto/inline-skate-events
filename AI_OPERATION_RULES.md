# AI_OPERATION_RULES.md
## インラインスケート大会情報収集システムのAI安全運用ルール
最終作成日: 2026-10-31
対象: `omochi-crypto/inline-skate-events`

### 目的と前提
GitHubアカウントのアクセス制限リスクと、誤公開・過剰収集・データ破損のリスクを減らす。以前のアカウント停止原因は未特定であり、このルールは停止を完全に防ぐ保証ではない。GitHubの利用規約・公式API制限を優先する。

### 最重要の3原則
1. **過剰なAPIアクセスを防ぐ**：必要なファイル・Issueだけ取得し、短間隔のポーリング、不要な同時リクエスト、大量更新を避ける。アクセス回数の固定上限を憶測で設定せず、GitHubのレート制限に従う。
2. **アクセス制限時は停止・待機する**：401/403/429、認証異常、二次的レート制限が出たら書き込みと自動再試行を停止する。Retry-After・レート制限リセット時刻に従う。再開の判断が必要な場合はユーザーへ報告し、連続再試行しない。
3. **自動処理の暴走・重複を防ぐ**：Actionsのスケジュール・同時実行・終了条件・Issue作成数を点検し、必要に応じconcurrency、上限、冪等性を設ける。収集対象サイトにも過度なアクセスをしない。

### 作業開始時
- AIは最初に本ファイルを読み、対象リポジトリ・目的・変更対象を確認する。読めなければその旨を報告し、書き込みを始めない。
- 最新のREADMEと対象ファイルを確認し、変更を最小限にする。既存の正常稼働機能を明示依頼なく変更しない。
- 天気通知用の別リポジトリ `omochi-crypto/-konan-weather` は変更しない。

### 大会情報固有の安全策
- 公式情報で大会名・日付・会場・URLを照合し、不明事項は推測で補わない。
- **大会のWeb公開にはユーザーによる大会ごとの明示承認が必要**。Issueのラベル変更・クローズ、単なる「続けて」は承認ではない。
- 実在大会をテスト承認しない。検証は隔離データか公開を伴わないpreviewを使用する。
- 重複候補のIssueを乱造しない。既存Issue、開催日、名称を確認し、承認履歴を残す。
- 収集先の利用規約、robots等の制約、レート制限を尊重し、取得制限を迂回しない。
- SNS情報は一次情報と区別し、開催済み大会を翌年開催として扱わない。
- アクセストークンや秘密情報をコード・Issue・ログ・回答に公開しない。

### 変更・検証・報告
- 書き込み前に対象ファイルの最新版とSHAを確認する。変更は可能な限りまとめ、不要な連続コミットを避ける。
- 大量の作成・削除、公開範囲・権限変更は事前にユーザーの承認を得る。
- 変更後は必要最小限の読み戻しで確認し、コード保存・Actions実行・Issue作成・Web公開の成功を区別して報告する。
- エラー時は原因調査を優先し、無制限の再試行をしない。

### GitHub公式参考資料
- https://docs.github.com/en/site-policy/github-terms/github-terms-of-service
- https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api
- https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api
- https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency

このファイルは運用指針であり、GitHub側で自動強制される設定ではない。
