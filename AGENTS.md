# AGENTS.md

## プロジェクト固有の注意事項

- `yarn` / `uv` は必ず `client/` / `server/` に移動して実行する
- python は必ず `uv run` 経由で実行する

## 開発環境構成

### サーバー API (port 7000、常にユーザー管理 / エージェントは起動・停止しない)

- 基本的にホットリロードモードで常駐している。**エージェントが起動・停止してはならない**
- listen ポートは常に 7000 固定 (Akebi HTTPS Server が `127.0.0.77:7010` をリバースプロキシ)
- 挙動確認: リロードモードなら自動反映。それ以外なら**ユーザーに「リロードモード起動への切り替え or 再起動」を依頼する**
- `python KonomiTV.py` / `uv run python KonomiTV.py` の直接実行も禁止。

### クライアント開発サーバー (port 7001、必要ならエージェント起動可)

- 普段は起動していない。UI 検証時は `client/` で `yarn dev` を起動してよい (Akebi 経由 7001、内部 Vite は `127.0.0.77:7011`)
- **重複起動禁止**。起動前に `ps -ef | grep vite` で既存プロセスを確認する
- `yarn dev` のクライアントは開発モード時のみ同ドメイン `:7000` の API を直接叩くようハードコード ([client/src/utils/Utils.ts](client/src/utils/Utils.ts) の `Utils.api_base_url` 参照)。Vite proxy 不要
- Chrome DevTools MCP では `https://my.local.konomi.tv:7001` にアクセス
- API が想定通り動かなくても**サーバーを立て直さない**。まず `Utils.api_base_url` の DEV 分岐を読み直し、`ps -ef | grep KonomiTV` で 7000 のサーバー状態を確認する

### その他

- **HTTPS 必須**: クリップボード等 Secure Context (HTTPS) でしか動かない API を使うため。Akebi HTTPS Server が `akebi.konomi.tv` keyless server 経由でリバースプロキシし、内部 HTTP は `127.0.0.77` でリッスンする

## 技術スタック

クライアント・サーバー構成のセルフホスト型 Web アプリ (PWA)。Windows / Linux のクロスプラットフォーム対応。

- `client/`: フロントエンド (PWA) — TypeScript / yarn v1 / Vite / Vue.js 3.x (Vuetify 3.x, Pinia)
- `server/`: バックエンド API — Python 3.11 / uv / Uvicorn / FastAPI (Pydantic v2) / Tortoise ORM + SQLite (ローカル動作必須のため SQLite 採用) + Aerich

## ディレクトリ構成

### クライアント (`client/`)

- `public/`: 静的ファイル
- `src/`: ソースコード
  - `views/`: ルートコンポーネント/ページ (`TV/` `Videos/` `Reservations/` `Settings/`、および `Login.vue` `Register.vue` `MyList.vue` `WatchedHistory.vue` `MyPage.vue` `NotFound.vue`)
  - `components/`: Vue コンポーネント
    - `Watch/`: 視聴画面向け。`Panel/` (右パネル内表示)、`Panel/Twitter/` (ツイート検索/TL/キャプチャ管理/表示)
    - `Timeshift/`: タイムシフト録画の恒久保存ダイアログ群。`SaveDialog.vue` (record 1本をまるごと保存) / `CutOutDialog.vue` (番組の区切りとは無関係にレコーダーのリングバッファ全体から絶対時刻で範囲を切り出し保存、複数 record にまたがってもOK)
    - `Settings/`: 設定ページのダイアログ群
    - `HeaderBar.vue` / `SPHeaderBar.vue` (スマホ用) / `Navigation.vue` / `BottomNavigation.vue` (スマホ用) / `Snackbars.vue` (通知) / `Breadcrumbs.vue` (パンくず)
  - `stores/`: Pinia ストア
  - `services/`: API サービスクライアント
    - `player/`: ライブ/ビデオプレイヤーのロジック (**重要**)
      - `managers/`: PlayerController に紐づく各機能の PlayerManager 群
      - `PlayerController.ts`: DPlayer 関連ロジックをラップする再生系の中核クラス
  - `utils/`: ユーティリティ
  - `workers/`: Web Workers コード (with Comlink)
  - `styles/`: グローバル CSS (メインは `App.vue`)
  - `router/`: Vue Router 設定 / `plugins/`: Vue プラグイン初期化
  - `App.vue`: ルートコンポーネント (グローバル CSS 定義含む) / `main.ts`: エントリーポイント
