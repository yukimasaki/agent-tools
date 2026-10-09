[English](./README.md) | **日本語**

# agent-tools

[herdr](https://herdr.dev) の上で **コーディングエージェントのグラフ** を動かすための道具集です。ワークスペースごとに 1 人の指揮役（lead）を置き、別のタブの作業役をまとめます。スキルは **Claude Code・Codex・Pi** で使えます。

- **agent-graph** — 命名、lead の昇格と引き継ぎ、作業役が止まったら lead を起こす監視、PR と Codex のレビューの小さな道具
- **memgate** — 並行して動く多数のエージェントがマシンのメモリを使い切らないようにする。重い起動の前の gate、同時実行の上限、調整役のエージェントへの知らせ

スキルが持つのは、最低限の道具と「いつ何をするか」の知識だけです。レビューの観点、モデルの評価、グラフの形は持ちません。ハーネスの機能が追いついたら、部品ごとに捨てられるようにするためです。

## 必要なもの

- [herdr](https://herdr.dev)（エージェントは herdr の pane の中で動かす。`HERDR_ENV=1`）
- Bash 4 以上、`jq`、`git`。PR の道具には [GitHub CLI](https://cli.github.com/)
- memgate には Python 3.11 以上（標準ライブラリだけ）
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
| `agent-graph` | Claude Code、Codex、Pi | lead と作業役の命名、lead の昇格と引き継ぎ、`watch.sh`（作業役が止まったり承認待ちになったりしたら lead を起こす）、`pr.sh`、`review.sh`、claude・codex・agy・Pi の起動引数 |

### memgate プラグイン

| スキル | 確認済み | 内容 |
|---|---|---|
| `memgate` | Claude Code | 重い起動の前の `gate`、同時実行の枠を取る `run`、`status`、メモリが減ったら調整役のエージェントに知らせる `loop`（設定で有効にすると、新しい lead への取り決めの送付と、新しい作業役の報告も行う） |

## 設定

マシンに固有の値はリポジトリに置きません。memgate は `~/.config/memgate/config.toml` を読みます。例は `plugins/memgate/skills/memgate/config.example.toml` にあります。

## 貢献

[CONTRIBUTING.ja.md](./CONTRIBUTING.ja.md) を読んでください。セキュリティの問題は [SECURITY.md](./SECURITY.md) へ。

## ライセンス

[MIT](./LICENSE)
