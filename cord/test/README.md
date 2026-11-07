# CORD test split: 100 fresh real receipts

The 100 receipts of CORD v2's test split ([naver-clova-ix/cord-v2](https://huggingface.co/datasets/naver-clova-ix/cord-v2), CC-BY-4.0), held back by the pilot (`cord/README.md`) and first used by experiment 7. Text only, converted the same way as the validation split:

```
curl -o rows.json "https://datasets-server.huggingface.co/rows?dataset=naver-clova-ix/cord-v2&config=default&split=test&offset=0&length=100"
python -I evaluation/cord_convert.py rows.json cord/test/receipts.jsonl
```

287 lines. `labels.jsonl` was written by the assistant from the line text and each receipt's other lines, following the pilot's labelling guide (`cord/README.md`), and committed before any model or embedding saw these receipts. It is not yet checked by hand. 5 lines are restricted (a blouse, makgeolli and three pieces of kitchenware, so three receipts are real violations), and 31 labels are marked unsure.
