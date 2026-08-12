from core.version import (
    APP_DISPLAY_VERSION,
    APP_VERSION,
    INTERNAL_APPLICATION_ID,
    PRODUCT_NAME,
)


def test_local_prompt_studio_identity_and_version():
    assert PRODUCT_NAME == "Local Prompt Studio"
    assert INTERNAL_APPLICATION_ID == "local_prompt_studio"
    assert APP_VERSION == "2.0.0-beta.1"
    assert APP_DISPLAY_VERSION == "v2.0.0-beta.1"
