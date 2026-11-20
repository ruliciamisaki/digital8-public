# Digital8 Anime V4 — レトロPC風画像変換・アニメ塗り

PC-8801風の8色タイル、PC-9801風の16色タイル、パレットで面を塗り分けるアニメ塗りを、一つの画面で使えるローカル画像変換ツールです。

## 起動

Python 3.10以降を用意して、次を実行します。

```powershell
cd distribution/Digital8_Anime
python -m pip install -r requirements.txt
python digital8_gui.py
```

次回からは `distribution/Digital8_Anime/起動_GUI.bat` でも起動できます。

- [Digital8 Anime V4の使い方・設定](distribution/Digital8_Anime/README.md)
- [変換サンプル一覧](distribution/Digital8_Anime/サンプル.md)
- [ブラウザで使う](https://ruliciamisaki.github.io/digital8-public/)（PC-8801／PC-9801／アニメ塗り）
- [Photoshop版の使い方](photoshop/README.md)（UXPパネル＋Python不要のC++エンジン、メイン画面プレビュー対応）

## 使い方

1. 「選択…」で入力画像を開くか、画面に画像をドラッグ＆ドロップします。
2. PC-8801、PC-9801、アニメ塗りから標準プリセットを選び、パレットや線の設定を調整します。
3. 「プレビュー更新」で確認します。「プレビューを自動更新する」をオンにすると、設定変更が同じ窓に反映されます。
4. 出力形式と保存先を選び、「変換・保存」を押します。PSDではタイル模様を保ったレイヤーに加え、「使用色ごと」「塗りごと」のグループを追加できます。

Python版の起動・編集は `distribution/Digital8_Anime/` を使います。旧版のソースは過去のコミットから確認できます。

## 機能

| 機種 | パレット・塗り方 |
| --- | --- |
| PC-8801 | デジタル8色／Dinsor、V4 タイルパターン式 |
| PC-9801 | 16色パレット、V4 タイルパターン式 |
| アニメ塗り | カラーTE／TE肌+／CW、各彩度上げ版、Cel120、編集可能なCustom。使用色数を指定してベタ塗り |

共通の色補正、仮想ピクセル、主線・輪郭抽出、0.25刻みの線の太さ、色トレス、透明度の保持・二値化に対応しています。プレビューは手動・自動更新に対応し、同じ窓で100%表示や倍率変更ができ、PNG・塗り色／パターン別のレイヤーPSD・透明PNGのZIPで出力できます。

PC-8801・PC-9801では、交互7:1と、個別に有効化できる3色・4色・5色のタイルが使えます。4色は4×2、5色は4×4です。追加の3色・4色・5色は既定で無効です。塗り色は全画面共通の色区分で決めます。ブラウザ版でも「面の塗り分け」の色差・半径・小さい面の整理を全モードで調整できます。

「2倍拡大・1/2縮小」でLANCZOS補間によるなめらかな仕上げも選べます。640px調整がOFFなら2倍で変換して縮小し、ONなら640px化・変換の後に拡大と縮小を行います。補間で中間色が生まれるため、この設定がONの出力は指定色数を超えることがあります。

アニメ塗り標準は色差128・24色です。以下のサンプルでは、比較用にカラーTE 彩度上げと線の太さ0.5を指定しています。

アニメ塗りの面処理はNumbaで高速化しています。初回にはコンパイル時間が加わり、次回以降はキャッシュを再利用します。Numba未導入時は同じ処理のPython実装で動作します。

アニメ塗りは全画面共通の色・明暗の区分を先に作り、その基準で塗り分けてから小さな斑点を整理します。通常／2倍処理でも元サイズから同じ色基準を作り、平滑化と整理面積を換算して影の配置差を抑えます。線を太くする挙動はこれまでどおりです。

PNG出力には変換設定とパレットのRGBを埋め込みます。アプリへドロップすると設定を復元し、同じフォルダーに元画像があればSHA-256を確認して入力も元画像へ戻します。

全機種でパレット編集に対応します。PC-8801は8色、PC-9801は16色のRGBを編集し、機種別のCustomとして使用できます。JSONへの保存・読み込みと、PNGからの復元も可能です。

## PC-8801 / PC-9801の変換例

入力・デジタル8色・16色パレットの掲載済みサンプルです。画像をクリックすると640×400pxのPNGを開けます。

| 変換元 | PC-8801出力例 | PC-9801出力例 |
| --- | --- | --- |
| <a href="distribution/Digital8_Anime/output_sample/retro/変換元/PC88-98_0000_00005-3273937094.png"><img src="distribution/Digital8_Anime/output_sample/retro/変換元/PC88-98_0000_00005-3273937094.png" width="250" alt="変換元 0000"></a> | <a href="distribution/Digital8_Anime/output_sample/retro/PC88/PC88-98_0000_00005-3273937094_digital8.png"><img src="distribution/Digital8_Anime/output_sample/retro/PC88/PC88-98_0000_00005-3273937094_digital8.png" width="250" alt="PC-8801出力 0000"></a> | <a href="distribution/Digital8_Anime/output_sample/retro/PC98/PC88-98_0000_00005-3273937094_pc98.png"><img src="distribution/Digital8_Anime/output_sample/retro/PC98/PC88-98_0000_00005-3273937094_pc98.png" width="250" alt="PC-9801出力 0000"></a><br>01 Dovema |
| <a href="distribution/Digital8_Anime/output_sample/retro/変換元/PC88-98_0002_00008-912367020.png"><img src="distribution/Digital8_Anime/output_sample/retro/変換元/PC88-98_0002_00008-912367020.png" width="250" alt="変換元 0002"></a> | <a href="distribution/Digital8_Anime/output_sample/retro/PC88/PC88-98_0002_00008-912367020_digital8.png"><img src="distribution/Digital8_Anime/output_sample/retro/PC88/PC88-98_0002_00008-912367020_digital8.png" width="250" alt="PC-8801出力 0002"></a> | <a href="distribution/Digital8_Anime/output_sample/retro/PC98/PC88-98_0002_00008-912367020_pc98.png"><img src="distribution/Digital8_Anime/output_sample/retro/PC98/PC88-98_0002_00008-912367020_pc98.png" width="250" alt="PC-9801出力 0002"></a><br>01 Dovema |
| <a href="distribution/Digital8_Anime/output_sample/retro/変換元/PC88-98_0010_00024-3602626460.png"><img src="distribution/Digital8_Anime/output_sample/retro/変換元/PC88-98_0010_00024-3602626460.png" width="250" alt="変換元 0010"></a> | <a href="distribution/Digital8_Anime/output_sample/retro/PC88/PC88-98_0010_00024-3602626460_digital8.png"><img src="distribution/Digital8_Anime/output_sample/retro/PC88/PC88-98_0010_00024-3602626460_digital8.png" width="250" alt="PC-8801出力 0010"></a> | <a href="distribution/Digital8_Anime/output_sample/retro/PC98/PC88-98_0010_00024-3602626460_pc98.png"><img src="distribution/Digital8_Anime/output_sample/retro/PC98/PC88-98_0010_00024-3602626460_pc98.png" width="250" alt="PC-9801出力 0010"></a><br>05 Dukena |
| <a href="distribution/Digital8_Anime/output_sample/retro/変換元/PC88-98_0014_00028-1267548753.png"><img src="distribution/Digital8_Anime/output_sample/retro/変換元/PC88-98_0014_00028-1267548753.png" width="250" alt="変換元 0014"></a> | <a href="distribution/Digital8_Anime/output_sample/retro/PC88/PC88-98_0014_00028-1267548753_digital8.png"><img src="distribution/Digital8_Anime/output_sample/retro/PC88/PC88-98_0014_00028-1267548753_digital8.png" width="250" alt="PC-8801出力 0014"></a> | <a href="distribution/Digital8_Anime/output_sample/retro/PC98/PC88-98_0014_00028-1267548753_pc98.png"><img src="distribution/Digital8_Anime/output_sample/retro/PC98/PC88-98_0014_00028-1267548753_pc98.png" width="250" alt="PC-9801出力 0014"></a><br>05 Dukena |
| <a href="distribution/Digital8_Anime/output_sample/retro/変換元/PC88-98_0016_00029-3559016998.png"><img src="distribution/Digital8_Anime/output_sample/retro/変換元/PC88-98_0016_00029-3559016998.png" width="250" alt="変換元 0016"></a> | <a href="distribution/Digital8_Anime/output_sample/retro/PC88/PC88-98_0016_00029-3559016998_digital8.png"><img src="distribution/Digital8_Anime/output_sample/retro/PC88/PC88-98_0016_00029-3559016998_digital8.png" width="250" alt="PC-8801出力 0016"></a> | <a href="distribution/Digital8_Anime/output_sample/retro/PC98/PC88-98_0016_00029-3559016998_pc98.png"><img src="distribution/Digital8_Anime/output_sample/retro/PC98/PC88-98_0016_00029-3559016998_pc98.png" width="250" alt="PC-9801出力 0016"></a><br>09 Silven |
| <a href="distribution/Digital8_Anime/output_sample/retro/変換元/PC88-98_0018_00033-3559017002.png"><img src="distribution/Digital8_Anime/output_sample/retro/変換元/PC88-98_0018_00033-3559017002.png" width="250" alt="変換元 0018"></a> | <a href="distribution/Digital8_Anime/output_sample/retro/PC88/PC88-98_0018_00033-3559017002_digital8.png"><img src="distribution/Digital8_Anime/output_sample/retro/PC88/PC88-98_0018_00033-3559017002_digital8.png" width="250" alt="PC-8801出力 0018"></a> | <a href="distribution/Digital8_Anime/output_sample/retro/PC98/PC88-98_0018_00033-3559017002_pc98.png"><img src="distribution/Digital8_Anime/output_sample/retro/PC98/PC88-98_0018_00033-3559017002_pc98.png" width="250" alt="PC-9801出力 0018"></a><br>11 Camuri |
| <a href="distribution/Digital8_Anime/output_sample/retro/変換元/PC88-98_0020_00036-3559017005.png"><img src="distribution/Digital8_Anime/output_sample/retro/変換元/PC88-98_0020_00036-3559017005.png" width="250" alt="変換元 0020"></a> | <a href="distribution/Digital8_Anime/output_sample/retro/PC88/PC88-98_0020_00036-3559017005_digital8.png"><img src="distribution/Digital8_Anime/output_sample/retro/PC88/PC88-98_0020_00036-3559017005_digital8.png" width="250" alt="PC-8801出力 0020"></a> | <a href="distribution/Digital8_Anime/output_sample/retro/PC98/PC88-98_0020_00036-3559017005_pc98.png"><img src="distribution/Digital8_Anime/output_sample/retro/PC98/PC88-98_0020_00036-3559017005_pc98.png" width="250" alt="PC-9801出力 0020"></a><br>12 Camura |

## PC-8801: Dinsorパレットとの比較

同じ変換元のデジタル8色版とDinsor版です。

| 変換元 | デジタル8色 | Dinsor |
| --- | --- | --- |
| <a href="distribution/Digital8_Anime/output_sample/retro/変換元/PC88-98_0010_00024-3602626460.png"><img src="distribution/Digital8_Anime/output_sample/retro/変換元/PC88-98_0010_00024-3602626460.png" width="250" alt="変換元 0010"></a> | <a href="distribution/Digital8_Anime/output_sample/retro/PC88/PC88-98_0010_00024-3602626460_digital8.png"><img src="distribution/Digital8_Anime/output_sample/retro/PC88/PC88-98_0010_00024-3602626460_digital8.png" width="250" alt="デジタル8色版 0010"></a> | <a href="distribution/Digital8_Anime/output_sample/retro/PC88/PC88-98_0010_00024-3602626460_dinosor8.png"><img src="distribution/Digital8_Anime/output_sample/retro/PC88/PC88-98_0010_00024-3602626460_dinosor8.png" width="250" alt="Dinsor版 0010"></a> |

## アニメ塗り：色差128 / 64

アニメ塗り専用の入力画像7枚を原寸で変換しています。画像をクリックすると原寸PNGを開けます。

共通設定は **カラーTE 彩度上げ（`te_saturated`）・使用色数24・線の太さ0.5・色トレスなし** です。主線しきい値58、輪郭抽出1.00、仮想ピクセル1、周囲をならす半径3、小さい面の整理12。縦長ドットと640px調整はOFF、色補正はすべて1.00です。列間では「面をまとめる色差」だけを128／64へ変えています。

| 入力画像 | アニメ塗り・色差128 | アニメ塗り・色差64 |
| --- | --- | --- |
| <a href="distribution/Digital8_Anime/output_sample/anime/source/00505-1900052826.png"><img src="distribution/Digital8_Anime/output_sample/anime/source/00505-1900052826.png" width="250" alt="アニメ塗り入力1 00505-1900052826"></a> | <a href="distribution/Digital8_Anime/output_sample/anime/tol128/00505-1900052826.png"><img src="distribution/Digital8_Anime/output_sample/anime/tol128/00505-1900052826.png" width="250" alt="アニメ塗り 色差128 1"></a> | <a href="distribution/Digital8_Anime/output_sample/anime/tol64/00505-1900052826.png"><img src="distribution/Digital8_Anime/output_sample/anime/tol64/00505-1900052826.png" width="250" alt="アニメ塗り 色差64 1"></a> |
| <a href="distribution/Digital8_Anime/output_sample/anime/source/00506-786292313.png"><img src="distribution/Digital8_Anime/output_sample/anime/source/00506-786292313.png" width="250" alt="アニメ塗り入力2 00506-786292313"></a> | <a href="distribution/Digital8_Anime/output_sample/anime/tol128/00506-786292313.png"><img src="distribution/Digital8_Anime/output_sample/anime/tol128/00506-786292313.png" width="250" alt="アニメ塗り 色差128 2"></a> | <a href="distribution/Digital8_Anime/output_sample/anime/tol64/00506-786292313.png"><img src="distribution/Digital8_Anime/output_sample/anime/tol64/00506-786292313.png" width="250" alt="アニメ塗り 色差64 2"></a> |
| <a href="distribution/Digital8_Anime/output_sample/anime/source/00008-2085017498.png"><img src="distribution/Digital8_Anime/output_sample/anime/source/00008-2085017498.png" width="250" alt="アニメ塗り入力3 00008-2085017498"></a> | <a href="distribution/Digital8_Anime/output_sample/anime/tol128/00008-2085017498.png"><img src="distribution/Digital8_Anime/output_sample/anime/tol128/00008-2085017498.png" width="250" alt="アニメ塗り 色差128 3"></a> | <a href="distribution/Digital8_Anime/output_sample/anime/tol64/00008-2085017498.png"><img src="distribution/Digital8_Anime/output_sample/anime/tol64/00008-2085017498.png" width="250" alt="アニメ塗り 色差64 3"></a> |
| <a href="distribution/Digital8_Anime/output_sample/anime/source/00035-438693616.png"><img src="distribution/Digital8_Anime/output_sample/anime/source/00035-438693616.png" width="250" alt="アニメ塗り入力4 00035-438693616"></a> | <a href="distribution/Digital8_Anime/output_sample/anime/tol128/00035-438693616.png"><img src="distribution/Digital8_Anime/output_sample/anime/tol128/00035-438693616.png" width="250" alt="アニメ塗り 色差128 4"></a> | <a href="distribution/Digital8_Anime/output_sample/anime/tol64/00035-438693616.png"><img src="distribution/Digital8_Anime/output_sample/anime/tol64/00035-438693616.png" width="250" alt="アニメ塗り 色差64 4"></a> |
| <a href="distribution/Digital8_Anime/output_sample/anime/source/00051-2140102643.png"><img src="distribution/Digital8_Anime/output_sample/anime/source/00051-2140102643.png" width="250" alt="アニメ塗り入力5 00051-2140102643"></a> | <a href="distribution/Digital8_Anime/output_sample/anime/tol128/00051-2140102643.png"><img src="distribution/Digital8_Anime/output_sample/anime/tol128/00051-2140102643.png" width="250" alt="アニメ塗り 色差128 5"></a> | <a href="distribution/Digital8_Anime/output_sample/anime/tol64/00051-2140102643.png"><img src="distribution/Digital8_Anime/output_sample/anime/tol64/00051-2140102643.png" width="250" alt="アニメ塗り 色差64 5"></a> |
| <a href="distribution/Digital8_Anime/output_sample/anime/source/00011-2623508507.png"><img src="distribution/Digital8_Anime/output_sample/anime/source/00011-2623508507.png" width="250" alt="アニメ塗り入力6 00011-2623508507"></a> | <a href="distribution/Digital8_Anime/output_sample/anime/tol128/00011-2623508507.png"><img src="distribution/Digital8_Anime/output_sample/anime/tol128/00011-2623508507.png" width="250" alt="アニメ塗り 色差128 6"></a> | <a href="distribution/Digital8_Anime/output_sample/anime/tol64/00011-2623508507.png"><img src="distribution/Digital8_Anime/output_sample/anime/tol64/00011-2623508507.png" width="250" alt="アニメ塗り 色差64 6"></a> |
| <a href="distribution/Digital8_Anime/output_sample/anime/source/00000-2610654151.png"><img src="distribution/Digital8_Anime/output_sample/anime/source/00000-2610654151.png" width="250" alt="アニメ塗り入力7 00000-2610654151"></a> | <a href="distribution/Digital8_Anime/output_sample/anime/tol128/00000-2610654151.png"><img src="distribution/Digital8_Anime/output_sample/anime/tol128/00000-2610654151.png" width="250" alt="アニメ塗り 色差128 7"></a> | <a href="distribution/Digital8_Anime/output_sample/anime/tol64/00000-2610654151.png"><img src="distribution/Digital8_Anime/output_sample/anime/tol64/00000-2610654151.png" width="250" alt="アニメ塗り 色差64 7"></a> |

## アニメ塗り：色トレスなし / あり

上の表の先頭画像を使った比較です。色差128の「色トレスなし」は上と同じPNGを再掲しています。他の設定はそのままで、右列だけ色トレスをON（明度0.55）にしています。

| 入力画像 | 色トレスなし・色差128 | 色トレスあり・色差128 |
| --- | --- | --- |
| <a href="distribution/Digital8_Anime/output_sample/anime/source/00505-1900052826.png"><img src="distribution/Digital8_Anime/output_sample/anime/source/00505-1900052826.png" width="250" alt="色トレス比較の入力"></a> | <a href="distribution/Digital8_Anime/output_sample/anime/tol128/00505-1900052826.png"><img src="distribution/Digital8_Anime/output_sample/anime/tol128/00505-1900052826.png" width="250" alt="色トレスなし 色差128"></a> | <a href="distribution/Digital8_Anime/output_sample/anime/trace/00505-1900052826_tol128_trace.png"><img src="distribution/Digital8_Anime/output_sample/anime/trace/00505-1900052826_tol128_trace.png" width="250" alt="色トレスあり 色差128"></a> |

## ライセンス

Digital8のソフトウェアは [MITライセンス](LICENSE) です。変換サンプルの画像はMITライセンスの対象外です。
