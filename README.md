# vision-mcp

Strataの「外付けの目」。NASの画像専用フォルダーから、ユーザーが指定した1画像と短い依頼だけをOpenAI公式Responses APIへ送り、観察結果をテキストで返します。最終推論・判断はStrataで続けます。

```text
Windows: スクリーンショット → NASの画像専用SMB共有へ保存
Strata: ファイル名と依頼 → HTTP MCP /mcp → vision-mcp (NAS Docker)
                                           ↓ 検証・縮小・メタデータ除去
                                       OpenAI Responses API
                                           ↓ 簡潔な観察テキスト
Strata: 観察結果を使って最終回答
```

MCPツールは `analyze_image` だけです。既存の `websearch-mcp` は別サーバーのまま使います。

## 調査結果と採用方式

調査日: 2026-10-07。Strataは環境の特徴に一致する [Niko1221/Strata](https://github.com/Niko1221/Strata) を対象に確認しました。導入済みの版・フォークとの一致は未確認です。

1. **Strataから画像そのものを渡せるか**: MCPの`tools/call`はJSON引数なので、ツールが定義すればbase64文字列も転送できます。ただしStrata Web UIの添付画像が自動的にツール引数へ入る仕組みは、確認した実装にはありません。`serve/web/app.js`は`health.images`がfalseのとき画像追加を拒否し、画像貼り付けも処理しません。`serve/mcp.py`は辞書のargumentsをそのまま転送し、画像のツール応答はモデルに表示しません。返り値はテキストにします。
2. **Windows AMDでも実現できるか**: 画像の代わりに相対ファイル名をテキストで渡せば、ローカルVision機能を使わずに実現できます。NASが画像を読み、OpenAIが解析します。AMD/GPU関連のライブラリはコンテナに不要です。
3. **最適な入力方式**: 初版はNAS専用共有内の相対ファイル名です。`C:\Users\...`はNASからアクセスできません。Windowsから共有へ保存し、共有内の名前だけを指定します。MCP `ImageContent`は主にコンテンツブロックであり、ツール引数への自動添付転送を保証しません。
4. **Responses API入力**: `input`内に`input_text`と`input_image`を置き、後者の`image_url`へbase64 data URLを指定します。公式には公開URL・data URL・Files APIのfile IDが使えます。この実装はサーバー側でdata URLを作るため、画像公開やFiles APIへの別アップロードは不要です。
5. **費用**: 非推論モデル`gpt-4.1-mini`、1画像・1リクエスト、出力上限800 tokens、SDK再試行0回、画像縮小、永続的な呼び出し回数制限を採用します。会話履歴は送りません。
6. **Docker構成**: Python MCP SDKのStreamable HTTP、コンテナ内`0.0.0.0:8000`固定、`/mcp`。ホスト側の公開ポートは変更できます。画像共有は読み取り専用、使用量だけ別volumeへ保存します。Host検証とBearer認証を有効にし、公開先は初期状態でループバックに限定します。

確認したStrata commit: `82f46a8c8f475f001ad76d92f58f4a4f8ffb0253`。

参照:

- [Strata画像添付の実装](https://github.com/Niko1221/Strata/blob/82f46a8c8f475f001ad76d92f58f4a4f8ffb0253/serve/web/app.js#L892)
- [Strata MCPクライアント](https://github.com/Niko1221/Strata/blob/82f46a8c8f475f001ad76d92f58f4a4f8ffb0253/serve/mcp.py#L438)
- [StrataのMCP接続手順](https://github.com/Niko1221/Strata/blob/82f46a8c8f475f001ad76d92f58f4a4f8ffb0253/docs/DETAILS.md#tools-from-mcp-servers)
- [MCPツール仕様](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)
- [OpenAI画像入力・detail仕様](https://developers.openai.com/api/docs/guides/images-vision)
- [GPT-4.1 mini仕様・料金](https://developers.openai.com/api/docs/models/gpt-4.1-mini)

### 入力方式の比較

| 方法 | 判断 |
|---|---|
| NAS専用共有の相対ファイル名 | 採用。短いJSON引数で済み、NASで読み取り範囲を制限できる |
| Windowsのローカル絶対パス | NAS上のコンテナからは直接読めない |
| 公開画像URL | APIは対応するが、初版では画像の外部公開を必要としない構成にする |
| LAN内画像URL | OpenAIから到達できない。NASでの取得処理・SSRF対策が別途必要 |
| base64をMCP引数に含める | プログラムからなら可能。Strataのコンテキストとログを画像データで膨らませるため初版では非対応 |
| ImageContent | 添付→ツール引数の橋渡しにはクライアント側実装が必要。初版では非対応 |
| アップロードHTTP endpoint | SMBが使えない場合の将来案。認証・保存期限・容量管理が必要 |
| クリップボード | 付属PowerShellで専用共有へPNG保存し、その名前をStrataへ渡す |

## NASでの起動

1. NAS上でこのリポジトリを配置します。以下のコマンドはNASのSSHシェル、リポジトリのディレクトリで実行します。UGREENのDockerプロジェクト機能でも同じComposeと`.env`を指定できます。
2. 画像専用の共有フォルダーを作成します。例: NAS実パス`/volume1/vision-images`、Windows共有名`\\192.168.1.20\vision-images`。実際のNASパス・IPへ置き換えてください。**Macから見える`/Volumes/...`やWindowsパスをNASのbind mount元に指定しません。**
3. `.env`を設定します。初期ファイルは空のキーで用意済みです。新規cloneでは`cp .env.example .env`で作成します。既存の`.env`を上書きしないでください。

```dotenv
OPENAI_API_KEY=自分のOpenAI_APIキー
OPENAI_MODEL=gpt-4.1-mini
VISION_MCP_TOKEN=別途生成した32文字以上のランダム文字列
MCP_BIND_IP=192.168.1.20
SERVER_IP=192.168.1.20
MCP_PORT=8001
MCP_ALLOWED_HOSTS=localhost:*,127.0.0.1:*
VISION_IMAGE_DIR=/volume1/vision-images
VISION_MAX_OUTPUT_TOKENS=800
VISION_MAX_CALLS_PER_DAY=20
VISION_MAX_CALLS_PER_10_MIN=3
```

`MCP_BIND_IP`はNASホストの公開先インターフェース、`SERVER_IP`はHost検証に追加するNASのIPです。両方設定してください。Composeで使う`.env`の`MCP_PORT`はホスト側の公開ポートだけに反映し、コンテナ内は8000番固定です。上の設定例では`NAS-IP:8001`からコンテナの8000番へ転送します。`MCP_PORT`未指定時のホスト側ポートは8102です。NAS名で接続する場合は`MCP_ALLOWED_HOSTS`へ`nas-name:8001`等、ホスト側のポートを含めて追加します。allowed hostsは接続元IP制限ではありません。

認証トークンの生成例（Pythonのある端末で実行）:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

OpenAIキーはNASだけに置き、Strataには**別の**`VISION_MCP_TOKEN`を設定します。NASの共有ACLでWindows利用者に書き込み、コンテナUID/GID `10001:10001`に読み取りとディレクトリ走査を許可します。NASの全データ領域をマウントせず、専用フォルダーだけを使用してください。

共有フォルダーの所有グループに読み取り・ディレクトリ走査が許可されている場合は、`.env`の`VISION_IMAGE_GID`にその数値GIDを設定する方法も使えます。例えばフォルダーと画像が`gid=10 mode=770`なら`VISION_IMAGE_GID=10`です。Composeの`group_add`で補助グループを追加し、コンテナの実行UIDは10001、画像マウントは読み取り専用を維持します。GIDはNASごとに確認し、今後保存する画像にもそのグループの読み取り権限を付けてください。変更後は`docker compose up -d --force-recreate`でコンテナを再作成します。

```bash
docker compose up -d --build
docker compose ps
docker compose logs --tail=30 vision-mcp
```

`.env`にキー・認証トークンが空のままだと起動しません。可能なら`.env`の権限を`chmod 600 .env`にします。`.env`は自動でPythonに読み込まれず、Composeが環境変数に展開します。

既定値の`MCP_BIND_IP=127.0.0.1`ではWindowsから接続できません。LAN利用時だけNASのLAN IPへ変更します。NASのファイアウォールでも必要なLAN端末に限定し、ルーターのポート転送は設定しません。HTTPは暗号化されないため、信頼できないネットワークを通す場合はTLSのリバースプロキシやVPNを使用してください。

## Strataへの登録

既存設定の`mcp_servers`へ`vision-mcp`を追加してStrataを再起動します。`websearch-mcp`の既存エントリはそのまま残します。

```json
{
  "mcp_servers": {
    "vision-mcp": {
      "url": "http://192.168.1.20:8001/mcp",
      "headers": {
        "Authorization": "Bearer .envのVISION_MCP_TOKENと同じ値"
      }
    }
  }
}
```

設定断片なので、既存のモデル設定JSON全体を置き換えないでください。Strataは`mcpServers`形式にも対応します。チャットの「Use tools from MCP servers」を有効にします。Strata内部ではサーバー名の正規化・接頭辞が付く場合がありますが、MCP上のツール名は`analyze_image`です。

### 画像解析

Windowsで画像を `\\192.168.1.20\vision-images\error.png` に保存し、Strataに次のように依頼します。

> vision-mcpのanalyze_imageで、image="error.png"、detail="high"、prompt="エラーダイアログの本文とコードを読み取って" を実行し、その結果から対処方法を考えて。

```python
analyze_image(
    image="error.png",  # または screenshots/error.png
    prompt="エラー本文・コード・ボタン名を読み取ってください。",
    detail="high"
) -> str
```

画像はPNG/JPEG/WebP/静止GIF、10 MiB以下・2000万画素以下です。アニメーション、シンボリックリンク、隠しパス、絶対パス、`..`、URL、base64は受け付けません。ファイルは呼び出し時点で読み取り、削除しません。差し替えを避けたい画像は一意な名前で保存してください。

### クリップボードから

Windowsで`Win+Shift+S`を押して画像をコピーし、リポジトリ内のスクリプトを実行します。

```powershell
powershell.exe -STA -File .\scripts\Save-VisionClipboard.ps1 -Share '\\192.168.1.20\vision-images'
```

PNGを共有へ保存し、Strataに渡すファイル名を表示します。スクリプトはOpenAIへ送信せず、クリップボードも変更しません。実際のAPI送信はその後の`analyze_image`呼び出しで行います。Windows PowerShell 5.1用で、組織のスクリプト実行ポリシーに従ってください。

## コストと出力

| 設定 | 既定値・動作 |
|---|---|
| `OPENAI_MODEL` | `gpt-4.1-mini`。Responses APIで画像入力可能なモデルへ変更可能 |
| `VISION_MAX_OUTPUT_TOKENS` | 800。設定範囲128〜2000。大量OCRは途中で切れる場合がある |
| `detail=high` | 既定。送信前に長辺2048px以下に縮小。文字/UI向け |
| `detail=low` | 送信前に長辺512px以下に縮小。写真の概要向け。小さい文字には不向き |
| `detail=auto` | 長辺2048px以下に縮小後、APIへautoとして送信 |
| `original` | 初版では非対応。既定モデルも非対応。原寸送信を意味するhighへの読み替えはしない |
| APIタイムアウト | HTTP timeout 45秒、呼び出し全体50秒。Strata既定60秒以内を目安 |
| 再試行 | SDKでは0回。1回1画像。同時実行は1件、混雑時は拒否 |
| `VISION_MAX_CALLS_PER_10_MIN` | 3回。直近600秒 |
| `VISION_MAX_CALLS_PER_DAY` | 20回。UTC 00:00（日本時間09:00）に日次集計が切り替わる |

失敗・タイムアウト・キャンセル時も予約した呼び出し回数を残します。画像検証で失敗した場合は予約前なので消費しません。回数はSQLiteに原子的に記録し、コンテナ再起動でも残ります。使用量DBにアクセスできない場合はAPIを呼びません。volumeを削除すると履歴も消えるため、通常運用では`docker compose down -v`を使わないでください。

`gpt-4.1-mini`の通常料金は入力$0.40/100万tokens、出力$1.60/100万tokens（調査時点）。出力800 tokensなら出力部分は最大$0.00128で、画像・依頼・指示の入力料金が別途加算されます。料金・モデル仕様は変更されるため公式ページを確認してください。呼び出し回数の上限は金額ベースの予算保証ではありません。

公式の現行画像仕様では、`gpt-4.1-mini`のlow/high/autoは同じ画像サイズ制約です。**detail=lowだけで必ず安くなるとは扱わず、初版では実際の画像を512pxに縮小します。** 高解像度のコード・細かいグラフは必要部分を切り出して渡すと、読み取り品質と費用を両立しやすくなります。highでも2048pxを超える画像は縮小されます。

モデルを変更しても推論パラメーターは自動設定しません。既定の非推論モデルを前提に簡潔な観察に限定しています。推論モデルへ変更した場合、推論tokensが出力予算を使い、本文なしで上限に達することがあります。必要ならモデル別設定を拡張してください。

出力は見える事実を中心に、通常12項目以内を指示します。画像内の命令はデータとして扱い、読めない部分は不明とします。指示による抑制であり、誤読や推論の混入を完全に防ぐ保証はありません。出力途中の場合はその旨を返し、自動で再解析しません。

## データと秘密情報

OpenAIへ送るのは指定画像の再エンコード結果、`prompt`、固定指示です。画像名・NASパス・Strataの会話履歴は送りません。ただし**画像やprompt内の個人情報は外部へ送信されます**。専用共有へ置く前に必要部分だけ切り出し、秘密情報を隠してください。解析結果はStrataの会話に残る可能性があります。

Responses APIには`store=false`を指定します。これはレスポンス保存を無効化する指定であり、API全体の保持をゼロにする保証ではありません。[OpenAIデータ管理](https://developers.openai.com/api/docs/guides/your-data)を参照してください。

- APIキー・`.env`・実画像・使用量DBはGitに含めません。Docker build contextにも含めません。
- SDKエラー本文は外へ返さず、秘密・入力本文・画像・パスをログに出しません。成功時の入力/出力token数だけを記録します。
- `OPENAI_BASE_URL`やプロキシ環境変数を使わず、公式`https://api.openai.com/v1`に接続します。
- HTTP MCPは専用Bearer tokenを必須にします。誰でもキーなしでAPIを使える状態にはしません。allowed hostsはDNS rebinding対策であり、認証やファイアウォールの代わりではありません。
- コンテナは非root、ルートFS読み取り専用、追加capabilityなし。入力フォルダー内の通常ファイルだけを読み、シンボリックリンクを各階層で拒否します。

## 確認・開発

Python 3.12以上、Linux/macOS向けです。NASでDocker実行することを想定し、Windowsネイティブでサーバーを起動する構成は対象外です。

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
```

接続確認は環境変数`VISION_MCP_TOKEN`を設定したシェルで:

```bash
.venv/bin/python check_mcp.py http://192.168.1.20:8001/mcp
```

この確認はinitializeとtools/listだけで、OpenAIを呼びません。実画像で1回だけ動作確認する場合（外部送信・課金あり）:

```bash
.venv/bin/python check_mcp.py http://192.168.1.20:8001/mcp --image error.png --detail high
```

ローカル開発でHTTPを起動する場合、`.env`の値をシェル環境に設定し、`VISION_IMAGE_ROOT`と`VISION_USAGE_DB`は存在するローカル保存先へ設定してから`python server.py --http`を実行します。`--http`なしはstdioです。Composeが使う`VISION_IMAGE_DIR`はbind mount元、Pythonが使う`VISION_IMAGE_ROOT`はコンテナ内の参照先という違いがあります。

公開Strataクライアントとの任意の互換性テスト:

```bash
STRATA_SOURCE=/path/to/Strata .venv/bin/python -m unittest discover -s tests -v
```

このテストはローカルTCP待ち受けを使い、Strataの`serve/mcp.py`からinitialize・tools/list・tools/callを実行します。OpenAI通信は実SDK + MockTransportに置き換えます。`STRATA_SOURCE`なしではこの1件はskipします。

### 検証状況

- 画像の形式・サイズ・パス・シンボリックリンク制限、APIのリクエスト形状、再試行なし、エラーの秘密情報除去、途中出力、SQLite同時予約、HTTP認証・Host/Origin・本文サイズ制限をテスト。
- 上記commitのStrata HTTPクライアントで実通信互換性を検証。
- 2026-10-07、利用者のUGREEN NASでDockerビルド・起動、Macからの認証付きMCP接続、共有画像の読み取り、OpenAI APIによる実画像解析の成功を確認。
- Windows AMD環境のStrata Web UIから`analyze_image`を呼び出し、解析テキストを利用した回答まで利用者が動作確認済み。
- クリップボード保存用PowerShellスクリプトのWindows実機実行は未検証。

### フォルダー構成と役割

外側の **`vision-mcp`（ハイフン）** は、Docker設定なども含むプロジェクト全体のフォルダーです。内側の **`vision_mcp`（アンダースコア）** は、画像解析のコア処理をまとめたPythonパッケージです。同じサーバーを二重に配置しているわけではありません。

```text
docker/vision-mcp/                  ← プロジェクト全体（NASでの配置例）
├─ docker-compose.yaml             ← コンテナの起動・ポート・共有フォルダー設定
├─ Dockerfile                      ← コンテナイメージのビルド定義
├─ .env                            ← APIキーなどの運用設定（Git管理外）
├─ .env.example                    ← 設定のひな形
├─ .dockerignore                   ← ビルドに含めるファイルの制御
├─ requirements.txt                ← Pythonライブラリの依存関係
├─ server.py                       ← サーバーの入口：起動・認証・MCPツール登録
├─ vision_mcp/                     ← 画像解析のコア処理
│  ├─ __init__.py                  ← Pythonパッケージの定義
│  ├─ config.py                    ← 環境変数から設定を読み込む
│  ├─ images.py                    ← 画像の読み込み・検証・縮小
│  ├─ vision.py                    ← OpenAIへの解析依頼・結果の取り出し
│  └─ budget.py                    ← API呼び出し回数の制限
├─ check_mcp.py                    ← MCP接続の確認
├─ scripts/
│  └─ Save-VisionClipboard.ps1      ← Windowsのクリップボード画像を共有へ保存
└─ tests/                          ← 自動テスト
```

`server.py`がMCPの窓口を担当し、`vision_mcp/`内の処理を呼び出します。PythonコードとDockerfileがこのパッケージ名を参照するため、内側のフォルダー名は`vision_mcp`のまま使用してください。解析対象の画像を保存するNAS共有（例：`/volume1/files/vision-images`）は、このプログラム用フォルダーとは別です。

将来ツールを増やす場合は、例えば次のようにパッケージ内に`tools/`を追加できます。**以下は将来の構成例で、現在は未実装です。**

```text
vision_mcp/
├─ config.py / images.py / vision.py / budget.py  ← 共通処理
└─ tools/
   ├─ __init__.py
   ├─ read_text.py                 ← 文字起こし用の処理
   ├─ analyze_ui.py                ← UI解析用の処理
   └─ compare_images.py            ← 複数画像の比較用の処理
```

共通の画像入力・API通信・回数制限を再利用し、目的別の指示や処理を追加する構成です。ツールを増やしても、DockerコンテナとStrataに登録するMCPサーバーは`vision-mcp`の1つのままで、そのサーバーが提供するツールが増えます。実際に追加するときは、`server.py`でのツール登録に加え、現在トップ階層のPythonファイルだけを許可している`.dockerignore`も更新して、`tools/`配下をビルドに含めます。

### 拡張する場所

- `server.py`: ツール登録・HTTP transport・Bearer認証
- `vision_mcp/images.py`: 安全な画像入力、形式検証・正規化
- `vision_mcp/vision.py`: OpenAIへの1回の呼び出し・観察用指示
- `vision_mcp/config.py`: 環境設定
- `vision_mcp/budget.py`: 永続的な回数制限

`read_text` / `analyze_ui` / `describe_chart`は共通の入力・API処理を再利用し、観察指示を分離して追加できます。`compare_images`は複数画像の合計サイズ・費用制限と入力スキーマを追加する必要があります。
