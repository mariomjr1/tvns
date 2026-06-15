# Protocol-Based Active Task Re-evaluation

Date: 2026-06-10

Source protocol: [`docs/protocols/Bay1_tvns_tasks_only.pdf`](protocols/Bay1_tvns_tasks_only.pdf) (Siemens MAGNETOM Terra protocol pages for
`ContinuousStim` and `BlockStim`).

## Protocol facts that affect the pipeline

- TR: 1.190 s
- Saved measurements: 326 per task run
- Saved-run duration from volume count: 326 x 1.190 = 387.94 s
- Imaging dummy TRs: 3
- ACS dummy TRs: 3
- FLEET dummy pulses: 5
- BOLD "Ignore meas. at start": 0
- 92 slices, 1.5 mm isotropic, SMS factor 4, in-plane acceleration 3
- Phase encoding: A to P
- Scanner reconstruction distortion correction: Off
- Scanner physiological recording: Off (the project uses external LabChart channels)

The scanner's dummy excitations, saved BOLD measurements, external MR-trigger TTL
pulses, and fMRIPrep non-steady-state regressors are separate concepts. The protocol
does not establish that dummy excitations produce MRTRIG pulses or saved NIfTI
volumes. fMRIPrep normally preserves BOLD time points and identifies non-steady-state
volumes as confounds; it does not silently shorten the series merely because dummy
metadata are present.

## Verdicts

| Task | Verdict | Severity | Protocol-based rationale |
|---|---|---:|---|
| 09 | Keep | Low | External piezo remains a limitation; scanner physio recording is Off. QC and respiration-only fallback already mitigate most risk. |
| 12 | Keep | Major | RETROICOR-before-fMRIPrep remains nonstandard and requires pilot report/QC validation. |
| 13 | Upgrade/Merge 28 | Major | Verification is non-fatal and Step 05 catches assembly failure, so stale/incomplete corrected BIDS can enter fMRIPrep. Owns fatal assembly, failed-subject manifests, and real-data validation. |
| 15 | Revise | Moderate | The protocol establishes a 387.94 s saved run but does not provide the external stimulation block schedule. A 128 s HPF is not proven wrong until the actual design period is read from the task log. |
| 16 | Keep | Major | Scanner distortion correction is Off and PE is A to P. Confirming fMRIPrep fieldmap-based SDC is essential at 7 T. |
| 17 | Revise | Moderate | TR=1.19 s supports comparing FAST with AR(1), but AR(1) is not automatically invalid because data are 7 T/SMS. Select using residual-whitening diagnostics. |
| 18 | Merge into 33 | None | SPM `iCC=1` already centers. Incomplete-covariate policy belongs to the platform/group second-level design. |
| 19 | Revise | Moderate | Confound choice should be a preregistered sensitivity analysis. The current extractor also omits fMRIPrep `non_steady_state_outlier_*` columns, which should be modeled or explicitly censored without shifting onsets. |
| 20 | Merge into 12 | None | The code uses second-order cardiac and respiratory terms. Remaining validation is part of the required two-platform pilot. |
| 21 | Discard | None | The active automatic R-DECO path creates `R_loc` from `peak_sec` relative to each segmented signal. `timeofday()` therefore recovers relative seconds, not acquisition clock time. |
| 22 | Keep | Major | Contrary to stale project memory, Step 07 still defaults to `Space=T1w` and `DoMNI=true`, so the secondary SPM normalization is the active default. Change the default to direct fMRIPrep MNI and validate the transition. |
| 23 | Discard | None | The provided protocol specifies one BlockStim and one ContinuousStim measurement. Reopen only if actual BIDS data contain run-02 or later. |
| 24 | Revise | Moderate | The primary issue is silent geometry/affine exclusion and subject-count loss. Partial-volume sphere weighting is optional methodology refinement. |
| 25 | Revise | Moderate | For trigger-bearing analyzed BOLD, do not subtract 3 TRs automatically; require cross-stage counts to equal the raw NIfTI. Triggerless sequences retain pseudotime validation instead. |
| 26 | Revise | Moderate | A universal 0.35 mm threshold is not established by this protocol. Use configurable FD/DVARS criteria and report sensitivity/exclusion rules. The 0.9 mm mean-FD flag is too permissive as QC. |
| 27 | Keep | Low | Contrast-name verification is inexpensive protection, though current contrast order is stable. |
| 28 | Merge into 13 | None | The concrete failure is corrected-BIDS assembly/verification continuing after error; Task 13 now owns it. |
| 29 | Upgrade | Moderate | The scanner transition makes exact analysis-environment provenance necessary before comparing platform pilots. |
| 30 | Keep | Moderate | The two-tailed threshold implementation is a confirmed code bug and is protocol-independent. |
| 31 | Revise | Major | Validation must be sequence-class-specific: trigger-bearing BOLD requires burst/cardinality checks; expected triggerless sequences require pseudotime duration/bounds checks. |
| 32 | Merge into 31 | None | No incorrect pseudotime boundary was demonstrated. Preserve the working MRTRIG-to-clock method and triggerless parsing; Task 31 owns anchor plausibility and midnight rollover. |
| 33 | Keep | Major | The early-2026 scanner/sequence transition may be imbalanced or confounded with group and must be addressed in the second-level design. |

