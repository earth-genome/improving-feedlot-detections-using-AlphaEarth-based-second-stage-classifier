# Germany Facility Detection — Second-Stage Classifier

A second-stage classifier that cleans up a set of prior cattle facility detections for Germany. The priors are candidate locations with significant false positives mixed in. This repo trains an MLP on AlphaEarth satellite embeddings, runs inference over the whole prior set, and post-processes the raw predictions into per-facility footprints and locator points. The original priors were trained using a resolution of 320m x 320m, whereas the AlphaEarth predictions have a resolution of 160m x 160m. 

**tl;dr: precision goes up ~18 points and we keep 90% of the original estimated true positives while reducing footprint area by almost 43%.**

## Results

All precision numbers come from manually validating a random 5% sample at each stage.

**Priors (input):** 29,133 total, 57.72% precision on a random 5% sample (n=1,457). That works out to ~16,816 estimated TPs and ~12,317 estimated FPs.

**After the second-stage classifier:** the model retains 18,290 of the priors. Precision on the same sample jumps to 76.44% — ~13,982 estimated TPs and ~4,309 FPs. Put another way, we keep 83.1% of the original TPs and cut FPs by 65%.

**Extras:** in some priors the classifier predicts more than one facility. The extras are any prediction that isn't the top-probability one in its prior. There are 1,610 of these at prob >= 0.8 — capped at 0.8 since they're by definition not the best prediction in their prior, so they should clear a higher bar. A 5% sample of these came out at 72% precision, ~1,159 TPs and ~451 FPs.

**Adding the extras back in:** 19,900 predictions total, pooled precision 76.07% ((688+56)/(688+56+212+22)) — ~15,138 estimated TPs and ~4,762 FPs. So for a 0.4 point hit to precision we recover a facility count equal to 90% of the original estimated TPs, and FPs are down 61.3% from the priors.

| Stage | Predictions | Precision | Est. TPs | Est. FPs |
|---|---|---|---|---|
| Priors | 29,133 | 57.72% | ~16,816 | ~12,317 |
| Classifier | 18,290 | 76.44% | ~13,982 | ~4,309 |
| Classifier + extras (>= 0.8) | 19,900 | 76.07% | ~15,138 | ~4,762 |

Held-out test metrics for the classifier itself are in `model/metrics.json`: F1 0.726, precision 0.717, recall 0.736 at threshold 0.45.

## Workflow

The notebooks in `src/` are numbered in the order they run:

1. **Pull AlphaEarth rasters** for the areas of interest with `0_gee_data_pull.py`.
2. **Featurize** the rasters into "expert" patch embeddings with `1_expert_embeddings.ipynb`.
3. **Train** the MLP classifier on labeled positives/negatives with `2_modeling.ipynb`.
4. **Run inference** over all prior locations with `3_inference.ipynb`.
5. **Validate and pick thresholds** with `4_ap_threshold_analysis.ipynb`.
6. **Post-process** raw prediction cells into clean facility polygons with `5_post_process_pipeline.ipynb`.

## Repo layout

### `src/` — pipeline code

