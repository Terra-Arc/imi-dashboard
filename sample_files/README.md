# Sample IMI outputs

Two small sets of real [IMI](https://imi.seas.harvard.edu/) outputs: one
single-period inversion and a short timeseries. They are the converter's test
fixtures. The tests assert exact
values from them, so do not edit them or add, remove or renumber periods
without updating the tests.

```
sample_files/
├── single_period/          one inversion: netCDF/ plus the statistics CSVs
└── timeseries/
    ├── period_1_plots/     one folder per period, laid out like single_period/
    ├── period_2_plots/
    └── period_4_plots/     (period 3 absent on purpose)
```

## `single_period/`

The IMI team's Permian Basin quick-start run, IMI 2.3.1. Its configuration,
`config_Permian_1mon_2.3.1.yml`, is included with the cluster paths replaced
by `/path/to/…`.

- Standard (not Kalman) inversion for May 2021, blended TROPOMI observations,
  GEOS-Chem 0.25° × 0.3125° grid.
- Domain 23 × 21 cells; 96 state-vector elements: 63 in the region of interest
  (1 cell each) and 33 buffer elements in three rings (2–9 cells each;
  `BufferRings: 3`, `BufferReductionFactor: 4`). Cropped to the region of
  interest: 9 × 7 cells.
- An 18-member ensemble (varying prior error, observation error, γ and
  boundary-condition error), so the CSV has ensemble columns.

| File | Read by the converter |
|---|---|
| `netCDF/posterior.nc`, `netCDF/prior.nc` | yes (required): emissions by sector |
| `netCDF/scale.nc` | yes: scale-factor map |
| `netCDF/avkern.nc` | yes: averaging-kernel map |
| `netCDF/obs_ds_regridded.nc` | yes: observation-count, XCH4 and ΔXCH4 maps |
| `netCDF/state_vector.nc` | yes: domain and buffer / region-of-interest split |
| `viz_notebook_statistics.csv` | yes: dates, sector totals with ensemble range, scalar diagnostics |
| `ensemble_statistics.csv`, `netCDF/obs_ds.nc`, `netCDF/super_observations_GC.nc`, `config_Permian_1mon_2.3.1.yml` | no |
| `posterior-prior_(regridded).png` | no; kept so the tests cover non-input files in a period folder |

## `timeseries/`

> **Not valid emission estimates.** This run's results are known to be wrong.
> They serve only to test the converter and demonstrate a timeseries
> dashboard. Do not use them as estimates of Romania's methane emissions.

Periods 1, 2 and 4 (January, February, April 2023) of a monthly Romania run
by Terra Arc S.M.P.C. with an older IMI development version, in the
Kalman-mode folder layout.

- GEOS-Chem 0.25° × 0.3125° grid; domain 60 × 63 cells; 359 state-vector
  elements: 351 in the region of interest (1 cell each) and 8 buffer clusters
  of 233–545 cells (older IMI's `nBufferClusters`). Cropped: 18 × 29 cells.
- Differences from IMI 2.3.1: the satellite variables in
  `obs_ds_regridded.nc` are `tropomi`, `diff_tropomi_prior` and
  `diff_tropomi_posterior` (now `satellite`); there is no ensemble, so no
  `ensemble_statistics.csv`, no ensemble columns and no uncertainty band.
- Each period holds the same files as `single_period/`, minus
  `ensemble_statistics.csv`, `super_observations_GC.nc` and the IMI config.
- `period_3_plots` is missing on purpose, to test gaps: `info` reports
  "numbered 1–4 (missing: 3)" and the charts leave a gap at March 2023.

## Attribution and licence

Reuse outside this repository should credit:

- the **Integrated Methane Inversion** (<https://imi.seas.harvard.edu/>;
  Varon et al., *Geoscientific Model Development*, 2022);
- the **satellite data**: "Contains modified Copernicus Sentinel data [year]"
  (2021 for `single_period/`, 2023 for `timeseries/`), as the Copernicus terms
  require. `single_period/` uses the blended TROPOMI+GOSAT product (Balasus et
  al., *Atmospheric Measurement Techniques*, 2023);
- the **prior inventories** in `prior.nc` and `posterior.nc`, as listed in the
  [IMI documentation](https://imi.readthedocs.io/) for the run's version, under
  their own terms (e.g. EDGAR, CC BY 4.0).

**`single_period/`** is redistributed with the permission of the IMI team
(Atmospheric Chemistry Modeling Group, Harvard University) and is not covered
by the repository's licence.

**`timeseries/`** is by Terra Arc S.M.P.C., under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/): credit Terra Arc
S.M.P.C. and the sources above. The licence permits reuse; it does not make
the numbers right.
