# CORD training split: 400 more real receipts

The first 400 receipts of CORD v2's training split ([naver-clova-ix/cord-v2](https://huggingface.co/datasets/naver-clova-ix/cord-v2), CC-BY-4.0), first used by experiment 8 as a stream of fresh receipts. The datasets-server API refuses this split (its Parquet files hold the images, over the API's scan limit), so only the `ground_truth` text column was read from the first two Parquet files by HTTP range requests: 0.75 MB, no images. The rows were then converted as the other splits, with IDs `CORD-R000` to `CORD-R399` (`R` for train, since test already uses `T`).

1,212 lines. `labels.jsonl` was written by the assistant from the line text and each receipt's other lines, following the pilot's labelling guide (`cord/README.md`), and committed before any model or embedding saw these receipts. It is not yet checked by hand.
- **20 lines are restricted, on 10 receipts:** two beers or wines, two makgeolli, shisha, a phone charger, baby toiletries, clothing and kitchenware.
- **113 labels are marked unsure.**

**One correction.** `CORD-R256-L3`, "TAKE AWAY CHARGE", was labelled `food_beverage`; by the labelling guide (rule 4) it is `service_fee`, as on R029. Experiment 8 found the error, and experiment 9's calibration was decided by it. It was corrected before experiment 10. Experiments 8 and 9 are left as recorded, with the wrong label.
