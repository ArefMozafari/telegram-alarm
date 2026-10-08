"""Tests for the config helpers in src/notifier.py."""
import notifier

VALID_CONFIG = {
    "api_id": 12345,
    "api_hash": "not-a-real-api-hash",
    "chats": ["SomeBot"],
}


class TestWatchedChats:
    def test_should_return_the_chats_list_without_leading_at_signs(self):
        config = {"chats": ["@SomeBot", " some_group ", 123456]}

        assert notifier.watched_chats(config) == ["SomeBot", "some_group", 123456]

    def test_should_fall_back_to_the_older_bot_username_setting(self):
        config = {"bot_username": "@SomeBot"}

        assert notifier.watched_chats(config) == ["SomeBot"]

    def test_should_prefer_chats_over_bot_username(self):
        config = {"chats": ["first_chat"], "bot_username": "SomeBot"}

        assert notifier.watched_chats(config) == ["first_chat"]

    def test_should_return_an_empty_list_when_neither_setting_is_present(self):
        assert notifier.watched_chats({}) == []


class TestAlertHeadline:
    def test_should_fill_the_chat_placeholder_with_the_chat_name(self):
        config = {"headline": "New message from {chat}"}

        assert notifier.alert_headline(config, "Jobs Bot") == "New message from Jobs Bot"

    def test_should_default_to_the_chat_name(self):
        assert notifier.alert_headline({}, "Jobs Bot") == "Jobs Bot"

    def test_should_fall_back_to_a_generic_headline_when_the_result_is_empty(self):
        assert notifier.alert_headline({}, "") == "New message"

    def test_should_keep_other_braces_as_written(self):
        config = {"headline": "{job} alert"}

        assert notifier.alert_headline(config, "Jobs Bot") == "{job} alert"


class TestConfigReady:
    def test_should_accept_credentials_and_a_chat(self):
        assert notifier.config_ready(VALID_CONFIG)

    def test_should_accept_the_older_bot_username_setting(self):
        config = {key: value for key, value in VALID_CONFIG.items() if key != "chats"}
        config["bot_username"] = "SomeBot"

        assert notifier.config_ready(config)

    def test_should_reject_a_config_without_chats(self):
        config = {key: value for key, value in VALID_CONFIG.items() if key != "chats"}

        assert not notifier.config_ready(config)

    def test_should_reject_the_example_chat_placeholder(self):
        config = {**VALID_CONFIG, "chats": ["PASTE_CHAT_USERNAME"]}

        assert not notifier.config_ready(config)

    def test_should_reject_the_example_api_hash_placeholder(self):
        config = {**VALID_CONFIG, "api_hash": "PASTE_API_HASH_HERE"}

        assert not notifier.config_ready(config)

    def test_should_reject_a_missing_api_id(self):
        config = {**VALID_CONFIG, "api_id": 0}

        assert not notifier.config_ready(config)
