# Extension Workflow

## Why this exists
You do not need to re-open `NanonisClass.py` every time a new function is needed.
The bridge now supports loading additional read parameters from a YAML manifest at runtime.

## Fast path
1. Discover relevant backend commands.
2. Generate or edit a parameter manifest.
3. Load manifest into `QcodesNanonisSTM`.
4. Use new parameters like built-in QCodes parameters.

## 1) Discover command names
List matching commands from installed `nanonis_spm`:

```powershell
python scripts/scaffold_extension_manifest.py --mode list --match LockIn
```

## 2) Scaffold manifest template
Generate a YAML file with `_Get` commands only:

```powershell
python scripts/scaffold_extension_manifest.py --mode manifest --match LockIn --output config/lockin_parameters.yaml
```

Then edit generated entries and fill `args` values where needed.

You can also start from `config/extra_parameters.template.yaml`.

## 3) Load manifest in QCodes driver

```python
from nanonis_qcodes_controller.qcodes_driver import QcodesNanonisSTM

nanonis = QcodesNanonisSTM(
    "nanonis",
    auto_connect=True,
    extra_parameters_manifest="config/lockin_parameters.yaml",
)

print(nanonis.lockin_mod_enabled())
print(nanonis.lockin_mod_amplitude_v())

nanonis.close()
```

## 4) Call any backend command directly
For commands that should not be exposed as a QCodes parameter:

```python
response = nanonis.call_backend_command(
    "LockIn_ModOnOffSet",
    args={"Modulator_number": 1, "On_Off": 1},
)
```

## Notes
- Dynamic manifest entries are read-only QCodes parameters (`set_cmd=False`).
- For write commands, use guarded write methods when available, or explicit `call_backend_command(...)` only with lab safety procedures.
- Available command list from active backend:

```python
print(nanonis.available_backend_commands(match="LockIn"))
```
