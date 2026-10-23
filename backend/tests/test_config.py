import pytest
import json
import logging

from app.config import Settings, validate_settings
from app.observability import JsonLogFormatter


def test_production_accepts_explicit_https_origin_and_public_feed():
    validate_settings(Settings(app_env="production", market_data_provider="biquote",
                              allowed_origin="https://console.example.com"))


def test_production_rejects_http_origin_and_test_provider_overrides():
    with pytest.raises(ValueError, match="HTTPS"):
        validate_settings(Settings(app_env="production", market_data_provider="biquote"))
    with pytest.raises(ValueError, match="test endpoint overrides"):
        validate_settings(Settings(app_env="production", market_data_provider="biquote",
                                   allowed_origin="https://console.example.com",
                                   oanda_rest_url="http://127.0.0.1:8101"))


def test_provider_database_and_origin_configuration_must_be_consistent():
    with pytest.raises(ValueError, match="OANDA_API_KEY and OANDA_ACCOUNT_ID"):
        validate_settings(Settings(app_env="production", market_data_provider="oanda",
                                   oanda_api_key="token", allowed_origin="https://console.example.com"))
    with pytest.raises(ValueError, match="both be set"):
        validate_settings(Settings(app_env="production", market_data_provider="biquote",
                                  allowed_origin="https://console.example.com",
                                  supabase_url="https://example.supabase.co"))
    with pytest.raises(ValueError, match="without paths"):
        validate_settings(Settings(allowed_origin="https://console.example.com/app"))


def test_multiple_origins_are_parsed_without_wildcards():
    settings = Settings(allowed_origin="https://console.example.com, https://preview.example.com")
    assert settings.allowed_origins == ["https://console.example.com", "https://preview.example.com"]


def test_vercel_mode_requires_production_and_allows_same_origin_without_cors_allowlist():
    hosted = Settings(app_env="production", deployment_mode="vercel", market_data_provider="biquote",
                      allowed_origin="")
    validate_settings(hosted)
    with pytest.raises(ValueError, match="requires APP_ENV=production"):
        validate_settings(Settings(deployment_mode="vercel", market_data_provider="biquote"))


def test_json_logs_include_safe_fields_and_drop_unapproved_secret_fields():
    record = logging.LogRecord("api", logging.INFO, "main.py", 12, "request completed", (), None)
    record.request_id = "request-1"
    record.event = "http_request_complete"
    record.api_key = "must-not-appear"
    payload = json.loads(JsonLogFormatter().format(record))
    assert payload["request_id"] == "request-1"
    assert payload["event"] == "http_request_complete"
    assert "api_key" not in payload and "must-not-appear" not in json.dumps(payload)
