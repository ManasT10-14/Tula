# Operating, backing up and restoring Tula

This procedure is for the single SQLite runtime used by the web application and account administration. Run maintenance from an installed Tula environment outside the runtime being restored. Keep the matching application wheel/source release and its dependency versions separately; the runtime archive is not an application installer.

## Choose and retain the runtime location

Both the web app and `python -m tula.security bootstrap` use `tula.config.runtime_root()`:

| Launch configuration | Runtime root |
| --- | --- |
| `TULA_DATA_DIR` supplied | That directory, expanded and resolved at process launch |
| Source checkout with `pyproject.toml` | The checkout root |
| Installed wheel without an override | The current operating-system user's `~/.tula` |

The database is `<root>/data/tula.db`; uploads and bench captures are under `data/`, and generated reports are under `out/`. Use an absolute `TULA_DATA_DIR` in service configuration, administration shells and maintenance commands. A relative override depends on the launch directory. A different service account has a different home directory. An explicit bootstrap `--database` overrides the shared default; this backup command only manages the standard `data/tula.db` runtime.

For a new installation, keep application files and runtime data separate. Example PowerShell configuration (substitute your actual paths):

```powershell
$env:TULA_DATA_DIR = 'C:\TulaRuntime'
python -m tula.security bootstrap --username deployment.admin
python -m uvicorn tula.web.app:app --host 127.0.0.1 --port 8765 --no-access-log
```

The bootstrap command prompts for a password and refuses to replace existing accounts. Never move an existing runtime merely to match these example paths: inspections can contain absolute evidence paths.

## Correlate requests, background analysis and reports

Tula writes compact JSON operational events through Python logging. Each HTTP response carries an `X-Request-ID`. A valid caller-supplied ID is retained; otherwise the service creates one. The upload request ID is stored with its private queued job and appears on later `inspection_started`, stage, completion or failure events, even though OCR runs in another thread. Report start/completion/failure events include inspection ID, format, revision, duration and the retained artifact ID where applicable.

These operational events omit request bodies, query strings, OCR transcriptions, filenames, evidence paths, cookies, passwords and exception messages. Failures record only the exception type. An unexpected pre-response server failure returns a generic message containing the request ID so support can locate the matching event without showing internal details. Operational logs are diagnostic records; the database audit trail remains the authoritative account of officer, rule, user and report actions.

Uvicorn's default access line is suppressed because it contains raw URL query strings and duplicates the structured request event. Keep `--no-access-log` in service configuration as defence in depth. Configure reverse proxies, service managers and infrastructure logs to exclude or redact query strings as well; application code cannot govern an upstream proxy's access log. Protect operational output, set retention and rotation in the service manager, and do not treat a request ID as authentication or evidence integrity proof.

## Make a consistent backup

1. Finish queued inspections and stop every web server, worker and CLI process using this runtime. A queued/running job blocks backup, including an interrupted job whose lease has not been recovered. Restart the application to recover interrupted work, finish or retry it, then shut down again; do not edit job rows to bypass the check.
2. Choose a new archive filename in an existing, access-restricted directory outside the runtime tree. Do not place the archive under `data/`, `out/` or the runtime root.
3. Run creation and independent verification:

```powershell
python -m tula.storage.backup create 'D:\TulaBackups\2026-09-07.zip' --root 'C:\TulaRuntime'
python -m tula.storage.backup verify 'D:\TulaBackups\2026-09-07.zip'
Get-FileHash -Algorithm SHA256 -LiteralPath 'D:\TulaBackups\2026-09-07.zip'
```

Retain the archive's SHA-256 separately in a trusted inventory. The ZIP contains a manifest with per-file sizes and SHA-256 values. These detect corruption; the manifest is not a signature and cannot establish the authenticity of an archive rewritten by someone with access to it.

The command copies `data/`, `out/`, and optional `rules/`. All tables in the standard database are retained, including users, password hashes, sessions, audit events, current inspections, immutable revisions, rule archives, relational indexes and report registrations. Original evidence and generated reports retain their bytes. Current and historical image references, report hashes and rule-archive hashes must verify; missing evidence, missing used rule archives and evidence outside this runtime cause explicit failure. No retained JSON or audit row is rewritten to make verification pass.

The database is copied through the [SQLite backup API](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup), while a separate writer reservation excludes SQL changes. Committed WAL pages are included in the snapshot; live `-wal`, `-shm` and `-journal` sidecars are not archived. File inventory and hashes are checked again after copying. A temporary archive is published only when complete, without replacing an existing archive.

Updated ASGI processes hold shared runtime leases; maintenance requires the exclusive lease. On Windows this uses [OS file locking](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-lockfileex). Multiple server workers can share the lease. A shutdown that times out while OCR continues retains the lease until the worker exits. The small `.tula-runtime-<hash>.lock` file is a sibling of the runtime and remains after shutdown; its existence does not mean the server is running. Do not delete it to bypass maintenance exclusion.

