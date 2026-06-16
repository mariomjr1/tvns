# Methodology

*7 T transcutaneous vagus-nerve-stimulation (tVNS) brainstem fMRI pipeline.*
**Last updated:** 2026-06-16 · This document is maintained alongside the code: it is
revised whenever the analysis methodology changes. Exact per-run software versions
and container digests are recorded automatically for every batch in
`codes/qc/provenance/provenance_latest.json` (see §12); the values below are the
reference configuration.

---

## 1. Participants

[TBD — N, groups (cases vs controls), age/sex distribution, inclusion/exclusion,
ethics approval and consent.] Group-level analyses contrast **cases > controls**
(directional hypothesis; see §10).

## 2. MRI acquisition

Imaging was performed on a 7 T Siemens MAGNETOM **Terra** system. During the study
the scanner console/software was upgraded (pre-2026: E12 software; 2026 onward:
Terra.X / XA60 software); this is a **console/software change on the same magnet and
gradient hardware**, not a hardware replacement, and both participant groups are
represented before and after the upgrade. Acquisition platform is therefore treated
as a balanced, date-based batch variable rather than a group confound (see §9).

Functional images were acquired with a multiband (simultaneous multi-slice, SMS)
gradient-echo EPI sequence (`ep2d_bold`): TR = 1.190 s, TE = 22 ms, 1.5 mm isotropic
voxels, 92 axial slices, SMS factor 4 [13], in-plane acceleration (GRAPPA) factor 3,
phase encoding A→P (BIDS `j-`), total readout time ≈ 0.0271 s (E12) / 0.0275 s
(XA60). The pre-upgrade protocol saved 326 volumes per task run. Three task
conditions were acquired per session: resting state, **BlockStim**, and
**ContinuousStim** (tVNS paradigms); task order varied across subjects. A T1-weighted
anatomical (multi-echo MPRAGE, `MEMP`) was acquired for registration.

On-scanner distortion correction was disabled; instead a **reverse-phase-encode EPI
pair** (`TOPUP_AP` / `TOPUP_PA`, same geometry as the BOLD) was acquired for offline
susceptibility-distortion correction (§6). On-scanner physiological recording was
disabled; physiology was recorded externally (§3).

## 3. Physiological recording

Cardiac and respiratory signals were recorded externally with an ADInstruments
LabChart system: a **piezoelectric pulse transducer** for the cardiac signal
(`RPIEZO`) and a respiratory belt (`RESP`), together with the scanner volume-trigger
TTL (`MRTRIG`) and the stimulus-marker channel (`STIMTRIG`), sampled at 1000 Hz. The
LabChart export is segmented per acquisition using `MRTRIG` and acquisition-time
pseudotime anchoring; channel content is validated (non-flat `RESP`/`RPIEZO`,
trigger presence) and the parsed-segment count is cross-checked against the BOLD
runs, with anomalies flagged and logged rather than silently dropped.

## 4. Physiological noise modeling — RETROICOR (steps 02–04)

Physiological noise was modeled with **RETROICOR** [9]: second-order Fourier
expansions of cardiac and respiratory phase. Cardiac phase was derived from
piezo R-peaks detected with **R-DECO** [10] (automatic detection with manual
correction); respiratory phase was derived from the respiratory belt. Because the
cardiac signal is piezo-derived rather than ECG, each run's piezo trace undergoes a
quality check (heart-rate plausibility, RR variability, beat-amplitude consistency)
and is reviewed per sequence; runs with unreliable piezo are processed
**respiration-only**, recorded in a per-run decision manifest. RETROICOR slice-wise
regressors and the physiologically **corrected BOLD series** (native space) are
produced before fMRIPrep (see §6 for ordering rationale).

**Ordering note (fixed design decision):** RETROICOR is applied **before** fMRIPrep
— physiological noise is removed from the native-space BOLD, and fMRIPrep then runs
on the corrected series. This order is fixed for this study.

## 5. BIDS conversion and corrected-BIDS assembly (steps 01, 05·1)

