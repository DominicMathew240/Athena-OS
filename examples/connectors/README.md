# Connector examples

Reference material for [Writing a connector](../../docs/guides/writing-a-connector.md). **Nothing here
has been run against a real scanner or any vendor protocol.**

| File | What it is |
| --- | --- |
| `synthetic_scanner.py` | The smallest complete `DataSourceConnector`, generating synthetic PNGs; runs with no hardware and no console (`PYTHONPATH=sdk/python python -m examples.connectors.synthetic_scanner`). |
| `connector.folder.example.json` | Config for the folder-watching reference connector → console check screen. |
| `connector.http_push.example.json` | Config for the HTTP push receiver (loopback, behind a TLS proxy in production). |

Credentials are never in these files: set `ATHENA_CONNECTOR_USER` and `ATHENA_CONNECTOR_PASSWORD`
(and the variable named by `secret_env` for HTTP push) in the environment.
