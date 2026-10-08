[English](./CONTRIBUTING.md) | **日本語**

# agent-tools への貢献

Issue と PR を歓迎します。

## 不具合の報告と機能の要望

[GitHub Issues](https://github.com/yukimasaki/agent-tools/issues) を使ってください。テンプレートで、対象のスキル、エージェントのハーネス（Claude Code / Codex / Pi）、herdr のバージョンを尋ねます。

## スキルを変えるとき

- スキルは小さく保つ。持つのは最低限の道具と、いつ使うかの知識だけ。1 本の道具は 1 つの操作だけを担い、他の道具に依存しない
- マシンに固有の値（パス、ポート、プロジェクト名、閾値）をリポジトリに置かない。`~/.config/<ツール名>/` の設定ファイルから読み、架空の値を入れた `config.example.*` を同梱する
- 別のエージェントで動作を確かめたら、次の 4 つを一緒に直す: `metadata.agent-tools-agents`、`package.json` の `pi.skills`、`compatibility`（Claude 専用のスキルだけ）、`agents/openai.yaml`（Codex で確かめていないスキルだけ）
- 他のプログラムの画面と照合する文字列（承認の確認、フッター）や herdr の状態の値は翻訳しない

## 検査

PR を出す前に全部の検査を通してください。

```bash
status=0; for t in tests/*.sh; do bash "$t" || { echo "FAILED: $t"; status=1; }; done; [ "$status" -eq 0 ]
```

検査には `jq` と、PyYAML の入った Python が要ります。`claude plugin validate` の部分は、claude CLI があるときだけ動きます。

## コミットメッセージ

[Conventional Commits](https://www.conventionalcommits.org/)（日本語で構いません）。

```text
feat(agent-graph): pr.sh に --dry-run を足す
fix(memgate): 集計し直したあとに知らせるかを判定し直す
```

## ライセンス

PR を出すことで、その貢献が [MIT License](./LICENSE) で公開されることに同意したものとみなします。
