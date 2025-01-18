# How to set up telemetry (not enabled by default)

1. Download the OpenTelemetry Collector (`otelcol`) binary for your platform. It does not have any dependencies.
 - [macOS](https://opentelemetry.io/docs/collector/installation/#macos)
 - [Linux](https://opentelemetry.io/docs/collector/installation/#linux)
 - [Windows](https://opentelemetry.io/docs/collector/installation/#windows) (not tested)
2. Write a configuration file for OpenTelemetry Collector. For example, Axiom supports OpenTelemetry. Consult your tracing frontend for more information. 
```
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318

processors:
  batch:

exporters:
  otlphttp:
    compression: gzip
    endpoint: https://api.axiom.co
    headers:
      authorization: Bearer xaat-
      x-axiom-dataset: elysium
    # You may need to enable this under high load
    # sending_queue:
    #  enabled: true
    #  queue_size: 1000000

service:
  pipelines:
    traces:
      receivers:
        - otlp
      processors:
      exporters:
        - otlphttp
```
3. Start the OpenTelemetry collector with this configuration file. Run `./otelcol -c otelcol.yaml`.