## Timing conclusion

The current critical timing risk is not "three dummy scans were dropped by
fMRIPrep." The defensible rule is:

1. Anchor LabChart time to scanner clock time using a known trigger-bearing sequence.
2. Classify every sequence as trigger-expected or triggerless-expected.
3. For trigger-bearing analyzed BOLD, identify the first TTL pulse corresponding to
   the first saved volume and confirm pulse count/spacing against raw NIfTI and JSON.
4. For pre-update task runs, separately compare that count with the 326-volume Bay1
   expectation. Establish post-update/rest counts directly from their NIfTIs.
5. Confirm the raw count through RETROICOR, fMRIPrep, confounds, motion regressors,
   and SPM for trigger-bearing analyzed BOLD.
6. For triggerless sequences, use acquisition-time pseudotime as the boundary and
   validate duration, bounds, order, and available markers rather than requiring TTLs.

Also verify BIDS metadata against the protocol: `RepetitionTime=1.190`, 92
`SliceTiming` entries consistent with SMS4, correct BIDS phase-encoding direction
for scanner A-to-P, and valid total-readout/fieldmap metadata.

## Real-sidecar update: two acquisition generations

Six BOLD sidecars from two subjects in [`docs/protocols/`](protocols/) were reviewed
on 2026-06-10.

Common across all runs:

- TR=1.190 s, TE=0.022 s, 1.5 mm slices
- 92 SliceTiming values with 23 unique times repeated fourfold
- in-plane acceleration 3 and BIDS `PhaseEncodingDirection="j-"`
- no dummy-volume or total-volume metadata

`sub-7T0111LC031523` uses the older Terra/E12
`ep2d_bold_sms_mgh_v1p2` sequence and closely matches Bay1.pdf:
`EffectiveEchoSpacing=0.000213334`, `TotalReadoutTime=0.0270934`,
`RefLinesPE=48`, explicit MB factor 4.

`sub-7T1019HC042726` uses a Terra.X/XA60 CMRR sequence:
`EffectiveEchoSpacing=0.000216665`, `TotalReadoutTime=0.0275164`,
`RefLinesPE=114`; MB factor is absent from the JSON but the SliceTiming pattern is
consistent with SMS4.

The scanner/system transition was confirmed to have occurred near the beginning of
2026. This makes acquisition generation a known longitudinal batch change rather than
an incidental metadata difference.

Consequences:

1. Pilot validation should include one subject from each acquisition platform.
2. Slice timing/readout metadata must be consumed per run, not copied from Bay1.pdf.
3. Missing explicit MB metadata is a documentation issue, not automatically invalid
   data when the timing/image pattern establishes SMS4.
4. Task order varies between subjects, so hybrid pseudotime/trigger alignment must
   not assume BlockStim precedes ContinuousStim.
5. NIfTI/confound inspection establishes per-run cardinality. Only the pre-update
   task protocol independently establishes a 326-volume expectation.
6. Audit acquisition platform across the full cohort. The two examples place an
   `LC` subject on the older platform and an `HC` subject on the newer platform;
   this is not proof of group confounding, but it is a serious possibility.

## Final active-task consolidation

The fresh code-and-memory audit reduced the active queue from 20 to 16 tasks:
Task 18 was merged into Task 33, Task 20 into Task 12, Task 28 into Task 13, and
Task 32 into Task 31.
The authoritative linear order is `agents/TEMPORARY_LINEAR_ROADMAP.md`.