- `package.json` / `vite.config.mts` / `tsconfig.json` / `.eslintrc.json`

### サーバー (`server/`)

- `app/`: FastAPI アプリケーションコード
  - `routers/`: API ルートハンドラー
    - `ChannelsRouter` (チャンネル) / `ProgramsRouter` (番組) / `VideosRouter` (録画番組) / `SeriesRouter` (シリーズ)
<<<<<<< HEAD
    - `LiveStreamsRouter` (ライブ配信) / `VideoStreamsRouter` (録画配信)
    - `ReservationsRouter` (EDCB 録画予約) / `ReservationConditionsRouter` (EPG 自動予約条件) / `DataBroadcastingRouter` (データ放送のネット接続)
=======
    - `TimeshiftRouter` (mirakc タイムシフト録画のレコーダー/レコード一覧、および恒久保存 API (`TimeshiftSaveTask` へのジョブ投入・進捗一覧))
    - `LiveStreamsRouter` (ライブ配信) / `VideoStreamsRouter` (録画配信) / `TimeshiftStreamsRouter` (タイムシフト録画配信)
    - `ReservationsRouter` (mirakc 録画予約) / `ReservationConditionsRouter` (EPG 自動予約条件) / `DataBroadcastingRouter` (データ放送のネット接続)
>>>>>>> ca5a50f1 (Docs: ReadmeとAGENTS.mdを更新)
    - `CapturesRouter` (キャプチャ) / `TwitterRouter` (Twitter) / `NiconicoRouter` (ニコニコ実況)
    - `UsersRouter` (ユーザー) / `SettingsRouter` (設定) / `MaintenanceRouter` (メンテ) / `VersionRouter` (バージョン)
  - `models/`: DB モデルとスキーマ
    - `Channel` (放送局/ch番号/ロゴ/ストリーム設定) / `Program` (番組メタ/EPG) / `RecordedProgram` (録画番組メタ/録画時刻) / `RecordedVideo` (動画ファイル情報)
    - `Series` (シリーズ) / `SeriesBroadcastPeriod` (放送期間) / `TwitterAccount` (Twitter 連携) / `User` (アカウント/権限)
  - `migrations/`: Aerich 向け DB マイグレーション定義 (自動生成を修正したもの)
  - `streams/`: ライブ/オンデマンド ストリーミング実装
    - `LiveEncodingTask` / `VideoEncodingTask` (エンコード・配信タスク) / `LiveStream` / `VideoStream` (状態管理) / `LivePSIDataArchiver` (PSI/SI 抽出・アーカイブ)
  - `metadata/`: 録画番組からのメタデータ抽出・保存
    - `RecordedScanTask` (録画フォルダ監視・DB 同期) / `MetadataAnalyzer` (メタデータ解析) / `TSInfoAnalyzer` (TS 番組情報解析) / `ThumbnailGenerator` (シークバー用タイル画像+代表サムネ生成) / `CMSectionsDetector` (CM 区間検出)
<<<<<<< HEAD
=======
  - `tasks/`: シングルトンのバックグラウンドタスク
    - `AutoReservationTask` (mirakc の SSE イベント + 定期スキャンでキーワード自動予約条件と EPG を突き合わせ、マッチした番組を mirakc の録画スケジュールに反映)
    - `TimeshiftSaveTask` (mirakc タイムシフト録画のリングバッファ内容を録画フォルダ配下へ無劣化コピーし恒久保存するジョブキュー。record 1本まるごと保存と、番組をまたぐ絶対時刻範囲の切り出し保存の2種類。書き出したファイルは `RecordedScanTask` が自動検知して DB 登録する)
>>>>>>> ca5a50f1 (Docs: ReadmeとAGENTS.mdを更新)
  - `utils/`:
    - `edcb/` (EDCB 連携クライアント) / `JikkyoClient` (ニコニコ実況・NX-Jikkyo) / `TwitterGraphQLAPI` (リバエン Twitter クライアント) / `TSInformation` (MPEG2-TS 情報取得)
    - `OAuthCallbackResponse` (OAuth コールバック用特殊レスポンス) / `DriveIOLimiter` (ドライブ別同時実行制限) / `ProcessLimiter` (プロセス別同時実行制限)
  - `app.py`: FastAPI/ルーター初期化・バックグラウンドタスク定義 / `config.py`: `config.yaml` ロード・バリデーション / `constants.py`: グローバル定数 / `logging.py`: ロギング設定 / `schemas.py`: API 用 Pydantic スキーマ