DICOMs were organized into BIDS [1] with **HeuDiConv** [2] (using **dcm2niix** [3]
for conversion). The reverse-phase-encode pair is stored as PEPOLAR fieldmaps
(`fmap/…_dir-AP_epi`, `…_dir-PA_epi`) and each fieldmap's `IntendedFor` is populated
automatically so that fMRIPrep applies distortion correction to the BOLD runs. A
parallel "corrected" BIDS tree is then assembled in which each functional BOLD is
replaced by its RETROICOR-corrected image (anatomical and fieldmap files reused,
sidecars preserved); this tree is integrity-checked (geometry/affine/volume count vs
raw, sidecar metadata — TR, SliceTiming, phase-encoding, readout — and `IntendedFor`
resolution), with issues flagged to an audit log.

## 6. fMRIPrep preprocessing (step 05)

The corrected BIDS dataset was preprocessed with **fMRIPrep 25.2.3** [4] (Singularity
container; **Nipype** workflow engine). Steps included: susceptibility-distortion
correction from the AP/PA PEPOLAR pair via SDCFlows/**TOPUP** [7]; slice-timing
correction; boundary-based co-registration of BOLD to T1w; brain extraction and
tissue segmentation; spatial normalization to **MNI152NLin2009cAsym** [11] with
**ANTs** [5]; surface reconstruction with **FreeSurfer** [6]; and computation of
confound time series (motion parameters, framewise displacement [12], DVARS,
aCompCor). Outputs were produced in native T1w and MNI space (plus CIFTI). That SDC
was actually applied is verified per run (report figure / sidecar / fieldmap link),
flagged when not confirmed.

## 7. Stimulus timing and nuisance regressors (step 06)

Stimulus onsets/durations were extracted from `STIMTRIG`, timed relative to the
first `MRTRIG` pulse (= first saved volume); volume cardinality is cross-checked
across stages (MR triggers, raw/corrected/fMRIPrep BOLD, confounds, motion, SPM) and
disagreements are flagged. Head-motion nuisance regressors (6 rigid-body parameters
plus one spike regressor per volume exceeding the framewise-displacement threshold)
were generated from the fMRIPrep confounds. Non-steady-state and aCompCor confounds
are available as optional sensitivity regressors.

## 8. First-level GLM (step 07)

First-level analysis used **SPM12** [8] in **MATLAB**. By default the GLM is fit
directly on the fMRIPrep **MNI152NLin2009cAsym** BOLD (the legacy route — modeling in
T1w space followed by an SPM unified-segmentation warp to MNI — remains available as
an optional sensitivity analysis). Stimulus regressors were convolved with the
canonical hemodynamic response function; head-motion (and, for the
RETROICOR-before-fMRIPrep configuration, the physiological) regressors were included
as covariates of no interest; a 128 s high-pass filter and an AR(1) serial-correlation
model were applied (the noise model is configurable). Spatial smoothing used a 3 mm
FWHM Gaussian kernel, with an optional brainstem-restricted smoothing/mask path for
NTS/LC/raphe analyses. The primary contrast was **Stim > baseline** (with the
reverse contrast also estimated); the contrast identity is verified by name from
`SPM.mat` before group analysis.

## 9. Group (second-level) analysis (step 08)

Group analysis used SPM12 two-sample t-tests (**cases vs controls**). For analyses
combining BlockStim and ContinuousStim, the within-subject mean contrast is used by
default (preserving independence; condition pooling is available as an option).
Age, sex, and mean framewise displacement are available as optional nuisance
covariates (SPM implicit centering, `iCC = 1`). Acquisition platform (console
version) may be added as an **optional** covariate; it is balanced across groups and
is not expected to be necessary (§2).

## 10. Statistical thresholding (step 09)

Given the directional hypothesis (cases > controls) on preliminary data, group maps
are thresholded **one-tailed (positive)**. The default is an uncorrected voxel
threshold of p < 0.05 (appropriate for a pilot); voxel-wise family-wise-error (RFT)
and false-discovery-rate correction are available as options, as is restriction to a
brainstem mask. (If a two-tailed test is selected, the threshold is correctly split
at p/2 per tail.)

## 11. ROI analysis (step 10)

Regions of interest were interrogated as spherical ROIs around peak coordinates (5
and 10 mm radii) and/or as anatomical mask/atlas ROIs (e.g. brainstem nuclei), with
per-subject single-voxel and mean values extracted to a table. Image geometry/affine
is checked per subject; a mismatch is resampled onto the reference grid and flagged
(never silently excluded), and the expected-vs-analyzed subject count is reconciled.

## 12. Quality control and reproducibility

Quality control follows a **flag-and-log** philosophy throughout: anomalies are
recorded to per-subject and cohort logs and surfaced for review, but the pipeline is
not silently halted and subjects are not silently dropped. QC artifacts include
per-step image montages, framewise-displacement summaries, the piezo cardiac-QC
review, the cross-stage cardinality audit, the SDC verification audit, the
corrected-BIDS integrity audit, and the contrast/ROI geometry checks (written under
`codes/qc/`). For reproducibility, a batch **provenance record** is captured before
analysis (`collect_provenance.py`): pipeline git commit, fMRIPrep container version
(and optional SHA-256), SPM/MATLAB versions, RETROICOR/R-DECO source hashes, and the
full Python environment (`pip freeze`), written to `codes/qc/provenance/`.

## Software and versions

Exact versions are captured per batch (§12); reference configuration:

| Tool | Role | Version |
|---|---|---|
| HeuDiConv + dcm2niix [2,3] | DICOM→BIDS conversion | recorded in provenance |
| fMRIPrep [4] | preprocessing (SDC/STC/coreg/norm/confounds) | **25.2.3** |
| FSL TOPUP (via SDCFlows) [7] | PEPOLAR distortion correction | bundled in fMRIPrep |
| ANTs [5] | spatial normalization | bundled in fMRIPrep |
| FreeSurfer [6] | surface reconstruction | bundled in fMRIPrep |
| SPM12 [8] | first/second-level GLM, thresholding | recorded in provenance |
| MATLAB | SPM/RETROICOR/R-DECO runtime | recorded in provenance |
| RETROICOR [9] | physiological noise model | in-repo (`generate_1D_fun_1.m`, `retroicor_main_modi.m`) |
| R-DECO [10] | piezo R-peak detection | in-repo (`utility/r-deco-master`) |
| Python (nibabel, numpy, scipy, matplotlib, Pillow) | utilities, QC, ROI | recorded in provenance |

## References

1. Gorgolewski KJ, et al. The Brain Imaging Data Structure (BIDS). *Sci Data* 2016;3:160044.
2. Halchenko YO, et al. HeuDiConv — flexible DICOM conversion into structured directory layouts. *J Open Source Softw* 2024.
3. Li X, et al. The first step for neuroimaging data analysis: DICOM to NIfTI conversion (dcm2niix). *J Neurosci Methods* 2016;264:47–56.
4. Esteban O, et al. fMRIPrep: a robust preprocessing pipeline for functional MRI. *Nat Methods* 2019;16:111–116.
5. Avants BB, et al. Symmetric diffeomorphic image registration (ANTs). *Med Image Anal* 2008;12:26–41.
6. Fischl B. FreeSurfer. *NeuroImage* 2012;62:774–781.
7. Andersson JLR, Skare S, Ashburner J. How to correct susceptibility distortions in spin-echo echo-planar images (TOPUP). *NeuroImage* 2003;20:870–888.
8. Penny WD, Friston KJ, Ashburner JT, Kiebel SJ, Nichols TE (eds). *Statistical Parametric Mapping: The Analysis of Functional Brain Images* (SPM12). Academic Press, 2007.
9. Glover GH, Li TQ, Ress D. Image-based method for retrospective correction of physiological motion effects in fMRI: RETROICOR. *Magn Reson Med* 2000;44:162–167.
10. Moeyersons J, et al. R-DECO: an open-source Matlab based graphical user interface for the detection and correction of R-peaks. *PeerJ Comput Sci* 2019;5:e226.
11. Fonov V, et al. Unbiased average age-appropriate atlases for pediatric studies (MNI152NLin2009cAsym). *NeuroImage* 2011;54:313–327.
12. Power JD, et al. Spurious but systematic correlations in functional connectivity MRI networks arise from subject motion (framewise displacement). *NeuroImage* 2012;59:2142–2154.
13. Moeller S, et al. Multiband multislice GE-EPI at 7 tesla, with 16-fold acceleration. *Magn Reson Med* 2010;63:1144–1153.

## Maintenance

This file is updated whenever the methodology changes (e.g. preprocessing order,
default spaces, statistical model, thresholds, or tool versions). Recent design
decisions reflected here: RETROICOR applied before fMRIPrep (fixed); first-level
default = direct fMRIPrep MNI with the SPM-warp route as optional legacy; one-tailed
(cases > controls) thresholding for preliminary data; AP/PA TOPUP used as PEPOLAR
fieldmaps for SDC; acquisition-platform (console) split treated as a balanced batch
variable with an optional covariate.