- `0_gee_data_pull.py` — CLI utility that tiles an AOI geojson and exports rasters from Google Earth Engine to GeoTiffs (AlphaEarth embeddings, Sentinel-1/2, etc.). Resumable: skips tiles already on disk.
- `1_expert_embeddings.ipynb` — converts a directory of AlphaEarth embedding GeoTiffs into "expert" patch embeddings: for each patch, the mean/std/min/max of every embedding channel concatenated into one long vector (4 × 64 = 256 features). Output is a GeoPackage with one row per patch and its centroid geometry.
- `2_modeling.ipynb` — trains the classifier. Matches positive and negative labels to their nearest embedding, balances classes to 1:5 pos:neg (topping up with random negatives), builds a spatially aware train/test split (points within 500 m always land in the same partition, so nearby chips can't leak across the split), fits a StandardScaler + MLP pipeline, sweeps thresholds for best F1, then retrains on all data and saves `model/model.pkl` + `model/metrics.json`.
- `3_inference.ipynb` — runs the model over the raster embeddings for every prior. Each tile is featurized and scored inside a worker process so only rows passing the threshold cross back to the parent — that's what keeps memory flat enough to run fine strides. Writes prediction shapefiles to `inference/stride_{N}/`, then combines stride-16 output (locators) with stride-8 output (footprints).
- `4_ap_threshold_analysis.ipynb` — validation analysis on the labeled 5% sample. Part A computes Average Precision and PR curves for the prior score vs. the MLP score. Part B sweeps the locator probability threshold against the shipped post-processed output, reporting precision, estimated TPs, and F1 at each cut.
- `5_post_process_pipeline.ipynb` — turns raw overlapping prediction cells into final facility polygons: filter by `pred_prob`, extract footprints that overlap locators, dissolve, remove holes, explode to single parts, merge transitively-intersecting groups, attach the max `pred_prob` per polygon, join back to priors, and keep the best polygon per prior. Also writes the "extras" layer — surviving polygons that weren't the best in their prior (or hit no prior at all).

### `gee/` — Earth Engine + tiling library

- `gee.py` — the heavy lifting for Earth Engine work: `DataConfig` (collections, bands, cloud masking), `GEE_Data_Extractor` (composites and concurrent tile downloads), an `InferenceEngine` for embedding-model inference, and a SAM2 wrapper for mask generation. Shared by the data pull script and the notebooks.
- `tile_utils.py` — tiling helpers: `CenteredTile` (a DLTile-like tile centered on a lat/lon rather than snapped to the global grid), tile generation for arbitrary geometries, and `cut_chips` for slicing rasters into patches.

### `data/`

- `priors_germany.gpkg` — the 29,133 prior facility candidates for Germany. This is what the whole pipeline is trying to clean up.
- `embeddings/tar_embeddings.gpkg` — expert embeddings for the targeted (prior) locations. *(Git LFS)*
- `embeddings/neg_rand_embeddings.gpkg` — expert embeddings for randomly sampled negative locations.
- `labels/pos/` — positive ground-truth labels (`pos_labels_tar.gpkg` used for training, `pos_labels_orig.gpkg` the original set).
- `labels/neg/` — negative labels: `neg_labels_tar.gpkg` (targeted hard negatives) and `neg_labels_rand.gpkg` (random negatives).
- `val/` — the validation samples: `rs_val_priors_germany.gpkg` (random 5% sample of priors), `rs_val_priors_germany_scored.gpkg` (same sample with model scores attached), and `rs_val_extras.gpkg` (the 5% sample of the extras).

### `model/`

- `model.pkl` — the final trained pipeline (StandardScaler + MLPClassifier), fit on all labeled data.
- `metrics.json` — held-out test metrics and the chosen classification threshold (0.45).
- `training_predictions.geojson` — model predictions on every labeled point, tagged train/test, for eyeballing in QGIS.

### `inference/`

- `stride_16/germany/` — raw stride-16 prediction cells over all of Germany (the locator layer). *(Git LFS — large)*
- `post/germany/` — post-processed outputs:
  - `footprints.*` — fine-stride prediction polygons (building-footprint scale).
  - `locators.*` — coarse-stride locator cells.
  - `final.gpkg` — the cleaned output: best polygon per prior.
  - `extras.gpkg` — additional high-probability polygons that weren't the top prediction in their prior.
  - `final_plus_extras_0.8.gpkg` — final output with extras at prob >= 0.8 merged back in. **This is the headline result: 19,900 predictions at ~76% precision.**

### Other

- `qgis_project.qgz` — QGIS project with the main layers loaded for visual inspection.

## Notes

- Large files (`data/embeddings/tar_embeddings.gpkg`, everything under `inference/stride_16/`) are tracked with Git LFS. Run `git lfs install` before cloning if you want them pulled.
- `gee.py` initializes Earth Engine against the `embeddings-download` project — you'll need your own EE credentials to run the data pull.
- Paths inside the notebooks are relative to `src/`, except a couple of absolute paths in `2_modeling.ipynb` and `3_inference.ipynb` you'll want to point at your own copies.