- `data/database.sqlite`: SQLite DB / `logs/`: ログ / `misc/`: メンテ・デバッグ用スクリプト
- `static/`: 提供静的ファイル (Git 管理下、放送局ロゴ等) / `thirdparty/`: ビルド済みエンコーダー等 (Git 管理外、`uv run task update-thirdparty` で更新)
- `pyproject.toml` / `KonomiTV.py` (エントリーポイント) / `KonomiTV-Service.py` (Windows サービス)

## コーディング規約

### 全般
- 斜め読みの可読性を高めるため日本語コメントを多めに書く。コメントは冗長なくらいでよく、条件分岐・ループ・例外処理の直前には意図を書く
- 既存コメントは、内容がコードと矛盾しない限り量に関わらず保持する
- **ログメッセージは文字化け回避のため必ず英語で書く**
- 上記以外のスタイルは変更箇所周辺のコードに合わせる
- 不要な薄いラッパーや別名関数は作らず、責務のあるコンポーネントのみ追加する
- Enum・Literal・Union 型の文字列表現は基本 UpperCamelCase (例: `'None' | 'TopLeft' | 'TopRight' | ...`)
- かなり特殊なソフトなので、不明点は Readme.md を読むか質問する
- **スキーマ定義 (Pydantic / TypeScript 共通)**:
  - 親レコード本体のスキーマを最上位に置き、子スキーマはフィールド定義順に親の直下へまとめて配置 (可読性を損なう配置変更はしない)
  - JSON フィールド値は辞書リテラル `{}` でなく TypedDict コンストラクタで型構造を明示する
  - 画像の幅/高さ/総数/間隔など視覚的に重要なフィールドは定義上部に重要度順で集約
  - TypeScript 側も Python 側と同じ順序を維持。差分が出る場合は理由をコメントで明記

### Python コード
- **編集後は必ず `uv run task lint` (Ruff + Pyright) を実行する**
- 文字列はシングルクォート (Docstring 除く)。Python 3.11 の機能を使う (3.10 以下は考慮不要)
- ビルトイン型で Type Hint (`from typing import List, Dict` は避ける)
- Pydantic は必ず Annotated 記法 (`= Field()` は使わない)
- 命名: 変数・インスタンス変数は snake_case / 関数・クラスは UpperCamelCase / クラスのメソッドは lowerCamelCase
  - FastAPI エンドポイント関数も UpperCamelCase。エンドポイント名はパス/操作を文法的に並び替え「〇〇API」形にする (例: GET `/streams/live/{id}/{quality}/mpegts` → `LiveMPEGTSStreamAPI`、PUT `/users/me` → `UserUpdateAPI`)
- 複数行コレクションは末尾カンマを含める
- `getattr()` で型チェッカーを黙らせるのは禁止。属性は型ヒント/プロパティで公開し、やむを得ない場合は「必ず存在する根拠」を詳細にコメント
- Docstring には Args / Returns を明記。`__init__()` で代入するインスタンス変数には「保持する情報/参照されるメソッド/前提条件」をコメント。クラス Docstring は責務のみ記載し引数説明は `__init__()` に集約
- ロギングは `import logging` でなく必ず `from app import logging` を使う

### Vue / TypeScript コード
- **編集後は必ず `yarn lint; yarn typecheck` (ESLint + tsc) を実行する**
- 文字列はシングルクォート。型安全性を確保する
- 新規実装は Vue 3 Composition API パターン (変数は原則 lowerCamelCase)。既存の Options API コンポーネントはそのまま維持する
- 外部 API フィールドはサーバー側 snake_case のまま参照してよい
- コンポーネント属性は可能な限り 1 行に (約100文字まで)
- **day.js は必ず `utils/index.ts` からインポートして使う。`new Date()` は絶対に使わない**

### CSS / SCSS
- 使用色 (CSS 変数) 等は `client/src/App.vue` / `client/src/plugins/vuetify.ts` を参照する
- 新規 UI は既存コンポーネント・ページのデザインの方向性を踏襲する
