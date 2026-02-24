# Parameter Extension Workflow

## Why this exists
You do not need to re-open `NanonisClass.py` every time a new function is needed.
The bridge uses one generated manifest at `config/parameters.yaml`.

## Fast path
1. Discover relevant backend commands.
2. Regenerate `config/parameters.yaml` from `nanonis_spm.Nanonis`.
3. Validate the generated manifest.
4. Use new parameters in CLI/QCodes without changing core code.

## 1) Discover command names

```powershell
nqctl parameters discover --match LockIn
```

## 2) Regenerate unified parameter manifest

```powershell
python scripts/generate_parameters_manifest.py --output config/parameters.yaml
```

## 3) Validate parameter file

```powershell
nqctl parameters validate --file config/parameters.yaml
```

## 4) Load files in QCodes driver

```python
from nanonis_qcodes_controller.qcodes_driver import QcodesNanonisSTM

nanonis = QcodesNanonisSTM(
    "nanonis",
    auto_connect=True,
    parameters_file="config/parameters.yaml",
)

print(nanonis.get_parameter_value("lockin_mod_enabled"))
nanonis.close()
```

## Notes
- Core behavior is generic and spec-driven.
- `set` and `ramp` operate on any writable parameter with safety settings.
- Command discovery is anchored at `Bias_Set`; callable members declared before
  that method are ignored to avoid importing internal helper methods.
- Discovered `Get`/`Set` methods are emitted under `parameters`, and non-`Get`/`Set`
  methods are emitted under `actions` with command-level descriptions.
