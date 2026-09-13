# CL Load Text File (Drag & Drop)

ボタン又はWindows Explorer等からのD&Dで、任意のローカル場所にあるUTF-8テキストをSTRINGへ渡します。`ComfyUI/input`へファイルをコピーせず、ブラウザが読んだ内容をワークフローJSONへ埋め込みます。

- UTF-8及びUTF-8 BOM対応
- 改行をLFへ正規化
- 最大16 MiB
- NUL、非UTF-8及び破損データはエラー
- 元ファイルを編集した場合は再度選択又はD&Dが必要
- ワークフローへ本文を保存するため、機密テキストを含むワークフローを共有しない

`text`をVocalの`lyrics_text`、Japanese to JSONの`plain_text`又は任意のSTRING入力へ接続できます。詳細は[Text File仕様](../spec/cl_text_file_spec.md)を参照してください。
