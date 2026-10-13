# ACCS-Trace

Local-first Issue-Commit link recovery and reliability review.

ACCS-Trace imports a project's issues and commits, generates candidate pairs inside a
configurable time window, scores them with a frozen project model, triages the results,
routes uncertain candidates to human review, and exports provenance-linked records.
The tool does not train models: ACCS (adaptive confidence calibration for self-training)
produces the task models, and ACCS-Trace consumes the model packages they export.

## Requirements

- Windows 10/11 with PowerShell 7 (setup, development and demo scripts)
- Python 3.11.x
- Node.js 20.19+ in the 20.x series, or 22.12+, with npm

## Quick start

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\bootstrap.ps1      # creates .venv, installs locked dependencies, initialises the local database
.\scripts\dev.ps1            # starts the API (127.0.0.1:8000), the worker and the web client (127.0.0.1:5173)
```

Open `http://127.0.0.1:5173`, create a project and upload `sample_data/issues.csv`
together with `sample_data/commits.csv`. `.\scripts\demo-api.ps1` drives the same flow
end to end over the HTTP API and downloads the CSV export.

To install the optional offline CPU inference environment used by real TorchScript
packages (Windows x86-64 lock with wheel hashes):

```powershell
.\scripts\bootstrap.ps1 -WithModelRuntime
```

## Tests

```powershell
.\scripts\test.ps1                  # backend and frontend unit tests, typecheck, lint, production build
.\scripts\test.ps1 -SkipFrontend    # backend only
.\scripts\test.ps1 -IncludeE2E      # adds the Playwright end-to-end run (needs the model runtime)
```

## Layout

```text
backend/app/       FastAPI application, SQLAlchemy models, candidate generation, runtimes, worker
backend/tests/     backend unit and workflow tests
frontend/          React + TypeScript + Vite client
alembic/           database schema and migrations
docs/              API, architecture, data format, model package and reproducibility notes
sample_data/       synthetic CSV files used by the demo and the tests
scripts/           bootstrap, dev, test, demo and model export entry points
```

## Models

The repository ships no ACCS task checkpoint. The built-in `Deterministic Demo Runtime`
is a deterministic heuristic scorer for development, tests and interface demos: its
scores are not calibrated probabilities and do not represent method results.

Real deployments use `accs_torchscript_v1` packages, which bundle a dual-input CPU
TorchScript model, its tokenizer, the frozen preprocessing configuration and a SHA-256
manifest. Convert a trusted legacy checkpoint in an isolated environment:

```powershell
.\.venv\Scripts\python.exe .\scripts\export-legacy-accs-package.py --help
```

Place the generated package under `ACCSTRACE_DATA_DIR/models/<package_name>/` and
register it through `POST /api/models/register`. The full contract, including the tensor
signature and the model scope fields, is documented in
[docs/model-package.md](docs/model-package.md).

## Configuration

`.env.example` lists the available settings (`ACCSTRACE_DATA_DIR`,
`ACCSTRACE_DATABASE_URL`, `ACCSTRACE_FRONTEND_ORIGIN`, the upload size limit and the
worker poll interval). The application targets single-user local use: it has no
authentication and no TLS, binds to loopback only, and makes no telemetry or remote
inference calls. Project data, review decisions and exports stay in the local data
directory.

## Documentation

- [Architecture and functional boundaries](docs/architecture.md)
- [HTTP API](docs/api.md)
- [Input and output data format](docs/data-format.md)
- [Model packages and runtime](docs/model-package.md)
- [Reproducibility and acceptance checks](docs/reproducibility.md)

## License

MIT. See [LICENSE](LICENSE).