Older application versions and independent scripts may not participate in that lease. Stop them explicitly. SQL writer locks and file consistency checks provide additional protection; this is an offline procedure, not support for arbitrary concurrent filesystem writers.

## Restore without altering evidence paths

1. Verify the archive while the current runtime remains intact. Inspect its `manifest.json` to confirm the original `runtime_root`, operating-system convention and `working_directory`.
2. Stop every runtime process. Preserve the existing runtime separately using your controlled recovery process. The command refuses any existing destination, including an empty directory, and never deletes or merges it.
3. From a separately installed Tula environment, restore to the **exact original absolute root**:

```powershell
python -m tula.storage.backup restore 'D:\TulaBackups\2026-09-07.zip' --destination 'C:\TulaRuntime'
```

Restoration checks member paths, duplicate/case-colliding names, links, manifest hashes, SQLite integrity, relational references, evidence and retained rule archives in an isolated staging directory before publication. Absolute paths and stored JSON remain unchanged. A failed verification does not publish the staged runtime. Windows directory rename refuses an existing destination. Linux restoration uses [`renameat2(RENAME_NOREPLACE)`](https://man7.org/linux/man-pages/man2/rename.2.html); unsupported operating systems/filesystems fail rather than use an overwrite-capable fallback. Atomic archive creation also requires filesystem hard-link support. Use a local supported filesystem, such as NTFS on Windows; network filesystem crash behavior is outside the tested recovery guarantee.

4. Reapply the same absolute `TULA_DATA_DIR`, use the matching application release, and launch it from the recorded working directory if legacy inspection paths are relative. Confirm that the original evidence, revision history, active rule version and sample report downloads work. Compare selected file hashes with the independently retained backup inventory before returning the service to use.

For a source checkout that also served as the runtime root, the archive restores its data directories, not `src/`, project metadata or an external environment. Use a separately installed wheel to run the restore and point `TULA_DATA_DIR` at the restored root. Retain the original source release separately. The tool intentionally rejects relocation; changing database path strings would invalidate historical provenance.

## Limits and protection

- Archives contain sensitive evidence, account hashes, sessions and audit history. Restrict directory access, protect the destination volume and transport, and apply your retention policy. ZIP encryption is not implemented. Session rows are restored exactly and can remain usable until their normal expiry; manage any required post-recovery credential/session response separately through account administration.
- The command supports up to 100,000 files and 50 GiB of uncompressed runtime content. Creation, verification and restoration need additional free disk space for staging. Verification uses the system temporary directory; protect that volume as well.
- Symlinks, junctions/reparse points, hard-linked source files, unsafe or ambiguous archive names, missing source hashes, external evidence and incomplete referenced rule archives are rejected. Repair the original retention problem from a trusted source, or use a separately reviewed recovery procedure; do not replace stored hashes to silence the error.
- Application code, environment variables, TLS/service configuration, external OCR model caches, custom account databases and external files are not captured unless they are ordinary files inside the three included directories. Keep deployment configuration and matching offline model/application packages separately.
- Backup verification establishes byte and reference consistency. It does not certify OCR accuracy, legal completeness, the truth of officer decisions, protection against hostile database administrators, or crash durability on every storage system.

Commands print counts and actionable errors, not passwords, account rows or label text. The automated recovery tests use isolated generated images and test accounts; they do not back up or alter the operational runtime.

## Check an installed release outside its source checkout

`scripts/check_installed_deployment.py` exercises a real loopback Uvicorn server and actual RapidOCR. It requires a newly created virtual environment, a fresh wheel, and a **new disposable work directory outside the checkout**. Copy the script outside the checkout and run it with Python's isolated `-I` option. It verifies the origins and bytes of installed modules and packaged assets, the installed rule pack, the three bundled ONNX models, a nested `TULA_DATA_DIR`, bootstrap/login/account permissions, image upload, OCR, retained review history, independent supervisor approval of the generated sample, all document exports and their audit events, and offline backup/restore. It also saves a private capture draft and checks its original bytes, edits, cleared attestations and regenerated preview after restore. Initial and restored servers run in separate installed Python processes and shut down gracefully. Inspector self-approval and another account's access to the draft must fail. The generated sample uses Hindi text; the font/shaping prerequisites in the user manual also apply to this check.

Example PowerShell commands, using a new acceptance folder for each run:

```powershell
$checkout = 'C:\Path\To\sih'
$acceptance = Join-Path $env:TEMP ('tula-deployment-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $acceptance | Out-Null
Set-Location -LiteralPath $checkout
python -m build --wheel --outdir (Join-Path $acceptance 'wheels') .
python -m venv --system-site-packages (Join-Path $acceptance 'venv')
$venvPython = Join-Path $acceptance 'venv\Scripts\python.exe'
$wheel = (Get-ChildItem -LiteralPath (Join-Path $acceptance 'wheels') -Filter '*.whl').FullName
& $venvPython -m pip install --no-index --no-deps $wheel
Copy-Item -LiteralPath (Join-Path $checkout 'scripts\check_installed_deployment.py') -Destination $acceptance
Set-Location -LiteralPath $acceptance
& $venvPython -I '.\check_installed_deployment.py' --work (Join-Path $acceptance 'run') --checkout $checkout --wheel $wheel
```

This particular command sequence reuses system dependencies, including HTTPX for the acceptance client. It establishes that **Tula itself comes from the installed wheel**, while the inference libraries/models may come from the host environment. It does not establish a clean dependency installation on another computer. For that separate check, create the venv without `--system-site-packages`, install the wheel and its dependencies plus HTTPX from the deployment's approved package source, provision the supported font and any optional Tesseract language data, then run the same script. Preserve dependency versions and required model files with the release. Standard isolated wheel builds are recommended; a `--no-isolation` build can be disrupted by unrelated global setuptools plugins.

To require an independent Python dependency environment, use this variation with the same absolute `$checkout` and `$wheel` variables:

```powershell
$cleanAcceptance = Join-Path $env:TEMP ('tula-clean-' + [guid]::NewGuid().ToString('N'))
python -m venv (Join-Path $cleanAcceptance 'venv')
$cleanPython = Join-Path $cleanAcceptance 'venv\Scripts\python.exe'
& $cleanPython -m pip install $wheel 'httpx>=0.27,<1'
& $cleanPython -m pip check
Copy-Item -LiteralPath (Join-Path $checkout 'scripts\check_installed_deployment.py') -Destination $cleanAcceptance
Set-Location -LiteralPath $cleanAcceptance
& $cleanPython -I '.\check_installed_deployment.py' --work (Join-Path $cleanAcceptance 'run') --checkout $checkout --wheel $wheel --require-clean
& $cleanPython -m pip freeze > '.\resolved-dependencies.txt'
```

`--require-clean` rejects shared system packages or an enabled user package directory, and verifies that every discovered Python distribution resides inside this venv. The evidence also records the actual font file, shaping support, optional Tesseract path/languages and selected OCR environment settings. Fonts, Tesseract and its language files remain operating-system dependencies; installing the Python wheel does not supply them.

When testing a previously accepted wheel while the checkout is being edited, `--baseline-evidence <previous-acceptance.json>` verifies the exact wheel hash and its retained source-file hashes. This explicitly records a historical source comparison and **does not claim that the current checkout was accepted**. Omit that argument for the final frozen-source check.

The script disables non-loopback Python socket connections during the check; it does not apply a machine-wide network firewall or prove that every native dependency is air-gapped. Bootstrap passwords are random, passed privately through stdin, and are not written to the acceptance JSON. The disposable runtime still contains sensitive session/account state and should receive the same access protection as other backups.

Successful output points to `acceptance.json`, recording the wheel hash, exact installed import paths, model hashes, HTTP results, OCR span count, document hashes, command results and per-table row/hash comparisons. Restoration must preserve every recorded database row and archived file, and the restarted app must retrieve the original record and evidence. Both the original test runtime (`runtime-before-restore`) and restored copy remain available for inspection; the script never deletes an existing runtime. A failure retains its diagnostic evidence and does not count as acceptance. Generated samples exercise deployment behavior, not legal accuracy across every product category, human approval quality or production load capacity.

### Earlier installed acceptance: 7 September 2026

An earlier frozen-source build completed acceptance at `2026-09-07T00:15:43Z` on Windows/Python 3.11. Its `tula-0.1.0-py3-none-any.whl` SHA-256 is `2667ef24c1bb6062111b648fe6dc7bff74d64c6769ab0f3dd5a0ea8ddb083383`. All 109 packaged modules/assets/rule files matched the installed bytes and preserved source, including 25 web assets/templates and the 18-rule `2026.09.07-legal-review-1` pack. Three local ONNX model files were hashed. No packaging or runtime-path code change was needed. This is historical evidence; the newer 112-file release is recorded below.

The actual generated Hindi/English image produced 11 RapidOCR spans and the correct `6 ml` quantity. Synthetic inspection `3BB39FF1C4` reached independently approved revision 3; inspector self-approval returned HTTP 403. The saved Processing page/API and 12 page/asset requests succeeded. PDF (14 pages), DOCX and notice exports were retained and audited. Offline creation, verification and restoration preserved 25 database tables exactly, five retained inspection records (current plus four revisions), the rule archive and every archived file. The restarted application authenticated a disposable inspector and returned the original approved record and image. The generated image and PDF opening page were also visually inspected.

Local evidence is retained under `C:\Users\offic\AppData\Local\Temp\tula-wheel-acceptance-f0d5sgiz`: `accepted-run\acceptance.json`, `accepted-wheel\tula-0.1.0-py3-none-any.whl`, `final-commands.txt`, `accepted-build.log`, and `accepted-install.log`. The acceptance JSON identifies every compared file and table hash. Earlier `preflight-*` and `final-*` run artifacts are diagnostic predecessors; **`accepted-run` and `accepted-wheel` are the final accepted pair**. Preserve the evidence directory deliberately if the operating system's temporary-file cleanup policy would otherwise remove it.

This used a disposable virtual environment with host dependencies and a Python-level outbound-socket restriction. It did not install every dependency from scratch, test an OS-wide air gap, deploy externally, or touch operational accounts/runtime data. OCR ambiguity, absent physical scale evidence, and the draft legal-pack warning remained visible; acceptance does not erase those limitations or certify real products.

### Clean dependency baseline: 7 September 2026

A second acceptance installed the same previously accepted wheel, all of its declared dependencies and HTTPX into a new Python 3.11 venv **without** `--system-site-packages`. `pip check` passed. All 50 discovered distributions were inside the venv and user-site packages were disabled. The resolved versions included FastAPI 0.141.1, Starlette 1.6.0, Uvicorn 0.52.4, OpenCV 5.0.0.93, NumPy 2.4.6, ONNX Runtime 1.29.0, RapidOCR 1.4.4, Pillow 12.3.0, ReportLab 5.0.1, pypdf 6.17.0, python-docx 1.2.0 and HTTPX 0.28.1. The complete resolved package list and installation report are retained with the evidence.

Both the normal run and a second run with `TULA_TESSERACT=off` passed actual image OCR, authenticated review and independent approval, Processing page/API access, all audited exports, backup/restore and restored access. A generated ArUco marker also passed through the installed OpenCV/metrology code with the expected `25/199` mm-per-pixel scale. The normal run's PDF had 13 pages under ReportLab 5.0.1; its complete page contact sheet was visually inspected.

All three ONNX files were present inside the installed RapidOCR package before first inference. Runtime Python socket connections to non-loopback addresses were rejected, and the required-model-only run did not invoke Tesseract. This demonstrates local first inference for this resolved Python package/model combination after installation. Package installation itself used the configured package source; an offline installer still needs the application and dependency wheels retained in advance. No OS-wide firewall test was performed.

The host supplied `C:\Windows\Fonts\Nirmala.ttc` for the generated Hindi fixture, with Pillow RAQM shaping enabled. The normal run additionally used the existing Tesseract executable and `eng+hin` language files; the second run proved the main sample workflow without that optional auxiliary engine. Fonts and Tesseract are not provided by the Python dependency installation.

Evidence root: `C:\Users\offic\AppData\Local\Temp\tula-clean-install-71byq87r`. The normal baseline is `baseline-run\acceptance.json`; the optional-engine-disabled result is `required-models-run\acceptance.json`. The package installation report is `install-report.json`, and `baseline-requirements-lock.txt` records resolved versions. These two runs explicitly use the previous accepted source manifest. **They verify clean dependencies for that wheel, not the subsequently edited checkout.** Current-source acceptance requires a new frozen wheel and a run without `--baseline-evidence`.

### Capture-draft backup source checkpoint: 7 September 2026

The current source now validates saved capture-draft originals during backup creation, archive verification and restoration. Each draft that was active and unexpired at the archive's recorded creation time must have its ordered `data/capture-drafts/<session>/<index>-original.bin` files, matching database-recorded SHA-256 values and byte counts. Checks also cover session paths, bounded image counts/sizes, edit geometry, total bytes and the saved manifest fingerprint. Verification continues to use that same archive time even after a draft expires. Deleted or already-expired draft metadata is retained without requiring originals that cleanup removed. Retained JSON and audit rows are not rewritten; recovery does not extend a draft's expiry or restore its cleared coverage/context attestations.

Two separate source test runs passed:

- `python -m pytest tests/test_backup_drafts.py tests/test_backup_restore.py -q --basetemp=.pytest-backup-draft-agent` — **69 passed in 65.63 seconds**, including real draft restoration, missing/tampered originals, rehashed outer archives, malformed manifests, lifecycle cleanup and snapshot-time verification.
- `python -m pytest tests/test_capture_drafts.py tests/test_runtime_config.py -q --basetemp=.pytest-backup-draft-regression-agent` — **36 passed in 46.08 seconds**. This was a separate regression run, not one combined 105-case run. Ruff passed for the changed backup/checker files and tests; the backup module and checker also passed Python compilation.

At this source-test checkpoint, the installed-deployment checker had been extended to save and resume a private draft through real HTTP, reject other supervisor/administrator access, regenerate its rotated/cropped preview, verify cleared attestations, and compare restored metadata and original bytes. Those new assertions were then written and compiled but had not yet run against a fresh wheel. The subsequent successful installed run is recorded next; the earlier wheel results above remain historical evidence for their own source versions.

### Earlier installed release before rescan continuity: 7 September 2026

That frozen production source passed clean installed acceptance at `2026-09-07T01:53:01Z`. The [retained wheel](C:/Users/offic/OneDrive/Desktop/sih/out/current-release-installed-qa/tula-0.1.0-py3-none-any.whl) has SHA-256 `567e73edb15c297d9783325aa0eef11614ea319bdb19ecf4c209083098f30b9f`. It was built from a fresh outside-checkout source snapshot, then installed in a new Python 3.11.4 venv without shared system or user packages. All **112 packaged files** matched that frozen source and installed bytes, including **25 web templates/assets** and the **18-rule** pack. All **50 Python distributions** resolved inside this venv; `pip check` passed. Three ONNX model files supplied by RapidOCR were present and hashed before inference. This remains historical evidence for that wheel; the newer release is recorded next.

Actual image upload produced 11 RapidOCR spans and the correct `6 ml` declaration. Generated QA inspection `98722AF2FC` reached Approved revision 3 through separate inspector/supervisor accounts; inspector self-approval was refused. This checks account separation, not independent review by two human officers. Processing and 12 page/asset requests worked. The PDF (13 pages), report DOCX and notice DOCX were retained and audited. Draft legal, uncertain OCR and missing physical-scale warnings remained visible; the sample's screened exemption is not a certification of a real product.

The private draft retained its 1440 × 2160 original image, back-panel assignment, 90-degree rotation, normalized crop `[0.1, 0.1, 0.9, 0.9]`, typed details and package selections. Coverage and context attestations stayed cleared. Other supervisor/administrator accounts could not read the draft or its original. Offline backup/restore preserved **all 26 database tables and eight archived files exactly**, including five retained inspection records, the rule archive, document exports and one active draft original. A fresh server process retrieved the unchanged approved inspection and private draft, then regenerated the draft's 1728 × 1152 edited preview. No operational runtime or account was changed.

The [successful acceptance receipt](C:/Users/offic/OneDrive/Desktop/sih/out/current-release-installed-qa/acceptance.json) contains source/import, model, dependency, process, export and per-table hash evidence. [ARTIFACTS.json](C:/Users/offic/OneDrive/Desktop/sih/out/current-release-installed-qa/ARTIFACTS.json) records byte-identical copies from the original temporary evidence; [execution-receipt.json](C:/Users/offic/OneDrive/Desktop/sih/out/current-release-installed-qa/execution-receipt.json) records the exact build/install/check arguments. The durable folder also contains the checker, dependency lock/install reports, process receipts and QA exports, but omits virtual environments, runtime databases, backup archives and original-upload directories. The original evidence remains under `C:\Users\offic\AppData\Local\Temp\tula-current-clean-f289a4eb432d45be8e0dee6016d6ec73`.

The first run exposed a same-process restart mistake in the checker. Only the checker changed; the application wheel remained identical. The [failed-run explanation](C:/Users/offic/OneDrive/Desktop/sih/out/current-release-installed-qa/FAILED_HARNESS.md) and failed receipt are preserved separately. The successful checker SHA-256 is `0c29892a26ddd6ff35384a824697b090cd588dd4ee49ac136d622d799c4892dd`. Reproduce with the clean-venv commands above, a new work directory, and the retained checker/wheel; for a source-match run, use the matching frozen checkout and omit `--baseline-evidence`.

That run used the host's Nirmala font with RAQM and optional Tesseract `eng+hin`, with no OCR environment overrides. Python socket guards applied to the checker and both server processes; this was not an OS-wide air-gap test. The previous optional-Tesseract-disabled result still belongs to the older wheel. Root integration tests ran concurrently, so that installed acceptance is not a latency or OCR-accuracy benchmark, an external deployment, or another-machine acceptance.

### Historical installed release with rescan continuity: 7 September 2026

The current frozen production source passed clean installed acceptance at **`2026-09-07T04:10:01Z`**. The [new retained wheel](C:/Users/offic/OneDrive/Desktop/sih/out/rescan-release-installed-qa/tula-0.1.0-py3-none-any.whl) has SHA-256 **`a809a0f8ab09669dc09f132653d100cd14cfcc434363376fdd62e9725af74a7f`**. A new snapshot outside the checkout supplied 119 build inputs. All **115 packaged modules/assets/rule files** matched the frozen production source and installed bytes, including **26 web templates/assets** and the **18-rule** `2026.09.07-legal-review-1` pack. The new Python 3.11.4 venv has no shared system or user packages; all **50 distributions** reside inside it and `pip check` passed. Declared dependencies are unchanged from the previous release. Three bundled RapidOCR ONNX models were present and hashed before inference.

The actual generated Hindi/English cosmetic image produced **11 RapidOCR spans** and the correct **6 ml** quantity. Inspection **`A572FCEFB6`** reached Approved revision 3 through separate disposable inspector/supervisor accounts; inspector self-approval was refused. The 12 page/asset requests and Processing worklist succeeded. The **13-page PDF**, report DOCX and notice DOCX were retained and audited. Legal-draft, OCR-conflict and missing-scale warnings remained visible. This is deployment acceptance using a generated package and account separation; it does not certify a real product or represent review by independent human officers.

Private draft `82c0a871019c0bf61bc95f5a673cae35` retained its 1440 × 2160 original, back-panel selection, 90-degree rotation, crop `[0.1, 0.1, 0.9, 0.9]`, typed details and package selections. Its original SHA-256 is `e21f615761cf33e8b6d7d66109f89f49cfdd27c4981207a85fd6a8c7516125d5`. Other administrator/supervisor accounts could not read it. Coverage and contextual attestations remained cleared. Offline restoration preserved **26 database tables and eight archived files exactly**, including five retained inspection records, one rule archive, three registered document exports and one active draft original. A fresh installed server process (PID 56372 before shutdown, PID 26716 after restoration) retrieved the unchanged approved inspection and draft, then regenerated the 1728 × 1152 edited preview. Both acceptance server processes stopped normally; operational accounts and runtimes were untouched.

The [successful receipt](C:/Users/offic/OneDrive/Desktop/sih/out/rescan-release-installed-qa/acceptance.json) records exact source/import, model, dependency, HTTP, export and table/file hashes. [ARTIFACTS.json](C:/Users/offic/OneDrive/Desktop/sih/out/rescan-release-installed-qa/ARTIFACTS.json) identifies 27 byte-identical copied artifacts; [execution-receipt.json](C:/Users/offic/OneDrive/Desktop/sih/out/rescan-release-installed-qa/execution-receipt.json) records reproduction arguments. The durable folder excludes venvs, runtime databases, account rows, original-upload directories and the backup ZIP. Original evidence remains under `C:\Users\offic\AppData\Local\Temp\tula-rescan-clean-908cc4bfb5164a7982e8700ed5e19639`. The first post-rescan wheel was superseded by the report-heading wording fix before acceptance; its bytes and build receipts remain in the [superseded subfolder](C:/Users/offic/OneDrive/Desktop/sih/out/rescan-release-installed-qa/superseded/README.md). Earlier accepted releases were not overwritten.

The final wheel was installed with `pip install --force-reinstall --no-deps` after provisioning this venv from the retained dependency lock; only Tula was replaced. The corrected checker SHA-256 remains `0c29892a26ddd6ff35384a824697b090cd588dd4ee49ac136d622d799c4892dd`. It ran with `-I`, `--require-clean`, a new work directory and **no historical baseline override**. Use the clean-venv reproduction above with the retained final wheel and matching source snapshot; do not substitute the superseded wheel merely because both distribution names are `tula-0.1.0`.

This installed run checked the standard inspection, private-draft and recovery workflow. The actual linked-rescan browser journey is separate source-runtime evidence in [rescan browser verification](C:/Users/offic/OneDrive/Desktop/sih/out/rescan-browser-qa/final/verification.json), not an additional installed-rescan OCR run. Nirmala/RAQM and optional Tesseract `eng+hin` came from the host; no OCR environment overrides were used. Python socket restrictions do not establish an OS-wide air gap. Full source tests ran concurrently after the timed OCR benchmarks finished, so this acceptance supplies no latency or real-photo accuracy claim. It is not an external deployment or another-machine acceptance.

### Historical installed release with operational correlation: 7 September 2026

That frozen source passed clean installed acceptance at **`2026-09-07T10:33:01Z`**. The [retained wheel](C:/Users/offic/OneDrive/Desktop/sih/out/observability-installed-qa/final/tula-0.1.0-py3-none-any.whl) has SHA-256 **`31faaac37533ede5aa5b054f1cd5313466f3e4c2de3cc2880e5e0f480f7add8b`**. All **116 packaged application, web-asset and active-rule files** matched that source and installed bytes. The newly created Python 3.11 environment contained all **50 distributions**, `pip check` passed, and three RapidOCR ONNX models were verified.

The standard installed workflow again passed actual generated-image OCR, private draft restoration, account-separated approval, PDF/DOCX/notice exports, Processing, evidence checks and exact restoration of **26 tables and eight referenced files** in a new process. The enhanced checker retained **128 JSON operational events**. Its upload request ID continued through start, all seven stages and completion; each export retained its own request ID through report start, completion and response. There were zero raw Uvicorn access lines, and the private filename/title probes were absent.

The [authoritative acceptance receipt](C:/Users/offic/OneDrive/Desktop/sih/out/observability-installed-qa/final/acceptance.json), [verification narrative](C:/Users/offic/OneDrive/Desktop/sih/out/observability-installed-qa/VERIFICATION.md), wheel, exports and server logs are retained without the disposable runtime, account database, uploads or backup archive. Full original evidence remains in `C:\Users\offic\AppData\Local\Temp\tula-observability-clean-20260907-01`. This remains local-machine acceptance using a generated package; it does not establish external proxy policy, production load, physical-device behavior, independent legal acceptance or representative OCR accuracy.

### Historical installed release with indexed repository reads: 7 September 2026

The current source passed clean installed acceptance at **`2026-09-07T11:25:36Z`**. The [retained wheel](C:/Users/offic/OneDrive/Desktop/sih/out/performance-installed-qa/final/tula-0.1.0-py3-none-any.whl) has SHA-256 **`b10e37976a6bb5c3593db320e534640f5216e2bda3536793a9a8797381bffe3b`**. All **116 packaged application, web-asset and active-rule files** matched current source and installed bytes. The isolated Python 3.11 environment contained all **50 distributions**, `pip check` passed, and three RapidOCR ONNX models were verified.

The standard installed workflow passed actual generated-image OCR, private draft recovery, account-separated approval, PDF/DOCX/notice exports, Processing, evidence checks and exact restoration of **26 tables and eight referenced files** in a new process. Its retained **103 JSON operational events** include upload correlation through all seven analysis stages and separate correlation for every report; no raw access line or private probe value appeared.

The repository read benchmark used the same synthetic local SQLite database before and after indexing: **100,000 inspections and 20,000 findings**. Median recent-page latency changed from **1,448.7 ms to 18.7 ms**, August filtering from **578.8 ms to 79.9 ms**, natural-language month search from **392.8 ms to 76.5 ms**, category/date filtering from **888.7 ms to 46.5 ms**, deep pagination from **2,500.4 ms to 23.5 ms**, and dashboard aggregation from **1,578.2 ms to 639.4 ms**. Sixty-four filtered reads across 16 threads changed from **15.5 seconds to 3.8 seconds**, with all results returned. A separate **100,000-job** worklist probe measured 34.3 ms for an all-account page and 12.3 ms for an owner page after indexing. Query plans confirm the new recency/day indexes and no temporary sort for recent inspection or all-job pages. These are local synthetic read measurements, not OCR throughput or production-infrastructure load claims.

The [acceptance receipt](C:/Users/offic/OneDrive/Desktop/sih/out/performance-installed-qa/final/acceptance.json), [read comparison](C:/Users/offic/OneDrive/Desktop/sih/out/performance-installed-qa/final/performance-verification.json), [worklist comparison](C:/Users/offic/OneDrive/Desktop/sih/out/performance-installed-qa/final/performance-jobs-verification.json), wheel, exports and logs are retained without the disposable database, accounts, uploads or backup archive. The original acceptance workspace remains at `C:\Users\offic\AppData\Local\Temp\tula-performance-acceptance-20260907-01`. The current 8766 demonstration was restarted onto this schema; port 8765 was not changed. External load, another machine, HTTPS/service operation, physical-device testing and independent legal acceptance remain unfinished.

### Previous installed release with image-quality guidance: 7 September 2026

The current source passed clean installed acceptance at **`2026-09-07T12:01:59Z`**. The [retained wheel](C:/Users/offic/OneDrive/Desktop/sih/out/quality-installed-qa/final/tula-0.1.0-py3-none-any.whl) has SHA-256 **`292dd81432fd5799a6384e61c01fb19e19fe797345061bfb059ab2fab99f50af`**. All **116 packaged application, web-asset and active-rule files** matched current source and installed bytes. The isolated Python 3.11 environment contained all **50 distributions**, `pip check` passed, and three RapidOCR ONNX models were verified.

The installed workflow passed actual generated-image OCR, private draft recovery, account-separated approval, PDF/DOCX/notice exports, Processing, evidence checks and exact restoration of **26 tables and eight referenced files** in a new process. Its **103 JSON operational events** cover upload correlation through all seven analysis stages and every report, with no raw access line or private probe value.

Generated image controls verified a readable bright label, a nearly white frame requiring recapture and a four-percent crop requiring full-panel context. The contact sheet was visually inspected. A SHA-deduplicated check of **14 retained real photographs** decoded every image and produced zero new overexposure flags; **41 duplicate files** were excluded. This does not establish representative live-camera false-positive rates or occlusion detection.

The [acceptance receipt](C:/Users/offic/OneDrive/Desktop/sih/out/quality-installed-qa/final/acceptance.json), [verification narrative](C:/Users/offic/OneDrive/Desktop/sih/out/quality-installed-qa/VERIFICATION.md), quality results, retained-photo audit, wheel, exports and logs are retained without the disposable database, accounts, uploads or backup archive. The disposable acceptance workspace remains at `C:\Users\offic\AppData\Local\Temp\tula-quality-acceptance-20260907-172948`. Port 8766 was restarted on this source with four completed jobs; port 8765 was not changed. External load, another machine, HTTPS/service operation, physical-device testing, representative OCR validation and independent legal acceptance remain unfinished.

### Previous installed release with optional inspection coordinates: 7 September 2026

That source passed clean installed acceptance at **`2026-09-07T19:11:27+05:30`**. The historical wheel remains under `out/geography-final-wheel-20260907-190723/` and has SHA-256 **`c339973a540ec2744959867e99361dd2b018ca833ce1b0f241c6c11e75d11fb1`**. All **117 packaged application, web-asset and active-rule members** matched that source and installed bytes, including **27 web templates/assets** and the 18-rule pack. The isolated Python 3.11 environment contained all **50 installed distributions**, `pip check` passed, and three RapidOCR ONNX models were verified.

The standard installed workflow passed actual RapidOCR, private saved-draft recovery, Processing, account-separated approval, PDF/DOCX/draft-notice exports, all seven analysis stages and a fresh-process restore. The offline archive restored **26 tables and eight retained files** exactly. The installed-only geography verifier then created separate operational and Bench coordinate records from `site-packages`, rejected an invalid coordinate, confirmed 0.001° dashboard rounding, rendered the accessible SVG/table, retained an outside-extent row and verified the opt-in `geolocation=(self)` policy.

The original acceptance workspace remains at `C:\Users\offic\AppData\Local\Temp\tula-geography-final-acceptance-20260907-191039`; the clean environment remains at `C:\Users\offic\AppData\Local\Temp\tula-geography-final-clean-20260907-190821`. The stable `out/geography-installed-qa/final/` paths now identify the newer drill-down release below.

On a copied 100,000-record SQLite fixture, coordinate-column backfill plus the partial covering index completed in **1,527.27 ms**. Two incremental coordinate aggregation queries measured **0.011 ms median** and **0.179 ms maximum**, selecting `idx_inspection_source_geo`. The fixture contained no coordinates and its minimal records omitted revision rows required by the current full repository upgrade, so the retained result is a direct migration/query measurement. It does not establish high-cardinality geography behavior, concurrent writes, OCR throughput or external production load.

The 8766 demonstration was restarted on the new schema with four completed jobs and 24 preserved inspections; its older records have null coordinates. Port 8765 was not changed. Physical GPS accuracy, an actual browser location-permission journey, another machine, HTTPS/service operation, representative OCR validation, formal accessibility and independent legal acceptance remain unfinished.

### Current installed release with coordinate drill-down: 7 September 2026

The current source passed clean installed acceptance at **`2026-09-07T19:56:43+05:30`**. The [retained wheel](C:/Users/offic/OneDrive/Desktop/sih/out/geography-installed-qa/final/tula-0.1.0-py3-none-any.whl) has SHA-256 **`d09d7e0d33a099e416a87f085c49a92ed62addec9f217f79bcec2c4ca986d725`**. All **117 packaged application, web-asset and active-rule members** matched current source and installed bytes, including **27 web templates/assets** and the 18-rule pack. All **50 installed distributions** resolved inside the isolated Python 3.11 environment, `pip check` passed, and three RapidOCR ONNX models were verified.

The standard installed workflow passed actual RapidOCR, private saved-draft recovery, Processing, account-separated approval, PDF/DOCX/draft-notice exports, all seven analysis stages and a fresh-process restore. The offline archive restored **26 tables and eight retained files** exactly. The installed-only geography verifier additionally confirmed that an accessible SVG point and table location retain operational dataset/date/rounded-coordinate scope and that following the same filter returns the matching inspection. Invalid coordinates, operational/Bench separation, outside-extent disclosure, both geography indexes and the opt-in `geolocation=(self)` policy also passed.

The [acceptance receipt](C:/Users/offic/OneDrive/Desktop/sih/out/geography-installed-qa/final/acceptance.json), [installed geography result](C:/Users/offic/OneDrive/Desktop/sih/out/geography-installed-qa/final/geography-installed-verification.json), [dense-group benchmark](C:/Users/offic/OneDrive/Desktop/sih/out/geography-installed-qa/final/geography-drilldown-performance.json), [verification narrative](C:/Users/offic/OneDrive/Desktop/sih/out/geography-installed-qa/VERIFICATION.md), wheel, exports, logs and current desktop/mobile visuals are retained. The original acceptance workspace remains at `C:\Users\offic\AppData\Local\Temp\tula-geo-drilldown-acceptance-20260907-195444`; the clean environment remains at `C:\Users\offic\AppData\Local\Temp\tula-geo-drilldown-clean-20260907-195444`.

The dense-group query benchmark used a copied synthetic 100,000-record SQLite fixture with 50,000 records in one rounded coordinate group. Group count plus a recent 25-record page improved from **59.549 ms to 8.427 ms median** after `idx_inspection_source_geo_recent`; both queries selected that index, the recent page required no temporary sort, and index creation took **443.43 ms**. Coordinates were assigned directly to relational columns, so this is not OCR, concurrent-write, browser-traffic or external production-load evidence.

The 8766 demonstration was restarted on the new schema with four completed jobs and 24 preserved inspections; both geography indexes are present and the historical inspections remain unlocated. Port 8765 remained on PID 42480. Physical GPS accuracy, an actual browser permission journey, another-machine HTTPS/service deployment, representative OCR validation, formal accessibility and independent legal acceptance remain unfinished.

