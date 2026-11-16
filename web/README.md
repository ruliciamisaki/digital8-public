# Digital8 Anime V4 ブラウザ版

[ブラウザ版を開く](https://ruliciamisaki.github.io/digital8-public/)

PC-8801、PC-9801、アニメ塗りの変換に、[Python版と同じV4エンジン](../distribution/Digital8_Anime/README.md)を使います。画像はブラウザ内で処理します。WebAssembly版のPython、NumPy、Pillowを初回に読み込むため、最初の画像を開くときは時間がかかります。

1. 画像を選ぶか、画面にドロップします。
2. 機種とパレットを選び、設定を調整します。色見本から選んだ色を編集し、カスタムパレットをJSONで保存・読み込みできます。
3. 「プレビュー更新」で変換します。「プレビューを自動更新する」をオンにすると、変更から約0.3秒後に同じプレビュー欄を更新します。変換中に変更した場合は、処理後に最新の設定で更新します。
4. 「変換・保存」でPNG、レイヤー付きPSD、レイヤー別PNGのZIPをダウンロードします。透明マスクの同時保存も選べます。

設定付きPNGを開くと、変換設定とパレットを復元します。「元画像を選ぶ」から元画像を指定すると、ファイル名とSHA-256が一致した場合に入力を切り替えます。

ブラウザ版のアニメ塗りはNumbaによるJIT高速化が使えないため、大きな画像や2倍処理では待ち時間が長くなります。「横幅640pxへ調整」で処理量を減らせます。変換中も設定を操作でき、「中止」で処理を打ち切れます。入力は2400万画素以下です。

## ローカルで動かす

リポジトリのルートで実行し、ブラウザで `http://localhost:8000/` を開きます。

```sh
python -m pip install numpy Pillow
python -m unittest discover -s web -p 'test_*.py'
python web/build_site.py
python -m http.server 8000 --directory _site
```

`build_site.py` はPython版の変換コード・パレット・標準設定をサイトに同梱します。GitHub Pagesにも同じ手順で生成したファイルを配信します。
