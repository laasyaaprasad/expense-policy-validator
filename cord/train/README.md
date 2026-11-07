# CORD training split: 400 more real receipts

The first 400 receipts of CORD v2's training split ([naver-clova-ix/cord-v2](https://huggingface.co/datasets/naver-clova-ix/cord-v2), CC-BY-4.0), first used by experiment 8 as a stream of fresh receipts. The datasets-server API refuses this split (its Parquet files hold the images, over the API's scan limit), so only the `ground_truth` text column was read from the first two Parquet files by HTTP range requests: 0.75 MB, no images. The rows were then converted as the other splits, with IDs `CORD-R000` to `CORD-R399` (`R` for train, since test already uses `T`).

1,212 lines. `labels.jsonl` was written by the assistant from the line text and each receipt's other lines, following the pilot's labelling guide (`cord/README.md`), and committed before any model or embedding saw these receipts. It is not yet checked by hand.
- **20 lines are restricted, on 10 receipts:** two beers or wines, two makgeolli, shisha, a phone charger, baby toiletries, clothing and kitchenware.
- **113 labels are marked unsure.**
