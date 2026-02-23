# Parameter Extension Workflow

## Why this exists
You do not need to re-open `NanonisClass.py` every time a new function is needed.
The bridge supports loading extra parameter specs from `config/extra_parameters.yaml`.

## Fast path
1. Discover relevant backend commands.
2. Scaffold or edit `config/extra_parameters.yaml`.
3. Validate parameter file.
4. Use new parameters in CLI/QCodes without changing core code.

## 1) Discover command names

```powershell
nqctl parameters discover --match LockIn
```

## 2) Scaffold extra parameter file

```powershell
nqctl parameters scaffold --match LockIn --output config/extra_parameters.yaml
```

## 3) Validate parameter file

```powershell
nqctl parameters validate --file config/extra_parameters.yaml
```

## 4) Load files in QCodes driver

```python
from nanonis_qcodes_controller.qcodes_driver import QcodesNanonisSTM

nanonis = QcodesNanonisSTM(
    "nanonis",
    auto_connect=True,
    parameters_file="config/default_parameters.yaml",
    extra_parameters_file="config/extra_parameters.yaml",
)

print(nanonis.get_parameter_value("lockin_mod_enabled"))
nanonis.close()
```

## Notes
- Core behavior is generic and spec-driven.
- `set` and `ramp` operate on any writable parameter with safety settings.
- Extra parameter file is add-only by design; colliding names are rejected.
