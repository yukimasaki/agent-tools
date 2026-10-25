[English](./README.md) | **日本語**

# agent-tools

[herdr](https://herdr.dev) の上で **コーディングエージェントのグラフ** を動かすための道具集です。ワークスペースごとに 1 人の指揮役（lead）を置き、別のタブの作業役をまとめます。スキルは **Claude Code・Codex・Pi・OpenCode** で使えます。

- **agent-graph** — 命名、lead の昇格と引き継ぎ、作業役が止まったら lead を起こす監視、PR と Codex のレビューの小さな道具
- **memgate** — 並行して動く多数のエージェントがマシンのメモリを使い切らないようにする。重い起動の前の gate、同時実行の上限、調整役のエージェントへの知らせ

スキルが持つのは、最低限の道具と「いつ何をするか」の知識だけです。レビューの観点、モデルの評価、グラフの形は持ちません。ハーネスの機能が追いついたら、部品ごとに捨てられるようにするためです。

## 必要なもの

- [herdr](https://herdr.dev)（エージェントは herdr の pane の中で動かす。`HERDR_ENV=1`）
- Bash 4 以上、`jq`、`git`。PR と Issue の道具には [GitHub CLI](https://cli.github.com/)
- memgate と agent-graph の起動の道具には Python 3.11 以上（標準ライブラリだけ）
- memgate が対応するのは **Linux と WSL** だけ（`/proc` と PSI を読む）

## インストール

### Claude Code

```text
/plugin marketplace add yukimasaki/agent-tools
/plugin install agent-graph@agent-tools
/plugin install memgate@agent-tools
```

### Codex

Codex は同じマーケットプレイスの定義を読みます。

```bash
codex plugin marketplace add yukimasaki/agent-tools
codex plugin add agent-graph@agent-tools
codex plugin add memgate@agent-tools
```

### Pi

```bash
pi install git:github.com/yukimasaki/agent-tools
```

Pi は `package.json` の `pi.skills` に列挙したスキル（Pi で動作を確かめたもの）だけを読み込みます。

## スキル

各スキルは、動作を確かめたエージェントを `SKILL.md` の frontmatter（`metadata.agent-tools-agents`）に記録しています。Codex で確かめていないスキルを Codex が暗黙に起動することはなく、Pi には Pi で確かめたスキルだけが配られます。

### agent-graph プラグイン

| スキル | 確認済み | 内容 |
|---|---|---|
| `agent-graph` | Claude Code、Codex、Pi、OpenCode | lead と作業役の命名、lead の昇格と引き継ぎ、`watch.sh`（作業役が止まったり承認待ちになったりしたら lead を起こす）、`start.sh`（タブの作成から最初の指示の受付確認まで）、`pr.sh`、`review.sh`、claude・codex・agy・Pi・OpenCode の起動引数 |
| `take-issue` | Claude Code、OpenCode の起動 | 「#N を取得して」から既存 workspace の作業タブを起動し、「#N をマージして」で本人の PR の管理者 Approve と必須 CI を確認して取り込む |

### memgate プラグイン

| スキル | 確認済み | 内容 |
|---|---|---|
| `memgate` | Claude Code | 重い起動の前の `gate`、同時実行の枠を取る `run`、`status`、メモリが減ったら調整役のエージェントに知らせる `loop`（設定で有効にすると、新しい lead への取り決めの送付と、新しい作業役の報告も行う） |

## 作業役の起動

agent-graph の [`start.sh`](plugins/agent-graph/skills/agent-graph/references/start.md) は、既存 workspace にタブを作り、指示書の受付まで確かめます。起動の時間切れでは同じ pane を読み、無名の agent を安全に確認できた場合だけ命名します。信頼や更新の確認には自動で答えません。

管理者は導入時に engine・モデル・推論・権限の組を承認できます。番号を入力する人には毎回選ばせず、新しい engine と権限の拡大時には承認し直します。

## Issue 番号から始める

`/agent-graph:take-issue` または「#42 を取得して」で、事前に担当を割り当てた Issue の作業を始めます。管理者は [入口の設定](plugins/agent-graph/skills/take-issue/references/setup.md) を先に行います。入口は既存 workspace にタブを足し、Issue ごとに workspace を増やしません。PR 前にはレビューと簡素化を行い、設定済み管理者へレビューを依頼します。「#42 をマージして」は、その利用者本人の PR だけを、最新コミットへの管理者の Approve と必須 CI を確認して取り込みます。ハーネスの追加確認が残る場合は、利用者の言語で対象と操作を案内します。英語の「Merge #42」など、ほかの言語の明示的な依頼も同じ入口で扱います。OpenCode は [専用の設定](plugins/agent-graph/skills/take-issue/references/setup.md#opencode-leads-and-workers)後に Issue の準備と作業役の受領確認まで使えます。herdr が状態を読み違えるため、lead は報告ファイルを待ちます。OpenCode での PR・マージは未検証です。

## 設定

マシンに固有の値はリポジトリに置きません。memgate は `~/.config/memgate/config.toml` を読みます。例は `plugins/memgate/skills/memgate/config.example.toml` にあります。

## 貢献

[CONTRIBUTING.ja.md](./CONTRIBUTING.ja.md) を読んでください。セキュリティの問題は [SECURITY.md](./SECURITY.md) へ。

## ライセンス

[MIT](./LICENSE)
