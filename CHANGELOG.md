# Changelog

## [0.1.6](https://github.com/yukimasaki/agent-tools/compare/v0.1.5...v0.1.6) (2026-10-25)


### Features

* **agent-graph:** OpenCode の作業役の起動と受け取りの確かめに対応する ([#20](https://github.com/yukimasaki/agent-tools/issues/20)) ([10dfee6](https://github.com/yukimasaki/agent-tools/commit/10dfee6e2619f6023e3548408d004a98bbb36a61))

## [0.1.5](https://github.com/yukimasaki/agent-tools/compare/v0.1.4...v0.1.5) (2026-10-24)


### Features

* **agent-graph:** Issue番号から作業開始と承認済みPRのマージを行う ([#15](https://github.com/yukimasaki/agent-tools/issues/15)) ([81c302e](https://github.com/yukimasaki/agent-tools/commit/81c302e32cc6cfd9eeb3aff126b4f34895676a54))


### Bug Fixes

* **agent-graph:** 起動直後の折り返した足元を見分け、見分けられない画面は少し待つ ([#18](https://github.com/yukimasaki/agent-tools/issues/18)) ([1b1fb06](https://github.com/yukimasaki/agent-tools/commit/1b1fb06577ffbb3205e11fa2e09409c660e4892b))

## [0.1.4](https://github.com/yukimasaki/agent-tools/compare/v0.1.3...v0.1.4) (2026-10-09)


### Features

* **memgate:** loop が新しい lead に取り決めを送り、新しい作業役を調整役に知らせる ([#10](https://github.com/yukimasaki/agent-tools/issues/10)) ([ffe2e39](https://github.com/yukimasaki/agent-tools/commit/ffe2e39c4a2df8a70fd4280c8575ad4755c6de4d))

## [0.1.3](https://github.com/yukimasaki/agent-tools/compare/v0.1.2...v0.1.3) (2026-10-08)


### Bug Fixes

* **memgate:** git worktree だけを開いているとき、本体と共用の Docker を残置の疑いにしない ([#8](https://github.com/yukimasaki/agent-tools/issues/8)) ([13c81a7](https://github.com/yukimasaki/agent-tools/commit/13c81a7e657a08c406b180c6afc8b65bc4b3a451))

## [0.1.2](https://github.com/yukimasaki/agent-tools/compare/v0.1.1...v0.1.2) (2026-10-08)


### Bug Fixes

* **memgate:** 空きが十分あるときの PSI の一瞬の山で、WARN にも gate の拒否にもしない ([#7](https://github.com/yukimasaki/agent-tools/issues/7)) ([6827e41](https://github.com/yukimasaki/agent-tools/commit/6827e41b5182deb90dac41793e0e8f7767d4fbbf))
* **memgate:** 起動して間もない Docker コンテナと --rm の一時コンテナを、残置の疑いにしない ([#5](https://github.com/yukimasaki/agent-tools/issues/5)) ([659feb2](https://github.com/yukimasaki/agent-tools/commit/659feb291c65381aee3742d1208a003e91e364d5))

## [0.1.1](https://github.com/yukimasaki/agent-tools/compare/v0.1.0...v0.1.1) (2026-10-08)


### Features

* エージェントグラフの道具集 agent-tools を公開する ([1f6bf33](https://github.com/yukimasaki/agent-tools/commit/1f6bf336e7483d9a9632d90ac6f95c83853c1335))
