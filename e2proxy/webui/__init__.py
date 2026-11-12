"""Browser UI: page builders, i18n, shared shell and the JavaScript self-check.

All browser-facing markup/JS lives in this package. Pages only depend on the
public accessors of the core modules (config, channels, favorites, logs,
accesslog, version), so the UI stays a clean, separately testable layer.
"""
