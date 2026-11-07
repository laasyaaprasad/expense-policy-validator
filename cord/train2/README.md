# CORD training split, receipts 400–599: 200 fresh receipts for experiment 9

Read the same way as `cord/train` (the `ground_truth` text column only, by HTTP range requests on the third Parquet file: 0.36 MB, no images). IDs `CORD-R400` to `CORD-R599`. 569 lines. `labels.jsonl` was written by the assistant from the line text and each receipt's other lines, following the pilot's labelling guide, and committed before any model or embedding saw these receipts. It is not yet checked by hand.
