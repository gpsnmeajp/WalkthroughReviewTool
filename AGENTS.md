# このリポジトリのAI向け入口

「walkthrough-reviewスキルを実行」または同等の資料作成依頼では、
[walkthrough-review](skills/walkthrough-review/SKILL.md) を読み、その手順を最後まで実行する。
引数がない場合の対象・モード・出力先はスキルの既定値を使い、人間にCLIやJSONの準備を依頼しない。
スキル自体の監査・変更依頼では、依頼された変更を行い、プロジェクト全体のレビュー生成へ置き換えない。

配布する実体は `skills/walkthrough-review/` 一式。Python実装と画面素材はそこだけで保守する。
元の要求は [dev/docs/DRECTION.md](dev/docs/DRECTION.md) に保持する。
開発資料・テスト・検証データ・補助CLI・配布ツール・開発用生成物は `dev/` 配下にまとめる。
開発用CLIはスキル内の実装を呼び出し、実装や画面素材を複製しない。
変更後は [dev/README.md](dev/README.md) の関係するテストと、
リポジトリルートから `python dev/tools/package_skill.py` で単独配布を検証する。
