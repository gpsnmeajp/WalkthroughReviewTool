"""公式npm配布物から、オフライン閲覧に必要なJavaScriptとライセンスを用意する。

このスクリプトは開発時に同梱ライブラリを更新するための補助ツールであり、
scan / build / mergeや生成HTMLの閲覧時には実行しない。
実行するとnpmレジストリへHTTPSで接続し、バージョンを固定した配布物を取得する。
配布元のintegrityに一致するアーカイブだけを読み、スキル内assets/ui/vendorの決めた出力を
上書きする。取得元・版・検証値はversions.jsonへ残し、後から由来を確認できる。
"""

import base64
import hashlib
import io
import json
from pathlib import Path
import tarfile
import urllib.request


# 呼び出したカレントディレクトリに左右されないよう、このスクリプト自身の位置から
# スキル内のassets/ui/vendorを決める。ユーザーのプロジェクトには書き込まない。
DESTINATION = Path(__file__).resolve().parents[2] / "skills/walkthrough-review/assets/ui/vendor"
# latestは使用しない。同じ版を明示することで、意図しない自動更新を避ける。
PACKAGES = {"prismjs": "1.30.0", "marked": "18.1.1"}


def download_package(name, version):
    """指定したnpmパッケージを検証し、アーカイブ内の通常ファイルを返す。

    入力はパッケージ名と固定バージョンの文字列。戻り値は、
    「アーカイブ内パス: ファイル内容bytes」の辞書と、取得元・版・integrityの辞書。
    メタデータとtarballを順番に取得するためネットワークへの読み取りが発生するが、
    この関数自身はローカルのファイルを作成・上書きしない。

    SHA-512がメタデータの値と一致しなければValueErrorで停止する。
    通信失敗・JSON不正・tar形式不正なども呼び出し元へそのまま伝え、
    検証できなかった内容を正常な配布物として返さない。
    """
    # 最初の応答から配布URLとintegrityを得る。timeoutは各HTTP要求の待ち時間。
    url = f"https://registry.npmjs.org/{name}/{version}"
    with urllib.request.urlopen(url, timeout=30) as response:
        metadata = json.load(response)
    with urllib.request.urlopen(metadata["dist"]["tarball"], timeout=30) as response:
        archive = response.read()
    # npmのintegrityと同じ「sha512- + Base64」の表記で比較する。
    # 展開後の個別ファイルではなく、取得した圧縮アーカイブ全体を検証する。
    expected = metadata["dist"]["integrity"]
    actual = "sha512-" + base64.b64encode(hashlib.sha512(archive).digest()).decode()
    if actual != expected:
        raise ValueError(f"{name}: 配布ファイルの検証に失敗しました")
    files = {}
    # extractallは使わず、パッケージ内のパスをファイルシステムへ展開しない。
    # 通常ファイルだけをメモリへ取り込み、リンクやディレクトリエントリは無視する。
    # 実際に配布へ採用するファイルの選択はmain側の固定パスで行う。
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as package:
        for item in package.getmembers():
            if item.isfile():
                files[item.name] = package.extractfile(item).read()
    return files, {"version": version, "source": url, "integrity": actual}


def main():
    """固定した2パッケージの配布用資産を保存し、取得履歴を書き出す。

    引数・戻り値はない。DESTINATIONを必要に応じて作成し、その中のJS・CSS・
    ライセンス・versions.jsonを上書きする。成功したパッケージ名を標準出力へ出す。
    main全体の一括ロールバックは行わないため、途中で例外が起きた場合は
    それ以前のファイルが更新済みである可能性がある。versions.jsonは全取得後に
    保存するので、更新作業では終了の成否と履歴ファイルを併せて確認する。
    """
    DESTINATION.mkdir(parents=True, exist_ok=True)
    provenance = {}
    for name, version in PACKAGES.items():
        files, provenance[name] = download_package(name, version)
        if name == "prismjs":
            # Prismはcoreと依存する言語を先に連結する。例えばTypeScriptは
            # JavaScriptの後、C++はCの後に置き、ブラウザーで順に評価できるようにする。
            components = [
                "core", "markup", "css", "clike", "javascript", "python",
                "typescript", "json", "bash", "c", "cpp", "csharp", "java",
                "go", "rust", "sql", "yaml", "markdown", "diff", "powershell",
            ]
            pieces = [b"/* Prism 1.30.0, MIT; see prism-LICENSE.txt */\n"]
            for component in components:
                pieces.append(files[f"package/components/prism-{component}.min.js"])
            # CDN参照ではなく、HTMLから相対パスで読む一つのJSへまとめる。
            # テーマとライセンスも配布物に含め、元の利用条件を失わないようにする。
            (DESTINATION / "prism.js").write_bytes(b"\n".join(pieces))
            (DESTINATION / "prism.css").write_bytes(files["package/themes/prism.css"])
            (DESTINATION / "prism-LICENSE.txt").write_bytes(files["package/LICENSE"])
            provenance[name]["components"] = components
        else:
            # markedのUMD版は通常のscriptタグで読み込める。
            # npm配布物のライセンス名に表記差があるため、既知の2候補から探す。
            (DESTINATION / "marked.js").write_bytes(files["package/lib/marked.umd.js"])
            license_file = next(
                path for path in ("package/LICENSE", "package/LICENSE.md")
                if path in files
            )
            (DESTINATION / "marked-LICENSE.txt").write_bytes(files[license_file])
        print(f"{name} {version}: 検証・保存しました")
    # 使用版・取得元・integrity・Prismの組み込み言語を、人が読めるUTF-8 JSONへ記録。
    (DESTINATION / "versions.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    # importで読み込まれただけの場合は通信・ファイル更新を実行しない。
    main()
