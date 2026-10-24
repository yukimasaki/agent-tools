# agent-graph の起動と Issue 番号からの作業開始

[Issue #14](https://github.com/yukimasaki/agent-tools/issues/14) の設計。5 の入口には「#N を取得して」と「#N をマージして」を含める。先行して作る範囲は要望 3 と 5 とし、別のマシンで Claude Code による入口の受け入れを試す。要望 1、2、4 と Q7、Q8 は後続の改善とする。この文書で将来形として書く引数と動作は、実装後に各道具の help と検査で確認する。

## 決定と範囲

| 判断 | 採用する形 | 選ばなかった形と理由 |
|---|---|---|
| Q1 起動の一操作 | `start.sh` がタブ作成から最初の指示の受付確認までを行う | 既存 pane の起動だけを道具化すると、繰り返した名前付け・信頼待ち・指示未達を手順側へ残す |
| Q2 Issue の配置 | 関心ごとの既存 workspace に Issue の作業タブを追加。worktree は `git worktree add --no-track` | Issue ごとに workspace を増やさない。`herdr worktree create` は新 workspace を作るので使わない |
| Q3 プロジェクトの準備 | プロジェクト用スクリプトへ委譲し、入口は設定からパスを読んで呼ぶ | env、port、依存、状態の保存先を汎用の道具へ埋め込まない |
| Q4 起動の組の承認 | 管理者が導入時に engine・モデル・推論・権限の組を承認してマシン側設定へ保存。以後はその組を使う | 毎回番号を入力する人には選ばせない。新 engine と権限拡大には改めて承認が必要 |
| Q5 OpenCode の範囲 | 上限時の控え。先行範囲の受け入れは Claude Code で通れば合格 | OpenCode の親への無人配信や #13 の解決を先行実装の必須条件にしない |
| Q6 別マシンの担当 | 管理者が Issue を切り、事前に担当を割り当てる | 自動取得・担当変更・マシン間の通知を実装しない。PR の知らせは人が伝える |
| 依頼されたマージ | 利用者本人が作った PR だけを、明示的なマージ依頼と管理者の最新 head への Approve、必須 CI を確認して取り込む | 管理者が全件を実行する原則の、設定済み入口への限定例外。別人の PR、自動マージ、GitHub の見張りは対象外 |
| workspace の lead | 関心ごとの lead ごとに workspace を分け、各 workspace は現在の watcher を一つ動かす | 同じ workspace に複数 lead を置く改善は後回し。統括と各 lead は既存の lead 間通信を使う |

マシン固有の値、実在プロジェクトの構成、秘密を公開文書や設定例に入れない。以下では架空の短いプロジェクト名 `demo` を使う。

## 現在の実装と、今回変えない範囲

[watch.sh](../../plugins/agent-graph/skills/agent-graph/scripts/watch.sh) は lead の名前を受け取り、その lead の workspace を解決する。workspace ごとに PID とログを持つ。同じ lead 用の監視が動いていれば何もせず、別 lead 用なら監視を置き換える。1 workspace 1 lead のまま使う。

`observe` は同じ workspace の、名前が `lead-` で始まらない agent を対象にする。無名も対象になる。最初の正常な観測に限り、すでに `done` または `idle` の agent は通知済みとして記録する。`working` と `unknown` は停止通知しない。背景シェルの表示を伴う `done/idle` は待つ。`blocked` は確認の目印があれば直ちに、なければ連続観測後に知らせる。agent が消えて pane が残る場合はプロセス終了として知らせ、pane も消えれば記録を捨てる。

`flush` は一人の lead へ候補をまとめて送る。lead が作業中・確認待ち、または Claude Code の質問画面なら待つ。送信失敗では送信済みにせず、次の周回で現在の停止を判断し直す。人への通知は独立して行う。報告ファイルの存在や中身を読まず、「停止」を知らせる。

1 workspace 1 lead の前提は、引数 `LEAD`、PID の lead 欄、`LEAD_TARGET/STATUS/KIND`、`lead-` の一括除外、lead が二周不在で監視全体を終える処理、昇格・引き継ぎ手順にある。今回の 3 と 5 のために親の割り当てや配信台帳は足さない。

[pr.sh](../../plugins/agent-graph/skills/agent-graph/scripts/pr.sh) は認証を一度解決して push と PR 作成に固定し、[review.sh](../../plugins/agent-graph/skills/agent-graph/scripts/review.sh) は merge-base を決めて Codex のレビューを呼ぶ。準備と起動の道具はこれらを呼ばない。入口の lead は作業役のレビューと簡素化、検証結果を読んでから、設定済み管理者をレビュアーにして PR を作る。マージには別の明示的な入力を要する。

### 確かめた herdr の挙動

herdr 0.9.3 の help と API schema を読み、自分の一時リポジトリとタブだけで試した。

- `worktree create --cwd <main-checkout> --branch <branch> --base main --path <path> --no-focus --trust-repository` は新しい workspace、タブ一つ、root pane 一つを作った。`--label` は workspace の表示名になり、タブ名は `1` だった。
- 作成した linked worktree の workspace を次の作成元にすると `linked_worktree_source` で失敗した。
- 作成した worktree と workspace、自分の作成元タブ、一時リポジトリを片付けた。他人のタブや agent は操作していない。

この結果から Q2 は git の worktree 作成と `tab create --workspace <id> --cwd <worktree> --no-focus` を組み合わせる。`agent start` は既存の対話 shell pane が必要で、成功は同じ端末で agent を検出して入力を受け付けられることまでを意味する。指示の受付確認は別に必要である。

## 3 一セッションを起動する

[promotion.md](../../plugins/agent-graph/skills/agent-graph/references/promotion.md) に「作業役一つを、タブの作成から最初の指示の受付確認まで立てることを一操作と数える」と明記する。道具間の依存禁止、台帳禁止、トポロジーを埋め込まない規則は保つ。

```text
start.sh --workspace <id> --cwd <absolute-path> --name <name>
         --kind <kind> --brief <absolute-path> --report <absolute-path>
         --receipt <absolute-path> --run-id <id>
         [--pane <id>] [--timeout <ms>] [-- <native-args...>]
```

### 起動と再開

1. 引数、名前の文字種と 32 文字制限、cwd と指示書、herdr の内側であることを検査する。名前の競合は live agent 一覧で確かめる。別 workspace、別 cwd、別 kind、別セッションを名前だけで流用しない。
2. pane が指定されなければ `tab create --workspace <id> --cwd <path> --label <name> --no-focus` を使う。応答から tab と root pane の ID を読む。既存 pane を渡す再開ではタブを増やさない。
3. `agent start <name> --kind <kind> --pane <id> -- <native-args>` を呼ぶ。native 引数に実行ファイル名を重ねない。起動引数には最初の作業指示を混ぜず、入力可能なことを確認した後で別送する。
4. 成功・失敗のどちらでも同じ pane の agent と画面を読む。時間切れ後に期待する agent が動いていて無名なら、pane を指定して名前を補う。名前が付いたことだけで成功にしない。同じ pane に二つ目を起動しない。
5. 信頼・更新・権限の確認、状態不明、画面取得失敗では、その段階と pane を人へ知らせて止まる。一律に `1` や Enter を送らない。既存画面の目印をハーネス間で推測して使わない。管理者は表示されたモデルと推論も承認済みの組と照合する。表示できない項目は確認範囲を記録する。
6. 指示書を全部読むこと、今回の run ID と指示書の SHA-256 を受付ファイルへ書くこと、報告先を含む短い起動指示を `agent prompt` で送る。作業役は受付を書いてから作業を進める。受付は一時ファイルから rename して確定する。
7. run ID とハッシュが一致する受付を読めたら成功とする。herdr の起動成功や状態変化だけでは指示を読んだことにしない。期限までに一致しなければ、指示未確認として画面を添えて止まる。

期限は起動・確認・受付を含む全体で管理する。再開では同じ run ID、brief、receipt、pane を使う。今回の受付があれば指示を送り直さない。投入済みか不明な状態で自動再送しない。送信時点を起動ログへ残し、確認を促す。受付・報告とログは成果物であり、セッションの台帳にはしない。

標準出力は JSON とし、結果、run ID、workspace・tab・pane、名前、kind、cwd、brief・receipt・report のパスを返す。終了値は 0 が受付済み、2 が引数不正、3 が配置・名前の競合、4 が確認待ち、5 が起動・指示の未確認。失敗でも自分のタブや worktree を勝手に消さない。`start.sh` は `watch.sh`、PR、レビュー、親への通知の道具を呼ばない。

## 5 「#N を取得して」から始める

入口はスキルの手順と、設定を読む準備の操作に分ける。準備の操作は Issue と worktree を用意し、プロジェクト用スクリプトを呼び、brief と起動に必要な値を返す。`start.sh` は呼ばず、スキルが戻り値を読んで次に使う。git・gh・herdr のコマンドに Issue 本文を shell コードとして渡さない。

### 設定の分担

| 内容 | 置き場所 | 境界 |
|---|---|---|
| repo、短いプロジェクト名、base、branch の規約、worktree の相対的な配置、準備スクリプト、必須 CI とマージ方法 | リポジトリの `.agent-graph/project.json` | 秘密とマシンの絶対パスを入れない |
| 利用者の gh アカウント、管理者レビュアー、承認済みの engine と native 引数、承認の記録 | `~/.config/agent-graph/projects/<project-key>.json` | 管理者が導入時に書く。新 engine と権限拡大では承認し直す |
| 依存、env・証明書、port、可変データの保存先と検証コマンド | プロジェクト用の準備スクリプト | プロジェクト側が所有する。再実行しても既存の作業を壊さない |
| brief、receipt、report、起動ログ | state root の workspace と run ごとの場所 | 作業役へ絶対パスを渡し、同じ run の再開で使う |

マシン設定にはトークンを書かない。gh のアカウントを一度解決し、そのトークンを今回の処理だけに固定する。環境トークンと指定アカウントが違えば止める。fetch も同じ認証または既存の SSH 設定と整合させる。プロジェクト用スクリプトは導入時に管理者が内容を確認する。

### 入口の流れ

1. 番号と設定を検査する。remote と設定の repo を照合し、`gh issue view` で状態・本文・URLを取得する。クローズ済みは開始しない。担当は管理者が事前に分けている前提とし、入口から Issue の担当を更新しない。
2. Issue の目的と今回行う範囲を人に短く説明する。branch は project.json の任意項目 `branch_template`（既定 `{type}/{number}{suffix}`）で生成する。種類 `{type}`、番号 `{number}`、英語の短い名前 `{slug}`、空でない名前の先頭にハイフンを付けた `{suffix}` を使える。明示された `--branch` は生成規約を強制せず、文字どおりの Git ブランチ名として扱う。どちらも `git check-ref-format --branch` で検査する。base を fetch し、既存 branch と worktree を照合する。不一致は上書きしない。
3. `git worktree add --no-track` で作る。すでに一致する worktree があれば、再開として使う。`herdr worktree create` と workspace の新設は行わない。
4. 設定のパスからプロジェクト用準備スクリプトを呼ぶ。repo、番号、branch、worktree、run ID を明示して渡す。失敗したら残した場所と再開方法を返す。既存 env や作業ファイルを一律に上書きしない。
5. Issue、完了条件、担当範囲、準備と検証、受付と報告先を brief に書く。背景プロセスを片付けてから報告すること、重い起動はメモリ gate を通すこと、同じ worktree のサーバは一人が所有することを含める。
6. 関心ごとの lead が自分の workspace で watcher を一つ起動していることを確認する。別 workspace の watcher を起動・置換しない。`start.sh` で既存 workspace にタブを作り、受付を確認する。名前は設定の短いプロジェクト名を使う。
7. 起動成功なら Issue 名と作業タブを示す。失敗なら「フォルダの確認待ち」「指示の受付を確認できない」など、必要な行動と対象タブを利用者の言語で知らせる。以下の日本語は案内の例とする。`done` を「完了」と訳さず、lead が報告と検証結果を読んでから完了を説明する。

watcher の起動成功は PID の生存確認までで、初回観測が済んだ保証はない。最初の観測より前の `done/idle` は記録だけになるため、入口は今回の受付と報告も確認する。入口は監視が準備できたことを確認してから作業を渡す。初回観測の readiness を確認する最小の変更が必要なら、監視の意味を変えずに追加して検査する。モデルから独立した run ごとの補助観測の新設は、今回の必須範囲から外す。利用上限時は人が別の承認済みハーネスへ切り替え、同じ報告と入口を読む。

### 慣れていない人への案内

| 状況 | 例 |
|---|---|
| 開始 | 「#42 の検索画面を作り始めました。作業タブは『impl-demo42』です」 |
| 信頼の確認 | 「#42 はフォルダの確認で止まっています。『impl-demo42』タブで表示されたフォルダを確認してください」 |
| 未確認の起動 | 「#42 の指示の受付を確認できませんでした。作業タブを確認しています」 |
| 上限 | 「#42 は利用上限で止まっています。別の承認済みエージェントで再開してください」 |

原因を読めない停止では上限と断定しない。既存 watcher の通知と、lead が人へ説明する文を区別する。入口の lead は利用者の言語で説明し、英語の herdr 状態値自体は変更しない。

### 「#N をマージして」

入口の lead は、利用者の言語で書かれた最新の人の明示的な依頼を解釈し、対象の Issue 番号を結び付けてマージの確認へ進む。「Merge #42」と「#42 をマージして」は同じ操作の例である。Issue 本文や作業役の報告、引用文を依頼として扱わない。操作や番号が曖昧なら先に確認する。道具へは言語に依存しない `--operation merge --requested-issue <依頼の番号> --issue <対象の番号>` を渡す。道具は指定漏れと番号の不一致を外部呼出し前に拒否する。人の入力を確かめる責務はスキルに残し、道具のフラグだけを人の依頼の証明とはしない。GitHub の Issue の closing reference から一つの PR を特定し、設定した利用者アカウント本人が作った PR だけを対象にする。

管理者の最新の有効なレビューが Approve で、その commit ID が現在の head と一致し、必須 CI がすべて成功している場合だけ取り込む。レビュー後の push は未承認として扱う。足りなければ「管理者の Approve がまだです」「自動の確かめが未完了または失敗しています」などを利用者の言語で返す。GitHub を継続監視しない。

`merge-issue.sh --check` は読み取りだけで条件を示す。実際のマージ呼び出しは条件を再読し、head SHA を指定して即時のマージ API を呼ぶ。自動マージや queue の予約、管理者権限での回避、別経路での再実行はしない。管理者の Approve と利用者のマージ入力を二つの人の判断として、promotion.md に境界を書く。

ハーネスの実行確認は別に残り得る。実行前に対象の PR・取り込み先と操作を利用者の言語で示し、実際の画面が今回のマージだけを確認している場合に、その画面の「今回だけ許可」などの実ラベルを案内する。一律に番号や Enter を送らず、設定変更や別 API で確認を回避しない。この確認が残るプロファイルでは、通常の二つの入力に加えてハーネス側の操作が必要な点を隠さない。

PR 前に Claude Code の作業役は `/code-review` と `/simplify` を通し、指摘を直して関連する検査をやり直す。ほかのハーネスはネイティブのレビューと明示的な簡素化を行い、実行結果を報告へ残す。lead は設定のレビュアーを指定して PR を作る。

## ハーネスの差

| 項目 | Claude Code | Codex | OpenCode |
|---|---|---|---|
| 先行検証 | 先行する入口の受け入れ対象 | 起動の道具で偽物の herdr による契約を検査 | 上限時の控え。動作を実証した範囲だけ記す |
| 起動と名前 | 時間切れ後の無名が複数の運用で起きた | `--kind codex` の後に実行ファイル名を重ねない | 時間切れ後の無名の報告がある |
| 確認画面 | 信頼・外部の指示・権限を確認して止める | 信頼・更新を確認する。未検証の画面を推測しない | `--auto` が全確認をなくすとは扱わない |
| 停止 | 背景シェルと質問画面への既存対処を使う | 親の質問画面の保護は未検証 | [#13](https://github.com/yukimasaki/agent-tools/issues/13) のため `done` を完了としない |
| スキル | `.claude/skills/<name>/SKILL.md` とプラグイン | `.agents/skills/<name>/SKILL.md` とプラグイン | `.opencode/skills`。公式 v2 資料は `.agents/skills` の互換探索も記載 |

配置は [Claude Code 公式](https://code.claude.com/docs/en/skills)、[OpenAI 公式](https://learn.chatgpt.com/docs/build-skills)、[OpenCode v2 公式](https://dev.opencode.ai/v2/docs/skills/) で確認した。配置の仕様と、プラグインでの実証は分けて記録する。起動引数・モデル・推論は導入版の help と表示で照合する。

## 作る順番と検査

| 区切り | 段階・合格条件 | 検査 |
|---|---|---|
| 決定の書き戻し | 先に文書だけをコミット | 選択した Q1〜Q6 と workspace の分離、後回しの範囲を確認。Markdown と相対リンクを検査 |
| 3 起動 | 先行範囲。タブ作成から指示受付までを一回で通す | `tests/agent-graph-start.sh`。偽物の herdr で ID、無名修復、競合、別 kind、信頼待ち、重複引数、古い受付、未受付、再開、API 失敗、全体期限を検査 |
| 5 入口 | 3 の検査後。番号から設定・worktree・準備・起動へ進む | ローカル git と偽物の gh・herdr。番号、認証固定、repo 不一致、クローズ済み、branch、途中失敗、既存 worktree と再実行を検査 |
| 5 マージの入口 | 明示依頼時だけ、本人の PR・最新 head の管理者 Approve・必須 CI を確認 | 偽 gh で本人以外、未承認、レビュー後 push、CI 待ち・失敗、複数 PR、head 変更、読み取り preview、書込みの再検査を検査。実 GitHub へは試験のマージをしない |
| 自分の実機確認 | 先行範囲の実装後、Claude Code で通す | 自分の試験 workspace とタブで、番号から受付・報告・停止通知まで進める。準備は小さな試験用スクリプト。作ったものだけ片付ける |
| 別マシン | 別のマシンでの受け入れの試し | 管理者が設定を承認後、慣れていない人が番号だけで開始。Claude Code で通れば足りる |
| 1・2・4 | 後続の改善 | 親の保存、再送、関心ごとの lead の型と Q7・Q8 を改めて検討 |

既存の検査は [CONTRIBUTING.ja.md](../../CONTRIBUTING.ja.md) に従って通す。監視の検査は偽物の herdr とテスト専用の PID・プロセスだけを使う。README の両言語、スキルの metadata、配布、manifest の整合も検査する。OpenCode の説明を足しただけで、確認済みハーネスを増やさない。

## 後続の改善と問い

### 1 親を覚える監視と Q7

同じ workspace に複数 lead を置く必要が改めて生じたら、親の保存先を比較し直す。名前への埋め込みは長さと改名、タブラベルは人の変更、環境変数は一覧からの再取得、pane の token は保持・TTL・source、親ファイルは台帳禁止と更新の排他が課題になる。初期下書きでは親ファイルを推したが、今回は採用も実装もしない。現在の一人の lead の監視をそのまま使う。

### 2 再送と Q8

独立した一通の通知を実行中だけ再試行する案と、永続の未送信箱を持つ案を残す。後者は台帳禁止の変更が必要。今回、`notify.sh`、未送信箱、マシン間の配信は作らない。

### 4 関心ごとの lead の型と名前の例

役の型の実装は後回しにする。現時点の命名例は、lead の接頭辞を維持して短いプロジェクト名で区別する。既存名を強制改名しない。

| 役 | 例 |
|---|---|
| 統括 | `lead-demo` |
| 関心ごとの lead | `lead-demo-rec`、`lead-demo-ui` |
| 作業役 | `impl-demo42`、`review-demo42` |
| 引き継ぎ前 | `lead-demo-rec-g1` |

すべて herdr 全体で一意、32 文字以内とし、`-next` と `-gN` の余地も空ける。名前から親を推測しない。統括と関心ごとの lead は workspace をまたいで指示書と報告を渡す。作業役は自分の workspace の lead だけへ報告する。

未決の Q7 は親の保存先、Q8 は未送信を永続化するかであり、どちらも 3 と 5 を止めない。プロジェクト固有の準備と検証はプロジェクト側が実装し、入口との引数の契約を合わせる。個別の構成や固有値をこの汎用設計へ持ち込まない。
