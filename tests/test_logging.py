import json
import logging
import logging.config
import sys

import pytest

from harry.logconfig import JSONFormatter, build_logging_config


@pytest.fixture(autouse=True)
def clear_log_env(monkeypatch):
    """Start every test with the logging env vars unset so resolution is hermetic."""
    for var in ("DJANGO_ENV", "DJANGO_LOG_LEVEL", "DJANGO_LOG_FORMAT"):
        monkeypatch.delenv(var, raising=False)


def make_record(
    *, msg="hello", args=(), level=logging.INFO, exc_info=None, func="do_work", **extra
):
    """Build a LogRecord, attaching any keyword args as ``extra``-style attributes."""
    record = logging.LogRecord(
        name="harry.test",
        level=level,
        pathname=__file__,
        lineno=10,
        msg=msg,
        args=args,
        exc_info=exc_info,
        func=func,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


# build_logging_config: structure


def test_config_shape():
    config = build_logging_config(env="prod")
    assert config["version"] == 1
    assert config["disable_existing_loggers"] is False
    assert set(config["formatters"]) == {"console", "json"}
    assert "console" in config["handlers"]
    for name in (
        "django",
        "django.server",
        "django.request",
        "django.security",
        "harry",
    ):
        assert name in config["loggers"]
    assert config["root"]["handlers"] == ["console"]


# build_logging_config: env / level / fmt resolution


@pytest.mark.parametrize(
    "env,level,fmt",
    [
        ("local", "DEBUG", "console"),
        ("test", "WARNING", "console"),
        ("prod", "INFO", "json"),
    ],
)
def test_env_profile_defaults(env, level, fmt):
    config = build_logging_config(env=env)
    assert config["root"]["level"] == level
    assert config["loggers"]["harry"]["level"] == level
    assert config["handlers"]["console"]["formatter"] == fmt


@pytest.mark.parametrize(
    "env,request_level",
    [("local", "WARNING"), ("test", "WARNING"), ("prod", "ERROR")],
)
def test_django_request_level_by_env(env, request_level):
    config = build_logging_config(env=env)
    assert config["loggers"]["django.request"]["level"] == request_level


def test_env_defaults_to_local():
    config = build_logging_config()
    assert config["root"]["level"] == "DEBUG"
    assert config["handlers"]["console"]["formatter"] == "console"


def test_explicit_args_are_normalized():
    config = build_logging_config(env="prod", level="debug", fmt="console")
    assert config["root"]["level"] == "DEBUG"
    assert config["handlers"]["console"]["formatter"] == "console"


def test_env_var_selects_profile(monkeypatch):
    monkeypatch.setenv("DJANGO_ENV", "prod")
    config = build_logging_config()
    assert config["root"]["level"] == "INFO"
    assert config["handlers"]["console"]["formatter"] == "json"


def test_env_vars_override_profile_defaults(monkeypatch):
    monkeypatch.setenv("DJANGO_LOG_LEVEL", "error")
    monkeypatch.setenv("DJANGO_LOG_FORMAT", "json")
    config = build_logging_config(env="local")
    assert config["root"]["level"] == "ERROR"
    assert config["handlers"]["console"]["formatter"] == "json"


def test_explicit_args_beat_env_vars(monkeypatch):
    monkeypatch.setenv("DJANGO_LOG_LEVEL", "error")
    monkeypatch.setenv("DJANGO_LOG_FORMAT", "console")
    config = build_logging_config(env="prod", level="info", fmt="json")
    assert config["root"]["level"] == "INFO"
    assert config["handlers"]["console"]["formatter"] == "json"


def test_invalid_env_raises():
    with pytest.raises(ValueError):
        build_logging_config(env="staging")


def test_invalid_fmt_raises():
    with pytest.raises(ValueError):
        build_logging_config(fmt="xml")


def test_invalid_level_raises():
    with pytest.raises(ValueError):
        build_logging_config(level="VERBOSE")


def test_invalid_level_env_var_raises(monkeypatch):
    monkeypatch.setenv("DJANGO_LOG_LEVEL", "LOUD")
    with pytest.raises(ValueError):
        build_logging_config()


# build_logging_config: extra_loggers


def test_extra_loggers_merge_keeps_builtins():
    config = build_logging_config(
        extra_loggers={"myapp": {"level": "INFO", "handlers": ["console"]}}
    )
    assert "myapp" in config["loggers"]
    assert "harry" in config["loggers"]


def test_extra_loggers_can_override_builtin():
    config = build_logging_config(
        extra_loggers={"harry": {"level": "ERROR", "handlers": ["console"]}}
    )
    assert config["loggers"]["harry"]["level"] == "ERROR"


# JSONFormatter


def test_json_formatter_basic_fields():
    data = json.loads(JSONFormatter().format(make_record(msg="sent %s", args=("ok",))))
    assert data["level"] == "INFO"
    assert data["logger"] == "harry.test"
    assert data["msg"] == "sent ok"
    assert "ts" in data


def test_json_formatter_omits_reserved_attrs():
    data = json.loads(JSONFormatter().format(make_record()))
    # Internal LogRecord attributes must not leak. ``msg``/``level``/``logger``/``func``/
    # ``lineno`` are the deliberately surfaced keys; the raw names are renamed away.
    for reserved in ("pathname", "args", "levelname", "name", "levelno", "funcName"):
        assert reserved not in data


def test_json_formatter_includes_source_location():
    data = json.loads(JSONFormatter().format(make_record(func="send_email")))
    assert data["func"] == "send_email"
    assert data["lineno"] == 10


def test_json_formatter_includes_extra():
    data = json.loads(JSONFormatter().format(make_record(message_id="abc-123")))
    assert data["message_id"] == "abc-123"


def test_json_formatter_extra_cannot_clobber_canonical_fields():
    # ``level``/``logger`` are not reserved LogRecord attributes, so logging accepts
    # them in ``extra``; the canonical payload fields must still win because the log
    # shipper parses severity from ``level``.
    record = make_record(logger="clobbered", level_hint="ok")
    record.level = "clobbered"
    data = json.loads(JSONFormatter().format(record))
    assert data["level"] == "INFO"
    assert data["logger"] == "harry.test"
    assert data["level_hint"] == "ok"


def test_json_formatter_includes_exception():
    try:
        raise ValueError("boom")
    except ValueError:
        record = make_record(exc_info=sys.exc_info())
    data = json.loads(JSONFormatter().format(record))
    assert "ValueError: boom" in data["exc_info"]


def test_json_formatter_includes_trace_fields_when_present():
    record = make_record(
        otelTraceID="trace-1", otelSpanID="span-1", otelServiceName="svc"
    )
    data = json.loads(JSONFormatter().format(record))
    assert data["trace_id"] == "trace-1"
    assert data["span_id"] == "span-1"
    assert data["service.name"] == "svc"
    # Raw otel attribute names are not leaked as extras.
    assert "otelTraceID" not in data


def test_json_formatter_omits_trace_fields_when_absent():
    data = json.loads(JSONFormatter().format(make_record()))
    assert "trace_id" not in data
    assert "span_id" not in data


# Integration: the produced config is accepted by dictConfig and emits JSON


@pytest.fixture
def isolate_logging():
    """Snapshot and restore the loggers build_logging_config touches."""
    names = ("", "harry", "django", "django.request", "django.security")
    saved = {
        name: (
            logging.getLogger(name).level,
            logging.getLogger(name).handlers[:],
            logging.getLogger(name).propagate,
        )
        for name in names
    }
    try:
        yield
    finally:
        for name, (level, handlers, propagate) in saved.items():
            logger = logging.getLogger(name)
            logger.setLevel(level)
            logger.handlers[:] = handlers
            logger.propagate = propagate


def test_dictconfig_emits_valid_json_line(isolate_logging, capsys):
    logging.config.dictConfig(build_logging_config(env="prod", fmt="json"))
    logging.getLogger("harry.integration").info("hello %s", "world")

    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    parsed = [json.loads(line) for line in lines]
    record = next(item for item in parsed if item["logger"] == "harry.integration")
    assert record["level"] == "INFO"
    assert record["msg"] == "hello world"
    assert "ts" in record
