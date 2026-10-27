# 全国インラインスケート大会情報

## 現在の仕組み
- 毎日10:25 JSTに `collect-inline-events.yml` が候補を収集し、未承認の候補をGitHub Issuesへ登録します。
- **候補は自動公開されません。** 主催者の公式発表で大会名・開催日・種目・場所・中止情報を確認してください。
- 候補IssueのJSONを編集し、正式な大会名・日程・都道府県・種目・公式URL（official_url）を確認して追記します。その後、リポジトリ所有者が Actions の `Approve reviewed inline event` を手動実行し、Issue番号を指定すると検証後に `events/approved.json` に登録します。ラベルだけでは公開されません。
- `build-inline-events-site.yml` は承認済みデータを検証し、GitHub Pagesにデプロイします。
- 一覧サイトは日本時間で大会最終日を過ぎると自動的に非表示にします。

## 公開前の設定
GitHubリポジトリの **Settings → Pages → Build and deployment → Source** で **GitHub Actions** を選択してください。設定後、Actionsの「Publish approved inline events site」を手動実行します。公開URLは通常 `https://omochi-crypto.github.io/inline-skate-events/` です。デプロイ成功まで公開を断定しないでください。

## 承認済みデータの例
```json
[
  {
    "title": "主催者が確認できた大会名",
    "date_start": "2027-01-01",
    "date_end": "2027-01-01",
    "category": "speed",
    "prefecture": "愛知県",
    "status": "approved",
    "official_url": "https://example.org/official-event"
  }
]
```
※これは記入例です。実在の大会情報ではありません。

## 現状の制約
- HTMLからの抽出は試作段階です。PDF、複数日開催、地域大会の網羅、名称と日付の分離には未対応です。
- ソースURLの正確性・網羅性、抽出結果の精度は今後検証が必要です。
- 承認は所有者による手動ワークフロー実行が必要です。候補IssueのJSONを編集してから承認してください。自動抽出された名称や日付を無確認で承認しないでください。
- LINEリッチメニューは未設定です。天気通知リポジトリは変更しません。
