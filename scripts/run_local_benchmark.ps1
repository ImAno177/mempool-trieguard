param(
  [string]$DatasetRoot = "D:\blockchain\da\29212703",
  [string]$Config = "configs\app.yaml",
  [string]$ResultsDir = "results"
)

New-Item -ItemType Directory -Force .\bin | Out-Null
go build -o .\bin\detector-cli.exe .\cmd\detector-cli

python python\benchmark_pipeline.py --dataset-root $DatasetRoot --config $Config --results-dir $ResultsDir --detector-cli .\bin\detector-cli.exe
